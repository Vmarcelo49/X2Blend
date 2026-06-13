#!/usr/bin/env python3
"""
blend_importer.py  —  Blender headless (.blend builder)
========================================================
Reads the JSON produced by x2blend.exe and creates a native .blend file
using the `bpy` module (Blender-as-a-Python-package).

Usage
-----
Install:
    pip install bpy

Run (standalone):
    python scripts/blend_importer.py <model.json> <output.blend>

Called automatically by x2blend.sh.
"""

import sys
import json
import math
import os

# ---------------------------------------------------------------------------
# bpy import — works both as `blender --python` and as a pip package
# ---------------------------------------------------------------------------
try:
    import bpy
    import mathutils
except ImportError:
    sys.exit("[blend_importer] ERROR: 'bpy' not found.  Install with: pip install bpy")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def mat4_to_mathutils(rows):
    """
    Convert a DirectX/D3DX row-vector matrix to Blender's column-vector matrix.

    D3DX stores translation in the last row and composes row vectors as
    v * M.  mathutils uses column vectors with translation in the last column,
    so the numerical matrix has to be transposed before Blender consumes it.
    """
    return mathutils.Matrix([rows[r] for r in range(4)]).transposed()


def vec_roll_to_mat3(nor, roll):
    target = mathutils.Vector((0, 1, 0))
    nor = nor.normalized()
    axis = target.cross(nor)
    if axis.dot(axis) > 1e-10:
        axis.normalize()
        theta = target.angle(nor)
        bMatrix = mathutils.Matrix.Rotation(theta, 3, axis)
    else:
        updown = 1 if target.dot(nor) > 0 else -1
        bMatrix = mathutils.Matrix.Scale(updown, 3)
    
    rMatrix = mathutils.Matrix.Rotation(roll, 3, nor)
    return rMatrix @ bMatrix


def _mat3_to_roll(mat3):
    """
    Compute bone roll angle from a 3x3 rotation matrix.
    Correctly aligns the bone local axes to match the matrix.
    """
    vec = mat3.col[1]
    vecmat = vec_roll_to_mat3(vec, 0)
    vecmatinv = vecmat.inverted()
    rollmat = vecmatinv @ mat3
    return math.atan2(rollmat[0][2], rollmat[2][2])


# ---------------------------------------------------------------------------
# Scene reset
# ---------------------------------------------------------------------------

def reset_scene():
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    for block in list(bpy.data.meshes):
        bpy.data.meshes.remove(block)
    for block in list(bpy.data.armatures):
        bpy.data.armatures.remove(block)
    for block in list(bpy.data.materials):
        bpy.data.materials.remove(block)
    for block in list(bpy.data.actions):
        bpy.data.actions.remove(block)


# ---------------------------------------------------------------------------
# Materials
# ---------------------------------------------------------------------------

def build_materials(mat_defs):
    """
    mat_defs: list of material dicts from JSON mesh.
    Returns a list of bpy.types.Material objects.
    """
    mats = []
    for md in mat_defs:
        name = md.get("name") or "Material"
        mat = bpy.data.materials.new(name=name)
        mat.use_nodes = True
        bsdf = mat.node_tree.nodes.get("Principled BSDF")
        if bsdf:
            d = md["diffuse"]
            alpha = md.get("alpha", 1.0)
            bsdf.inputs["Base Color"].default_value = (d[0], d[1], d[2], alpha)
            if "Alpha" in bsdf.inputs:
                bsdf.inputs["Alpha"].default_value = alpha
            if alpha < 0.999:
                mat.blend_method = 'BLEND'
                if hasattr(mat, "use_screen_refraction"):
                    mat.use_screen_refraction = True
            spec = md.get("specular", [0, 0, 0])
            spec_power = md.get("specular_power", 0.0)
            # Map specular_power (0-128) to roughness (1-0)
            roughness = 1.0 - min(spec_power / 128.0, 1.0)
            bsdf.inputs["Roughness"].default_value = roughness
            em = md.get("emissive", [0, 0, 0])
            bsdf.inputs["Emission Color"].default_value = (em[0], em[1], em[2], 1.0)
            # Texture
            tex_file = md.get("texture", "")
            if tex_file:
                tex_node = mat.node_tree.nodes.new("ShaderNodeTexImage")
                if os.path.isfile(tex_file):
                    tex_node.image = bpy.data.images.load(tex_file)
                else:
                    # Create a placeholder so the slot exists
                    tex_node.label = tex_file
                mat.node_tree.links.new(
                    tex_node.outputs["Color"],
                    bsdf.inputs["Base Color"]
                )
        mats.append(mat)
    return mats


