"""blend_importer.bone_axis — Auto-detect and fix the bone axis convention.

The .X file format (like FBX) does not store a "bone direction" — it only
stores frame transforms.  Different authoring tools use different local
axes as the bone's "direction":

  * **3ds Max Biped** (the most common .X authoring pipeline for Japanese
    game development, including Higurashi Daybreak) uses **X** as the bone
    direction axis.
  * **Maya** uses **Y** (matches Blender's convention).
  * Some custom rigs use **Z**.

Blender **requires** the bone's local Y axis = head→tail direction.  This
is baked into ``bone.matrix_local`` and used by the armature modifier for
skinning.

If the .X file uses X as the bone axis, the current importer (which uses
``bind_mat.col[1]`` = Y for the tail) produces bones that point in wrong
directions (head correct, tail wrong).  Switching to ``bind_mat.col[0]``
(X) for the tail would fix the visual direction but BREAK SKINNING,
because ``bone.matrix_local`` would no longer match the inverse bind
matrix.

THE FIX: apply a consistent axis permutation to ALL data in the JSON
model before importing.  If the bone axis is X, swap X and Y everywhere:

  * Bone local transforms (4x4 matrices)
  * Inverse bind matrices (4x4 matrices)
  * Baked animation world matrices (4x4 matrices)
  * Vertex positions (3-vectors)
  * Vertex normals (3-vectors)
  * Triangle winding (toggle the index swap — two reflections cancel)

Because the permutation is applied UNIFORMLY to all bones and all
vertices, the skinning formula is preserved:

    deformed_new = sum_i(w_i * P@pose_i @ inv(P@rest_i) @ P@vertex)
                 = P @ sum_i(w_i * pose_i @ inv(rest_i) @ vertex)
                 = P @ deformed_old

The deformed vertex is in "permuted space," but since the vertices and
armature are all permuted consistently, the result is visually correct
in Blender's coordinate system.

After permutation, ``bind_mat.col[1]`` (Y) is the original bone
direction axis (was X), so the importer's existing tail-direction logic
produces visually-correct bones AND correct skinning.
"""

import logging
import math

_log = logging.getLogger(__name__)

# Axis indices
AXIS_X = 0
AXIS_Y = 1
AXIS_Z = 2


