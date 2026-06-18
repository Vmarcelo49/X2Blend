"""tests/python/test_math_utils.py — pytest tests for blend_importer.math_utils.

These tests exercise the pure-Python helpers in
``blend_importer.math_utils``:

  * ``mat4_to_mathutils``       — D3DX row-vector -> Blender column-vector.
  * ``pose_local_matrix``       — Textbook pose-local derivation.
  * ``vec_roll_to_mat3``        — Blender's bone-axis algorithm.
  * ``mat3_to_roll``            — Inverse of ``vec_roll_to_mat3`` (round-trip).

They run WITHOUT Blender installed.  The only third-party dependency is
``mathutils`` (``pip install mathutils``), which is skipped via
``pytest.importorskip`` if unavailable so the rest of the test-suite
still collects.
"""

import math

import pytest

mathutils = pytest.importorskip("mathutils")

# Make scripts/ importable as the blend_importer package root.
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.normpath(os.path.join(_HERE, "..", "..", "scripts"))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from blend_importer import math_utils  # noqa: E402


# ---------------------------------------------------------------------------
# mat4_to_mathutils
# ---------------------------------------------------------------------------

def test_mat4_to_mathutils_identity():
    rows = [
        [1, 0, 0, 0],
        [0, 1, 0, 0],
        [0, 0, 1, 0],
        [0, 0, 0, 1],
    ]
    m = math_utils.mat4_to_mathutils(rows)
    expected = mathutils.Matrix.Identity(4)
    assert m == expected


def test_mat4_to_mathutils_transposes_translation():
    # D3DX stores translation in the last ROW (row-vector convention).
    # mathutils.Matrix uses column vectors with translation in the last
    # COLUMN, so mat4_to_mathutils transposes.  Constructing from the
    # rows verbatim and then calling .transposed() yields a matrix whose
    # translation column == the input row.
    rows = [
        [1, 0, 0, 0],
        [0, 1, 0, 0],
        [0, 0, 1, 0],
        [10, 20, 30, 1],
    ]
    m = math_utils.mat4_to_mathutils(rows)
    # Translation column is the last column (index 3).
    assert m[0][3] == 10
    assert m[1][3] == 20
    assert m[2][3] == 30
    # And the row-vector translation (the last row of the input) is now
    # zero in the output's last row (except for the W component).
    assert m[3][0] == 0
    assert m[3][1] == 0
    assert m[3][2] == 0
    assert m[3][3] == 1


def test_mat4_to_mathutils_round_trip():
    # mat4_to_mathutils(M).transposed() == mathutils.Matrix(M_rows).
    rows = [
        [1, 2, 3, 4],
        [5, 6, 7, 8],
        [9, 10, 11, 12],
        [13, 14, 15, 16],
    ]
    m = math_utils.mat4_to_mathutils(rows)
    # The function returns Matrix(rows).transposed(); transposing it
    # back must recover Matrix(rows).
    assert m.transposed() == mathutils.Matrix(rows)


# ---------------------------------------------------------------------------
# pose_local_matrix
# ---------------------------------------------------------------------------

def test_pose_local_matrix_no_parent_identity():
    # If M_world == M_rest, the no-parent formula M_rest^-1 @ M_world
    # is the identity.
    M_rest = mathutils.Matrix.Translation((1.0, 2.0, 3.0))
    M_world = M_rest.copy()
    M_pose = math_utils.pose_local_matrix(M_world, M_rest)
    assert M_pose == mathutils.Matrix.Identity(4)


def test_pose_local_matrix_no_parent_translation():
    # Pure translation world matrix relative to a translated rest:
    # M_pose = M_rest^-1 @ M_world should equal the difference.
    M_rest = mathutils.Matrix.Translation((1.0, 0.0, 0.0))
    M_world = mathutils.Matrix.Translation((4.0, 0.0, 0.0))
    M_pose = math_utils.pose_local_matrix(M_world, M_rest)
    expected = mathutils.Matrix.Translation((3.0, 0.0, 0.0))
    assert M_pose == expected


def test_pose_local_matrix_with_parent_identity():
    # If M_world == M_rest and M_world_parent == M_rest_parent, the
    # parent formula reduces to the identity.
    M_rest = mathutils.Matrix.Translation((1.0, 0.0, 0.0))
    M_rest_parent = mathutils.Matrix.Translation((0.0, 1.0, 0.0))
    M_world = M_rest.copy()
    M_world_parent = M_rest_parent.copy()
    M_pose = math_utils.pose_local_matrix(
        M_world, M_rest, M_world_parent, M_rest_parent
    )
    assert M_pose == mathutils.Matrix.Identity(4)


