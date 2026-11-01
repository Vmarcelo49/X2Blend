"""Reader for DirectX .X files (txt, bin, tzip and bzip). No Blender dependency.

Both encodings reduce to the same tree: an object holds its numbers in file order,
its strings, and its children. Binary files flatten declared template members into
integer and float runs, so the templates themselves are never needed, and floats are
kept as the file's own bits instead of passing through text.

Values are left exactly as stored: matrices are row-vector and left-handed, and key
times are ticks. Conversion belongs to build.py.
"""

import re
import struct
import zlib
from array import array
from dataclasses import dataclass, field

# Binary tokens are 16-bit; the MSDN text's 32-bit claim does not match shipped files.
_NAME, _STRING, _INTEGER = 1, 2, 3
_GUID, _INT_LIST, _FLOAT_LIST = 5, 6, 7
_OBRACE, _CBRACE = 10, 11
_TEMPLATE = 31

KEY_ROTATION, KEY_SCALE, KEY_POSITION = 0, 1, 2
_KEY_MATRIX = (3, 4)
_KEY_ARITY = {KEY_ROTATION: 4, KEY_SCALE: 3, KEY_POSITION: 3, 3: 16, 4: 16}


class XFileError(ValueError):
    pass


# ---------------------------------------------------------------------------
# Object tree
# ---------------------------------------------------------------------------

@dataclass
class XObject:
    type: str
    name: bytes = b""
    reference: bool = False
    numbers: list = field(default_factory=list)   # arrays, in file order
    strings: list = field(default_factory=list)   # bytes
    children: list = field(default_factory=list)  # XObject, references included

    def child(self, type_name):
        return next((c for c in self.children if c.type == type_name and not c.reference), None)

    def reader(self):
        return _Numbers(self.numbers)


class _Numbers:
    """Walks an object's numbers without caring how the file split them into runs."""

    def __init__(self, chunks):
        self._chunks = chunks
        self._chunk = 0
        self._pos = 0

    def take(self, count):
        out = None
        while count > 0:
            if self._chunk >= len(self._chunks):
                raise XFileError("object ends before its declared data")
            chunk = self._chunks[self._chunk]
            piece = chunk[self._pos:self._pos + count]
            count -= len(piece)
            self._pos += len(piece)
            if self._pos >= len(chunk):
                self._chunk += 1
                self._pos = 0
            out = piece if out is None else list(out) + list(piece)
        return [] if out is None else out

    def integer(self):
        return int(self.take(1)[0])

    def count(self, limit=1 << 26):
        value = self.integer()
        if value > limit:
            raise XFileError(f"implausible element count {value}")
        return value