# ---------------------------------------------------------------------------
# Armature
# ---------------------------------------------------------------------------

def build_armature(nodes_data, meshes_data):
    """
    Create a Blender Armature from node data.
    Bone rest poses are derived from the inverse bind matrices stored in the mesh
    skinning data (most accurate source) with a fallback to the node frame hierarchy.
    This ensures that Blender's armature modifier rest-pose inverse matches the
    DirectX SkinWeights inverseBindMatrix exactly.
    Returns (armature_object, {node_index: bone_name}, world_mats).
    """
    arm_data = bpy.data.armatures.new("Armature")
    arm_obj  = bpy.data.objects.new("Armature", arm_data)
    bpy.context.collection.objects.link(arm_obj)
    bpy.context.view_layer.objects.active = arm_obj

    # Viewport display settings to make bones visible and clean
    arm_obj.show_in_front = True
    arm_data.display_type = 'STICK'

    # --- Compute world-space matrices for each node (fallback path) ---
    n = len(nodes_data)
    world_mats = [None] * n
    for i, nd in enumerate(nodes_data):
        # Use the original frame matrix here.  It preserves the exact DirectX
        # bind hierarchy and goes through the same row-vector conversion as the
        # skin offset matrices.
        local = mat4_to_mathutils(nd["local_transform"])
        pi = nd["parent_index"]
        world_mats[i] = (world_mats[pi] @ local) if pi >= 0 else local

    # --- Collect bind matrices from inverse bind matrices (primary path) ---
    # bind_mat = inv(inverseBindMatrix) = bone world matrix at bind time.
    # This is the authoritative source for the bone rest pose; using it ensures
    # that Blender's internal M_rest^-1 matches the stored inverseBindMatrix.
    bone_bind_mats = {}  # bone_name → 4×4 bind matrix
    for mesh in meshes_data:
        for bname, ibm_rows in zip(
            mesh.get("bone_names", []),
            mesh.get("inverse_bind_matrices", [])
        ):
            if bname and bname not in bone_bind_mats:
                ibm = mat4_to_mathutils(ibm_rows)
                try:
                    bone_bind_mats[bname] = ibm.inverted()
                except ValueError:
                    pass  # singular matrix — fall back to frame hierarchy

    bpy.ops.object.mode_set(mode='EDIT')
    bone_name_map = {}  # node_index → edit_bone name

    for i, nd in enumerate(nodes_data):
        if not nd["is_bone"]:
            continue
        eb = arm_data.edit_bones.new(nd["name"])
        eb.use_connect = False

        bname = nd["name"]
        if bname in bone_bind_mats:
            bind_mat = bone_bind_mats[bname]
        else:
            bind_mat = world_mats[i]  # fallback

        eb.head = bind_mat.translation.copy()
        # Tail: 0.05 units along the bone's local Y axis (preserves orientation)
        y_axis = bind_mat.col[1].xyz.normalized()
        eb.tail = eb.head + y_axis * 0.05
        # Roll: align bone X/Z axes to match the bind matrix
        eb.roll = _mat3_to_roll(bind_mat.to_3x3())

        # Set parent bone
        pi = nd["parent_index"]
        if pi >= 0 and pi in bone_name_map:
            parent_eb = arm_data.edit_bones.get(bone_name_map[pi])
            if parent_eb:
                eb.parent = parent_eb
        bone_name_map[i] = eb.name

    bpy.ops.object.mode_set(mode='OBJECT')
    return arm_obj, bone_name_map, world_mats


# ---------------------------------------------------------------------------
# Mesh objects
# ---------------------------------------------------------------------------

