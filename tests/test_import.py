"""End-to-end check of one real .X, run inside Blender. Use tests/run.sh.

The reference is tests/oracle/x_oracle.cpp: daybreak-decomp's xmodel.h, a separate
parser. Between keys, the reference follows the rule the game's core evaluates:
actor_controller_srt.hpp lerps the components and normalizes the quaternion.

The checks run against the .blend after a save and reload, so they also cover what
survives in the file.
"""

import json
import math
import os
import sys
import tempfile
import time

import bpy
from mathutils import Matrix, Quaternion, Vector

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from x2blend.build import convert_vector, import_x  # noqa: E402
from x2blend.cnv import read_cnv  # noqa: E402
from x2blend.xfile import decode_name, read_model_file  # noqa: E402

args = sys.argv[sys.argv.index("--") + 1:]
X_PATH, ORACLE_PATH = args[0], args[1]
READER_ONLY = "--reader-only" in args

# Differences below are in file units; the fighters are about 145 units tall.
KEY_MATRIX_TOLERANCE = 1e-3
POSE_TOLERANCE = 1e-2
VERTEX_TOLERANCE = 5e-2

failures = []


def check(condition, message):
    if not condition:
        failures.append(message)
        print("FAIL:", message)


def hex_name(value):
    return decode_name(bytes.fromhex(value))


def row_major(values):
    return Matrix((values[0:4], values[4:8], values[8:12], values[12:16]))


C = Matrix(((1, 0, 0, 0), (0, 0, 1, 0), (0, 1, 0, 0), (0, 0, 0, 1)))


def to_blender(matrix):
    return C @ matrix @ C


def largest_difference(a, b):
    return max(abs(a[r][c] - b[r][c]) for r in range(4) for c in range(4))


def oracle_key_matrix(key):
    return Matrix.LocRotScale(Vector(key[5:8]), Quaternion(key[1:5]), Vector(key[8:11]))


oracle = json.load(open(ORACLE_PATH))
frame_names = [hex_name(f["name"]) for f in oracle["frames"]]


# ---------------------------------------------------------------------------
# Reader against the oracle
# ---------------------------------------------------------------------------

def same_corners(mine, theirs, tolerance=2e-3):
    """Equal as multisets up to rounding. Exact rounded keys split at grid boundaries."""
    if len(mine) != len(theirs):
        return False
    cells = {}
    for corner in theirs:
        cells.setdefault(tuple(math.floor(c / 0.01) for c in corner[:3]), []).append(corner)
    for corner in mine:
        x, y, z = (math.floor(c / 0.01) for c in corner[:3])
        found = False
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    bucket = cells.get((x + dx, y + dy, z + dz), ())
                    for i, other in enumerate(bucket):
                        if max(abs(p - q) for p, q in zip(corner, other)) < tolerance:
                            del bucket[i]
                            found = True
                            break
                    if found:
                        break
                if found:
                    break
            if found:
                break
        if not found:
            return False
    return True


