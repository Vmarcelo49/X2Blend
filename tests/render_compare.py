"""Renders a .X in Blender from the same camera as a daybreak --out capture and compares.

Run inside Blender:
    blender --background --factory-startup --python tests/render_compare.py -- \
        model.X out_dir set:seconds:capture.ppm [set:seconds:capture.ppm ...]

The captures come from
    daybreak --model=model.X --anim=N --time=S --camera=front --size=768 \
        --no-audio --no-shadows --headless --out=capture.ppm

--camera=front puts the eye at center + (0,0,-1) * 2r in file space, 45 degrees vertical,
where center and r come from the bounds (app/scene.inc). The shading is not comparable,
since the game draws its own toon effect, so the number reported is the silhouette
overlap. Pose and texture are for the eye, in the side-by-side PNGs.

The rest pose has no capture to compare with: the armature rests in the T-pose from the
SkinWeights offsets, while --no-anim draws the frame matrices, which equal set 0's first key.
"""

import math
import os
import sys

import bpy
import numpy
from mathutils import Vector

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from x2blend.build import import_file  # noqa: E402
from x2blend.character import read_character  # noqa: E402
from x2blend.xfile import read_model_file  # noqa: E402

args = sys.argv[sys.argv.index("--") + 1:]
X_PATH, OUT_DIR = args[0], args[1]
REQUESTS = args[2:]
SCALE = 0.01


def read_ppm(path):
    with open(path, "rb") as f:
        data = f.read()
    fields, pos = [], 0
    while len(fields) < 4:
        end = pos
        while data[end] not in b" \n\t\r":
            end += 1
        fields.append(data[pos:end])
        pos = end + 1
    width, height = int(fields[1]), int(fields[2])
    return numpy.frombuffer(data, numpy.uint8, width * height * 3, pos).reshape(height, width, 3)


def capture_bounds(path):
    """What the capture frames: every model's raw vertices, a weapon's in its own file's space (Scene::bounds)."""
    paths = [path]
    if path.lower().endswith(".dat"):
        character = read_character(path)
        paths = [character.x_path] + [w.path for w in character.weapons]
    points = []
    for model_path in paths:
        for mesh in read_model_file(model_path).meshes:
            p = mesh.positions
            points += [Vector((p[i], p[i + 2], p[i + 1])) for i in range(0, len(p), 3)]
    low = Vector(tuple(min(p[i] for p in points) for i in range(3)))
    high = Vector(tuple(max(p[i] for p in points) for i in range(3)))
    return low, high


def setup_camera(low, high, size):
    center = (low + high) * 0.5 * SCALE
    radius = (high - low).length * 0.5 * SCALE
    camera = bpy.data.cameras.new("Compare")
    camera.sensor_fit = 'VERTICAL'
    camera.angle_y = math.radians(45.0)
    camera.clip_end = 100.0
    obj = bpy.data.objects.new("Compare", camera)
    bpy.context.collection.objects.link(obj)
    # File -Z is Blender -Y, so this camera looks along +Y with Z up.
    obj.location = center + Vector((0.0, -1.0, 0.0)) * radius * 2.0
    obj.rotation_euler = (math.pi / 2.0, 0.0, 0.0)
    scene = bpy.context.scene
    scene.camera = obj
    scene.render.resolution_x = scene.render.resolution_y = size
    scene.render.resolution_percentage = 100


def setup_render():
    scene = bpy.context.scene
    scene.render.engine = 'CYCLES'
    scene.cycles.device = 'CPU'
    scene.cycles.samples = 16
    scene.render.film_transparent = True
    scene.render.image_settings.file_format = 'PNG'
    scene.render.image_settings.color_mode = 'RGBA'
    scene.view_settings.view_transform = 'Standard'
    world = bpy.data.worlds.new("Flat")
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs[0].default_value = (1.0, 1.0, 1.0, 1.0)
    scene.world = world


def game_mask(capture):
    background = capture[0, 0].astype(numpy.int16)
    # A low threshold: the trousers are nearly the background's colour.
    return numpy.abs(capture.astype(numpy.int16) - background).sum(axis=2) > 2


def render_mask(path):
    image = bpy.data.images.load(path, check_existing=False)
    width, height = image.size
    pixels = numpy.empty(width * height * 4, dtype=numpy.float32)
    image.pixels.foreach_get(pixels)
    bpy.data.images.remove(image)
    return pixels.reshape(height, width, 4)[::-1, :, 3] > 0.5


bpy.ops.wm.read_factory_settings(use_empty=True)
armature, warnings = import_file(X_PATH, scale=SCALE)
print("warnings:", warnings)
actions = {a["x_game_index"]: a for a in bpy.data.actions}
low, high = capture_bounds(X_PATH)
setup_render()
os.makedirs(OUT_DIR, exist_ok=True)

worst = 1.0
for request in REQUESTS:
    set_text, seconds_text, capture_path = request.split(":", 2)
    set_index, seconds = int(set_text), float(seconds_text)
    capture = read_ppm(capture_path)
    setup_camera(low, high, capture.shape[0])
    scene = bpy.context.scene
    armature.animation_data.action = actions[set_index]
    armature.animation_data.action_slot = actions[set_index].slots[0]
    frame = seconds * 60.0
    scene.frame_set(int(frame), subframe=frame - int(frame))
    name = f"set{set_index:03d}_{seconds:g}s"
    path = os.path.join(OUT_DIR, f"blender_{name}.png")
    scene.render.filepath = path
    bpy.ops.render.render(write_still=True)
    a, b = game_mask(capture), render_mask(path)
    overlap = (a & b).sum() / max((a | b).sum(), 1)
    worst = min(worst, overlap)
    print(f"RESULT {name}: silhouette overlap {overlap:.4f} (game {a.sum()} px, blender {b.sum()} px)")

print(f"RESULT worst overlap {worst:.4f}")
