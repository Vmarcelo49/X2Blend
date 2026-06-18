"""blend_importer.armature — Build the armature from inverse-bind matrices.

Ports ``build_armature`` from the original ``blend_importer.py`` (lines
147-224).  The original hardcoded a ``0.05`` bone tail length, which made
all bones look like spheres when the skeleton was at a realistic scale.
This module computes tail lengths from the bone hierarchy instead.

CRITICAL DESIGN DECISION — tail direction vs. skinning accuracy
================================================================

The .X file format (like FBX) does NOT store a "bone direction" — it only
stores frame transforms.  In 3ds Max / Maya, bones visually point towards
their child, but that's a display property, not stored in the transform.

Blender REQUIRES the bone's local Y axis = head→tail direction, and this
affects ``bone.matrix_local`` (the rest matrix).  The armature modifier
uses ``inv(bone.matrix_local)`` for skinning.

This creates a fundamental conflict:

  * **Skinning-accurate mode** (default, ``--visual-tails`` OFF):
    Tail direction = ``bind_mat.col[1]`` (the bind matrix's local Y axis).
    This guarantees ``bone.matrix_local == bind_mat``, so the armature
    modifier's ``inv(rest_world)`` matches the DirectX ``inverseBindMatrix``
    exactly and skinning is pixel-perfect.
    BUT: the local Y axis of the .X frame may not point "along the bone"
    (towards the child), so bones may visually point in odd directions
    (backward, sideways, etc.).

  * **Visual mode** (``--visual-tails`` ON):
    Tail direction = towards the nearest child bone (child-directed tails,
    matching Blender's FBX/glTF importers).  Bones look correct.
    BUT: ``bone.matrix_local != bind_mat``, so the armature modifier's
    ``inv(rest_world)`` no longer matches the DirectX inverse bind matrix,
    and SKINNING IS INCORRECT (vertices deform wrongly).
    The ANIMATION still works (it uses ``bone.matrix_local`` from Blender,
    which auto-compensates), but the mesh doesn't follow correctly.

This is a fundamental limitation of converting .X → Blender for
soft-skinned meshes.  There is no way to have both visually-correct tails
AND correct skinning without re-baking the vertex positions (which is
lossy for soft-skinned vertices with multiple influences).

The default is skinning-accurate (``--visual-tails`` OFF) because
functional correctness > visual correctness.  Use ``--visual-tails`` for
debugging the hierarchy when skinning isn't needed.

Tail LENGTHS are always computed from the hierarchy (see
``math_utils.compute_bone_tail_lengths``) — this does NOT affect
``matrix_local`` (only the tail DIRECTION matters for that), so length
computation is safe in both modes.
"""

import logging

import bpy
import mathutils

from .math_utils import mat4_to_mathutils, mat3_to_roll, compute_bone_tail_lengths

_log = logging.getLogger(__name__)


