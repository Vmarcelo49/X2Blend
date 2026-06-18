"""blend_importer.math_utils — Shared matrix helpers.

Pure-Python module: imports only ``math`` at module top.  ``mathutils`` is
imported lazily inside the functions that need it, so this module is
importable (and unit-testable) without Blender installed.  This is the
only module in the package with that property; everything else assumes
``bpy`` / ``mathutils`` are available because the caller has booted
Blender.

Functions
---------
mat4_to_mathutils(rows)
    Convert a D3DX row-vector 4x4 matrix to a Blender column-vector Matrix.

vec_roll_to_mat3(nor, roll)
    Build a 3x3 bone-orientation matrix from a direction + roll (Blender's
    canonical bone-axis algorithm).

_mat3_to_roll(mat3)
    Inverse of vec_roll_to_mat3: recover the roll angle from a 3x3 matrix.

pose_local_matrix(M_world, M_rest, M_world_parent, M_rest_parent)
    Derive the Blender pose-bone local matrix from baked world matrices.

compute_bone_tail_lengths(bone_heads, bone_parent, bone_children, fallback_length)
    Compute per-bone visual tail lengths from the hierarchy.  Pure function,
    no Blender dependency — extracted for testability.
"""

import math

__all__ = [
    "mat4_to_mathutils",
    "vec_roll_to_mat3",
    "mat3_to_roll",
    "pose_local_matrix",
    "compute_bone_tail_lengths",
]


def mat4_to_mathutils(rows):
    """
    Convert a DirectX/D3DX row-vector matrix to Blender's column-vector matrix.

    D3DX stores translation in the last row and composes row vectors as
    ``v * M``.  ``mathutils.Matrix`` uses column vectors with translation in
    the last column, so the numerical matrix has to be transposed before
    Blender consumes it.

    Parameters
    ----------
    rows : sequence of 4 sequences of 4 floats
        The D3DX matrix as ``[[r0c0, r0c1, r0c2, r0c3], ...]``.

    Returns
    -------
    mathutils.Matrix
        A 4x4 Blender matrix (translation in the last column).
    """
    import mathutils  # lazy: keeps module importable without Blender

    return mathutils.Matrix([rows[r] for r in range(4)]).transposed()


def vec_roll_to_mat3(nor, roll):
    """
    Build a 3x3 bone-orientation matrix from a direction vector and a roll
    angle (Blender's canonical bone-axis algorithm).

    Ported verbatim from the original blend_importer.py lines 49-62 so the
    rest-pose roll derivation produces byte-identical results.
    """
    import mathutils  # lazy

    target = mathutils.Vector((0, 1, 0))
    nor = nor.normalized()
    axis = target.cross(nor)
    if axis.dot(axis) > 1e-10:
        axis.normalize()
        theta = target.angle(nor)
        b_matrix = mathutils.Matrix.Rotation(theta, 3, axis)
    else:
        updown = 1 if target.dot(nor) > 0 else -1
        b_matrix = mathutils.Matrix.Scale(updown, 3)

    r_matrix = mathutils.Matrix.Rotation(roll, 3, nor)
    return r_matrix @ b_matrix


def mat3_to_roll(mat3):
    """
    Compute bone roll angle from a 3x3 rotation matrix.

    Correctly aligns the bone local axes to match the matrix.  Ported
    verbatim from the original ``_mat3_to_roll`` (lines 65-74).  Renamed to
    drop the leading underscore because it is now part of the package's
    public surface (used by ``armature.build_armature``).
    """
    import mathutils  # lazy

    vec = mat3.col[1]
    vecmat = vec_roll_to_mat3(vec, 0)
    vecmatinv = vecmat.inverted()
    rollmat = vecmatinv @ mat3
    return math.atan2(rollmat[0][2], rollmat[2][2])


def pose_local_matrix(M_world, M_rest, M_world_parent=None, M_rest_parent=None):
    """
    Derive the Blender pose-bone local matrix from baked world matrices.

    The formula is the textbook decomposition that matches what Blender's
    pose-bone ``.matrix`` (world-space pose) and ``.matrix_local`` (rest
    pose in armature space) expect:

        With parent:    ``M_pose_local = M_rest^-1 @ M_rest_parent @ M_world_parent^-1 @ M_world``
        Without parent: ``M_pose_local = M_rest^-1 @ M_world``

    Falls back to a 4x4 Identity matrix on singular inputs (matches the
    original blend_importer.py try/except ValueError fallback at lines
    492-503).

    Parameters
    ----------
    M_world : mathutils.Matrix
        Baked world matrix of this bone at the current frame.
    M_rest : mathutils.Matrix
        Rest pose (``bone.matrix_local``) of this bone.
    M_world_parent : mathutils.Matrix, optional
        Baked world matrix of this bone's parent at the current frame.
    M_rest_parent : mathutils.Matrix, optional
        Rest pose of this bone's parent.

    Returns
    -------
    mathutils.Matrix
        The 4x4 pose-local matrix Blender expects to be decomposed into
        location / rotation_quaternion / scale and written to the F-curve.
    """
    import mathutils  # lazy

    try:
        if M_world_parent is not None and M_rest_parent is not None:
            return M_rest.inverted() @ M_rest_parent @ M_world_parent.inverted() @ M_world
        return M_rest.inverted() @ M_world
    except ValueError:
        # Singular matrix — fall back to identity (preserves original
        # behavior so a single bad bone doesn't abort the whole action).
        return mathutils.Matrix.Identity(4)