def test_reader():
    model = read_model_file(X_PATH)
    check(model.ticks_per_second == oracle["ticks_per_second"], "ticks per second differ")
    check(len(model.frames) == len(oracle["frames"]), "frame count differs")
    for frame, expected in zip(model.frames, oracle["frames"]):
        check(frame.name == hex_name(expected["name"]), f"frame name {frame.name!r}")
        check(frame.parent == expected["parent"], f"frame parent {frame.name!r}")
        got = Matrix((frame.local[0:4], frame.local[4:8], frame.local[8:12], frame.local[12:16]))
        got.transpose()
        check(largest_difference(got, row_major(expected["local"])) < 1e-4, f"frame matrix {frame.name!r}")

    check(len(model.meshes) == len(oracle["meshes"]), "mesh count differs")
    for index, (mesh, expected) in enumerate(zip(model.meshes, oracle["meshes"])):
        check(mesh.frame == expected["frame"], f"mesh {index} frame")
        # The oracle fans faces into triangles, so only triangle meshes compare corner by corner.
        if all(len(f) == 3 for f in mesh.faces):
            mine = [(*mesh.positions[v * 3:v * 3 + 3], *(mesh.uvs[v * 2:v * 2 + 2] if mesh.uvs else (0, 0)))
                    for face in mesh.faces for v in face]
            theirs = [expected["vertices"][i][:5] for i in expected["indices"]]
            check(same_corners(mine, theirs), f"mesh {index} corners (position, uv) differ")
        check([m.texture for m in mesh.materials] == [hex_name(t) for t in expected["textures"]][:len(mesh.materials)],
              f"mesh {index} materials differ")
        check(len(mesh.skins) == len(expected["bones"]), f"mesh {index} bone count")
        for skin, bone in zip(mesh.skins, expected["bones"]):
            check(frame_names[bone["frame"]] == skin.bone, f"mesh {index} bone {skin.bone!r}")
            got = Matrix((skin.offset[0:4], skin.offset[4:8], skin.offset[8:12], skin.offset[12:16]))
            got.transpose()
            check(largest_difference(got, row_major(bone["offset"])) < 1e-3, f"offset of {skin.bone!r}")

    check(len(model.sets) == len(oracle["sets"]), "set count differs")
    for file_index, animation_set in enumerate(model.sets):
        expected = oracle["sets"][len(model.sets) - 1 - file_index]
        check(animation_set.name == hex_name(expected["name"]), f"set order at {file_index}")
        ours = {c.frame: c for c in animation_set.channels}
        check(len(ours) == len(expected["channels"]), f"{animation_set.name}: channel count")
        for channel in expected["channels"]:
            name = frame_names[channel["frame"]]
            mine = ours.get(name)
            if mine is None:
                check(False, f"{animation_set.name}: no channel for {name!r}")
                continue
            keys = channel["keys"]
            for sequence in mine.sequences:
                check(sequence.is_matrix, f"{animation_set.name}/{name}: this check covers matrix keys only")
                check(sequence.times == [k[0] for k in keys], f"{animation_set.name}/{name}: key times differ")
                for k, key in enumerate(keys):
                    raw = sequence.values[k * 16:k * 16 + 16]
                    got = Matrix((raw[0:4], raw[4:8], raw[8:12], raw[12:16]))
                    got.transpose()
                    if largest_difference(got, oracle_key_matrix(key)) > KEY_MATRIX_TOLERANCE:
                        check(False, f"{animation_set.name}/{name}: key {k} value differs")
                        break
    print(f"reader: {sum(len(c['keys']) for s in oracle['sets'] for c in s['channels'])} keys compared")


# ---------------------------------------------------------------------------
# Import, save, reload
# ---------------------------------------------------------------------------

def import_and_reload():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    started = time.time()
    armature, warnings = import_x(X_PATH)
    print(f"import: {time.time() - started:.2f}s, warnings: {warnings}")
    name = armature.name
    path = os.path.join(tempfile.mkdtemp(), "reload.blend")
    bpy.ops.wm.save_as_mainfile(filepath=path)
    bpy.ops.wm.open_mainfile(filepath=path)
    return bpy.data.objects[name]


def actions_by_index():
    return {a["x_game_index"]: a for a in bpy.data.actions}


def curves_of(action):
    return action.layers[0].strips[0].channelbags[0].fcurves


def test_keys(armature):
    """Same count and same converted times as the file, curve by curve, no resampling."""
    actions = actions_by_index()
    check(len(actions) == len(oracle["sets"]), "one action per set")
    tpf = oracle["ticks_per_second"] / 60.0
    total_file = total_blender = 0
    for index, expected in enumerate(oracle["sets"]):
        action = actions[index]
        check(action.name.startswith(f"{index:03d} ") and action.name.endswith(hex_name(expected["name"])),
              f"action name {action.name!r}")
        curves = {(c.data_path, c.array_index): c for c in curves_of(action)}
        for channel in expected["channels"]:
            bone = frame_names[channel["frame"]]
            times = [k[0] for k in channel["keys"]]
            total_file += len(times)
            total_blender += len(curves[(f'pose.bones["{bone}"].rotation_quaternion', 0)].keyframe_points)
            paths = [("rotation_quaternion", i) for i in range(4)] + [("location", i) for i in range(3)]
            for prop, i in paths:
                curve = curves.get((f'pose.bones["{bone}"].{prop}', i))
                if curve is None:
                    check(False, f"{action.name}: no curve {bone}.{prop}[{i}]")
                    continue
                points = curve.keyframe_points
                check(len(points) == len(times), f"{action.name}: {bone}.{prop}[{i}] has {len(points)} keys")
                bad = [p.co.x for p, t in zip(points, times) if p.co.x * tpf != t]
                check(not bad, f"{action.name}: {bone}.{prop}[{i}] times differ, e.g. {bad[:3]}")
                check(all(p.interpolation == 'LINEAR' for p in points), f"{action.name}: non-linear key")
    check(total_file == total_blender, f"key totals: file {total_file}, blender {total_blender}")
    print(f"keys: {total_blender} keys in the file and in the actions, {len(actions)} actions")


