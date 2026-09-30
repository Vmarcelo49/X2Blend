# X2Blend

High-precision converter for DirectX 9 `.x` models into Blender `.blend`
files, built for legacy game assets such as *Higurashi Daybreak kai*
(2008). It converts the full frame hierarchy — meshes, materials,
skinning, and animation — into a `.blend` with an armature, skinned
meshes, and frame-accurate F-curves.

## How it works

The pipeline is two stages. Stage 1 must run on Windows (via Wine)
because it depends on the DirectX 9 / D3DX libraries; Stage 2 runs
inside Blender:

```
DirectX .x model
       |
       v   (Wine)
  x2blend.exe              <- Stage 1: C++ / D3DX loader + animation baker
       |
       v
   model.json              <- intermediate (compact JSON + meta block)
       |
       v   (bpy / blender --background)
  blend_importer           <- Stage 2: Blender Python importer
       |
       v
   output.blend
```

**Stage 1 — `x2blend.exe` (C++).** Creates a headless D3D9 device,
loads the `.x` file with `D3DXLoadMeshHierarchyFromX`, flattens the
frame hierarchy, and extracts meshes, materials, and skin weights.
The animation is baked by advancing the D3DX animation controller at a
configurable sample rate (default 60 FPS) and recording each bone's
world matrix. Everything is serialized to `model.json`, with a `meta`
block carrying the pipeline configuration (bake mode, bake FPS, source
ticks-per-second, max influences, exporter version).

**Stage 2 — `blend_importer` (Python, runs inside Blender).** Reads
the JSON, builds the armature from the inverse-bind matrices, creates
mesh objects with vertex groups and an armature modifier, and writes
F-curves by deriving each bone's local pose from the baked world
matrices:

```
M_local = M_rest⁻¹ · M_rest_parent · M_world_parent⁻¹ · M_world
```

This derivation is what keeps the animation exact: the importer
reconstructs the pose the D3DX controller would have produced, instead
of re-interpolating sampled poses.

