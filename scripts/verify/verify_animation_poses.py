#!/usr/bin/env python3
"""
verify_animation_poses.py
========================================================
Lite verification script that compares pose-bone world matrices in the
generated .blend against the ``baked_keys`` matrices in the JSON, on the
first 10 frames of the first 3 animations.

This is a light refactor of the original
``scripts/verify_animation_poses.py``.  Changes vs. the original:

  - Lives under ``scripts/verify/`` instead of ``scripts/``.
  - Usage banner updated to reflect the new path.
  - Reads ``bake_fps`` from ``model["meta"]["bake_fps"]`` instead of
    hardcoding ``fps = 60.0``.  Falls back to 60.0 (with a warning)
    when the meta block is missing or malformed — this matches the
    original C++ bake rate and the ``FALLBACK_BAKE_FPS`` constant in
    ``blend_importer.config``.
  - ``mat4_to_mathutils`` is duplicated locally (with this comment) so
    the script remains standalone-runnable inside Blender's bundled
    Python without requiring the ``blend_importer`` package to be on
    ``sys.path``.  The canonical copy lives in
    ``scripts/blend_importer/math_utils.py`` — keep the two in sync if
    you edit the matrix conversion.

Mathematical behavior is identical to the original.

Usage:
    blender --background --python scripts/verify/verify_animation_poses.py -- \\
        <model.json> <output.blend>
"""

import sys
import json
import os
import math

import bpy
import mathutils


# ---------------------------------------------------------------------------
# Local copy of mat4_to_mathutils — duplicated from
# scripts/blend_importer/math_utils.py for standalone execution.
# Keep in sync if you edit the matrix conversion logic.
# ---------------------------------------------------------------------------

def mat4_to_mathutils(rows):
    """Convert a DirectX/D3DX row-vector matrix to Blender's column-vector matrix."""
    return mathutils.Matrix([rows[r] for r in range(4)]).transposed()


# Fallback FPS when the JSON's meta block is missing or malformed.
# Matches the original C++ bake rate and blend_importer.config.FALLBACK_BAKE_FPS.
_FALLBACK_BAKE_FPS = 60.0


def compare_matrices(actual, expected):
    """Compute translation distance and rotation difference (in degrees) between two matrices."""
    t_act = actual.translation
    t_exp = expected.translation
    pos_diff = (t_act - t_exp).length

    q_act = actual.to_quaternion()
    q_exp = expected.to_quaternion()
    try:
        angle_rad = q_act.rotation_difference(q_exp).angle
        rot_diff = math.degrees(angle_rad)
        # Handle 360-degree wrapping
        if rot_diff > 180.0:
            rot_diff = abs(360.0 - rot_diff)
    except Exception:
        rot_diff = 0.0

    return pos_diff, rot_diff


def _resolve_bake_fps(model):
    """
    Read bake_fps from the JSON meta block.

    Returns a float.  Falls back to ``_FALLBACK_BAKE_FPS`` (60.0) and
    emits a warning when the meta block is missing, when ``bake_fps`` is
    absent, or when the value cannot be coerced to a float.
    """
    meta = model.get("meta")
    if not isinstance(meta, dict):
        print(f"[verify_animation_poses] WARNING: JSON has no 'meta' "
              f"block; falling back to {_FALLBACK_BAKE_FPS} FPS.")
        return _FALLBACK_BAKE_FPS
    raw = meta.get("bake_fps")
    if raw is None:
        print(f"[verify_animation_poses] WARNING: meta.bake_fps is missing; "
              f"falling back to {_FALLBACK_BAKE_FPS} FPS.")
        return _FALLBACK_BAKE_FPS
    try:
        return float(raw)
    except (TypeError, ValueError):
        print(f"[verify_animation_poses] WARNING: meta.bake_fps={raw!r} is "
              f"not a number; falling back to {_FALLBACK_BAKE_FPS} FPS.")
        return _FALLBACK_BAKE_FPS


def main():
    args = []
    if "--" in sys.argv:
        args = sys.argv[sys.argv.index("--") + 1:]

    if len(args) < 2:
        print("[verify_animation_poses] Usage: blender --background --python "
              "scripts/verify/verify_animation_poses.py -- "
              "<model.json> <output.blend>")
        sys.exit(1)

    json_path = args[0]
    blend_path = args[1]

    with open(json_path, "r", encoding="utf-8") as f:
        model = json.load(f)

    nodes_data = model.get("nodes", [])
    anims_data = model.get("animations", [])

    if not anims_data:
        print("[verify_animation_poses] No animations to verify.")
        sys.exit(0)

    # Resolve the bake FPS from the meta block (refactor: was hardcoded 60.0).
    fps = _resolve_bake_fps(model)
    print(f"[verify_animation_poses] Using bake FPS: {fps}")

    bpy.ops.wm.open_mainfile(filepath=os.path.abspath(blend_path))

    arm_obj = None
    for obj in bpy.data.objects:
        if obj.type == 'ARMATURE':
            arm_obj = obj
            break

    if not arm_obj:
        print("[verify_animation_poses] ERROR: Armature not found.")
        sys.exit(1)

    name_to_idx = {nd["name"]: i for i, nd in enumerate(nodes_data)}

    max_pos_err = 0.0
    max_rot_err = 0.0
    total_checks = 0

    print("Running detailed verification...")
    for anim in anims_data[:3]:  # Check the first 3 animations in detail
        anim_name = anim.get("name") or "Action"
        print(f"\nVerifying animation: {anim_name}")

        # Activate the animation in Blender
        action = bpy.data.actions.get(anim_name)
        if not action:
            print(f"ERROR: Action '{anim_name}' not found in blend file!")
            continue

        if not arm_obj.animation_data:
            arm_obj.animation_data_create()

        arm_obj.animation_data.action = action
        if hasattr(action, "slots") and action.slots:
            arm_obj.animation_data.action_slot = action.slots[0]

        frames_map = {}
        for ch in anim.get("channels", []):
            target = ch.get("target_node", "")
            baked_keys = ch.get("baked_keys", [])
            for kf in baked_keys:
                f_num = int(round(kf["t"] * fps))
                if f_num not in frames_map:
                    frames_map[f_num] = {}
                frames_map[f_num][target] = kf["m"]

        for f_num in sorted(frames_map.keys())[:10]:  # Check first 10 frames
            bpy.context.scene.frame_set(f_num)
            bpy.context.view_layer.update()

            for bone_name, mat_raw in frames_map[f_num].items():
                pb = arm_obj.pose.bones.get(bone_name)
                if pb:
                    expected_mat = mat4_to_mathutils(mat_raw)
                    actual_mat = pb.matrix

                    pos_err, rot_err = compare_matrices(actual_mat, expected_mat)

                    max_pos_err = max(max_pos_err, pos_err)
                    max_rot_err = max(max_rot_err, rot_err)
                    total_checks += 1

                    if pos_err > 1e-3 or rot_err > 0.1:
                        print(f"Error on Anim: {anim_name}, Frame: {f_num}, Bone: {bone_name}")
                        print(f"  Pos error: {pos_err:.6f}, Rot error: {rot_err:.4f}°")

    print("\n" + "=" * 50)
    print("ANIMATION VERIFICATION SUMMARY (LITE):")
    print(f"  Total Pose Checks: {total_checks}")
    print(f"  Max Position Error: {max_pos_err:.6f} units")
    print(f"  Max Rotation Error: {max_rot_err:.4f}°")
    print("=" * 50)


if __name__ == "__main__":
    main()
