#!/usr/bin/env python3
"""
verify_bones.py
========================================================
Verification script to check if the bone conversion data is correct on XtoBlend.
Compares the expected world/bind matrices from the JSON model definition against
the actual bone matrices in the generated .blend file.

Usage:
    blender --background --python scripts/verify_bones.py -- <model.json> <output.blend> [report.txt]
"""

import sys
import json
import os
import math

# Try to import bpy and mathutils
try:
    import bpy
    import mathutils
except ImportError:
    sys.exit("[verify_bones] ERROR: 'bpy' not found. This script must be run inside Blender.")


def mat4_to_mathutils(rows):
    """Convert a DirectX/D3DX row-vector matrix to Blender's column-vector matrix."""
    return mathutils.Matrix([rows[r] for r in range(4)]).transposed()


def get_expected_parent_bone(nodes, parent_idx):
    """Find the first ancestor node in the JSON hierarchy that is designated as a bone."""
    curr = parent_idx
    while curr >= 0:
        if nodes[curr].get("is_bone"):
            return nodes[curr]["name"]
        curr = nodes[curr]["parent_index"]
    return None


def compare_matrices(actual, expected):
    """Compute translation distance and rotation difference (in degrees) between two matrices."""
    # Translation distance
    t_act = actual.translation
    t_exp = expected.translation
    pos_diff = (t_act - t_exp).length

    # Rotation difference in degrees
    q_act = actual.to_quaternion()
    q_exp = expected.to_quaternion()
    try:
        angle_rad = q_act.rotation_difference(q_exp).angle
        rot_diff = math.degrees(angle_rad)
    except Exception:
        # Fallback if angle computation fails (e.g. singular matrices or zero rotations)
        rot_diff = 0.0

    return pos_diff, rot_diff


