"""tests/python/test_bone_axis.py — pytest tests for blend_importer.bone_axis.

Tests the bone axis detection and permutation logic without requiring
Blender.  The bone_axis module is pure-Python (no bpy dependency).

IMPORTANT: All matrices in these tests use D3DX row-major format (matching
the JSON output from the C++ side), where translation is in the LAST ROW:
  m[3][0] = tx, m[3][1] = ty, m[3][2] = tz
  m[0][3] = m[1][3] = m[2][3] = 0, m[3][3] = 1
"""

import os
import sys
import math

import pytest

# Make scripts/ importable as the blend_importer package root.
_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.normpath(os.path.join(_HERE, "..", "..", "scripts"))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from blend_importer import bone_axis


# ---------------------------------------------------------------------------
# Helper: build a minimal model dict for testing
# ---------------------------------------------------------------------------

def _identity_4x4():
    return [[1.0, 0.0, 0.0, 0.0],
            [0.0, 1.0, 0.0, 0.0],
            [0.0, 0.0, 1.0, 0.0],
            [0.0, 0.0, 0.0, 1.0]]


def _translation_matrix(x, y, z):
    """D3DX row-major: translation in the LAST ROW (m[3][0..2])."""
    m = _identity_4x4()
    m[3][0] = x
    m[3][1] = y
    m[3][2] = z
    return m


def _make_model_x_axis_bones():
    """Build a model where bones point along their local X axis (3ds Max Biped).

    Parent at origin, child translated along +X (the bone direction).
    The inverse bind matrix for the parent is the identity (bone at origin).
    The inverse bind matrix for the child is a translation along -X
    (inverse of the child's world position at (10,0,0)).
    """
    parent_ibm = _identity_4x4()  # inv bind = identity → bone at origin
    child_ibm = _translation_matrix(-10.0, 0.0, 0.0)  # inv bind → bone at (10,0,0)

    model = {
        "nodes": [
            {"name": "parent", "is_bone": True, "parent_index": -1,
             "local_transform": _identity_4x4(), "mesh_index": -1},
            {"name": "child", "is_bone": True, "parent_index": 0,
             "local_transform": _translation_matrix(10.0, 0.0, 0.0), "mesh_index": -1},
        ],
        "meshes": [
            {"name": "mesh", "has_skin": True,
             "bone_names": ["parent", "child"],
             "inverse_bind_matrices": [parent_ibm, child_ibm],
             "vertices": [{"p": [0, 0, 0], "n": [1, 0, 0], "uv": [0, 0],
                           "ji": [0, 0, 0, 0], "jw": [0, 0, 0, 0]}],
             "indices": [0, 1, 2]},
        ],
        "animations": [],
    }
    return model


def _make_model_y_axis_bones():
    """Build a model where bones point along Y (Maya convention)."""
    parent_ibm = _identity_4x4()
    child_ibm = _translation_matrix(0.0, -10.0, 0.0)

    model = {
        "nodes": [
            {"name": "parent", "is_bone": True, "parent_index": -1,
             "local_transform": _identity_4x4(), "mesh_index": -1},
            {"name": "child", "is_bone": True, "parent_index": 0,
             "local_transform": _translation_matrix(0.0, 10.0, 0.0), "mesh_index": -1},
        ],
        "meshes": [
            {"name": "mesh", "has_skin": True,
             "bone_names": ["parent", "child"],
             "inverse_bind_matrices": [parent_ibm, child_ibm],
             "vertices": [], "indices": []},
        ],
        "animations": [],
    }
    return model


# ---------------------------------------------------------------------------
# detect_bone_axis
# ---------------------------------------------------------------------------

def test_detect_x_axis_biped():
    """A model where bones point along X should detect 'x'."""
    model = _make_model_x_axis_bones()
    result = bone_axis.detect_bone_axis(model)
    assert result == "x"


def test_detect_y_axis_maya():
    """A model where bones point along Y should detect 'y'."""
    model = _make_model_y_axis_bones()
    result = bone_axis.detect_bone_axis(model)
    assert result == "y"