def build_mesh_object(mesh_data, all_materials_cache, arm_obj, nodes_data, mesh_index):
    """
    Build a single Blender mesh object from a JSON mesh dict.
    Returns the created bpy.types.Object.
    """
    name     = mesh_data.get("name") or "Mesh"
    vertices = mesh_data["vertices"]
    indices  = mesh_data["indices"]
    mat_defs = mesh_data.get("materials", [])

    # Build positions and face index lists
    positions = [tuple(v["p"]) for v in vertices]
    normals   = [tuple(v["n"]) for v in vertices]
    uvs_raw   = [tuple(v["uv"]) for v in vertices]

    # Indices come as a flat triangle list (3 per face)
    if len(indices) % 3 != 0:
        print(f"[blend_importer] WARNING: mesh '{name}' index count not multiple of 3", file=sys.stderr)
    faces = [
        (indices[i], indices[i+1], indices[i+2])
        for i in range(0, len(indices) - 2, 3)
    ]

    me = bpy.data.meshes.new(name)
    me.from_pydata(positions, [], faces)
    me.update()

    # Smooth shading per face (use_auto_smooth removed in Blender 4.1+)
    for poly in me.polygons:
        poly.use_smooth = True

    # Custom split normals — try the API, skip gracefully if unavailable
    try:
        me.normals_split_custom_set_from_vertices(normals)
    except (AttributeError, RuntimeError):
        pass  # Normals will be recomputed from geometry

    # UV layer
    uv_layer = me.uv_layers.new(name="UVMap")
    for poly in me.polygons:
        for loop_idx in poly.loop_indices:
            vi = me.loops[loop_idx].vertex_index
            uv_layer.data[loop_idx].uv = uvs_raw[vi]

    # Materials
    mats = build_materials(mat_defs)
    for m in mats:
        me.materials.append(m)

    # Per-face material assignment
    fmi = mesh_data.get("face_material_indices", [])
    if fmi and len(mats) > 1:
        for fi, poly in enumerate(me.polygons):
            if fi < len(fmi):
                poly.material_index = fmi[fi]

    obj = bpy.data.objects.new(name, me)
    bpy.context.collection.objects.link(obj)

    # Skinning: vertex groups + armature modifier
    if mesh_data.get("has_skin") and arm_obj:
        bone_names = mesh_data.get("bone_names", [])
        # Create a vertex group per bone
        vg_map = {}
        for bn in bone_names:
            vg_map[bn] = obj.vertex_groups.new(name=bn)

        for vi, vert in enumerate(vertices):
            ji = vert["ji"]
            jw = vert["jw"]
            for slot in range(4):
                bi = ji[slot]
                w  = jw[slot]
                if w > 0.0 and bi < len(bone_names):
                    vg_map[bone_names[bi]].add([vi], w, 'REPLACE')

        # Use parent_set operator so Blender wires up the dependency graph,
        # matrix_parent_inverse, and armature modifier correctly.
        bpy.ops.object.select_all(action='DESELECT')
        obj.select_set(True)
        arm_obj.select_set(True)
        bpy.context.view_layer.objects.active = arm_obj
        bpy.ops.object.parent_set(type='ARMATURE_NAME', keep_transform=False)
        bpy.ops.object.select_all(action='DESELECT')

    # Parenting for unskinned meshes attached to frames
    if not mesh_data.get("has_skin") and arm_obj:
        node_name = None
        for nd in nodes_data:
            if nd.get("mesh_index") == mesh_index:
                node_name = nd["name"]
                break
        if node_name and node_name in arm_obj.pose.bones:
            obj.parent = arm_obj
            obj.parent_type = 'BONE'
            obj.parent_bone = node_name
            obj.matrix_parent_inverse = (arm_obj.matrix_world @ arm_obj.pose.bones[node_name].matrix).inverted()

    return obj


def detect_fps(anim_list):
    """
    Detect the frame rate (FPS) of the animations by analyzing
    the time differences between consecutive keyframes.
    Defaults to 30.0 if no animations are found or if rate is ambiguous.
    """
    if not anim_list:
        return 30.0

    min_diff = 1.0
    for anim in anim_list:
        for ch in anim.get("channels", []):
            for keys_name in ["translation_keys", "rotation_keys", "scale_keys"]:
                keys = ch.get(keys_name, [])
                if len(keys) > 1:
                    for i in range(len(keys) - 1):
                        diff = keys[i+1]["t"] - keys[i]["t"]
                        if diff > 0.001 and diff < min_diff:
                            min_diff = diff

    if min_diff < 1.0:
        detected_fps = 1.0 / min_diff
        # Round to nearest common standard FPS
        for target_fps in [60.0, 30.0, 24.0]:
            if abs(detected_fps - target_fps) < 2.0:
                return target_fps
    return 30.0