# --- Tail-length computation constants (used by compute_bone_tail_lengths) ---

# When a bone has children, the tail extends this fraction of the distance
# to the nearest child.  0.8 leaves a small visual gap.
CHILD_TAIL_FRACTION = 0.8

# When a bone has no children (leaf), its tail is this fraction of its
# parent's tail length.
LEAF_TAIL_FRACTION = 0.5

# When we can't determine a length from the hierarchy at all, use this
# fraction of the average parent->child distance.
FALLBACK_TAIL_FRACTION = 0.3


def compute_bone_tail_lengths(bone_heads, bone_parent, bone_children, fallback_length):
    """Compute per-bone visual tail lengths from the hierarchy.

    Pure function — no Blender dependency.  Extracted from
    ``armature.build_armature`` so the tail-length algorithm is unit-testable
    without importing ``bpy``.

    Algorithm
    ---------
    * A bone with children gets ``CHILD_TAIL_FRACTION`` (0.8) of the distance
      to its nearest child.
    * A leaf bone (no children) gets ``LEAF_TAIL_FRACTION`` (0.5) of its
      parent's tail length.
    * If no parent→child distance exists in the skeleton (single root bone,
      or all bones coincident), the ``fallback_length`` is used.

    Bones must be processed in topological order (parents before children)
    so leaf bones can inherit from their parent's computed length.  The
    caller is responsible for ensuring ``bone_heads`` keys are in that order
    (depth-first hierarchy traversal order).

    Parameters
    ----------
    bone_heads : dict
        ``{bone_id: (x, y, z)}`` — the head position of each bone.  Keys can
        be any hashable (node index, bone name, etc.).  Values are any
        3-element sequence (tuple, list, Vector).
    bone_parent : dict
        ``{bone_id: parent_bone_id_or_None}`` — the nearest bone ancestor
        for each bone.  ``None`` (or a key not in ``bone_heads``) means root.
    bone_children : dict
        ``{bone_id: [child_bone_id, ...]}`` — the direct bone children of
        each bone.  Must have an entry for every key in ``bone_heads``.
    fallback_length : float
        Used when the hierarchy can't determine a length.

    Returns
    -------
    dict
        ``{bone_id: float}`` — the computed tail length for each bone.
    """
    # --- Compute average parent->child distance (for leaf fallback) ---
    all_dists = []
    for bi, children in bone_children.items():
        for child in children:
            dx = bone_heads[child][0] - bone_heads[bi][0]
            dy = bone_heads[child][1] - bone_heads[bi][1]
            dz = bone_heads[child][2] - bone_heads[bi][2]
            d = math.sqrt(dx * dx + dy * dy + dz * dz)
            if d > 1e-6:
                all_dists.append(d)
    avg_dist = (sum(all_dists) / len(all_dists)) if all_dists else 0.0

    # --- Compute tail lengths (topological order: parents before children) ---
    tail_lengths = {}
    for bi in bone_heads:  # caller ensures depth-first order
        children = bone_children.get(bi, [])
        if children:
            # Distance to nearest child bone.
            min_d = float('inf')
            for c in children:
                dx = bone_heads[c][0] - bone_heads[bi][0]
                dy = bone_heads[c][1] - bone_heads[bi][1]
                dz = bone_heads[c][2] - bone_heads[bi][2]
                d = math.sqrt(dx * dx + dy * dy + dz * dz)
                if d < min_d:
                    min_d = d
            if min_d > 1e-6:
                tail_lengths[bi] = min_d * CHILD_TAIL_FRACTION
            elif avg_dist > 1e-6:
                tail_lengths[bi] = avg_dist * FALLBACK_TAIL_FRACTION
            else:
                tail_lengths[bi] = fallback_length
        else:
            # Leaf bone: inherit from parent, or use average/fallback.
            pi = bone_parent.get(bi)
            if pi is not None and pi in tail_lengths and tail_lengths[pi] > 1e-6:
                tail_lengths[bi] = tail_lengths[pi] * LEAF_TAIL_FRACTION
            elif avg_dist > 1e-6:
                tail_lengths[bi] = avg_dist * FALLBACK_TAIL_FRACTION
            else:
                tail_lengths[bi] = fallback_length

    return tail_lengths