class _BinaryParser:
    def __init__(self, data, start, float_format):
        self.data = data
        self.pos = start
        self.float_format = float_format
        self.float_size = 8 if float_format == "d" else 4

    def _u16(self):
        value = struct.unpack_from("<H", self.data, self.pos)[0]
        self.pos += 2
        return value

    def _u32(self):
        value = struct.unpack_from("<I", self.data, self.pos)[0]
        self.pos += 4
        return value

    def _bytes(self, size):
        if self.pos + size > len(self.data):
            raise XFileError("binary token runs past the end of the file")
        value = self.data[self.pos:self.pos + size]
        self.pos += size
        return value

    def _skip_payload(self, token):
        if token == _NAME:
            self._bytes(self._u32())
        elif token == _STRING:
            self._bytes(self._u32())
            self._u16()
        elif token == _INTEGER:
            self._u32()
        elif token == _GUID:
            self._bytes(16)
        elif token == _INT_LIST:
            self._bytes(self._u32() * 4)
        elif token == _FLOAT_LIST:
            self._bytes(self._u32() * self.float_size)

    def _skip_template(self):
        depth = 0
        while True:
            token = self._u16()
            self._skip_payload(token)
            if token == _OBRACE:
                depth += 1
            elif token == _CBRACE:
                depth -= 1
                if depth == 0:
                    return

    def _peek(self):
        return struct.unpack_from("<H", self.data, self.pos)[0]

    def _object(self, type_name):
        obj = XObject(type_name.decode("latin-1"))
        while True:
            token = self._u16()
            if token == _NAME:
                obj.name = self._bytes(self._u32())
            elif token == _GUID:
                self._bytes(16)
            elif token == _OBRACE:
                break
            else:
                raise XFileError(f"unexpected token {token} in the header of {obj.type}")
        while True:
            token = self._u16()
            if token == _CBRACE:
                return obj
            if token == _INT_LIST:
                obj.numbers.append(array("I", self._bytes(self._u32() * 4)))
            elif token == _FLOAT_LIST:
                count = self._u32()
                obj.numbers.append(array(self.float_format, self._bytes(count * self.float_size)))
            elif token == _INTEGER:
                obj.numbers.append(array("I", (self._u32(),)))
            elif token == _STRING:
                obj.strings.append(self._bytes(self._u32()))
                self._u16()
            elif token == _NAME:
                obj.children.append(self._object(self._bytes(self._u32())))
            elif token == _OBRACE:
                obj.children.append(self._reference())
            else:
                self._skip_payload(token)

    def _reference(self):
        ref = XObject("", reference=True)
        while True:
            token = self._u16()
            if token == _CBRACE:
                return ref
            if token == _NAME:
                ref.name = self._bytes(self._u32())
            else:
                self._skip_payload(token)

    def run(self):
        roots = []
        while self.pos + 2 <= len(self.data):
            token = self._u16()
            if token == _TEMPLATE:
                self._skip_template()
            elif token == _NAME:
                roots.append(self._object(self._bytes(self._u32())))
            elif token == 0:
                break  # padding after the last object
            else:
                raise XFileError(f"unexpected top-level token {token}")
        return roots


_TEXT_TOKEN = re.compile(
    r'//[^\n]*|#[^\n]*|/\*.*?\*/|"[^"]*"|[{}]|<[^>]*>|[;,]+|[^\s{}<>;,"]+', re.S)
_TEXT_NUMBER = re.compile(r"[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?\Z")


class _TextParser:
    def __init__(self, text):
        self.tokens = [t for t in _TEXT_TOKEN.findall(text)
                       if t[0] not in ";," and not t.startswith(("//", "#", "/*"))]
        self.pos = 0

    def _next(self):
        if self.pos >= len(self.tokens):
            raise XFileError("text file ends inside an object")
        token = self.tokens[self.pos]
        self.pos += 1
        return token

    def _object(self, type_name):
        obj = XObject(type_name)
        while True:
            token = self._next()
            if token == "{":
                break
            if not token.startswith("<"):
                obj.name = token.encode("latin-1")
        numbers = []
        def flush():
            if numbers:
                obj.numbers.append(array("d", numbers))
                numbers.clear()
        while True:
            token = self._next()
            if token == "}":
                flush()
                return obj
            if token == "{":
                flush()
                ref = XObject("", reference=True)
                while (inner := self._next()) != "}":
                    if not inner.startswith("<"):
                        ref.name = inner.encode("latin-1")
                obj.children.append(ref)
            elif token.startswith('"'):
                flush()
                obj.strings.append(token[1:-1].encode("latin-1"))
            elif _TEXT_NUMBER.match(token):
                numbers.append(float(token))
            else:
                flush()
                obj.children.append(self._object(token))

    def run(self):
        roots = []
        while self.pos < len(self.tokens):
            token = self._next()
            if token == "template":
                while self._next() != "{":
                    pass
                depth = 1
                while depth:
                    inner = self._next()
                    depth += (inner == "{") - (inner == "}")
            else:
                roots.append(self._object(token))
        return roots