def detect_bone_axis(model):
    """Auto-detect which local axis (X/Y/Z) the .X file uses for bone direction.

    For each bone with children, computes the direction from the bone's head
    to its nearest child (in bone-local space) and finds which local axis
    (X/Y/Z) has the highest |dot product| with that direction.  Tallies
    votes across all bones and returns the axis with the most votes.

    Parameters
    ----------
    model : dict
        The JSON model dict with ``nodes``, ``meshes``, ``animations``.

    Returns
    -------
    str
        ``"x"``, ``"y"``, or ``"z"``.
    """
    nodes = model.get("nodes", [])
    meshes = model.get("meshes", [])

    # Build a name → node index map
    name_to_idx = {nd["name"]: i for i, nd in enumerate(nodes)}

    # Collect inverse bind matrices to determine each bone's rest orientation
    # (the inverse bind matrix, when inverted, gives the bone's world bind
    # matrix, whose columns are the local axes in world space).
    bone_bind_mats = {}  # name → 4x4 list-of-lists (world bind matrix)
    for mesh in meshes:
        for bname, ibm_rows in zip(
            mesh.get("bone_names", []),
            mesh.get("inverse_bind_matrices", [])
        ):
            if bname and bname not in bone_bind_mats:
                try:
                    inv = _invert_4x4(ibm_rows)
                    if inv:
                        bone_bind_mats[bname] = inv
                except Exception:
                    pass

    # For bones without inverse bind matrices, fall back to the local
    # transform's world accumulation.
    world_mats = [None] * len(nodes)
    for i, nd in enumerate(nodes):
        local = nd.get("local_transform")
        if local is None:
            continue
        pi = nd.get("parent_index", -1)
        if pi >= 0 and world_mats[pi] is not None:
            world_mats[i] = _matmul_4x4(world_mats[pi], local)
        else:
            world_mats[i] = local

    # Build bone parent/child map (nearest bone ancestor)
    bone_indices = [i for i, nd in enumerate(nodes) if nd.get("is_bone")]
    bone_parent = {}
    bone_children = {i: [] for i in bone_indices}
    for i in bone_indices:
        curr = nodes[i].get("parent_index", -1)
        while curr >= 0:
            if nodes[curr].get("is_bone"):
                bone_parent[i] = curr
                bone_children[curr].append(i)
                break
            curr = nodes[curr].get("parent_index", -1)

    # Tally axis votes
    axis_votes = {AXIS_X: 0, AXIS_Y: 0, AXIS_Z: 0}
    total = 0

    for bi in bone_indices:
        bname = nodes[bi]["name"]
        children = bone_children.get(bi, [])
        if not children:
            continue

        # Get this bone's world bind matrix
        bind = bone_bind_mats.get(bname) or world_mats[bi]
        if bind is None:
            continue

        # Get this bone's head position (world space).
        # D3DX row-major: translation is in the LAST ROW (m[3][0..2]).
        head = (bind[3][0], bind[3][1], bind[3][2])

        # Find nearest child's head position
        nearest_child_head = None
        nearest_dist = float('inf')
        for child_idx in children:
            cname = nodes[child_idx]["name"]
            cbind = bone_bind_mats.get(cname) or world_mats[child_idx]
            if cbind is None:
                continue
            chead = (cbind[3][0], cbind[3][1], cbind[3][2])
            dist = math.sqrt(
                (chead[0] - head[0]) ** 2 +
                (chead[1] - head[1]) ** 2 +
                (chead[2] - head[2]) ** 2
            )
            if dist < nearest_dist and dist > 1e-6:
                nearest_dist = dist
                nearest_child_head = chead

        if nearest_child_head is None:
            continue

        # Direction to nearest child (world space)
        child_dir = [
            nearest_child_head[0] - head[0],
            nearest_child_head[1] - head[1],
            nearest_child_head[2] - head[2],
        ]
        child_dir = _normalize(child_dir)
        if child_dir is None:
            continue

        # Transform child direction into the bone's LOCAL space.
        # D3DX row-vector convention: local = world @ inv(bind)
        #   local[j] = sum_i(world[i] * inv_bind[i][j])
        inv_bind = _invert_4x4(bind)
        if inv_bind is None:
            continue
        local_dir = [
            inv_bind[0][0] * child_dir[0] + inv_bind[1][0] * child_dir[1] + inv_bind[2][0] * child_dir[2],
            inv_bind[0][1] * child_dir[0] + inv_bind[1][1] * child_dir[1] + inv_bind[2][1] * child_dir[2],
            inv_bind[0][2] * child_dir[0] + inv_bind[1][2] * child_dir[1] + inv_bind[2][2] * child_dir[2],
        ]

        # Find which local axis (X/Y/Z) best aligns
        dots = [abs(local_dir[0]), abs(local_dir[1]), abs(local_dir[2])]
        best_axis = dots.index(max(dots))
        axis_votes[best_axis] += 1
        total += 1

    if total == 0:
        _log.warning("No bone→child pairs found for axis detection; defaulting to Y.")
        return "y"

    axis_names = {AXIS_X: "x", AXIS_Y: "y", AXIS_Z: "z"}
    best = max(axis_votes, key=axis_votes.get)
    result = axis_names[best]

    _log.info(
        "Bone axis detection: X=%d (%.0f%%), Y=%d (%.0f%%), Z=%d (%.0f%%) → '%s'",
        axis_votes[AXIS_X], 100.0 * axis_votes[AXIS_X] / total,
        axis_votes[AXIS_Y], 100.0 * axis_votes[AXIS_Y] / total,
        axis_votes[AXIS_Z], 100.0 * axis_votes[AXIS_Z] / total,
        result,
    )

    return result


def apply_axis_permutation(model, bone_axis):
    """Permute all data in the model so that `bone_axis` becomes Y.

    This is a consistent axis swap applied to all matrices, positions,
    normals, and triangle winding.  See module docstring for the math.

    Parameters
    ----------
    model : dict
        The JSON model dict (modified in place).
    bone_axis : str
        ``"x"``, ``"y"``, or ``"z"``.  If ``"y"``, no change is made.
    """
    if bone_axis == "y":
        return  # no permutation needed

    if bone_axis not in ("x", "z"):
        _log.warning("Unknown bone_axis %r; skipping permutation.", bone_axis)
        return

    _log.info("Applying axis permutation: %s → y (swapping %s and y)", bone_axis, bone_axis)

    # The permutation swaps the bone_axis with Y.
    # For "x": swap X and Y (indices 0 and 1)
    # For "z": swap Y and Z (indices 1 and 2)
    if bone_axis == "x":
        i, j = 0, 1  # swap X and Y
    else:
        i, j = 1, 2  # swap Y and Z

    # --- Permute bone local transforms ---
    for nd in model.get("nodes", []):
        lt = nd.get("local_transform")
        if lt:
            nd["local_transform"] = _permute_matrix_4x4(lt, i, j)

    # --- Permute mesh data ---
    for mesh in model.get("meshes", []):
        # Vertex positions and normals
        for v in mesh.get("vertices", []):
            p = v.get("p")
            if p:
                v["p"] = _permute_vec3(p, i, j)
            n = v.get("n")
            if n:
                v["n"] = _permute_vec3(n, i, j)

        # Inverse bind matrices
        mesh["inverse_bind_matrices"] = [
            _permute_matrix_4x4(m, i, j)
            for m in mesh.get("inverse_bind_matrices", [])
        ]

        # Toggle triangle winding (two reflections cancel: the C++ Y/Z
        # swap already flipped winding; our permutation is another
        # reflection, so we un-flip by swapping indices 1 and 2 again).
        indices = mesh.get("indices", [])
        for k in range(0, len(indices) - 2, 3):
            indices[k + 1], indices[k + 2] = indices[k + 2], indices[k + 1]

    # --- Permute baked animation matrices ---
    for anim in model.get("animations", []):
        for ch in anim.get("channels", []):
            for kf in ch.get("baked_keys", []):
                kf["m"] = _permute_matrix_4x4(kf["m"], i, j)