def test_detect_no_bones_defaults_to_y():
    """A model with no bones should default to 'y'."""
    model = {"nodes": [], "meshes": [], "animations": []}
    result = bone_axis.detect_bone_axis(model)
    assert result == "y"


# ---------------------------------------------------------------------------
# apply_axis_permutation
# ---------------------------------------------------------------------------

def test_permutation_y_is_noop():
    """Permuting with 'y' should be a no-op."""
    model = _make_model_x_axis_bones()
    original = {
        "nodes": [dict(n) for n in model["nodes"]],
        "meshes": [dict(m) for m in model["meshes"]],
    }
    bone_axis.apply_axis_permutation(model, "y")
    # Nothing should change
    assert model["nodes"][1]["local_transform"] == original["nodes"][1]["local_transform"]


def test_permutation_x_swaps_x_and_y_in_translation():
    """Permuting 'x' should swap X and Y coordinates of vertex positions."""
    model = _make_model_x_axis_bones()
    # Add a vertex at (1, 2, 3)
    model["meshes"][0]["vertices"] = [
        {"p": [1.0, 2.0, 3.0], "n": [0.0, 1.0, 0.0], "uv": [0, 0],
         "ji": [0, 0, 0, 0], "jw": [0, 0, 0, 0]}
    ]
    bone_axis.apply_axis_permutation(model, "x")
    # After X↔Y swap: position should be (2, 1, 3)
    assert model["meshes"][0]["vertices"][0]["p"] == [2.0, 1.0, 3.0]
    # Normal (0,1,0) → (1,0,0)
    assert model["meshes"][0]["vertices"][0]["n"] == [1.0, 0.0, 0.0]


def test_permutation_x_swaps_matrix_rows_and_cols():
    """Permuting 'x' should swap rows 0,1 and cols 0,1 of matrices.
    In D3DX row-major, translation is in the last ROW (m[3][0..2])."""
    model = _make_model_x_axis_bones()
    # The child's local transform has translation (10, 0, 0) — in m[3][0].
    # After X↔Y swap, m[3][1] should be 10.
    bone_axis.apply_axis_permutation(model, "x")
    child_lt = model["nodes"][1]["local_transform"]
    assert child_lt[3][1] == pytest.approx(10.0)  # was m[3][0], now m[3][1]
    assert child_lt[3][0] == pytest.approx(0.0)   # was m[3][1], now m[3][0]


def test_permutation_toggles_triangle_winding():
    """Permuting should swap indices [1] and [2] in each triangle."""
    model = _make_model_x_axis_bones()
    model["meshes"][0]["indices"] = [0, 1, 2, 3, 4, 5]
    bone_axis.apply_axis_permutation(model, "x")
    # Each triangle (a,b,c) becomes (a,c,b)
    assert model["meshes"][0]["indices"] == [0, 2, 1, 3, 5, 4]


def test_permutation_makes_x_bone_point_y():
    """After X→Y permutation, the child's local translation should be along Y.
    In D3DX row-major, translation is in m[3][0..2]."""
    model = _make_model_x_axis_bones()
    # Before: child local translation = (10, 0, 0) — along X, in m[3][0]
    assert model["nodes"][1]["local_transform"][3][0] == 10.0
    assert model["nodes"][1]["local_transform"][3][1] == 0.0

    bone_axis.apply_axis_permutation(model, "x")

    # After: child local translation = (0, 10, 0) — along Y, in m[3][1]
    child_lt = model["nodes"][1]["local_transform"]
    assert child_lt[3][0] == pytest.approx(0.0)
    assert child_lt[3][1] == pytest.approx(10.0)


def test_permutation_preserves_inverse_bind_translation():
    """After permutation, the inverse bind matrix should be consistently swapped.
    In D3DX row-major, translation is in m[3][0..2]."""
    model = _make_model_x_axis_bones()
    # Child IBM has translation (-10, 0, 0) — along X, in m[3][0]
    assert model["meshes"][0]["inverse_bind_matrices"][1][3][0] == -10.0

    bone_axis.apply_axis_permutation(model, "x")

    # After X↔Y swap: translation should be (0, -10, 0) — along Y, in m[3][1]
    ibm = model["meshes"][0]["inverse_bind_matrices"][1]
    assert ibm[3][0] == pytest.approx(0.0)
    assert ibm[3][1] == pytest.approx(-10.0)


