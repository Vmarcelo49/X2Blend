"""tests/python/test_animation.py — pytest tests for blend_importer.animation.

These tests exercise the pure-Python pieces of ``blend_importer.animation``
that do NOT require a running Blender instance:

  * ``_rdp``                      — Ramer-Douglas-Peucker decimation.
  * ``_perp_distance``            — Perpendicular distance to a line.
  * ``_is_static_channel``        — Static-channel detection (needs mathutils).
  * ``_matrices_equal``           — Per-element matrix equality.
  * ``_write_trs_channel``        — TRS keyframe writing + quaternion reorder.

The unit tests need neither Blender nor ``bpy``: the test module installs
a tiny ``bpy`` stub into ``sys.modules`` before importing
``blend_importer.animation`` so the module-top ``import bpy`` succeeds,
then exercises the helpers directly.  ``mathutils`` must be installed
(``pip install mathutils``) because the helpers under test call
``mathutils.Matrix``.
"""

import math
import os
import sys
import types

import pytest

mathutils = pytest.importorskip("mathutils")

# Make scripts/ importable as the blend_importer package root.
_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.normpath(os.path.join(_HERE, "..", "..", "scripts"))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)


# ---------------------------------------------------------------------------
# bpy stub — minimal, just enough for `import bpy` at animation.py top to
# succeed.  The functions under test (_rdp, _perp_distance, _is_static_channel,
# _matrices_equal, _write_trs_channel) do not actually use bpy, so the stub
# can be empty.  _write_trs_channel uses the caller-supplied
# `get_or_create_fcurves` callable and never touches bpy directly.
# ---------------------------------------------------------------------------
if "bpy" not in sys.modules:
    bpy_stub = types.ModuleType("bpy")
    sys.modules["bpy"] = bpy_stub

from blend_importer import animation  # noqa: E402
from blend_importer import config     # noqa: E402


# ---------------------------------------------------------------------------
# _rdp (Ramer-Douglas-Peucker)
# ---------------------------------------------------------------------------

def test_rdp_short_input_returned_as_is():
    # Lists with fewer than 3 elements are returned as-is.
    assert animation._rdp([], 1.0) == []
    assert animation._rdp([(0.0, 0.0)], 1.0) == [(0.0, 0.0)]
    assert animation._rdp([(0.0, 0.0), (1.0, 1.0)], 1.0) == [(0.0, 0.0), (1.0, 1.0)]


def test_rdp_collinear_collapses_to_endpoints():
    points = [(0.0, 0.0), (1.0, 0.0), (2.0, 0.0), (3.0, 0.0)]
    kept = animation._rdp(points, epsilon=1e-3)
    assert kept == [(0.0, 0.0), (3.0, 0.0)]


def test_rdp_preserves_outlier():
    # A spike at index 2 must be retained.  The collinear interior
    # points (1, 0) and (2, 0) on the original (0,0)-(3,0) line may or
    # may not be kept depending on whether RDP recurses onto a sub-line
    # that no longer passes through them; what's guaranteed is that
    # (1, 5) is kept and the endpoints are preserved.
    points = [(0.0, 0.0), (1.0, 0.0), (1.0, 5.0), (2.0, 0.0), (3.0, 0.0)]
    kept = animation._rdp(points, epsilon=1e-3)
    # Endpoints always preserved.
    assert kept[0] == (0.0, 0.0)
    assert kept[-1] == (3.0, 0.0)
    # Spike preserved.
    assert (1.0, 5.0) in kept


def test_rdp_removes_collinear_interior_point():
    # All points on a single horizontal line: RDP must collapse to
    # just the two endpoints.
    points = [(0.0, 0.0), (1.0, 0.0), (2.0, 0.0), (3.0, 0.0), (4.0, 0.0)]
    kept = animation._rdp(points, epsilon=1e-3)
    assert kept == [(0.0, 0.0), (4.0, 0.0)]


def test_rdp_endpoints_always_preserved():
    import random
    random.seed(42)
    points = [(float(i), random.random()) for i in range(50)]
    kept = animation._rdp(points, epsilon=0.01)
    assert kept[0] == points[0]
    assert kept[-1] == points[-1]


def test_rdp_kept_is_strict_subsequence():
    # RDP must preserve the input ordering; kept must be a subsequence of
    # the input (each kept element must appear in the input in the same
    # order).
    points = [(float(i), math.sin(i * 0.5)) for i in range(30)]
    kept = animation._rdp(points, epsilon=0.05)

    # Walk through `points` and consume `kept` in order.
    kept_idx = 0
    for p in points:
        if kept_idx < len(kept) and p == kept[kept_idx]:
            kept_idx += 1
    assert kept_idx == len(kept)