# ---------------------------------------------------------------------------
# Animations
# ---------------------------------------------------------------------------

def build_animations(anim_list, arm_obj, bone_name_map, nodes_data, fps=30.0):
    """
    Create NLA-ready Actions on the armature for each XAnimation.
    bone_name_map: {node_index → blender_bone_name}
    """
    if not arm_obj or not anim_list:
        return

    # Reverse lookup: node_name → node_index
    name_to_idx = {nd["name"]: i for i, nd in enumerate(nodes_data)}

    for anim_data in anim_list:
        anim_name = anim_data.get("name") or "Action"
        action = bpy.data.actions.new(name=anim_name)

        if not arm_obj.animation_data:
            arm_obj.animation_data_create()
        arm_obj.animation_data.action = action

        # Compatibility helper to support both legacy and slotted actions
        fcurves_container = None
        if hasattr(action, "fcurves"):
            fcurves_container = action
        else:
            # Blender >= 4.4 slotted action API
            if not action.slots:
                slot = action.slots.new(id_type='OBJECT', name=arm_obj.name)
            else:
                slot = action.slots[0]
            
            arm_obj.animation_data.action_slot = slot
            
            if not action.layers:
                layer = action.layers.new(name="Base Layer")
            else:
                layer = action.layers[0]
                
            if not layer.strips:
                strip = layer.strips.new(type='KEYFRAME')
            else:
                strip = layer.strips[0]
                
            fcurves_container = strip.channelbag(slot, ensure=True)

        # Helper to create F-curve safely
        def create_fcurve(data_path, index, group_name):
            try:
                return fcurves_container.fcurves.new(
                    data_path=data_path,
                    index=index,
                    action_group=group_name
                )
            except TypeError:
                return fcurves_container.fcurves.new(
                    data_path=data_path,
                    index=index
                )

        # Collect baked keys by frame index: frame_num -> {bone_name: Matrix}
        frames_map = {}
        for ch in anim_data.get("channels", []):
            target = ch.get("target_node", "")
            ni = name_to_idx.get(target, -1)
            bone_name = bone_name_map.get(ni)
            if not bone_name:
                continue
            
            baked_keys = ch.get("baked_keys", [])
            for kf in baked_keys:
                f_num = int(round(kf["t"] * fps))
                if f_num not in frames_map:
                    frames_map[f_num] = {}
                
                # Convert matrix raw list of lists to column-major mathutils.Matrix
                mat_raw = kf["m"]
                mat = mathutils.Matrix(mat_raw)
                mat.transpose()
                frames_map[f_num][bone_name] = mat

        if not frames_map:
            continue

        # Get the list of bone names present in the frames
        first_frame_num = sorted(frames_map.keys())[0]
        active_bones = list(frames_map[first_frame_num].keys())

        # Create F-curves for all active bones
        bone_fcurves = {}
        for bname in active_bones:
            data_path_prefix = f'pose.bones["{bname}"]'
            bone_fcurves[bname] = {
                "location": [create_fcurve(f"{data_path_prefix}.location", i, bname) for i in range(3)],
                "rotation_quaternion": [create_fcurve(f"{data_path_prefix}.rotation_quaternion", i, bname) for i in range(4)],
                "scale": [create_fcurve(f"{data_path_prefix}.scale", i, bname) for i in range(3)]
            }

        # Cache rest matrices and parent relations to avoid repeated lookups
        rest_mats = {}
        rest_parents = {}
        parent_names = {}
        
        for bname in active_bones:
            bone = arm_obj.data.bones.get(bname)
            if bone:
                rest_mats[bname] = bone.matrix_local
                parent_names[bname] = bone.parent.name if bone.parent else None
                if bone.parent:
                    rest_parents[bname] = bone.parent.matrix_local

        # Compute keyframe pose values mathematically for each frame
        for f_num in sorted(frames_map.keys()):
            for bname in active_bones:
                if bname not in frames_map[f_num]:
                    continue
                
                M_world = frames_map[f_num][bname]
                M_rest = rest_mats.get(bname)
                if not M_rest:
                    continue

                parent_name = parent_names.get(bname)
                
                if parent_name and parent_name in frames_map[f_num]:
                    M_world_parent = frames_map[f_num][parent_name]
                    M_rest_parent = rest_parents.get(bname)
                    
                    # M_pose_local = M_rest^-1 * M_rest_parent * M_world_parent^-1 * M_world
                    try:
                        M_pose_local = M_rest.inverted() @ M_rest_parent @ M_world_parent.inverted() @ M_world
                    except ValueError:
                        # Fallback in case of singular matrices
                        M_pose_local = mathutils.Matrix.Identity(4)
                else:
                    # No parent in the armature hierarchy (root bone)
                    # M_pose_local = M_rest^-1 * M_world
                    try:
                        M_pose_local = M_rest.inverted() @ M_world
                    except ValueError:
                        M_pose_local = mathutils.Matrix.Identity(4)

                # Decompose the relative pose matrix into loc, rot, scale
                loc, rot, scale = M_pose_local.decompose()

                # Write directly to F-curves
                fcs = bone_fcurves[bname]
                for i in range(3):
                    fcs["location"][i].keyframe_points.insert(f_num, loc[i])
                for i in range(4):
                    fcs["rotation_quaternion"][i].keyframe_points.insert(f_num, rot[i])
                for i in range(3):
                    fcs["scale"][i].keyframe_points.insert(f_num, scale[i])

        # Push to NLA strip so multiple animations co-exist
        if arm_obj.animation_data:
            track = arm_obj.animation_data.nla_tracks.new()
            track.name = anim_name
            track.strips.new(anim_name, 1, action)
            arm_obj.animation_data.action = None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) < 3:
        print("Usage: blend_importer.py <model.json> <output.blend>", file=sys.stderr)
        sys.exit(1)

    json_path  = sys.argv[1]
    blend_path = sys.argv[2]

    with open(json_path, "r", encoding="utf-8") as f:
        model = json.load(f)

    nodes_data = model.get("nodes", [])
    meshes_data = model.get("meshes", [])
    anims_data  = model.get("animations", [])

    # --- 1. Reset scene ---
    reset_scene()

    # --- 2. Armature (needed before meshes for parenting) ---
    has_bones = any(nd.get("is_bone") for nd in nodes_data)
    arm_obj      = None
    bone_name_map = {}
    world_mats    = []
    if has_bones:
        arm_obj, bone_name_map, world_mats = build_armature(nodes_data, meshes_data)

    # --- 3. Meshes ---
    mesh_objects = []
    for idx, mesh_data in enumerate(meshes_data):
        obj = build_mesh_object(mesh_data, {}, arm_obj, nodes_data, idx)
        mesh_objects.append(obj)

    # --- 4. Scale Adjustment ---
    # The default size of the imported models (particularly the 00.X model) is
    # extremely large. To ensure the model is at a correct and usable scale in Blender
    # (preventing viewport clipping and display issues) we scale all root objects
    # (objects with no parent, such as the armature and unparented meshes) down
    # to 0.01. This automates the manual scale adjustment.
    for obj in bpy.context.scene.objects:
        if obj.parent is None:
            obj.scale = (0.01, 0.01, 0.01)



    # --- 5. Animations ---
    if anims_data:
        fps = detect_fps(anims_data)
        print(f"[blend_importer] Detected animation rate: {fps} FPS")
        bpy.context.scene.render.fps = int(fps)
        build_animations(anims_data, arm_obj, bone_name_map, nodes_data, fps=fps)

    # --- 6. Save ---
    bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(blend_path))


# Support both `python blend_importer.py args` and `blender --python blend_importer.py -- args`
if __name__ == "__main__":
    # When called as `blender --background --python script.py -- arg1 arg2`
    # sys.argv contains everything after `--`; strip any blender-injected prefix.
    if "--" in sys.argv:
        sys.argv = [sys.argv[0]] + sys.argv[sys.argv.index("--") + 1:]
    main()
