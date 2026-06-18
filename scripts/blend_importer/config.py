"""blend_importer.config — Named constants for the importer.

Replaces the magic numbers that were sprinkled through the original
``blend_importer.py`` (0.05 bone tail, 0.01 root scale, 128 specular power,
60 FPS, 4 max influences, ...).  Centralising them here lets the CLI expose
overrides and lets the test suite assert against stable values.
"""

# ---------------------------------------------------------------------------
# Geometry defaults
# ---------------------------------------------------------------------------

# Default bone visual tail length in Blender units (matches original 0.05).
DEFAULT_BONE_TAIL_LENGTH = 0.05

# Default root-object scale (1.0 = no rescale; the original hardcoded 0.01
# for Higurashi Daybreak — now exposed as --root-scale).
DEFAULT_ROOT_SCALE = 1.0

# Default max bone influences per vertex (informational; capping happens
# C++-side in MeshExtractor).  Plumbed through here for documentation and
# for the importer's vertex-group weight loop.
DEFAULT_MAX_INFLUENCES = 4


# ---------------------------------------------------------------------------
# Material / D3DX constants
# ---------------------------------------------------------------------------

# D3DX specular-power max (MatD3D.Power range), mapped to BSDF roughness.
# The original blend_importer.py line 122 divided spec_power by 128.0;
# surfacing it here makes the D3DX provenance explicit.
D3DX_SPECULAR_POWER_MAX = 128.0


# ---------------------------------------------------------------------------
# Animation defaults
# ---------------------------------------------------------------------------

# Default F-curve decimation error tolerance (Blender's graph.decimate ratio
# is 0..1; this is the "remove ratio" — 0.0 = keep all, 1.0 = remove all).
DEFAULT_DECIMATE_RATIO = 0.0  # off by default; opt-in via --decimate

# F-curve decimate mode.  "RATIO" uses bpy.ops.graph.decimate (requires a
# valid graph editor context — fragile in headless mode); "ERROR" uses a
# manual Ramer-Douglas-Peucker pass with an absolute error bound on the
# fcurve's value axis (works headless).
DECIMATE_MODE = "RATIO"

# Tolerance used by the Python-side static-channel skip (defense in depth
# alongside the C++-side static-bone optimization).  1e-7 matches the C++
# value in animation_baker.cpp (k_staticTol).
STATIC_CHANNEL_TOLERANCE = 1e-7

# Fallback FPS when the JSON's meta block is missing or malformed.  60.0
# matches the original C++ bake rate.
FALLBACK_BAKE_FPS = 60.0


# ---------------------------------------------------------------------------
# Versioning
# ---------------------------------------------------------------------------

# X2Blend refactor version (must match the C++ meta block).
X2BLEND_VERSION = "2.0.0-refactor"