def main():
    # Parse arguments after '--'
    args = []
    if "--" in sys.argv:
        args = sys.argv[sys.argv.index("--") + 1:]

    if len(args) < 2:
        print("[verify_bones] Usage: blender --background --python verify_bones.py -- <model.json> <output.blend> [report.txt]")
        sys.exit(1)

    json_path = args[0]
    blend_path = args[1]
    report_path = args[2] if len(args) > 2 else None

    print(f"\n[verify_bones] Loading intermediate JSON: {json_path}")
    with open(json_path, "r", encoding="utf-8") as f:
        model = json.load(f)

    nodes_data = model.get("nodes", [])
    meshes_data = model.get("meshes", [])

    # --- 1. Compute expected frame world matrices ---
    n = len(nodes_data)
    expected_world_mats = [None] * n
    name_to_node_idx = {}
    for i, nd in enumerate(nodes_data):
        name_to_node_idx[nd["name"]] = i
        local = mat4_to_mathutils(nd["local_transform"])
        pi = nd["parent_index"]
        expected_world_mats[i] = (expected_world_mats[pi] @ local) if pi >= 0 else local

    # --- 2. Gather expected skin bind matrices ---
    expected_bind_mats = {}
    for mesh in meshes_data:
        bone_names = mesh.get("bone_names", [])
        ibms = mesh.get("inverse_bind_matrices", [])
        for bname, ibm_rows in zip(bone_names, ibms):
            if bname and bname not in expected_bind_mats:
                ibm = mat4_to_mathutils(ibm_rows)
                try:
                    expected_bind_mats[bname] = ibm.inverted()
                except ValueError:
                    pass  # Singular matrix

    # --- 3. Open the blend file ---
    print(f"[verify_bones] Opening blend file: {blend_path}")
    if not os.path.exists(blend_path):
        print(f"[verify_bones] ERROR: Blend file not found at {blend_path}")
        sys.exit(1)
    bpy.ops.wm.open_mainfile(filepath=os.path.abspath(blend_path))

    # --- 4. Find the armature ---
    arm_obj = None
    for obj in bpy.data.objects:
        if obj.type == 'ARMATURE':
            arm_obj = obj
            break

    if not arm_obj:
        print("[verify_bones] ERROR: Armature object not found in the scene!")
        sys.exit(1)

    print(f"[verify_bones] Found Armature: {arm_obj.name}")
    armature_data = arm_obj.data

    # --- 5. Perform bone comparison ---
    report_lines = []
    summary_data = {
        "total_bones": 0,
        "missing_bones": [],
        "mismatched_parents": [],
        "max_frame_pos_err": 0.0,
        "max_frame_rot_err": 0.0,
        "max_bind_pos_err": 0.0,
        "max_bind_rot_err": 0.0,
        "details": []
    }

    report_lines.append(f"Bone Verification Report for: {blend_path}")
    report_lines.append(f"JSON Source: {json_path}")
    report_lines.append("=" * 80)
    report_lines.append(f"{'Bone Name':<25} | {'Parent (Exp/Act)':<30} | {'Frame Pos/Rot Err':<20} | {'Bind Pos/Rot Err':<20}")
    report_lines.append("-" * 105)

    for i, nd in enumerate(nodes_data):
        if not nd.get("is_bone"):
            continue

        summary_data["total_bones"] += 1
        bname = nd["name"]
        bone = armature_data.bones.get(bname)

        if not bone:
            summary_data["missing_bones"].append(bname)
            report_lines.append(f"{bname:<25} | {'MISSING IN BLENDER':<30} | {'-':<20} | {'-':<20}")
            continue

        # Check Parent
        expected_parent = get_expected_parent_bone(nodes_data, nd["parent_index"])
        actual_parent = bone.parent.name if bone.parent else None

        parent_status = f"{str(expected_parent)} / {str(actual_parent)}"
        if expected_parent != actual_parent:
            summary_data["mismatched_parents"].append({
                "bone": bname,
                "expected": expected_parent,
                "actual": actual_parent
            })
            parent_status += " [MISMATCH]"

        # actual_matrix is bone.matrix_local (rest matrix in Armature local space)
        actual_matrix = bone.matrix_local

        # 1. Compare vs Frame World Matrix
        expected_frame_matrix = expected_world_mats[i]
        frame_pos_err, frame_rot_err = compare_matrices(actual_matrix, expected_frame_matrix)

        summary_data["max_frame_pos_err"] = max(summary_data["max_frame_pos_err"], frame_pos_err)
        summary_data["max_frame_rot_err"] = max(summary_data["max_frame_rot_err"], frame_rot_err)

        # 2. Compare vs Skin Bind Matrix
        bind_pos_err, bind_rot_err = 0.0, 0.0
        has_bind = bname in expected_bind_mats
        if has_bind:
            expected_bind_matrix = expected_bind_mats[bname]
            bind_pos_err, bind_rot_err = compare_matrices(actual_matrix, expected_bind_matrix)
            summary_data["max_bind_pos_err"] = max(summary_data["max_bind_pos_err"], bind_pos_err)
            summary_data["max_bind_rot_err"] = max(summary_data["max_bind_rot_err"], bind_rot_err)

        frame_err_str = f"{frame_pos_err:.5f} / {frame_rot_err:.2f}°"
        bind_err_str = f"{bind_pos_err:.5f} / {bind_rot_err:.2f}°" if has_bind else "N/A"

        report_lines.append(f"{bname:<25} | {parent_status:<30} | {frame_err_str:<20} | {bind_err_str:<20}")

        summary_data["details"].append({
            "name": bname,
            "expected_parent": expected_parent,
            "actual_parent": actual_parent,
            "frame_pos_err": frame_pos_err,
            "frame_rot_err": frame_rot_err,
            "has_bind": has_bind,
            "bind_pos_err": bind_pos_err,
            "bind_rot_err": bind_rot_err
        })

    # Summary section
    report_lines.append("-" * 105)
    report_lines.append(f"SUMMARY:")
    report_lines.append(f"  Total Bones checked: {summary_data['total_bones']}")
    report_lines.append(f"  Missing Bones count: {len(summary_data['missing_bones'])}")
    if summary_data['missing_bones']:
        report_lines.append(f"    Missing: {', '.join(summary_data['missing_bones'])}")
    report_lines.append(f"  Mismatched Parents count: {len(summary_data['mismatched_parents'])}")
    for mismatch in summary_data['mismatched_parents']:
        report_lines.append(f"    Bone '{mismatch['bone']}': expected parent '{mismatch['expected']}', got '{mismatch['actual']}'")
    report_lines.append(f"  Max Pos Error (vs Frame World Matrix): {summary_data['max_frame_pos_err']:.6f} units")
    report_lines.append(f"  Max Rot Error (vs Frame World Matrix): {summary_data['max_frame_rot_err']:.4f}°")
    report_lines.append(f"  Max Pos Error (vs Skin Bind Matrix):  {summary_data['max_bind_pos_err']:.6f} units")
    report_lines.append(f"  Max Rot Error (vs Skin Bind Matrix):  {summary_data['max_bind_rot_err']:.4f}°")

    # Output to console
    print("\n" + "\n".join(report_lines) + "\n")

    # Write report file if path provided
    if report_path:
        with open(report_path, "w", encoding="utf-8") as rf:
            rf.write("\n".join(report_lines) + "\n")
        print(f"[verify_bones] Saved detailed report to: {report_path}")

    # Determine exit code (fail if missing bones, parent mismatch, or significant matrix mismatch)
    success = True
    if summary_data["missing_bones"]:
        print("[verify_bones] FAILED: Missing bones found.")
        success = False
    if summary_data["mismatched_parents"]:
        print("[verify_bones] FAILED: Parent hierarchy mismatch found.")
        success = False
    # Threshold for errors: 1e-4 units for translation, 0.5 degrees for rotation
    if summary_data["max_frame_pos_err"] > 1e-4 or summary_data["max_frame_rot_err"] > 0.5:
        print("[verify_bones] FAILED: Bone rest pose diverges from expected frame world matrix.")
        success = False
    if summary_data["max_bind_pos_err"] > 1e-4 or summary_data["max_bind_rot_err"] > 0.5:
        print("[verify_bones] WARNING/FAILED: Bone rest pose diverges from expected skin bind matrix.")
        success = False

    if success:
        print("[verify_bones] SUCCESS: Bone conversions are correct!")
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
