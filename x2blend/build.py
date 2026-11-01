"""Builds Blender data from an xfile.Model, without resampling any animation.

Everything is converted once by conjugating with C, which swaps Y and Z: left-handed
Y-up becomes right-handed Z-up. Conjugation is a change of basis, so it preserves
hierarchy products. C is a reflection, which negates cross products, so faces reverse
their corner order to keep facing the way the file's normals say.

The armature's rest pose is the bind pose from the SkinWeights offsets; a file's frame
matrices hold a fighting pose instead. Each bone adds a fixed rotation A because Blender
needs bone Y along the bone and 3ds Max Biped runs it along X.

With W a frame's animated world matrix, skinning needs pose * rest^-1 == W * bind^-1.
Since rest == bind * A, that gives pose == W * A, and each bone's channels come to:

    channels = rest_relative^-1 * A_parent^-1 * key * A

Every factor but the key is constant and rigid, so per key:

    location = G * key.location              (affine)
    rotation = q_G * key.rotation * q_A      (linear in the quaternion components)
    scale    = key.scale with A's axes       (a permutation)

All of those are linear in the key's components, so one LINEAR F-curve segment between
two keys is exactly the segment the game evaluates: GetSRT lerps the components and
normalizes the quaternion (actor_controller_srt.hpp), and Blender normalizes when it
turns the channels into a matrix. One key in the file is one key here.
"""

import os
from array import array

import bpy
import numpy
from mathutils import Matrix, Quaternion, Vector

from .character import read_character
from .cnv import read_cnv
from .xfile import KEY_POSITION, KEY_ROTATION, KEY_SCALE, read_model_file

_C = Matrix(((1, 0, 0, 0), (0, 0, 1, 0), (0, 1, 0, 0), (0, 0, 0, 1)))

# Shipped key scales are 1 apart from rounding (largest seen 1.5e-5). Curves of pure
# noise would cost three more F-curves per bone and animate nothing.
_UNIT_SCALE_TOLERANCE = 2e-5

_LINEAR = 1  # BeztTriple interpolation enum, for foreach_set
_DEFAULT_BONE_LENGTH = 5.0  # file units


def convert_matrix(values):
    """16 row-major floats, row-vector as stored -> column-vector in Blender's space."""
    m = Matrix((values[0:4], values[4:8], values[8:12], values[12:16]))
    m.transpose()
    return _C @ m @ _C


def convert_vector(x, y, z):
    return Vector((x, z, y))


def ticks_per_frame(model):
    """Shipped keys all land on multiples of 80 ticks at 4800/s: 60 frames per second."""
    return model.ticks_per_second / 60.0


def game_index(model, file_index):
    """The original's controller enumerates sets in reverse file order, and it binds by that index."""
    return len(model.sets) - 1 - file_index


# ---------------------------------------------------------------------------
# Skeleton plan
# ---------------------------------------------------------------------------

def _kept_frames(model, extra=()):
    """Frames that do something. Biped's scaled helper boxes are unnamed, animated by nothing, and empty."""
    used = set(extra)
    for mesh in model.meshes:
        if mesh.skins:
            used.update(model.frame_index(s.bone) for s in mesh.skins)
        elif mesh.frame >= 0:
            used.add(mesh.frame)
    for animation_set in model.sets:
        used.update(model.frame_index(c.frame) for c in animation_set.channels)
    used.discard(-1)
    for index in sorted(used):
        parent = model.frames[index].parent
        while parent >= 0 and parent not in used:
            used.add(parent)
            parent = model.frames[parent].parent
    return sorted(used)


def _bind_matrices(model, kept, warnings):
    """Frame index -> bind matrix in Blender's space."""
    locals_ = {i: convert_matrix(model.frames[i].local) for i in kept}
    bind = {}
    for mesh in model.meshes:
        for skin in mesh.skins:
            index = model.frame_index(skin.bone)
            if index < 0:
                raise ValueError(f"skin bone {skin.bone!r} has no frame")
            matrix = convert_matrix(skin.offset).inverted()
            if index in bind and _largest_difference(bind[index], matrix) > 1e-3:
                warnings.append(f"bone {skin.bone!r} has different offsets across meshes; using the first")
            bind.setdefault(index, matrix)
    # Unskinned bones take their bind from the frame matrices: from a skinned child when
    # they have one (the root sits above the T-pose), else from their parent.
    for index in reversed(kept):
        parent = model.frames[index].parent
        if index in bind and parent >= 0 and parent not in bind:
            bind[parent] = bind[index] @ locals_[index].inverted()
    for index in kept:
        if index not in bind:
            parent = model.frames[index].parent
            bind[index] = bind[parent] @ locals_[index] if parent >= 0 else locals_[index]
    return bind