def reference_local(channel, tick):
    keys = channel["keys"]
    if tick <= keys[0][0]:
        a = b = keys[0]
        t = 0.0
    elif tick >= keys[-1][0]:
        a = b = keys[-1]
        t = 0.0
    else:
        low, high = 0, len(keys) - 1
        while high - low > 1:
            mid = (low + high) // 2
            if keys[mid][0] <= tick:
                low = mid
            else:
                high = mid
        a, b = keys[low], keys[high]
        t = (tick - a[0]) / (b[0] - a[0])
    qa = Quaternion(a[1:5])
    qb = Quaternion(b[1:5])
    if qa.dot(qb) < 0.0:
        qb.negate()
    q = Quaternion([x + (y - x) * t for x, y in zip(qa, qb)])
    q.normalize()
    return Matrix.LocRotScale(Vector(a[5:8]).lerp(b[5:8], t), q, Vector(a[8:11]).lerp(b[8:11], t))


def reference_worlds(expected_set, tick):
    """Frame -> world matrix in Blender's space, for frames whose whole ancestry is keyed."""
    channels = {c["frame"]: c for c in expected_set["channels"]}
    worlds = {}
    for index, frame in enumerate(oracle["frames"]):
        parent = frame["parent"]
        if index not in channels or (parent >= 0 and parent not in worlds):
            continue
        local = to_blender(reference_local(channels[index], tick))
        worlds[index] = worlds[parent] @ local if parent >= 0 else local
    return worlds


def set_tick(tick, tpf):
    frame = tick / tpf
    whole = math.floor(frame)
    bpy.context.scene.frame_set(int(whole), subframe=frame - whole)


def assign(armature, action):
    armature.animation_data.action = action
    armature.animation_data.action_slot = action.slots[0]


def bone_axis_rotation(armature):
    """A is the same for every bone; recover it from one with a known bind."""
    for mesh in oracle["meshes"]:
        for bone in mesh["bones"]:
            bind = to_blender(row_major(bone["offset"])).inverted()
            rest = armature.data.bones[frame_names[bone["frame"]]].matrix_local
            A = bind.inverted() @ rest
            return Matrix(tuple(tuple(round(v) for v in row) for row in A))
    return Matrix.Identity(4)


def test_poses(armature):
    """Blender's evaluated pose equals W * A at every key of every set and halfway between keys."""
    actions = actions_by_index()
    A = bone_axis_rotation(armature)
    tpf = oracle["ticks_per_second"] / 60.0
    worst = 0.0
    compared = 0
    pose_bones = armature.pose.bones
    for index, expected in enumerate(oracle["sets"]):
        assign(armature, actions[index])
        times = sorted({k[0] for c in expected["channels"] for k in c["keys"]})
        ticks = set(times)
        ticks.update((a + b) / 2.0 for a, b in zip(times, times[1:]))
        for tick in sorted(ticks):
            set_tick(tick, tpf)
            worlds = reference_worlds(expected, tick)
            for frame_index, world in worlds.items():
                got = pose_bones[frame_names[frame_index]].matrix
                difference = largest_difference(got, world @ A)
                worst = max(worst, difference)
                compared += 1
    check(compared > 0, "no pose was compared")
    check(worst < POSE_TOLERANCE, f"worst pose difference {worst}")
    print(f"poses: {compared} bone poses compared, worst difference {worst:.2e} file units")


def test_deformation(armature):
    """Blender's skinned vertices equal the game's palette blend, including the rest pose."""
    actions = actions_by_index()
    tpf = oracle["ticks_per_second"] / 60.0
    meshes = sorted((o for o in armature.children if o.type == 'MESH'), key=lambda o: o.name)
    by_frame = {frame_names[m["frame"]]: m for m in oracle["meshes"] if m["frame"] >= 0}
    worst = 0.0
    compared = 0
    samples = [(i, f) for i in (0, 1, 30, len(oracle["sets"]) - 1) if i < len(oracle["sets"])
               for f in (0.0, 0.5, 1.0)]
    for set_index, fraction in samples:
        expected = oracle["sets"][set_index]
        duration = max((c["keys"][-1][0] for c in expected["channels"]), default=0.0)
        if any(len(c["keys"]) == 0 for c in expected["channels"]) or \
                len(expected["channels"]) < sum(1 for n in frame_names if n.startswith("Bip")):
            continue  # sparse sets leave some bones to whatever played before
        tick = duration * fraction
        assign(armature, actions[set_index])
        set_tick(tick, tpf)
        bpy.context.view_layer.update()
        depsgraph = bpy.context.evaluated_depsgraph_get()
        worlds = reference_worlds(expected, tick)
        for obj in meshes:
            source = by_frame.get(obj.name)
            if source is None:
                check(False, f"no oracle mesh for object {obj.name!r}")
                continue
            evaluated = obj.evaluated_get(depsgraph).to_mesh()
            rest = {}
            for vertex in obj.data.vertices:
                rest.setdefault(tuple(round(c, 3) for c in vertex.co), []).append(vertex.index)
            palette = [(worlds[b["frame"]] @ to_blender(row_major(b["offset"]))) for b in source["bones"]]
            for vertex in source["vertices"]:
                p = convert_vector(*vertex[:3])
                want = Vector((0.0, 0.0, 0.0))
                for b, w in zip(vertex[5:9], vertex[9:13]):
                    if w > 0.0:
                        want += (palette[b] @ p) * w
                candidates = rest.get(tuple(round(c, 3) for c in p), [])
                check(bool(candidates), f"{obj.name}: vertex {p} missing from Blender")
                if candidates:
                    # Seam duplicates with different weights share a position; one of them is this vertex.
                    best = min((evaluated.vertices[c].co - want).length for c in candidates)
                    worst = max(worst, best)
                    compared += 1
            obj.evaluated_get(depsgraph).to_mesh_clear()
    check(compared > 0, "no vertex was compared")
    check(worst < VERTEX_TOLERANCE, f"worst vertex difference {worst}")
    print(f"deformation: {compared} vertices compared, worst difference {worst:.2e} file units")