def _inflate_mszip(data, start):
    """Blocks are chained: each one's deflate stream uses the previous 32 KB as its dictionary."""
    want = struct.unpack_from("<I", data, start)[0] - 16
    pos = start + 4
    out = bytearray()
    while len(out) < want:
        if pos + 6 > len(data):
            raise XFileError("MSZIP block header runs past the end of the file")
        block_out, block_comp = struct.unpack_from("<HH", data, pos)
        if data[pos + 4:pos + 6] != b"CK":
            raise XFileError("MSZIP block is missing its CK signature")
        inflater = zlib.decompressobj(-15, zdict=bytes(out[-32768:]))
        block = inflater.decompress(data[pos + 6:pos + 4 + block_comp])
        if len(block) != block_out:
            raise XFileError("MSZIP block size does not match its header")
        out += block
        pos += 4 + block_comp
    return bytes(out)


def read_objects(data):
    """Parse a whole file into its top-level objects."""
    if len(data) < 16 or data[:4] != b"xof ":
        raise XFileError("not a DirectX .X file")
    encoding = data[8:12].decode("latin-1").strip()
    float_format = "d" if data[12:16] == b"0064" else "f"
    start = 16
    if encoding in ("bzip", "tzip"):
        data = _inflate_mszip(data, 16)
        start = 0
        if data[:4] == b"xof ":  # a compressed text file keeps its own header
            start = 16
    if encoding in ("txt", "tzip"):
        return _TextParser(data[start:].decode("latin-1")).run()
    if encoding in ("bin", "bzip"):
        return _BinaryParser(data, start, float_format).run()
    raise XFileError(f"unsupported .X encoding {encoding!r}")


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------

def decode_name(raw):
    """Shipped names and texture paths are Shift-JIS, with an occasional trailing NUL."""
    return raw.rstrip(b"\0").decode("cp932", errors="replace")


@dataclass
class Frame:
    name: str
    parent: int
    local: list  # 16 floats, row-major, as stored


@dataclass
class Material:
    name: str
    diffuse: tuple
    power: float
    specular: tuple
    emissive: tuple
    texture: str = ""


@dataclass
class Skin:
    bone: str
    vertices: list
    weights: list
    offset: list  # mesh space -> bone space, 16 floats as stored


@dataclass
class Mesh:
    name: str
    frame: int                     # owning frame, -1 at the top level
    positions: list                # flat xyz
    faces: list                    # vertex index tuples
    normals: list = None           # flat xyz
    normal_faces: list = None      # normal index tuples, parallel to faces
    uvs: list = None               # flat uv, parallel to positions
    face_materials: list = None    # material index per face
    materials: list = field(default_factory=list)
    skins: list = field(default_factory=list)


@dataclass
class KeySequence:
    key_type: int
    times: list
    values: list   # flat, _KEY_ARITY[key_type] floats per key

    @property
    def is_matrix(self):
        return self.key_type in _KEY_MATRIX


@dataclass
class Channel:
    frame: str
    sequences: list


@dataclass
class AnimationSet:
    name: str
    channels: list


@dataclass
class Model:
    ticks_per_second: float = 4800.0   # the D3DX default, when the file has no override
    frames: list = field(default_factory=list)   # parents always before children
    meshes: list = field(default_factory=list)
    sets: list = field(default_factory=list)     # file order

    def frame_index(self, name):
        return next((i for i, f in enumerate(self.frames) if f.name == name), -1)


def _read_material(obj):
    n = obj.reader()
    colors = list(n.take(11))
    if len(colors) != 11:
        raise XFileError("short Material")
    texture = obj.child("TextureFilename") or obj.child("TextureFileName")
    return Material(
        decode_name(obj.name),
        tuple(colors[0:4]), colors[4], tuple(colors[5:8]), tuple(colors[8:11]),
        decode_name(texture.strings[0]) if texture and texture.strings else "",
    )


def _read_index_lists(n, count):
    lists = []
    for _ in range(count):
        size = n.count(1 << 16)
        lists.append(tuple(int(v) for v in n.take(size)))
    return lists