def _largest_difference(a, b):
    return max(abs(a[r][c] - b[r][c]) for r in range(4) for c in range(4))


def _bone_axis_rotation(model, kept, bind):
    """A, chosen from the file: the signed local axis that bones most consistently run along."""
    total = Vector((0.0, 0.0, 0.0))
    for index in kept:
        parent = model.frames[index].parent
        if parent >= 0:
            offset = (bind[parent].inverted() @ bind[index]).translation
            if offset.length > 1e-6:
                total += offset.normalized()
    axis = max(range(3), key=lambda i: abs(total[i]))
    along = Vector((0.0, 0.0, 0.0))
    along[axis] = 1.0 if total[axis] >= 0 else -1.0
    side = Vector((1.0, 0.0, 0.0)) if axis == 2 else Vector((0.0, 0.0, 1.0))
    A = Matrix.Identity(4)
    A.col[0].xyz = along.cross(side)
    A.col[1].xyz = along
    A.col[2].xyz = side
    return A


def _bone_lengths(model, kept, bind):
    children = {i: [] for i in kept}
    for index in kept:
        parent = model.frames[index].parent
        if parent >= 0:
            children[parent].append(index)
    lengths = {}
    for index in kept:
        distances = [(bind[c].translation - bind[index].translation).length for c in children[index]]
        distances = [d for d in distances if d > 1e-3]
        if distances:
            lengths[index] = min(distances)
        else:
            parent = model.frames[index].parent
            lengths[index] = lengths[parent] * 0.5 if parent >= 0 else _DEFAULT_BONE_LENGTH
    return lengths


def _rigid(matrix):
    location, rotation, _ = matrix.decompose()
    return Matrix.LocRotScale(location, rotation, None)


def _build_armature(model, name, kept, bind, A, collection, warnings, extra_deforming=()):
    data = bpy.data.armatures.new(name)
    obj = bpy.data.objects.new(name, data)
    collection.objects.link(obj)
    view_layer = bpy.context.view_layer
    view_layer.objects.active = obj
    obj.select_set(True)

    lengths = _bone_lengths(model, kept, bind)
    deforming = {model.frame_index(s.bone) for m in model.meshes for s in m.skins}
    deforming.update(m.frame for m in model.meshes if not m.skins and m.frame >= 0)
    deforming.update(extra_deforming)
    bone_names = {}
    bpy.ops.object.mode_set(mode='EDIT')
    edit_bones = {}
    for index in kept:
        frame = model.frames[index]
        bone = data.edit_bones.new(frame.name or f"frame_{index}")
        bone_names[index] = bone.name
        bone.length = max(lengths[index], 1e-3)
        scale = bind[index].to_scale()
        if max(abs(s - 1.0) for s in scale) > 1e-3:
            warnings.append(f"bone {bone.name!r} has a scaled rest matrix; its rest pose keeps only the rigid part")
        bone.matrix = _rigid(bind[index]) @ A
        bone.use_deform = index in deforming
        edit_bones[index] = bone
    for index in kept:
        parent = model.frames[index].parent
        if parent >= 0:
            edit_bones[index].parent = edit_bones[parent]
    bpy.ops.object.mode_set(mode='OBJECT')
    return obj, bone_names


# ---------------------------------------------------------------------------
# Textures and materials
# ---------------------------------------------------------------------------

def _find_texture(directory, texture):
    """Materials name .bmp files; what ships beside them is .cnv under the same stem."""
    base = os.path.basename(texture.replace("\\", "/"))
    stem = os.path.splitext(base)[0]
    by_lower_name = {n.lower(): n for n in os.listdir(directory)} if os.path.isdir(directory) else {}
    for candidate in (stem + ".cnv", base, stem + ".bmp", stem + ".png", stem + ".tga", stem + ".dds"):
        found = by_lower_name.get(candidate.lower())
        if found:
            return os.path.join(directory, found)
    return None