def test_permutation_x_then_detect_y():
    """After X→Y permutation, detect_bone_axis should return 'y'."""
    model = _make_model_x_axis_bones()
    assert bone_axis.detect_bone_axis(model) == "x"

    bone_axis.apply_axis_permutation(model, "x")

    # After permutation, the bone direction (was X) is now Y
    assert bone_axis.detect_bone_axis(model) == "y"


# ---------------------------------------------------------------------------
# _permute_matrix_4x4 (internal helper)
# ---------------------------------------------------------------------------

def test_permute_matrix_identity_unchanged():
    """Permuting an identity matrix should give identity."""
    m = _identity_4x4()
    result = bone_axis._permute_matrix_4x4(m, 0, 1)  # swap X,Y
    assert result == _identity_4x4()


def test_permute_matrix_translation_swap():
    """A translation (tx, ty, tz) should become (ty, tx, tz) after X↔Y swap.
    D3DX row-major: translation is in m[3][0..2]."""
    m = _translation_matrix(1.0, 2.0, 3.0)
    result = bone_axis._permute_matrix_4x4(m, 0, 1)  # swap X,Y
    assert result[3][0] == pytest.approx(2.0)  # was ty
    assert result[3][1] == pytest.approx(1.0)  # was tx
    assert result[3][2] == pytest.approx(3.0)  # unchanged


def test_permute_matrix_90deg_rotation():
    """A 90° rotation around Z should map correctly under X↔Y swap.
    D3DX row-major: rotation rows are m[0..2][0..2]."""
    # Rotation 90° around Z (row-vector): X→Y, Y→-X
    # Matrix: [[0,1,0,0],[-1,0,0,0],[0,0,1,0],[0,0,0,1]]
    m = [[0.0, 1.0, 0.0, 0.0],
         [-1.0, 0.0, 0.0, 0.0],
         [0.0, 0.0, 1.0, 0.0],
         [0.0, 0.0, 0.0, 1.0]]
    result = bone_axis._permute_matrix_4x4(m, 0, 1)  # swap X,Y
    # After swap: [[0,-1,0,0],[1,0,0,0],[0,0,1,0],[0,0,0,1]] = rotation -90° around Z
    assert result[0][0] == pytest.approx(0.0)
    assert result[0][1] == pytest.approx(-1.0)
    assert result[1][0] == pytest.approx(1.0)
    assert result[1][1] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# _invert_4x4 (internal helper) — D3DX row-major format
# ---------------------------------------------------------------------------

def test_invert_identity():
    m = _identity_4x4()
    inv = bone_axis._invert_4x4(m)
    assert inv == _identity_4x4()


def test_invert_translation():
    """Inverting a translation (10,20,30) should give (-10,-20,-30).
    D3DX row-major: translation is in m[3][0..2]."""
    m = _translation_matrix(10.0, 20.0, 30.0)
    inv = bone_axis._invert_4x4(m)
    assert inv[3][0] == pytest.approx(-10.0)
    assert inv[3][1] == pytest.approx(-20.0)
    assert inv[3][2] == pytest.approx(-30.0)
    # The last column should be all zeros (D3DX convention)
    assert inv[0][3] == pytest.approx(0.0)
    assert inv[1][3] == pytest.approx(0.0)
    assert inv[2][3] == pytest.approx(0.0)
    assert inv[3][3] == pytest.approx(1.0)


def test_invert_singular_returns_none():
    m = [[0.0, 0.0, 0.0, 0.0],
         [0.0, 0.0, 0.0, 0.0],
         [0.0, 0.0, 0.0, 0.0],
         [0.0, 0.0, 0.0, 1.0]]
    inv = bone_axis._invert_4x4(m)
    assert inv is None


def test_invert_round_trip():
    """Inverting an inverse should give back the original."""
    m = _translation_matrix(5.0, -3.0, 7.0)
    inv = bone_axis._invert_4x4(m)
    inv_inv = bone_axis._invert_4x4(inv)
    for r in range(4):
        for c in range(4):
            assert inv_inv[r][c] == pytest.approx(m[r][c])