# ---------------------------------------------------------------------------
# _perp_distance
# ---------------------------------------------------------------------------

def test_perp_distance_horizontal_line():
    # Line from (0, 0) to (10, 0).  Point at (5, 3) -> perp dist = 3.
    d = animation._perp_distance((5.0, 3.0), (0.0, 0.0), (10.0, 0.0))
    assert pytest.approx(d, abs=1e-6) == 3.0


def test_perp_distance_vertical_line():
    # Line from (0, 0) to (0, 10).  Point at (4, 5) -> perp dist = 4.
    d = animation._perp_distance((4.0, 5.0), (0.0, 0.0), (0.0, 10.0))
    assert pytest.approx(d, abs=1e-6) == 4.0


def test_perp_distance_degenerate_returns_euclidean():
    # When a == b, the function returns the Euclidean distance to a.
    d = animation._perp_distance((3.0, 4.0), (0.0, 0.0), (0.0, 0.0))
    assert pytest.approx(d, abs=1e-6) == 5.0  # 3-4-5 triangle


def test_perp_distance_point_on_line_is_zero():
    d = animation._perp_distance((5.0, 0.0), (0.0, 0.0), (10.0, 0.0))
    assert pytest.approx(d, abs=1e-6) == 0.0


# ---------------------------------------------------------------------------
# _matrices_equal
# ---------------------------------------------------------------------------

def test_matrices_equal_identical():
    a = mathutils.Matrix.Identity(4)
    b = mathutils.Matrix.Identity(4)
    assert animation._matrices_equal(a, b, 1e-7) is True


def test_matrices_equal_within_tolerance():
    a = mathutils.Matrix.Identity(4)
    b = mathutils.Matrix.Identity(4)
    b[0][0] = 1.0 + 1e-9
    assert animation._matrices_equal(a, b, 1e-7) is True


def test_matrices_equal_outside_tolerance():
    a = mathutils.Matrix.Identity(4)
    b = mathutils.Matrix.Identity(4)
    b[0][0] = 1.0 + 1e-3
    assert animation._matrices_equal(a, b, 1e-7) is False


# ---------------------------------------------------------------------------
# _is_static_channel
# ---------------------------------------------------------------------------

def _identity_baked_keys(n):
    """Build n baked_keys whose m is a 4x4 identity (as nested lists)."""
    ident = [[1.0, 0.0, 0.0, 0.0],
             [0.0, 1.0, 0.0, 0.0],
             [0.0, 0.0, 1.0, 0.0],
             [0.0, 0.0, 0.0, 1.0]]
    return [{"t": float(i) / 60.0, "m": [row[:] for row in ident]} for i in range(n)]


def test_is_static_channel_identical_keys():
    baked = _identity_baked_keys(5)
    assert animation._is_static_channel(baked) is True


def test_is_static_channel_differing_keys():
    baked = _identity_baked_keys(5)
    # Perturb the third key's translation X by a large amount.
    baked[2]["m"][3][0] = 10.0
    assert animation._is_static_channel(baked) is False


def test_is_static_channel_single_key_returns_false():
    # Single-key channels have nothing to skip; the helper returns False
    # so the writer always emits the one key.
    baked = _identity_baked_keys(1)
    assert animation._is_static_channel(baked) is False


def test_is_static_channel_empty_returns_false():
    assert animation._is_static_channel([]) is False


def test_is_static_channel_uses_config_tolerance():
    # A perturbation just above STATIC_CHANNEL_TOLERANCE (1e-7) must
    # be detected as non-static.
    baked = _identity_baked_keys(3)
    perturb = config.STATIC_CHANNEL_TOLERANCE * 10  # well above tol
    baked[1]["m"][0][0] = 1.0 + perturb
    assert animation._is_static_channel(baked) is False


# ---------------------------------------------------------------------------
# _write_trs_channel — quaternion component reorder
# ---------------------------------------------------------------------------
# C++ XQuaternion is {x, y, z, w}; io/json_exporter.cpp emits it as
# [x, y, z, w] in the JSON.  Blender's rotation_quaternion F-curve
# indices are 0=w, 1=x, 2=y, 3=z.  _write_trs_channel must remap:
#   F-curve index 0  <-  w  (JSON index 3)
#   F-curve index 1  <-  x  (JSON index 0)
#   F-curve index 2  <-  y  (JSON index 1)
#   F-curve index 3  <-  z  (JSON index 2)

class _FakeKeyframePoints:
    def __init__(self):
        self.inserts = []  # list of (frame, value)

    def insert(self, frame, value):
        self.inserts.append((frame, value))