def _load_image(path):
    name = os.path.basename(path)
    if not path.lower().endswith(".cnv"):
        image = bpy.data.images.load(path, check_existing=True)
        image.pack()
        return image
    width, height, rgba = read_cnv(path)
    image = bpy.data.images.new(os.path.splitext(name)[0], width, height, alpha=True)
    pixels = numpy.frombuffer(rgba, dtype=numpy.uint8).reshape(height, width, 4)
    image.pixels.foreach_set((pixels[::-1].astype(numpy.float32) / 255.0).ravel())
    image.update()
    image.pack()
    return image


def _build_material(source, directory, images, warnings):
    material = bpy.data.materials.new(os.path.splitext(source.texture)[0] or source.name or "material")
    material.diffuse_color = source.diffuse
    # The game shades with its own toon effect. Keep its inputs for whoever rebuilds that.
    material["x_diffuse"] = source.diffuse
    material["x_power"] = source.power
    material["x_specular"] = source.specular
    material["x_emissive"] = source.emissive
    material["x_texture"] = source.texture
    material.use_nodes = True
    nodes = material.node_tree.nodes
    shader = nodes.get("Principled BSDF")
    shader.inputs["Base Color"].default_value = source.diffuse
    shader.inputs["Roughness"].default_value = 1.0
    if not source.texture:
        return material
    path = _find_texture(directory, source.texture)
    if not path:
        warnings.append(f"texture {source.texture!r} not found in {directory}")
        return material
    image = images.get(path)
    if image is None:
        image = images[path] = _load_image(path)
    node = nodes.new("ShaderNodeTexImage")
    node.image = image
    node.location = (-350, 200)
    links = material.node_tree.links
    links.new(node.outputs["Color"], shader.inputs["Base Color"])
    if _alpha_is_meaningful(image):
        links.new(node.outputs["Alpha"], shader.inputs["Alpha"])
    return material


def _alpha_is_meaningful(image):
    """Files declaring 24 bpp often store a constant fourth byte that is not coverage."""
    pixels = numpy.empty(len(image.pixels), dtype=numpy.float32)
    image.pixels.foreach_get(pixels)
    alpha = pixels[3::4]
    return alpha.size > 0 and float(alpha.min()) != float(alpha.max())


# ---------------------------------------------------------------------------
# Meshes
# ---------------------------------------------------------------------------

