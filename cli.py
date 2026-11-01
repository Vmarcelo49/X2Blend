#!/usr/bin/env python3
"""Command line for X2Blend.

    cli.py info input                      summary, no Blender needed
    cli.py convert input out.blend         full import through a headless Blender

The input is a .X, or a Daybreak character file (data/<character>/NN.dat), which brings
the model it names and that slot's weapon.

BLENDER names the executable when it is not on PATH.
"""

import argparse
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)


def info(path):
    from x2blend.character import read_character
    from x2blend.xfile import read_model_file
    if path.lower().endswith(".dat"):
        character = read_character(path)
        print(f"character file: model {os.path.basename(character.x_path)}, weapon {character.weapon_id}")
        for weapon in character.weapons:
            print(f"  item model {weapon.index}: {weapon.mesh}{'' if weapon.path else ' (not on disk)'}")
        path = character.x_path
    model = read_model_file(path)
    keys = sum(len(s.times) for a in model.sets for c in a.channels for s in c.sequences)
    print(f"{len(model.frames)} frames, {len(model.meshes)} meshes, {len(model.sets)} animation sets, "
          f"{keys} keys at {model.ticks_per_second:g} ticks/s")
    for mesh in model.meshes:
        owner = model.frames[mesh.frame].name if mesh.frame >= 0 else "(top level)"
        print(f"  mesh in {owner}: {len(mesh.positions) // 3} vertices, {len(mesh.faces)} faces, "
              f"{len(mesh.skins)} bones, textures {sorted({m.texture for m in mesh.materials})}")


def convert_in_blender(path, out, scale):
    import bpy
    from x2blend.build import import_file
    bpy.ops.wm.read_factory_settings(use_empty=True)
    armature, warnings = import_file(path, scale=scale)
    for warning in warnings:
        print("warning:", warning)
    bpy.ops.wm.save_as_mainfile(filepath=out)
    print(f"{armature.name}: {len(armature.data.bones)} bones, {len(bpy.data.actions)} actions -> {out}")


def main(argv):
    parser = argparse.ArgumentParser(description="DirectX .X to Blender, keeping the file's keys")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("info").add_argument("input")
    convert = commands.add_parser("convert")
    convert.add_argument("input")
    convert.add_argument("output")
    convert.add_argument("--scale", type=float, default=0.01,
                         help="armature object scale; keys stay in file units (default 0.01)")
    args = parser.parse_args(argv)

    if args.command == "info":
        info(args.input)
        return 0
    command = [os.environ.get("BLENDER", "blender"), "--background", "--factory-startup",
               "--python-exit-code", "1", "--python", os.path.abspath(__file__), "--",
               "--inside-blender", os.path.abspath(args.input), os.path.abspath(args.output), str(args.scale)]
    return subprocess.call(command)


if __name__ == "__main__":
    arguments = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
    if arguments[:1] == ["--inside-blender"]:
        convert_in_blender(arguments[1], arguments[2], float(arguments[3]))
    else:
        sys.exit(main(arguments))