class _FakeFCurve:
    def __init__(self):
        self.keyframe_points = _FakeKeyframePoints()


def _fake_fcurves():
    return {
        "location": [_FakeFCurve() for _ in range(3)],
        "rotation_quaternion": [_FakeFCurve() for _ in range(4)],
        "scale": [_FakeFCurve() for _ in range(3)],
    }


def test_write_trs_channel_identity_quaternion_reorder():
    # Identity quaternion: JSON v = [0, 0, 0, 1] (x, y, z, w).
    r_keys = [{"t": 0.0, "v": [0.0, 0.0, 0.0, 1.0]}]
    fcs = _fake_fcurves()
    animation._write_trs_channel("Bone", [], r_keys, [], 60.0,
                                 lambda bname: fcs)

    rot = fcs["rotation_quaternion"]
    # F-curve 0 (w) should have (0, 1.0); F-curves 1/2/3 (x, y, z) should
    # have (0, 0.0).
    assert rot[0].keyframe_points.inserts == [(0, 1.0)]
    assert rot[1].keyframe_points.inserts == [(0, 0.0)]
    assert rot[2].keyframe_points.inserts == [(0, 0.0)]
    assert rot[3].keyframe_points.inserts == [(0, 0.0)]


def test_write_trs_channel_90y_quaternion_reorder():
    # 90-degree rotation around Y: q = (x=0, y=sqrt(2)/2, z=0, w=sqrt(2)/2).
    # JSON v = [0, sqrt(2)/2, 0, sqrt(2)/2].
    s = math.sqrt(2.0) / 2.0
    r_keys = [{"t": 0.0, "v": [0.0, s, 0.0, s]}]
    fcs = _fake_fcurves()
    animation._write_trs_channel("Bone", [], r_keys, [], 60.0,
                                 lambda bname: fcs)

    rot = fcs["rotation_quaternion"]
    # F-curve 0 (w) <- s, 1 (x) <- 0, 2 (y) <- s, 3 (z) <- 0.
    assert rot[0].keyframe_points.inserts == [(0, pytest.approx(s, abs=1e-6))]
    assert rot[1].keyframe_points.inserts == [(0, 0.0)]
    assert rot[2].keyframe_points.inserts == [(0, pytest.approx(s, abs=1e-6))]
    assert rot[3].keyframe_points.inserts == [(0, 0.0)]


def test_write_trs_channel_time_to_frame_conversion():
    # Time t is converted to frame via int(round(t * bake_fps)).
    r_keys = [
        {"t": 0.0,    "v": [0.0, 0.0, 0.0, 1.0]},
        {"t": 1.0/60.0, "v": [0.0, 0.0, 0.0, 1.0]},
        {"t": 2.0/60.0, "v": [0.0, 0.0, 0.0, 1.0]},
    ]
    fcs = _fake_fcurves()
    animation._write_trs_channel("Bone", [], r_keys, [], 60.0,
                                 lambda bname: fcs)

    frames = [f for (f, _v) in fcs["rotation_quaternion"][0].keyframe_points.inserts]
    assert frames == [0, 1, 2]


def test_write_trs_channel_translation_keys_written():
    t_keys = [
        {"t": 0.0, "v": [1.0, 2.0, 3.0]},
        {"t": 1.0/60.0, "v": [4.0, 5.0, 6.0]},
    ]
    fcs = _fake_fcurves()
    animation._write_trs_channel("Bone", t_keys, [], [], 60.0,
                                 lambda bname: fcs)

    loc = fcs["location"]
    assert loc[0].keyframe_points.inserts == [(0, 1.0), (1, 4.0)]
    assert loc[1].keyframe_points.inserts == [(0, 2.0), (1, 5.0)]
    assert loc[2].keyframe_points.inserts == [(0, 3.0), (1, 6.0)]


def test_write_trs_channel_scale_keys_written():
    s_keys = [
        {"t": 0.0, "v": [1.0, 1.0, 1.0]},
        {"t": 1.0/60.0, "v": [2.0, 0.5, 1.0]},
    ]
    fcs = _fake_fcurves()
    animation._write_trs_channel("Bone", [], [], s_keys, 60.0,
                                 lambda bname: fcs)

    scl = fcs["scale"]
    assert scl[0].keyframe_points.inserts == [(0, 1.0), (1, 2.0)]
    assert scl[1].keyframe_points.inserts == [(0, 1.0), (1, 0.5)]
    assert scl[2].keyframe_points.inserts == [(0, 1.0), (1, 1.0)]