def _vertex_influences(model, mesh):
    """Per vertex: sorted (frame index, weight) pairs. Unskinned meshes follow their frame."""
    influences = [[] for _ in range(len(mesh.positions) // 3)]
    if not mesh.skins:
        if mesh.frame >= 0:
            for entry in influences:
                entry.append((mesh.frame, 1.0))
        return influences
    for skin in mesh.skins:
        index = model.frame_index(skin.bone)
        for vertex, weight in zip(skin.vertices, skin.weights):
            if weight > 0.0:
                influences[vertex].append((index, weight))
    return [sorted(entry) for entry in influences]


def _welded(positions, influences):
    """Seam duplicates merge when nothing distinguishes them but corner data, which Blender keeps per corner."""
    remap = []
    unique = {}
    for vertex in range(len(positions) // 3):
        key = (tuple(positions[vertex * 3:vertex * 3 + 3]), tuple(influences[vertex]))
        remap.append(unique.setdefault(key, len(unique)))
    return remap, len(unique)


def _usable_faces(source, remap, name, warnings):
    """Face indices Blender can hold: at least 3 corners, in range, no vertex repeated after welding."""
    usable = []
    for f, face in enumerate(source.faces):
        if len(face) >= 3 and all(v < len(remap) for v in face) and len({remap[v] for v in face}) == len(face):
            usable.append(f)
    if len(usable) != len(source.faces):
        warnings.append(f"mesh {name!r}: dropped {len(source.faces) - len(usable)} of {len(source.faces)} degenerate faces")
    return usable


def _frame_world(model, index):
    world = Matrix.Identity(4)
    while index >= 0:
        world = convert_matrix(model.frames[index].local) @ world
        index = model.frames[index].parent
    return world


def _build_mesh(model, source, index, armature, bone_names, directory, materials, images, collection, warnings,
                attachment=None, name=None):
    """`attachment` is (rig frame index, its rigid bind), for a weapon that rides one bone of another model."""
    owner = model.frames[source.frame].name if source.frame >= 0 else ""
    name = name or source.name or owner or f"mesh_{index}"
    if attachment:
        influences = [[(attachment[0], 1.0)] for _ in range(len(source.positions) // 3)]
        place = attachment[1] @ _frame_world(model, source.frame)
    else:
        influences = _vertex_influences(model, source)
        # D3DX ignores the owning frame of a skinned mesh: its vertices are already in bind space.
        place = _frame_world(model, source.frame) if source.frame >= 0 and not source.skins else None
    normal_matrix = place.to_3x3().inverted().transposed() if place else None
    remap, unique_count = _welded(source.positions, influences)

    points = [None] * unique_count
    for vertex in range(len(remap)):
        p = convert_vector(*source.positions[vertex * 3:vertex * 3 + 3])
        points[remap[vertex]] = place @ p if place else p

    usable = _usable_faces(source, remap, name, warnings)
    faces = [tuple(remap[v] for v in reversed(source.faces[f])) for f in usable]
    data = bpy.data.meshes.new(name)
    data.from_pydata(points, [], faces)

    # from_pydata keeps face and corner order, which the per-corner data below relies on.
    if source.uvs:
        layer = data.uv_layers.new(name="UVMap")
        uv = array("f")
        for f in usable:
            for v in reversed(source.faces[f]):
                uv.extend((source.uvs[v * 2], 1.0 - source.uvs[v * 2 + 1]))  # .X V runs downward
        layer.uv.foreach_set("vector", uv)

    if source.face_materials:
        for material in source.materials:
            key = (directory, material.texture, material.diffuse, material.power, material.specular, material.emissive)
            if key not in materials:
                materials[key] = _build_material(material, directory, images, warnings)
            data.materials.append(materials[key])
        if data.materials:
            data.polygons.foreach_set("material_index", [source.face_materials[f] for f in usable])

    if source.normals and all(len(source.normal_faces[f]) == len(source.faces[f]) for f in usable):
        corner_normals = []
        for f in usable:
            for n in reversed(source.normal_faces[f]):
                normal = convert_vector(*source.normals[n * 3:n * 3 + 3])
                corner_normals.append(tuple((normal_matrix @ normal).normalized() if normal_matrix else normal))
        # No validate(): it deletes the file's double-sided faces, which share all their vertices.
        data.normals_split_custom_set(corner_normals)
    data.update()

    obj = bpy.data.objects.new(name, data)
    collection.objects.link(obj)
    obj.parent = armature
    modifier = obj.modifiers.new("Armature", 'ARMATURE')
    modifier.object = armature

    groups = {}
    done = set()
    for vertex, target in enumerate(remap):
        if target in done:
            continue
        done.add(target)
        for frame_index, weight in influences[vertex]:
            bone = bone_names[frame_index]
            if bone not in groups:
                groups[bone] = obj.vertex_groups.new(name=bone)
            groups[bone].add((target,), weight, 'REPLACE')
    return obj


# ---------------------------------------------------------------------------
# Animation
# ---------------------------------------------------------------------------

def _key_components(channel, rest_local):
    """(time, value) lists per component, in Blender's space. A component without keys holds the frame's own value."""
    rotations, locations, scales = [], [], []
    for sequence in channel.sequences:
        times, values = sequence.times, sequence.values
        if sequence.is_matrix:
            for k, time in enumerate(times):
                location, rotation, scale = convert_matrix(values[k * 16:k * 16 + 16]).decompose()
                locations.append((time, location))
                rotations.append((time, rotation))
                scales.append((time, scale))
        elif sequence.key_type == KEY_ROTATION:
            for k, time in enumerate(times):
                rotation = Quaternion(values[k * 4:k * 4 + 4]).to_matrix().to_4x4()
                rotations.append((time, (_C @ rotation @ _C).to_quaternion()))
        elif sequence.key_type == KEY_SCALE:
            for k, time in enumerate(times):
                scales.append((time, convert_vector(*values[k * 3:k * 3 + 3])))
        elif sequence.key_type == KEY_POSITION:
            for k, time in enumerate(times):
                locations.append((time, convert_vector(*values[k * 3:k * 3 + 3])))
    location, rotation, scale = rest_local.decompose()
    return (rotations or [(0.0, rotation)], locations or [(0.0, location)], scales or [(0.0, scale)])


def _add_curve(bag, path, index, group, times, values):
    curve = bag.fcurves.new(path, index=index, group_name=group)
    points = curve.keyframe_points
    points.add(len(times))
    co = array("f", bytes(8 * len(times)))
    co[0::2] = array("f", times)
    co[1::2] = array("f", values)
    points.foreach_set("co", co)
    points.foreach_set("interpolation", [_LINEAR] * len(times))
    curve.update()
    return curve


def _build_action(model, file_index, armature, bone_names, A, per_frame, warnings):
    source = model.sets[file_index]
    index = game_index(model, file_index)
    action = bpy.data.actions.new(f"{index:03d} {source.name}")
    action.use_fake_user = True
    action["x_game_index"] = index
    slot = action.slots.new(id_type='OBJECT', name=armature.name)
    bag = action.layers.new("Keys").strips.new(type='KEYFRAME').channelbag(slot, ensure=True)

    bones = armature.data.bones
    q_A = A.to_quaternion()
    # A's columns are signed axes, so conjugating a diagonal scale only permutes it.
    scale_axis = [max(range(3), key=lambda r: abs(A[r][c])) for c in range(3)]
    last_frame = 0.0
    for channel in source.channels:
        frame_index = model.frame_index(channel.frame)
        if frame_index not in bone_names:
            warnings.append(f"set {source.name!r} animates unknown frame {channel.frame!r}")
            continue
        name = bone_names[frame_index]
        bone = bones[name]
        parent = model.frames[frame_index].parent
        if parent >= 0:
            G = (bones[bone_names[parent]].matrix_local.inverted() @ bone.matrix_local).inverted() @ A.inverted()
        else:
            G = bone.matrix_local.inverted()
        q_G = G.to_quaternion()

        rest_local = convert_matrix(model.frames[frame_index].local)
        rotations, locations, scales = _key_components(channel, rest_local)
        path = f'pose.bones["{name}"]'

        frames = [t / per_frame for t, _ in rotations]
        channel_rotations = []
        previous = None
        for _, rotation in rotations:
            q = q_G @ rotation @ q_A
            # The same rotation either way, but curves must not interpolate through zero.
            # The game's controller makes this same neighbour choice (actor_controller_keys).
            if previous is not None and previous.dot(q) < 0.0:
                q.negate()
            channel_rotations.append(q)
            previous = q
        for i in range(4):
            _add_curve(bag, path + ".rotation_quaternion", i, name, frames, [q[i] for q in channel_rotations])

        frames = [t / per_frame for t, _ in locations]
        channel_locations = [G @ location for _, location in locations]
        for i in range(3):
            _add_curve(bag, path + ".location", i, name, frames, [v[i] for v in channel_locations])

        if any(abs(s[i] - 1.0) > _UNIT_SCALE_TOLERANCE for _, s in scales for i in range(3)):
            frames = [t / per_frame for t, _ in scales]
            for i in range(3):
                _add_curve(bag, path + ".scale", i, name, frames, [s[scale_axis[i]] for _, s in scales])

        last_frame = max(last_frame, rotations[-1][0] / per_frame, locations[-1][0] / per_frame)

    action.use_frame_range = True
    action.frame_start = 0.0
    action.frame_end = max(last_frame, 1.0)
    return action, slot


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _attach_weapon(weapon, armature, bone_names, bind, hands, materials, images, collection, warnings):
    """One item model on the bone of the hand that holds it when a move starts.

    Moves rebind items to hands while they run (FUN_00434a70), which is executable logic
    with no data in these files. So each model keeps one bone here, and the models no hand
    holds at the start start hidden: hands hold models 0 and 1 until a move changes that
    (roster_held_models in the core's development_animated_combat.hpp).
    """
    if not weapon.path:
        warnings.append(f"weapon model {weapon.mesh!r} is not on disk")
        return
    model = read_model_file(weapon.path)
    if model.sets:
        warnings.append(f"weapon model {weapon.mesh!r} has its own animation, which is not imported")
    held = weapon.index < 2
    frame_index = hands[weapon.index] if held else hands[0]
    stem = os.path.splitext(os.path.basename(weapon.path))[0]
    for mesh_index, mesh in enumerate(model.meshes):
        if mesh.skins:
            warnings.append(f"weapon model {weapon.mesh!r} is skinned; it rides the hand as one piece")
        suffix = f".{mesh_index}" if len(model.meshes) > 1 else ""
        obj = _build_mesh(
            model, mesh, mesh_index, armature, bone_names, os.path.dirname(weapon.path), materials, images,
            collection, warnings, attachment=(frame_index, _rigid(bind[frame_index])),
            name=f"weapon{weapon.index} {stem}{suffix}")
        obj["x_item_model"] = weapon.index
        if not held:
            obj.hide_set(True)
            obj.hide_render = True


def import_x(path, scale=0.01, set_scene_timing=True, collection=None, weapons=(), name=None):
    """Import a .X file into the current scene. Returns (armature object, warnings).

    Data stays in file units, so F-curve values read the same at any scale; `scale`
    (0.01 puts the shipped fighters at about 1.5 Blender units) lives on the object.
    """
    model = read_model_file(path)
    directory = os.path.dirname(os.path.abspath(path))
    # The shipped files are all NN.X, so the fighter is the folder's name.
    name = name or f"{os.path.basename(directory)}_{os.path.splitext(os.path.basename(path))[0]}"
    collection = collection or bpy.context.collection
    warnings = []

    # The frames FUN_00433eb0 resolves: Prop1, falling back to Prop2, and Prop2 for the
    # other hand, falling back to the first (placeAttachedProp in daybreak-decomp).
    prop1, prop2 = model.frame_index("Bip01_Prop1"), model.frame_index("Bip01_Prop2")
    main_hand = prop1 if prop1 >= 0 else prop2
    hands = (main_hand, prop2 if prop2 >= 0 else main_hand)
    if weapons and main_hand < 0:
        warnings.append("the rig has no Bip01_Prop1 or Bip01_Prop2; its weapon was not imported")
        weapons = ()

    if bpy.context.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    weapon_bones = hands if weapons else ()
    kept = _kept_frames(model, weapon_bones)
    bind = _bind_matrices(model, kept, warnings)
    A = _bone_axis_rotation(model, kept, bind)
    armature, bone_names = _build_armature(model, name, kept, bind, A, collection, warnings, weapon_bones)
    armature.scale = (scale, scale, scale)
    armature["x_source"] = os.path.basename(path)
    armature["x_ticks_per_second"] = model.ticks_per_second

    materials, images = {}, {}
    for index, mesh in enumerate(model.meshes):
        _build_mesh(model, mesh, index, armature, bone_names, directory, materials, images, collection, warnings)
    for weapon in weapons:
        _attach_weapon(weapon, armature, bone_names, bind, hands, materials, images, collection, warnings)

    per_frame = ticks_per_frame(model)
    armature.animation_data_create()
    first = None
    # Actions are created in the game's order so that the list reads 000, 001, ...
    for file_index in reversed(range(len(model.sets))):
        action, slot = _build_action(model, file_index, armature, bone_names, A, per_frame, warnings)
        if first is None:
            first = action, slot
    if first:
        armature.animation_data.action, armature.animation_data.action_slot = first
        if set_scene_timing:
            scene = bpy.context.scene
            scene.render.fps = 60
            scene.render.fps_base = 1.0
            scene.frame_start = 0
            scene.frame_end = int(first[0].frame_end + 0.5)
            scene.frame_set(0)
    return armature, warnings


def import_character(path, scale=0.01, set_scene_timing=True, collection=None):
    """Import data/<character>/NN.dat: the character's model and that slot's weapon."""
    character = read_character(path)
    folder = os.path.basename(os.path.dirname(character.dat_path))
    stem = os.path.splitext(os.path.basename(character.dat_path))[0]
    armature, warnings = import_x(character.x_path, scale, set_scene_timing, collection,
                                  weapons=character.weapons, name=f"{folder}_{stem}")
    armature["x_weapon_id"] = character.weapon_id
    return armature, warnings


def import_file(path, scale=0.01, set_scene_timing=True, collection=None):
    """A character .dat brings its weapon; a .X brings what is in it."""
    importer = import_character if path.lower().endswith(".dat") else import_x
    return importer(path, scale, set_scene_timing, collection)
