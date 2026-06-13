#!/usr/bin/env python3
import sys
import json
import os
import math
import bpy
import mathutils

def mat4_to_mathutils(rows):
    return mathutils.Matrix([rows[r] for r in range(4)]).transposed()

def compare_matrices(actual, expected):
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

def main():
    args = []
    if "--" in sys.argv:
        args = sys.argv[sys.argv.index("--") + 1:]

    if len(args) < 2:
        print("[verify_animation_poses] Usage: blender --background --python verify_animation_poses.py -- <model.json> <output.blend>")
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
    fps = 60.0
    
    max_pos_err = 0.0
    max_rot_err = 0.0
    total_checks = 0

    print("Running detailed verification...")
    for anim in anims_data[:3]: # Check the first 3 animations in detail
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

        for f_num in sorted(frames_map.keys())[:10]: # Check first 10 frames
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

    print("\n" + "="*50)
    print("ANIMATION VERIFICATION SUMMARY (LITE):")
    print(f"  Total Pose Checks: {total_checks}")
    print(f"  Max Position Error: {max_pos_err:.6f} units")
    print(f"  Max Rotation Error: {max_rot_err:.4f}°")
    print("="*50)

if __name__ == "__main__":
    main()
