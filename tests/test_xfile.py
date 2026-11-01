"""Reader checks that need neither Blender nor the game: one small model in every encoding."""

import importlib.util
import os
import struct
import sys
import zlib

path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "x2blend", "xfile.py")
spec = importlib.util.spec_from_file_location("xfile", path)
xfile = importlib.util.module_from_spec(spec)
sys.modules["xfile"] = xfile
spec.loader.exec_module(xfile)

IDENTITY = [1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0]
BONE_NAME = "Bone骨".encode("cp932")

TEXT = b"""xof 0303txt 0032
template Unused { <00000000-0000-0000-0000-000000000000> DWORD a; }
// a comment with { an unbalanced brace
AnimTicksPerSecond { 4800; }
Frame Root {
  FrameTransformMatrix { 1.0,0,0,0, 0,1.0,0,0, 0,0,1.0,0, 0,0,0,1.0;; }
  Frame """ + BONE_NAME + b""" {
    FrameTransformMatrix { 1.0,0,0,0, 0,1.0,0,0, 0,0,1.0,0, 0,0,0,1.0;; }
    Mesh {
      3; 0.0;0.0;0.0;, 1.0;0.0;0.0;, 0.0;1.0;0.0;;
      1; 3;0,1,2;;
      MeshNormals { 1; 0.0;0.0;1.0;; 1; 3;0,0,0;; }
      MeshTextureCoords { 3; 0.0;0.0;, 1.0;0.0;, 0.0;1.0;; }
      MeshMaterialList { 1; 1; 0;;
        Material { 1.0;1.0;1.0;1.0;; 2.0; 0.0;0.0;0.0;; 0.5;0.5;0.5;;
          TextureFilename { "tex.bmp"; }
        }
      }
      SkinWeights { "Root"; 1; 0; 1.0; 1.0,0,0,0, 0,1.0,0,0, 0,0,1.0,0, 0,0,0,1.0;; }
    }
  }
}
AnimationSet Anim-1 {
  Animation {
    { Root }
    AnimationKey { 4; 2;
      0;16;1.0,0,0,0, 0,1.0,0,0, 0,0,1.0,0, 0,0,0,1.0;;;,
      80;16;1.0,0,0,0, 0,1.0,0,0, 0,0,1.0,0, 5.0,0,0,1.0;;;;
    }
  }
}
"""


def t(token):
    return struct.pack("<H", token)


def name(raw):
    return t(1) + struct.pack("<I", len(raw)) + raw


def string(raw):
    return t(2) + struct.pack("<I", len(raw)) + raw + t(20)


def ints(*values):
    return t(6) + struct.pack(f"<I{len(values)}I", len(values), *values)


def floats(*values):
    return t(7) + struct.pack(f"<I{len(values)}f", len(values), *values)


OPEN, CLOSE = t(10), t(11)


def obj(type_name, *body, object_name=None):
    head = name(type_name)
    if object_name is not None:
        head += name(object_name)
    return head + OPEN + b"".join(body) + CLOSE


def binary_body():
    material = obj(b"Material", floats(1, 1, 1, 1, 2, 0, 0, 0, 0.5, 0.5, 0.5),
                   obj(b"TextureFilename", string(b"tex.bmp")))
    mesh = obj(
        b"Mesh", ints(3), floats(0, 0, 0, 1, 0, 0, 0, 1, 0), ints(1, 3, 0, 1, 2),
        obj(b"MeshNormals", ints(1), floats(0, 0, 1), ints(1, 3, 0, 0, 0)),
        obj(b"MeshTextureCoords", ints(3), floats(0, 0, 1, 0, 0, 1)),
        obj(b"MeshMaterialList", ints(1, 1, 0), material),
        obj(b"SkinWeights", string(b"Root"), ints(1, 0), floats(1.0, *IDENTITY)),
    )
    bone = obj(b"Frame", obj(b"FrameTransformMatrix", floats(*IDENTITY)), mesh, object_name=BONE_NAME)
    root = obj(b"Frame", obj(b"FrameTransformMatrix", floats(*IDENTITY)), bone, object_name=b"Root")
    moved = list(IDENTITY)
    moved[12] = 5.0
    key = obj(b"AnimationKey", ints(4, 2, 0, 16), floats(*IDENTITY), ints(80, 16), floats(*moved))
    animation = obj(b"Animation", OPEN + name(b"Root") + CLOSE, key)
    # A template, to prove it is skipped.
    template = t(31) + name(b"Unused") + OPEN + t(5) + bytes(16) + t(41) + name(b"a") + t(20) + CLOSE
    return (template + obj(b"AnimTicksPerSecond", ints(4800)) + root
            + obj(b"AnimationSet", animation, object_name=b"Anim-1"))


def mszip(body):
    """Two chained blocks, where the second refers back into the first."""
    out = struct.pack("<I", len(body) + 16)
    middle = len(body) // 2
    done = b""
    for piece in (body[:middle], body[middle:]):
        compressor = zlib.compressobj(9, zlib.DEFLATED, -15, zdict=done[-32768:]) if done \
            else zlib.compressobj(9, zlib.DEFLATED, -15)
        deflated = compressor.compress(piece) + compressor.flush()
        out += struct.pack("<HH", len(piece), len(deflated) + 2) + b"CK" + deflated
        done += piece
    return out


def verify(model, label):
    assert model.ticks_per_second == 4800.0, label
    assert [f.name for f in model.frames] == ["Root", "Bone骨"], (label, model.frames)
    assert [f.parent for f in model.frames] == [-1, 0], label
    assert list(model.frames[0].local) == IDENTITY, label
    mesh = model.meshes[0]
    assert mesh.frame == 1, label
    assert list(mesh.positions) == [0, 0, 0, 1, 0, 0, 0, 1, 0], label
    assert mesh.faces == [(0, 1, 2)], label
    assert mesh.normal_faces == [(0, 0, 0)] and list(mesh.normals) == [0, 0, 1], label
    assert list(mesh.uvs) == [0, 0, 1, 0, 0, 1], label
    assert mesh.face_materials == [0], label
    material = mesh.materials[0]
    assert material.texture == "tex.bmp" and material.power == 2.0 and material.emissive == (0.5, 0.5, 0.5), label
    skin = mesh.skins[0]
    assert skin.bone == "Root" and list(skin.vertices) == [0] and list(skin.weights) == [1.0], label
    assert list(skin.offset) == IDENTITY, label
    animation_set = model.sets[0]
    assert animation_set.name == "Anim-1", label
    channel = animation_set.channels[0]
    assert channel.frame == "Root", label
    sequence = channel.sequences[0]
    assert sequence.key_type == 4 and sequence.times == [0.0, 80.0], label
    assert sequence.values[16 + 12] == 5.0, label


verify(xfile.read_model(TEXT), "txt")
verify(xfile.read_model(b"xof 0303bin 0032" + binary_body()), "bin")
verify(xfile.read_model(b"xof 0303bzip0032" + mszip(binary_body())), "bzip")
verify(xfile.read_model(b"xof 0303tzip0032" + mszip(TEXT)), "tzip")

for bad in (b"", b"not a model at all", b"xof 0303abc 0032"):
    try:
        xfile.read_model(bad)
    except xfile.XFileError:
        continue
    raise AssertionError(f"accepted {bad!r}")

print("xfile: OK")