def test_pose_local_matrix_with_parent_relative_translation():
    # M_world_parent = rest-parent translated by (5, 0, 0)
    # M_world = rest translated by (5, 0, 0)
    # Expect the pose-local to be identity (bone moved with parent).
    M_rest = mathutils.Matrix.Translation((1.0, 0.0, 0.0))
    M_rest_parent = mathutils.Matrix.Translation((0.0, 1.0, 0.0))
    M_world = M_rest @ mathutils.Matrix.Translation((5.0, 0.0, 0.0))
    M_world_parent = M_rest_parent @ mathutils.Matrix.Translation((5.0, 0.0, 0.0))
    M_pose = math_utils.pose_local_matrix(
        M_world, M_rest, M_world_parent, M_rest_parent
    )
    # The bone stayed at its rest offset relative to its parent.
    assert M_pose == mathutils.Matrix.Identity(4)


def test_pose_local_matrix_singular_falls_back_to_identity():
    # A singular M_rest (all zeros) should trigger the ValueError
    # fallback in pose_local_matrix, returning the 4x4 identity.
    M_rest = mathutils.Matrix(((0, 0, 0, 0),
                                (0, 0, 0, 0),
                                (0, 0, 0, 0),
                                (0, 0, 0, 0)))
    M_world = mathutils.Matrix.Identity(4)
    M_pose = math_utils.pose_local_matrix(M_world, M_rest)
    assert M_pose == mathutils.Matrix.Identity(4)


# ---------------------------------------------------------------------------
# vec_roll_to_mat3 / mat3_to_roll round-trip
# ---------------------------------------------------------------------------

def test_vec_roll_to_mat3_default_axis_is_y():
    # For nor = +Y and roll = 0, vec_roll_to_mat3 returns a rotation
    # matrix whose second column (the bone-axis column in Blender) is +Y.
    nor = mathutils.Vector((0.0, 1.0, 0.0))
    m = math_utils.vec_roll_to_mat3(nor, 0.0)
    assert pytest.approx(m[0][1], abs=1e-6) == 0.0
    assert pytest.approx(m[1][1], abs=1e-6) == 1.0
    assert pytest.approx(m[2][1], abs=1e-6) == 0.0


def test_mat3_to_roll_zero_for_axis_aligned_bone():
    # If the bone's local matrix is exactly what vec_roll_to_mat3(+Y, 0)
    # produces, then mat3_to_roll should return ~0.
    nor = mathutils.Vector((0.0, 1.0, 0.0))
    m = math_utils.vec_roll_to_mat3(nor, 0.0)
    roll = math_utils.mat3_to_roll(m)
    assert pytest.approx(roll, abs=1e-6) == 0.0


def test_mat3_to_roll_round_trip_with_nonzero_roll():
    # For an axis-aligned bone (nor = +Y) and a known roll, the
    # vec_roll_to_mat3 -> mat3_to_roll round-trip should recover the
    # roll to within ~1e-6.
    nor = mathutils.Vector((0.0, 1.0, 0.0))
    for roll in (0.0, 0.1, math.pi / 4, math.pi / 2, math.pi):
        m = math_utils.vec_roll_to_mat3(nor, roll)
        recovered = math_utils.mat3_to_roll(m)
        assert pytest.approx(recovered, abs=1e-6) == roll


def test_vec_roll_to_mat3_negative_axis_uses_scale_neg1():
    # When nor is antiparallel to the target (0, 1, 0), the cross
    # product is zero and the function falls back to Scale(-1, 3).  The
    # resulting matrix should map +Y to -Y.
    nor = mathutils.Vector((0.0, -1.0, 0.0))
    m = math_utils.vec_roll_to_mat3(nor, 0.0)
    # Second column == nor direction (because R(b_matrix) maps +Y to nor).
    assert pytest.approx(m[0][1], abs=1e-6) == 0.0
    assert pytest.approx(m[1][1], abs=1e-6) == -1.0
    assert pytest.approx(m[2][1], abs=1e-6) == 0.0


# ---------------------------------------------------------------------------
# compute_bone_tail_lengths
#
# Pure-Python (no mathutils needed for these tests — positions are tuples).
# Verifies the hierarchy-based tail-length algorithm that replaced the
# original fixed-0.05 hack.
# ---------------------------------------------------------------------------

def test_tail_length_single_root_bone_uses_fallback():
    """A single bone with no parent and no children falls back to the
    provided fallback_length (was hardcoded 0.05 in the original)."""
    bone_heads = {0: (0.0, 0.0, 0.0)}
    bone_parent = {0: None}
    bone_children = {0: []}
    tl = math_utils.compute_bone_tail_lengths(bone_heads, bone_parent,
                                              bone_children, fallback_length=0.05)
    assert tl[0] == pytest.approx(0.05)


def test_tail_length_parent_with_child_uses_child_distance():
    """A bone with one child gets 80% of the distance to that child."""
    bone_heads = {0: (0.0, 0.0, 0.0), 1: (0.0, 10.0, 0.0)}
    bone_parent = {0: None, 1: 0}
    bone_children = {0: [1], 1: []}
    tl = math_utils.compute_bone_tail_lengths(bone_heads, bone_parent,
                                              bone_children, fallback_length=0.05)
    # Distance = 10.0, tail = 10.0 * 0.8 = 8.0
    assert tl[0] == pytest.approx(8.0)