def test_mesh(armature):
    model = read_model_file(X_PATH)
    objects = {o.name: o for o in armature.children if o.type == 'MESH'}
    directory = os.path.dirname(X_PATH)
    for mesh in model.meshes:
        name = model.frames[mesh.frame].name
        obj = objects.get(name)
        check(obj is not None, f"no object for mesh {name!r}")
        if obj is None:
            continue
        data = obj.data
        check(len(data.polygons) == len(mesh.faces), f"{name}: face count")
        check(all(mat for mat in data.materials), f"{name}: empty material slot")
        check([p.material_index for p in data.polygons] == mesh.face_materials, f"{name}: face materials differ")

        # The winding claim: geometry faces the same way as the file's own normals.
        agree = total = 0
        corner_normals = data.corner_normals
        for polygon in data.polygons:
            for corner in polygon.loop_indices:
                total += 1
                agree += corner_normals[corner].vector.dot(polygon.normal) > 0.0
        # Not all: smooth normals legitimately turn away from the faces of thin hair tufts.
        print(f"mesh {name}: {agree}/{total} corner normals agree with winding")
        check(agree / max(total, 1) > 0.9, f"{name}: only {agree}/{total} corner normals agree with winding")

        # Custom normals survived and match the file.
        file_corners = [convert_vector(*mesh.normals[n * 3:n * 3 + 3]).normalized()
                        for face in mesh.normal_faces for n in reversed(face)]
        worst = max((corner_normals[i].vector - n).length for i, n in enumerate(file_corners))
        print(f"mesh {name}: worst corner normal difference {worst:.2e}")
        check(worst < 2e-2, f"{name}: corner normals differ from the file by {worst}")

        # UVs, corner by corner, with V flipped.
        layer = data.uv_layers[0].uv
        file_uvs = [(mesh.uvs[v * 2], 1.0 - mesh.uvs[v * 2 + 1]) for face in mesh.faces for v in reversed(face)]
        worst = max(max(abs(layer[i].vector.x - u), abs(layer[i].vector.y - v)) for i, (u, v) in enumerate(file_uvs))
        check(worst < 1e-5, f"{name}: UVs differ by {worst}")

        weights = [sum(g.weight for g in v.groups) for v in data.vertices]
        check(min(weights) > 0.999 and max(weights) < 1.001, f"{name}: weights do not sum to 1")

    for image in bpy.data.images:
        check(image.packed_file is not None, f"image {image.name!r} is not packed")
        width, height, rgba = read_cnv(os.path.join(directory, image.name + ".cnv"))
        check((width, height) == tuple(image.size), f"image {image.name!r} size")
        # Top-left in the file is the last row in Blender.
        offset = (height - 1) * width * 4
        got = [round(c * 255) for c in image.pixels[offset:offset + 4]]
        check(got == list(rgba[:4]), f"image {image.name!r} corner pixel {got} != {list(rgba[:4])}")
    print(f"mesh: {len(model.meshes)} meshes, {len(bpy.data.images)} images, {len(bpy.data.materials)} materials")


test_reader()
if not READER_ONLY:
    armature = import_and_reload()
    test_mesh(armature)
    test_keys(armature)
    test_poses(armature)
    test_deformation(armature)

if failures:
    print(f"{len(failures)} failure(s)")
    raise SystemExit(1)
print("OK")
