"""blend_importer.mesh — Mesh object builder with skinning + parenting.

Ports ``build_mesh_object`` from the original ``blend_importer.py`` (lines
231-329).  Changes vs. the original:

  - The unused ``all_materials_cache`` parameter is dropped (it was
    always passed ``{}`` and never read).
  - The hardcoded ``range(4)`` in the vertex-group weight loop now uses
    ``max_influences`` so the importer can match a C++-side cap other
    than 4 (defensive — the C++ side caps at 4 today, but the JSON may
    carry fewer slots for some assets).
"""

import sys
import logging

import bpy

from .materials import build_materials

_log = logging.getLogger(__name__)


def build_mesh_object(mesh_data, arm_obj, nodes_data, mesh_index,
                      max_influences=4, source_x_path=None):
    """
    Build a single Blender mesh object from a JSON mesh dict.

    Parameters
    ----------
    mesh_data : dict
        One entry from the JSON ``meshes`` array.  Expected keys:
        ``name``, ``vertices`` (list of ``{p, n, uv, ji, jw}``),
        ``indices`` (flat triangle list), ``materials`` (mat defs),
        ``face_material_indices``, ``has_skin``, ``bone_names``,
        ``inverse_bind_matrices``.
    arm_obj : bpy.types.Object or None
        The armature to skin against / parent to.  ``None`` for a
        scene with no bones.
    nodes_data : list of dict
        The JSON ``nodes`` array (used to find the node a non-skinned
        mesh is attached to so it can be BONE-parented to its frame).
    mesh_index : int
        Index of this mesh in the JSON ``meshes`` array (used to find
        the owning node).
    max_influences : int, optional
        Maximum bone-influence slots to write per vertex.  Defaults to
        4 (matches the C++ MeshExtractor cap).

    Returns
    -------
    bpy.types.Object
        The created mesh object.
    """
    name     = mesh_data.get("name") or "Mesh"
    vertices = mesh_data["vertices"]
    indices  = mesh_data["indices"]
    mat_defs = mesh_data.get("materials", [])

    # Build positions and face index lists.
    positions = [tuple(v["p"]) for v in vertices]
    normals   = [tuple(v["n"]) for v in vertices]
    uvs_raw   = [tuple(v["uv"]) for v in vertices]

    # Indices come as a flat triangle list (3 per face).
    if len(indices) % 3 != 0:
        print(f"[blend_importer] WARNING: mesh '{name}' index count not "
              f"multiple of 3", file=sys.stderr)
    faces = [
        (indices[i], indices[i+1], indices[i+2])
        for i in range(0, len(indices) - 2, 3)
    ]

    me = bpy.data.meshes.new(name)
    me.from_pydata(positions, [], faces)
    me.update()

    # Smooth shading per face (use_auto_smooth removed in Blender 4.1+).
    for poly in me.polygons:
        poly.use_smooth = True

    # Custom split normals — try the API, skip gracefully if unavailable.
    try:
        me.normals_split_custom_set_from_vertices(normals)
    except (AttributeError, RuntimeError):
        pass  # Normals will be recomputed from geometry.

    # UV layer.
    uv_layer = me.uv_layers.new(name="UVMap")
    for poly in me.polygons:
        for loop_idx in poly.loop_indices:
            vi = me.loops[loop_idx].vertex_index
            uv_layer.data[loop_idx].uv = uvs_raw[vi]

    # Materials.  Pass source_x_path so texture paths can be resolved
    # against the .X source directory.
    mats = build_materials(mat_defs, source_x_path=source_x_path)
    for m in mats:
        me.materials.append(m)

    # Per-face material assignment.
    fmi = mesh_data.get("face_material_indices", [])
    if fmi and len(mats) > 1:
        for fi, poly in enumerate(me.polygons):
            if fi < len(fmi):
                poly.material_index = fmi[fi]

    obj = bpy.data.objects.new(name, me)
    bpy.context.collection.objects.link(obj)

    # Skinning: vertex groups + armature modifier.
    if mesh_data.get("has_skin") and arm_obj:
        bone_names = mesh_data.get("bone_names", [])
        # Create a vertex group per bone.
        vg_map = {}
        for bn in bone_names:
            vg_map[bn] = obj.vertex_groups.new(name=bn)

        for vi, vert in enumerate(vertices):
            ji = vert["ji"]
            jw = vert["jw"]
            for slot in range(max_influences):
                bi = ji[slot]
                w  = jw[slot]
                if w > 0.0 and bi < len(bone_names):
                    vg_map[bone_names[bi]].add([vi], w, 'REPLACE')

        # Use parent_set operator so Blender wires up the dependency
        # graph, matrix_parent_inverse, and armature modifier correctly.
        bpy.ops.object.select_all(action='DESELECT')
        obj.select_set(True)
        arm_obj.select_set(True)
        bpy.context.view_layer.objects.active = arm_obj
        bpy.ops.object.parent_set(type='ARMATURE_NAME', keep_transform=False)
        bpy.ops.object.select_all(action='DESELECT')

    # Parenting for unskinned meshes attached to frames.
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
            obj.matrix_parent_inverse = (arm_obj.matrix_world
                                          @ arm_obj.pose.bones[node_name].matrix).inverted()

    return obj