A standalone `viewer.exe` provides an interactive DX9 preview of the
source model (see [Usage](#4-view-a-model)).

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the module
dependency graph and pipeline details.

### Bone axis correction

`.X` files from different authoring tools use different local axes as
the bone direction: 3ds Max Biped (the source of most Japanese game
assets, including Higurashi Daybreak) uses **X**, Maya uses **Y**
(Blender's convention), and some custom rigs use **Z**. Blender
requires the bone's local Y axis to point head→tail.

By default (`--bone-axis auto`) the importer detects which axis the
file uses by checking, for each bone with children, which local axis
best aligns with the direction to its nearest child, and applies a
single consistent axis permutation to **all** data (bone transforms,
inverse bind matrices, baked matrices, vertex positions, normals,
triangle winding). Because the permutation is uniform, skinning stays
exact while the bones end up pointing the right way — this solves the
"bones point backwards" problem without breaking deformation. Run with
`--log-level DEBUG` to see the detection diagnostics.

> **Note on bone tails:** the `.X` format stores no bone lengths —
> only frame transforms. Tail lengths are therefore reconstructed from
> the hierarchy (80% of the distance to the nearest child; 50% of the
> parent's tail for leaves). The default tail direction is
> skinning-accurate, which can make bones look odd; `--visual-tails`
> points tails toward children for readability but **breaks skinning**
> — use it only for hierarchy inspection.

## Features

- **Two-stage pipeline** (C++/Wine → JSON → bpy) that isolates the
  Windows-only D3DX dependency from Blender.
- **Frame-accurate animation** via world-matrix baking plus exact
  pose-local derivation on the Python side.
- **Automatic bone axis correction** (`--bone-axis auto`, default).
- **`--no-bake` sparse key-time baking** — evaluates at the original
  keyframe times (typically 20–50) with the same exact math as the
  dense path, producing smaller, sparser, editable actions.
- **F-curve decimation** (`--decimate-mode error --decimate 1e-4`
  recommended for headless runs) to cut bake size within a tolerance.
- **Static-bone optimization** — channels whose baked matrices are
  constant collapse to a single keyframe (50–80% smaller bakes).
- **Idempotent `.x` template injection** — the preprocessor is safe to
  run repeatedly on the same file.
- **Shift-JIS (CP932) texture-path decoding** preserved end-to-end.
- **Texture auto-resolution and packing** — textures placed alongside
  the `.x` file are found automatically and packed into the `.blend`.
- **Structured logging** with four levels on both sides.
- **Standalone DX9 viewer** (`viewer.exe`) for interactive preview.
- **Validation scripts** that numerically verify bone matrices and
  animation poses against the source `.x` file.
- **Unit tests** for the pure-math and serialization pieces, runnable
  without Wine or Blender.

## Requirements

- **Linux host** with:
  - `x86_64-w64-mingw32-g++` (MinGW cross-compiler, 14.2.0 or newer)
  - `wine` (9.0 or newer) — to run the Windows executables
  - `blender` (5.1 or newer) **or** `pip install bpy`
  - `cmake` (≥ 3.15) and `make`
  - Optional: Python 3.10+ with `pytest` + `mathutils` for the Python
    unit tests (none of the unit tests need Blender)

## Build

```bash
./build.sh
```

Produces `build/x2blend.exe` and `build/viewer.exe`. The executables
are statically linked (`-static-libgcc -static-libstdc++ -static`) so
they are self-contained under Wine — no MinGW runtime DLLs are needed
in the Wine prefix.

To also build and run the C++ unit tests:

```bash
BUILD_TESTS=1 ./build.sh
```

## Usage

### 1. Convert a model (full pipeline)

```bash
./x2blend.sh input.x output.blend
```

The script runs Stage 1 under Wine, then Stage 2 via whichever Python
bpy runtime it finds (a `.venv/bin/python` with bpy, then system
`python3` with bpy, then system `blender --background`).

Everything before `--` goes to Stage 1 (`x2blend.exe`); everything
after `--` goes to Stage 2 (the Python importer). If you omit `--`,
all extra flags go to Stage 1.

#### Higurashi Daybreak assets

The original Higurashi models ship at a scale of 0.01 relative to
Blender units. Pass it explicitly:

```bash
./x2blend.sh higurashi_model.x out.blend -- --root-scale 0.01
```

#### Recommended headless invocation

```bash
./x2blend.sh input.x output.blend \
    --bake-fps 60 \
    -- --root-scale 0.01 \
       --decimate-mode error --decimate 1e-4 \
       --log-level INFO
```

### 2. CLI flags

#### Stage 1 — `x2blend.exe`

| Flag | Default | Description |
|---|---|---|
| `--no-bake` | (off) | Sparse key-time baking: sample at the original keyframe times instead of a fixed 60 FPS grid. Falls back to dense baking (with a warning) if the animation set is not keyframed. |
| `--bake-fps N` | `60` | Bake sample rate in Hz. Lower for 30 FPS sources, higher for 120 FPS. |
| `--max-influences N` | `4` | Bone-influence cap per vertex, in [1, 8]. |
| `--log-level <level>` | `info` | One of `debug`, `info`, `warn`, `error`. |

#### Stage 2 — `blend_importer`

| Flag | Default | Description |
|---|---|---|
| `--root-scale N` | `1.0` | Scale applied to every root object. Pass `0.01` for Higurashi assets. |
| `--bone-tail-length N` | `0.05` | Fallback tail length, used only when the hierarchy can't determine one. Tails are normally computed from the bone hierarchy. |
| `--max-influences N` | `4` | Informational; actual capping happens in Stage 1. |
| `--decimate N` | (off) | Decimation ratio (`--decimate-mode ratio`) or absolute error tolerance (`--decimate-mode error`). |
| `--decimate-mode ratio\|error` | `ratio` | `ratio` uses `bpy.ops.graph.decimate` (requires a graph editor context — interactive sessions only); `error` uses a manual Ramer-Douglas-Peucker pass, **headless-safe, recommended for `blender --background`**. |
| `--no-decimate` | (off) | Disable decimation; overrides `--decimate`. |
| `--no-flip-uv` | (off) | Disable UV V-flip (on by default — DirectX has V=0 at top, Blender at bottom). |
| `--emissive-strength N` | `0.0` | Emission strength for materials. Old anime games bake lighting into high emissive values; `1.0` reproduces the game's bright look in Material Preview. |
| `--visual-tails` | (off) | Point bone tails toward children (visually correct, **breaks skinning**). Debugging only. |
| `--bone-axis <auto\|x\|y\|z>` | `auto` | Which local axis the `.X` file uses for bone direction. `auto` detects from the hierarchy. |
| `--log-level <level>` | `INFO` | One of `DEBUG`, `INFO`, `WARN`, `ERROR`. `DEBUG` shows bone axis-alignment diagnostics. |

### 3. Manual stages

The two stages can be run separately:

```bash
# Stage 1: .x -> JSON
wine build/x2blend.exe model.x model.json --bake-fps 60

# Stage 2: JSON -> .blend (venv bpy)
.venv/bin/python -m blend_importer model.json output.blend \
    --root-scale 0.01 --decimate-mode error --decimate 1e-4

# ... or with system Blender
blender --background --python scripts/blend_importer/main.py -- \
    model.json output.blend --root-scale 0.01
```

### 4. View a model

```bash
wine build/viewer.exe model.x
```

| Control | Action |
|---|---|
| Left drag | Orbit |
| Mouse wheel | Zoom |
| Space / ← / → | Cycle animations |
| `R` | Reset camera |

### 5. Textures

The `.x` file references textures by filename (Shift-JIS names are
decoded to UTF-8 by Stage 1). The importer resolves paths in order:

1. Absolute path, if the file exists.
2. Relative to the `.x` source directory (the common layout — textures
   sit alongside the `.x` file, matching how D3DX resolves them).
3. Current working directory (fallback).

Blender loads BMP, PNG, JPEG, TIFF, TGA, and DDS natively; the
Higurashi assets use BMP and work out of the box. Every loaded texture
is **packed into the `.blend`**, making the file self-contained.
Missing textures produce a labeled placeholder node and a WARNING log
line saying where the importer looked.

### 6. Validation

The verification scripts numerically compare the matrices in a
`.blend` against D3DX-computed reference matrices from the source
`.x`:

```bash
# Bone rest-pose accuracy (< 1e-4 units, < 0.5°)
blender --background --python scripts/verify/verify_bones.py -- \
    model.json output.blend

# Animation pose accuracy (< 1e-3 units, < 0.1°, first 3 anims × 10 frames)
blender --background --python scripts/verify/verify_animation_poses.py -- \
    model.json output.blend
```

## Project layout

```
├── CMakeLists.txt
├── build.sh                 MinGW cross-compile driver
├── x2blend.sh               two-stage orchestrator
├── docs/
│   ├── ARCHITECTURE.md      pipeline + module dependency graph
│   └── X_FORMAT_RESEARCH.md .X format research notes
├── src/
│   ├── main.cpp             Stage 1 CLI entry
│   ├── core/                data model, math, coordinate conversion, codec, log
│   ├── io/                  .x preprocessor, JSON exporter
│   ├── d3d/                 D3D9 context, hierarchy allocator
│   ├── loader/              loader, hierarchy builder, mesh extractor, animation baker
│   └── viewer/              standalone DX9 viewer
├── scripts/
│   ├── blend_importer/      Python package (Stage 2)
│   └── verify/              bone + animation-pose verification scripts
└── tests/
    ├── cpp/                 C++ unit tests
    ├── python/              pytest tests (no Blender required)
    ├── fixtures/            synthetic .x fixtures
    └── README.md            how to run the tests
```

## Tests

See [`tests/README.md`](tests/README.md) for full instructions.

```bash
# C++ unit tests (MinGW cross-compile)
BUILD_TESTS=1 ./build.sh

# C++ unit tests, natively (non-D3D tests only)
mkdir build && cd build && cmake .. -DBUILD_TESTS=ON && make && ctest

# Python unit tests (pytest + mathutils, no Blender)
pip install pytest mathutils
pytest tests/python/
```

The unit tests cover the pure-math and serialization pieces (quaternion
and matrix ops, D3DX→Blender coordinate conversion, JSON `meta` block,
pose-local derivation, RDP decimation, static-channel detection). None
require Wine or Blender; only the integration-level verification
scripts do.

## Notes

- Tested with Wine 9.0, Blender 5.1, and `x86_64-w64-mingw32-g++` 14.2.0.
- Generated `.blend` files include NLA tracks for easy animation
  switching.
- `docs/X_FORMAT_RESEARCH.md` documents the `.X` format details and the
  pose-derivation math in depth.
