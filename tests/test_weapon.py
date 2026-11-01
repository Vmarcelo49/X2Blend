"""A character file brings the right model and weapon, and the weapon rides the hand. Run inside Blender.

    blender -b --factory-startup --python tests/test_weapon.py -- NN.dat rig.json weapon.json weapon_id

rig.json and weapon.json are x_oracle dumps, from a parser separate from x2blend. The weapon id
is given by the caller: ASSET-CATALOG section 3.3 pins it per slot. Blender's posed weapon
vertices must equal the game's placement, rig bone world * weapon frame world * vertex
(placeAttachedProp in daybreak-decomp).
"""

import json
import math
import os
import sys

import bpy
from mathutils import Matrix, Quaternion, Vector
from mathutils.kdtree import KDTree

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from x2blend.build import import_file  # noqa: E402
from x2blend.xfile import decode_name  # noqa: E402

args = sys.argv[sys.argv.index("--") + 1:]
DAT_PATH, RIG_PATH, WEAPON_PATH, WEAPON_ID = args[0], args[1], args[2], int(args[3])
TOLERANCE = 2e-3   # file units; fighters are about 145 tall

C = Matrix(((1, 0, 0, 0), (0, 0, 1, 0), (0, 1, 0, 0), (0, 0, 0, 1)))
rig = json.load(open(RIG_PATH))
weapon = json.load(open(WEAPON_PATH))
frame_names = [decode_name(bytes.fromhex(f["name"])) for f in rig["frames"]]


def row_major(v):
    return Matrix((v[0:4], v[4:8], v[8:12], v[12:16]))


def to_blender(m):
    return C @ m @ C


def local_at(channel, tick):
    keys = channel["keys"]
    if tick <= keys[0][0]:
        a = b = keys[0]
        t = 0.0
    elif tick >= keys[-1][0]:
        a = b = keys[-1]
        t = 0.0
    else:
        i = next(k for k in range(len(keys) - 1) if keys[k][0] <= tick <= keys[k + 1][0])
        a, b = keys[i], keys[i + 1]
        t = (tick - a[0]) / (b[0] - a[0])
    qa, qb = Quaternion(a[1:5]), Quaternion(b[1:5])
    if qa.dot(qb) < 0.0:
        qb.negate()
    q = Quaternion([x + (y - x) * t for x, y in zip(qa, qb)])
    q.normalize()
    return Matrix.LocRotScale(Vector(a[5:8]).lerp(b[5:8], t), q, Vector(a[8:11]).lerp(b[8:11], t))


def prop_world(expected_set, tick):
    channels = {c["frame"]: c for c in expected_set["channels"]}
    index = frame_names.index("Bip01_Prop1")
    chain = []
    while index >= 0:
        chain.append(index)
        index = rig["frames"][index]["parent"]
    world = Matrix.Identity(4)
    for index in reversed(chain):
        if index not in channels:
            return None
        world = world @ local_at(channels[index], tick)
    return world


def weapon_vertices():
    points = []
    worlds = []
    for frame in weapon["frames"]:
        local = row_major(frame["local"])
        worlds.append(worlds[frame["parent"]] @ local if frame["parent"] >= 0 else local)
    for mesh in weapon["meshes"]:
        place = worlds[mesh["frame"]] if mesh["frame"] >= 0 else Matrix.Identity(4)
        points += [place @ Vector(v[:3]) for v in mesh["vertices"]]
    return points


bpy.ops.wm.read_factory_settings(use_empty=True)
armature, warnings = import_file(DAT_PATH)
print("warnings:", warnings)
failures = []
if armature.get("x_weapon_id") != WEAPON_ID:
    failures.append(f"weapon id {armature.get('x_weapon_id')}, expected {WEAPON_ID}")
if not armature.data.bones["Bip01_Prop1"].use_deform:
    failures.append("the hand bone does not deform")

held = [o for o in armature.children if o.get("x_item_model") == 0]
if not held:
    failures.append("no object for item model 0")
else:
    obj = held[0]
    actions = {a["x_game_index"]: a for a in bpy.data.actions}
    want_points = weapon_vertices()
    tpf = rig["ticks_per_second"] / 60.0
    worst = 0.0
    compared = 0
    for set_index in range(0, len(rig["sets"]), 13):
        expected = rig["sets"][set_index]
        if not expected["channels"]:
            continue
        duration = max(c["keys"][-1][0] for c in expected["channels"])
        armature.animation_data.action = actions[set_index]
        armature.animation_data.action_slot = actions[set_index].slots[0]
        for tick in (0.0, duration * 0.37, duration):
            world = prop_world(expected, tick)
            if world is None:
                continue  # a sparse set leaves this bone to whatever played before
            frame = tick / tpf
            bpy.context.scene.frame_set(math.floor(frame), subframe=frame - math.floor(frame))
            bpy.context.view_layer.update()
            depsgraph = bpy.context.evaluated_depsgraph_get()
            evaluated = obj.evaluated_get(depsgraph).to_mesh()
            tree = KDTree(len(evaluated.vertices))
            for i, v in enumerate(evaluated.vertices):
                tree.insert(v.co, i)
            tree.balance()
            for p in want_points:
                _, _, distance = tree.find((to_blender(world) @ C @ p.to_4d()).to_3d())
                worst = max(worst, distance)
                compared += 1
            obj.evaluated_get(depsgraph).to_mesh_clear()
    print(f"weapon: {compared} vertices compared, worst difference {worst:.2e} file units")
    if compared == 0:
        failures.append("no weapon vertex was compared")
    if worst > TOLERANCE:
        failures.append(f"worst weapon vertex difference {worst}")

for failure in failures:
    print("FAIL:", failure)
if failures:
    raise SystemExit(1)
print("OK")