def _read_mesh(obj, frame, shared_materials):
    n = obj.reader()
    vertex_count = n.count()
    positions = list(n.take(vertex_count * 3))
    faces = _read_index_lists(n, n.count())
    mesh = Mesh(decode_name(obj.name), frame, positions, faces)

    normals = obj.child("MeshNormals")
    if normals:
        r = normals.reader()
        mesh.normals = list(r.take(r.count() * 3))
        mesh.normal_faces = _read_index_lists(r, r.count())
        if len(mesh.normal_faces) != len(faces):
            raise XFileError("MeshNormals has a different face count than its Mesh")

    coords = obj.child("MeshTextureCoords")
    if coords:
        r = coords.reader()
        mesh.uvs = list(r.take(r.count() * 2))
        if len(mesh.uvs) != vertex_count * 2:
            raise XFileError("MeshTextureCoords has a different vertex count than its Mesh")

    material_list = obj.child("MeshMaterialList")
    if material_list:
        r = material_list.reader()
        r.count()
        indices = [int(v) for v in r.take(r.count())]
        # A short list means the last index covers the remaining faces.
        indices += [indices[-1] if indices else 0] * (len(faces) - len(indices))
        mesh.face_materials = indices
        for child in material_list.children:
            if child.reference:
                if child.name not in shared_materials:
                    raise XFileError(f"unresolved material reference {decode_name(child.name)!r}")
                mesh.materials.append(shared_materials[child.name])
            elif child.type == "Material":
                mesh.materials.append(_read_material(child))

    for child in obj.children:
        if child.type == "SkinWeights" and not child.reference:
            if not child.strings:
                raise XFileError("SkinWeights without a bone name")
            r = child.reader()
            count = r.count()
            vertices = [int(v) for v in r.take(count)]
            weights = list(r.take(count))
            mesh.skins.append(Skin(decode_name(child.strings[0]), vertices, weights, list(r.take(16))))
    return mesh


def _read_animation(obj):
    frame = None
    sequences = []
    for child in obj.children:
        if child.reference:
            frame = decode_name(child.name)
        elif child.type == "AnimationKey":
            n = child.reader()
            key_type = n.integer()
            if key_type not in _KEY_ARITY:
                raise XFileError(f"unknown AnimationKey type {key_type}")
            arity = _KEY_ARITY[key_type]
            count = n.count()
            times, values = [], []
            for _ in range(count):
                times.append(float(n.integer()))
                if n.integer() != arity:
                    raise XFileError(f"AnimationKey type {key_type} with the wrong value count")
                values.extend(n.take(arity))
            order = sorted(range(count), key=times.__getitem__)
            if order != list(range(count)):
                times = [times[i] for i in order]
                values = [v for i in order for v in values[i * arity:(i + 1) * arity]]
            sequences.append(KeySequence(key_type, times, values))
    return Channel(frame, sequences) if frame is not None else None


def _read_frame(obj, parent, model, shared_materials):
    transform = obj.child("FrameTransformMatrix")
    local = list(transform.reader().take(16)) if transform else [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
    index = len(model.frames)
    model.frames.append(Frame(decode_name(obj.name), parent, local))
    for child in obj.children:
        if child.reference:
            continue
        if child.type == "Frame":
            _read_frame(child, index, model, shared_materials)
        elif child.type == "Mesh":
            model.meshes.append(_read_mesh(child, index, shared_materials))


def read_model(data):
    """bytes of a .X file -> Model."""
    model = Model()
    roots = read_objects(data)
    shared_materials = {o.name: _read_material(o) for o in roots if o.type == "Material"}
    for obj in roots:
        if obj.type == "AnimTicksPerSecond":
            model.ticks_per_second = float(obj.reader().integer())
        elif obj.type == "Frame":
            _read_frame(obj, -1, model, shared_materials)
        elif obj.type == "Mesh":
            model.meshes.append(_read_mesh(obj, -1, shared_materials))
        elif obj.type == "AnimationSet":
            channels = [c for c in (_read_animation(a) for a in obj.children
                                    if a.type == "Animation" and not a.reference) if c]
            model.sets.append(AnimationSet(decode_name(obj.name), channels))
    return model


def read_model_file(path):
    with open(path, "rb") as f:
        return read_model(f.read())