def test_tail_length_leaf_bone_inherits_from_parent():
    """A leaf bone (no children) gets 50% of its parent's tail length."""
    bone_heads = {0: (0.0, 0.0, 0.0), 1: (0.0, 10.0, 0.0)}
    bone_parent = {0: None, 1: 0}
    bone_children = {0: [1], 1: []}
    tl = math_utils.compute_bone_tail_lengths(bone_heads, bone_parent,
                                              bone_children, fallback_length=0.05)
    # Parent tail = 8.0, leaf tail = 8.0 * 0.5 = 4.0
    assert tl[1] == pytest.approx(4.0)


def test_tail_length_uses_nearest_child_when_multiple():
    """When a bone has multiple children, the nearest one's distance is used."""
    bone_heads = {
        0: (0.0, 0.0, 0.0),
        1: (0.0, 5.0, 0.0),   # near child
        2: (0.0, 20.0, 0.0),  # far child
    }
    bone_parent = {0: None, 1: 0, 2: 0}
    bone_children = {0: [1, 2], 1: [], 2: []}
    tl = math_utils.compute_bone_tail_lengths(bone_heads, bone_parent,
                                              bone_children, fallback_length=0.05)
    # Nearest child at distance 5.0, tail = 5.0 * 0.8 = 4.0
    assert tl[0] == pytest.approx(4.0)


def test_tail_length_deep_chain_proportional():
    """A 3-bone chain: root → mid → leaf.  Each bone's tail should be
    proportional to the actual bone spacing, not a fixed 0.05."""
    bone_heads = {
        0: (0.0, 0.0, 0.0),   # root
        1: (0.0, 3.0, 0.0),   # mid (3 units from root)
        2: (0.0, 7.0, 0.0),   # leaf (4 units from mid)
    }
    bone_parent = {0: None, 1: 0, 2: 1}
    bone_children = {0: [1], 1: [2], 2: []}
    tl = math_utils.compute_bone_tail_lengths(bone_heads, bone_parent,
                                              bone_children, fallback_length=0.05)
    # Root: nearest child at dist 3.0, tail = 3.0 * 0.8 = 2.4
    assert tl[0] == pytest.approx(2.4)
    # Mid: nearest child at dist 4.0, tail = 4.0 * 0.8 = 3.2
    assert tl[1] == pytest.approx(3.2)
    # Leaf: inherits parent's tail * 0.5 = 3.2 * 0.5 = 1.6
    assert tl[2] == pytest.approx(1.6)
    # All tails are much larger than the old fixed 0.05 — bones are visible.
    assert all(v > 0.05 for v in tl.values())


def test_tail_length_large_skeleton_not_tiny():
    """The bug report: bones looked like spheres because 0.05 was tiny
    relative to the skeleton.  With hierarchy-based computation, a
    skeleton with 50-unit bone spacing gets ~40-unit tails."""
    bone_heads = {
        0: (0.0, 0.0, 0.0),
        1: (0.0, 50.0, 0.0),
        2: (0.0, 100.0, 0.0),
    }
    bone_parent = {0: None, 1: 0, 2: 1}
    bone_children = {0: [1], 1: [2], 2: []}
    tl = math_utils.compute_bone_tail_lengths(bone_heads, bone_parent,
                                              bone_children, fallback_length=0.05)
    # Root tail = 50.0 * 0.8 = 40.0 (was 0.05 — 800x larger, now visible)
    assert tl[0] == pytest.approx(40.0)
    assert tl[0] > 1.0  # sanity: not a sphere anymore


def test_tail_length_coincident_bones_uses_fallback():
    """When all bones are at the same position (degenerate), distances
    are zero and the fallback is used.  The parent gets the fallback
    directly; the leaf inherits 50% of the parent's (fallback-derived)
    length, which is still non-zero so Blender accepts the bone."""
    bone_heads = {0: (1.0, 2.0, 3.0), 1: (1.0, 2.0, 3.0)}
    bone_parent = {0: None, 1: 0}
    bone_children = {0: [1], 1: []}
    tl = math_utils.compute_bone_tail_lengths(bone_heads, bone_parent,
                                              bone_children, fallback_length=0.05)
    # Parent: no valid child distance, avg_dist is 0 → fallback 0.05.
    assert tl[0] == pytest.approx(0.05)
    # Leaf: inherits parent's tail * 0.5 = 0.05 * 0.5 = 0.025.
    # Still non-zero, so Blender accepts the bone.
    assert tl[1] == pytest.approx(0.025)
    assert tl[1] > 0.0  # non-zero is what matters for Blender