def build_armature(nodes_data, meshes_data, bone_tail_length, visual_tails=False):
    """Create a Blender Armature from node data.

    Parameters
    ----------
    nodes_data : list of dict
        The JSON ``nodes`` array.
    meshes_data : list of dict
        The JSON ``meshes`` array (for inverse-bind matrices).
    bone_tail_length : float
        Fallback tail length when the hierarchy can't determine one.
    visual_tails : bool, optional
        If True, use child-directed tails (visual correctness, breaks
        skinning).  If False (default), use bind-matrix local Y axis
        (skinning accuracy, tails may point oddly).  See module docstring.

    Returns
    -------
    (arm_obj, bone_name_map, world_mats)
    """
    arm_data = bpy.data.armatures.new("Armature")
    arm_obj  = bpy.data.objects.new("Armature", arm_data)
    bpy.context.collection.objects.link(arm_obj)
    bpy.context.view_layer.objects.active = arm_obj

    # Viewport display settings.
    # OCTAHEDRON shows the bone's full 3D orientation (not just the Y axis),
    # which makes it easier to see how the bind matrix's local axes are
    # oriented — useful for diagnosing why tails point oddly.
    arm_obj.show_in_front = True
    arm_data.display_type = 'OCTAHEDRAL'

    # --- Compute world-space matrices for each node (fallback path) ---
    n = len(nodes_data)
    world_mats = [None] * n
    for i, nd in enumerate(nodes_data):
        local = mat4_to_mathutils(nd["local_transform"])
        pi = nd["parent_index"]
        world_mats[i] = (world_mats[pi] @ local) if pi >= 0 else local

    # --- Collect bind matrices from inverse bind matrices (primary path) ---
    bone_bind_mats = {}
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
                    pass

    # --- Pass 1: collect bone indices, heads, bind matrices ---
    bone_indices = []
    bone_heads = {}
    bone_bind_lookup = {}
    for i, nd in enumerate(nodes_data):
        if not nd["is_bone"]:
            continue
        bname = nd["name"]
        if bname in bone_bind_mats:
            bind_mat = bone_bind_mats[bname]
        else:
            bind_mat = world_mats[i]
        bone_indices.append(i)
        bone_heads[i] = bind_mat.translation.copy()
        bone_bind_lookup[i] = bind_mat

    # --- Build the bone parent/child map (nearest bone ancestor) ---
    bone_parent = {}
    bone_children = {i: [] for i in bone_indices}
    for i in bone_indices:
        curr = nodes_data[i]["parent_index"]
        parent_bone = -1
        while curr >= 0:
            if curr in bone_heads:
                parent_bone = curr
                break
            curr = nodes_data[curr]["parent_index"]
        bone_parent[i] = parent_bone if parent_bone >= 0 else None
        if parent_bone >= 0:
            bone_children[parent_bone].append(i)

    # --- Compute tail lengths from the hierarchy ---
    tail_lengths = compute_bone_tail_lengths(
        bone_heads=bone_heads,
        bone_parent=bone_parent,
        bone_children=bone_children,
        fallback_length=bone_tail_length,
    )

    # --- Diagnostics: analyze which local axis best aligns with child ---
    # This helps understand WHY bones point oddly in skinning-accurate mode.
    # If most bones' best-aligning axis is X or Z (not Y), it means the .X
    # authoring tool (3ds Max/Panda) used a different bone axis convention.
    if _log.isEnabledFor(logging.DEBUG) and not visual_tails:
        _log_axis_diagnostics(bone_indices, bone_bind_lookup, bone_heads,
                              bone_children, nodes_data)

    if visual_tails:
        _log.warning(
            "visual_tails=True: using child-directed tails. "
            "Bone ORIENTATION will be visually correct, but SKINNING WILL BE "
            "INCORRECT (the armature modifier's inverse-bind no longer matches "
            "the DirectX inverseBindMatrix). Animation F-curves are unaffected. "
            "Use this mode only for hierarchy debugging."
        )

    # --- Pass 2: create edit bones ---
    bpy.ops.object.mode_set(mode='EDIT')
    bone_name_map = {}

    for i in bone_indices:
        nd = nodes_data[i]
        eb = arm_data.edit_bones.new(nd["name"])
        eb.use_connect = False

        bind_mat = bone_bind_lookup[i]
        eb.head = bone_heads[i].copy()

        if visual_tails:
            # Child-directed tail: point towards the nearest child bone.
            # This produces visually-correct bones but breaks skinning.
            tail_dir = _compute_child_directed_tail(
                i, bone_heads, bone_children, bind_mat
            )
            eb.tail = eb.head + tail_dir * tail_lengths[i]
            # Roll: align X/Z to the bind matrix as closely as possible
            # given the new Y direction.  This won't reproduce bind_mat
            # exactly (the Y axis changed), but gets the closest roll.
            eb.roll = _roll_for_direction(bind_mat, tail_dir)
        else:
            # Skinning-accurate tail: bind matrix's local Y axis.
            # Guarantees bone.matrix_local == bind_mat → correct skinning.
            y_axis = bind_mat.col[1].xyz.normalized()
            eb.tail = eb.head + y_axis * tail_lengths[i]
            eb.roll = mat3_to_roll(bind_mat.to_3x3())

        # Set parent bone (nearest bone ancestor).
        pi = bone_parent[i]
        if pi is not None and pi in bone_name_map:
            parent_eb = arm_data.edit_bones.get(bone_name_map[pi])
            if parent_eb:
                eb.parent = parent_eb
        bone_name_map[i] = eb.name

    bpy.ops.object.mode_set(mode='OBJECT')
    return arm_obj, bone_name_map, world_mats