# ---------------------------------------------------------------------------
# Pure-Python matrix/vector helpers (no mathutils dependency — testable
# without Blender).
# ---------------------------------------------------------------------------

def _permute_vec3(v, i, j):
    """Swap components i and j of a 3-element vector."""
    result = list(v)
    result[i], result[j] = result[j], result[i]
    return result


def _permute_matrix_4x4(m, i, j):
    """Apply a similarity transform that swaps axes i and j.

    For a 4x4 matrix M (stored as list-of-lists, row-major),
    the permuted matrix is P @ M @ P^-1, where P is the permutation
    that swaps axes i and j.  This means: swap rows i,j AND swap
    columns i,j.
    """
    result = [list(row) for row in m]  # deep copy

    # Swap rows i and j
    result[i], result[j] = result[j][:], result[i][:]

    # Swap columns i and j
    for row in result:
        row[i], row[j] = row[j], row[i]

    return result


def _matmul_4x4(a, b):
    """Multiply two 4x4 matrices (list-of-lists, row-major)."""
    result = [[0.0] * 4 for _ in range(4)]
    for r in range(4):
        for c in range(4):
            s = 0.0
            for k in range(4):
                s += a[r][k] * b[k][c]
            result[r][c] = s
    return result


def _invert_4x4(m):
    """Invert a 4x4 matrix (list-of-lists, D3DX row-major).

    D3DX row-major layout: translation is in the LAST ROW (m[3][0..2]).
    For a transform [R|0; t|1] (row-vector convention), the inverse is
    [R^-1|0; -t*R^-1|1].

    Returns None if the matrix is singular.
    """
    # Extract the 3x3 rotation/scale part (top-left 3x3)
    r = [[m[r][c] for c in range(3)] for r in range(3)]
    # Extract translation from the LAST ROW (D3XX row-major convention)
    t = [m[3][0], m[3][1], m[3][2]]

    # Compute determinant of the 3x3
    det = (
        r[0][0] * (r[1][1] * r[2][2] - r[1][2] * r[2][1])
        - r[0][1] * (r[1][0] * r[2][2] - r[1][2] * r[2][0])
        + r[0][2] * (r[1][0] * r[2][1] - r[1][1] * r[2][0])
    )
    if abs(det) < 1e-12:
        return None

    inv_det = 1.0 / det

    # Cofactor matrix (inverse of the 3x3 rotation/scale part)
    inv_r = [[0.0] * 3 for _ in range(3)]
    inv_r[0][0] = (r[1][1] * r[2][2] - r[1][2] * r[2][1]) * inv_det
    inv_r[0][1] = (r[0][2] * r[2][1] - r[0][1] * r[2][2]) * inv_det
    inv_r[0][2] = (r[0][1] * r[1][2] - r[0][2] * r[1][1]) * inv_det
    inv_r[1][0] = (r[1][2] * r[2][0] - r[1][0] * r[2][2]) * inv_det
    inv_r[1][1] = (r[0][0] * r[2][2] - r[0][2] * r[2][0]) * inv_det
    inv_r[1][2] = (r[0][2] * r[1][0] - r[0][0] * r[1][2]) * inv_det
    inv_r[2][0] = (r[1][0] * r[2][1] - r[1][1] * r[2][0]) * inv_det
    inv_r[2][1] = (r[0][1] * r[2][0] - r[0][0] * r[2][1]) * inv_det
    inv_r[2][2] = (r[0][0] * r[1][1] - r[0][1] * r[1][0]) * inv_det

    # Compute -t * R^-1 (row-vector multiplication: result[j] = sum_i(t[i] * inv_r[i][j]))
    inv_t = [
        -(t[0] * inv_r[0][0] + t[1] * inv_r[1][0] + t[2] * inv_r[2][0]),
        -(t[0] * inv_r[0][1] + t[1] * inv_r[1][1] + t[2] * inv_r[2][1]),
        -(t[0] * inv_r[0][2] + t[1] * inv_r[1][2] + t[2] * inv_r[2][2]),
    ]

    # Build the inverse in D3DX row-major format (translation in last row)
    return [
        [inv_r[0][0], inv_r[0][1], inv_r[0][2], 0.0],
        [inv_r[1][0], inv_r[1][1], inv_r[1][2], 0.0],
        [inv_r[2][0], inv_r[2][1], inv_r[2][2], 0.0],
        [inv_t[0], inv_t[1], inv_t[2], 1.0],
    ]


def _normalize(v):
    l = math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2)
    if l < 1e-12:
        return None
    return [v[0] / l, v[1] / l, v[2] / l]