def _compute_child_directed_tail(bone_idx, bone_heads, bone_children, bind_mat):
    """Compute a tail direction pointing towards the nearest child bone.

    If the bone has children, the direction is (nearest_child_head - this_head)
    normalized.  If it's a leaf bone (no children), fall back to the bind
    matrix's local Y axis (same as skinning-accurate mode) — there's no
    child to point towards.
    """
    children = bone_children.get(bone_idx, [])
    this_head = bone_heads[bone_idx]

    if children:
        # Direction to nearest child.
        nearest = min(children, key=lambda c: (bone_heads[c] - this_head).length)
        child_dir = (bone_heads[nearest] - this_head)
        if child_dir.length > 1e-6:
            return child_dir.normalized()

    # Leaf bone: no child to point to.  Use the bind matrix's local Y axis
    # (same as skinning-accurate mode) — there's no better option.
    return bind_mat.col[1].xyz.normalized()


def _roll_for_direction(bind_mat, tail_dir):
    """Compute the roll that best aligns the bone's X/Z axes to the bind
    matrix, given a specific tail direction (Y axis).

    This is similar to ``mat3_to_roll`` but uses the provided ``tail_dir``
    instead of ``bind_mat.col[1]``.  The result won't reproduce ``bind_mat``
    exactly (because the Y axis changed), but produces the closest roll.
    """
    from .math_utils import vec_roll_to_mat3
    try:
        vecmat = vec_roll_to_mat3(mathutils.Vector(tail_dir), 0)
        vecmatinv = vecmat.inverted()
        rollmat = vecmatinv @ bind_mat.to_3x3()
        return math.atan2(rollmat[0][2], rollmat[2][2])
    except (ValueError, ZeroDivisionError):
        return 0.0


def _log_axis_diagnostics(bone_indices, bone_bind_lookup, bone_heads,
                          bone_children, nodes_data):
    """Log which local axis (X/Y/Z) of each bind matrix best aligns with
    the direction to the nearest child.  Helps diagnose why bones point
    oddly in skinning-accurate mode.

    If most bones' best axis is NOT Y, it means the .X authoring tool used
    a different bone-axis convention, and the bind matrix's Y axis doesn't
    correspond to the "natural" bone direction.
    """
    axis_counts = {0: 0, 1: 0, 2: 0}  # X, Y, Z
    total = 0
    for bi in bone_indices:
        children = bone_children.get(bi, [])
        if not children:
            continue
        this_head = bone_heads[bi]
        nearest = min(children, key=lambda c: (bone_heads[c] - this_head).length)
        child_dir = (bone_heads[nearest] - this_head)
        if child_dir.length < 1e-6:
            continue
        child_dir.normalize()

        bind_mat = bone_bind_lookup[bi]
        # Transform child direction into the bone's local space.
        try:
            local_child_dir = (bind_mat.inverted().to_3x3() @ child_dir).normalized()
        except ValueError:
            continue

        # Find which local axis (X=0, Y=1, Z=2) best aligns.
        dots = [
            abs(local_child_dir.x),  # X axis
            abs(local_child_dir.y),  # Y axis
            abs(local_child_dir.z),  # Z axis
        ]
        best_axis = dots.index(max(dots))
        axis_counts[best_axis] += 1
        total += 1

    if total > 0:
        _log.debug(
            "Bone axis alignment (which local axis points towards child): "
            "X=%d (%.0f%%), Y=%d (%.0f%%), Z=%d (%.0f%%) of %d bones with children. "
            "If Y is not the majority, the .X authoring tool used a non-Y "
            "bone axis convention, and skinning-accurate mode will produce "
            "odd-looking tails.",
            axis_counts[0], 100.0 * axis_counts[0] / total,
            axis_counts[1], 100.0 * axis_counts[1] / total,
            axis_counts[2], 100.0 * axis_counts[2] / total,
            total,
        )
