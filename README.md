# X2Blend

High-precision converter for DirectX 9 `.x` models into Blender `.blend`
files, optimized for legacy game assets such as *Higurashi Daybreak kai*
(2008).

This is the **refactored** tree.  It preserves the original project's
two-stage architecture and mathematical behavior end-to-end, while
reorganizing the source into focused modules, removing dead code,
parameterizing hardcoded magic numbers, and adding unit tests.  See
[`docs/ANALYSIS.md`](docs/ANALYSIS.md) for the full analysis and
refactoring plan, and [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for
the high-level pipeline.

## Features

- **Two-stage pipeline** (C++/Wine -> JSON -> bpy) that isolates the
  Windows-only DirectX 9 / D3DX dependency from Blender.
- **Frame-accurate animation** via world-matrix baking plus the
  textbook pose-local derivation on the Python side
  (`M_rest⁻¹ · M_rest_parent · M_world_parent⁻¹ · M_world`).
- **Automatic bone axis correction** (`--bone-axis auto`, the default) —
  detects whether the .X file uses X (3ds Max Biped), Y (Maya), or Z as
  the bone direction axis, and applies a consistent axis permutation so
  that BOTH skinning AND visually-correct bone tails are achieved.
  This solves the "bones point backwards" problem without breaking
  skinning.  See "Bone axis correction" below.
- **Optional `--no-bake` sparse key-time baking** — evaluates at the
  original keyframe times (typically 20–50 vs 300+ at 60 FPS) while
  using the same mathematically-exact world-matrix formula as the
  default dense path.
- **F-curve decimation** (`--decimate-mode error --decimate 1e-4` is
  recommended for headless runs) to cut bake size without sacrificing
  mathematical accuracy within the tolerance.
- **Static-bone optimization** on the C++ side (and defense-in-depth on
  the Python side): channels whose baked matrices are constant collapse
  to a single keyframe, cutting bake size by 50-80% on typical
  skeletons.
- **Idempotent `.x` template injection**: only the missing templates are
  injected; the preprocessor is safe to run repeatedly on the same file.
- **Structured logging** with four levels (`debug|info|warn|error`) on
  both sides.
- **Shift-JIS (CP932) texture-path decoding** preserved end-to-end.
- **Standalone DX9 viewer** (`viewer.exe`) for interactive preview.
- **Validation scripts** under `scripts/verify/` that numerically verify
  bone matrices and animation poses against the source `.x` file.
- **Unit tests** for the pure-math and serialization pieces (C++ and
  Python), runnable without Wine or Blender.

## Architecture

```
DirectX .x model
       |
       v   (Wine)
  x2blend.exe              <- Stage 1: C++ / D3DX loader + baker
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

**Stage 1** (`x2blend.exe`, C++): creates a headless D3D9 device, loads
the `.x` file via `D3DXLoadMeshHierarchyFromX`, flattens the frame
hierarchy, extracts meshes/materials/skinning, bakes animations at a
configurable sample rate (default 60 FPS) by advancing the D3DX
animation controller and recording world matrices, and serializes
everything to JSON.  A `meta` block at the top of the JSON carries the
pipeline configuration (bake mode, bake FPS, source ticks-per-second,
max influences, exporter version).

**Stage 2** (`scripts/blend_importer`, Python): runs inside Blender
(via `pip install bpy` or system `blender --background`), reads the
JSON, builds an armature from inverse-bind matrices, constructs mesh
objects with vertex groups + armature modifier, and writes F-curves by
mathematically deriving local pose matrices from the baked world
matrices.  Optionally decimates the resulting F-curves.

A standalone `viewer.exe` provides an interactive DX9 preview.

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the module
dependency graph and pipeline details.

## Project layout

```
X2Blend-refactored/
├── CMakeLists.txt
├── build.sh                 MinGW cross-compile driver
├── x2blend.sh               two-stage orchestrator
├── README.md                this file
├── docs/
│   ├── ANALYSIS.md          codebase analysis and refactoring plan
│   └── ARCHITECTURE.md      high-level pipeline + module graph
├── src/
│   ├── main.cpp             Stage 1 CLI entry
│   ├── core/                middleman data model, math, coord, codec, log
│   ├── io/                  x_file_preprocessor, json_exporter
│   ├── d3d/                 d3d_context, hierarchy_allocator
│   ├── loader/              x_loader, hierarchy_builder, mesh_extractor, animation_baker
│   └── viewer/              standalone DX9 viewer
├── scripts/
│   ├── blend_importer/      Python package (Stage 2)
│   └── verify/              bone + animation-pose verification scripts
└── tests/
    ├── cpp/                 C++ unit tests (x_math, coord, json_exporter)
    ├── python/              pytest tests (math_utils, animation pure helpers)
    ├── fixtures/            synthetic .x fixtures
    └── README.md            how to run the tests
```

## Requirements

- **Linux host** with:
  - `x86_64-w64-mingw32-g++` (MinGW cross-compiler, 14.2.0 or newer)
  - `wine` (9.0 or newer) — to run the Windows executables
  - `blender` (5.1 or newer) OR `pip install bpy`
  - `cmake` (>= 3.15) and `make`
  - Optional: Python 3.10+ with `pytest` + `mathutils` for the Python
    unit tests (none of the unit tests need Blender).

## Usage

### 1. Build

```bash
./build.sh
```

Produces `build/x2blend.exe` and `build/viewer.exe`.  Pass
`BUILD_TESTS=1` to also build the C++ unit tests:

```bash
BUILD_TESTS=1 ./build.sh
```

### 2. Convert a model (full pipeline)

```bash
./x2blend.sh input.x output.blend
```

The script runs Stage 1 under Wine, then Stage 2 via whichever Python
bpy runtime it finds (a `.venv/bin/python` with bpy, then system
`python3` with bpy, then system `blender --background`).

#### Higurashi Daybreak assets

The original X2Blend hardcoded a `0.01` root scale because every
shipped Higurashi model needed it.  The refactor makes the scale
opt-in.  For Higurashi assets, pass it to Stage 2 explicitly:

```bash
./x2blend.sh higurashi_model.x out.blend -- --root-scale 0.01
```

Everything before `--` goes to Stage 1 (`x2blend.exe`); everything
after `--` goes to Stage 2 (the Python importer).  If you omit `--`,
all extra flags go to Stage 1.

### 3. CLI flags reference

#### Stage 1 — `x2blend.exe`

| Flag | Default | Description |
|---|---|---|
| `--no-bake` | (off) | Sparse key-time baking: sample at original keyframe times instead of fixed 60 FPS. See below. |
| `--bake-fps N` | `60` | Bake sample rate in Hz.  Lower for 30 FPS sources, higher for 120 FPS. |
| `--max-influences N` | `4` | Bone-influence cap per vertex.  Must be in [1, 8]. |
| `--log-level <level>` | `info` | One of `debug`, `info`, `warn`, `error`. |

#### Stage 2 — `blend_importer`

| Flag | Default | Description |
|---|---|---|
| `--root-scale N` | `1.0` | Scale applied to every root object.  Pass `0.01` for Higurashi assets. |
| `--bone-tail-length N` | `0.05` | Fallback bone tail length (used only when the hierarchy can't determine one). By default, tails are computed from the bone hierarchy — see below. |
| `--max-influences N` | `4` | Informational; actual capping happens C++-side in `MeshExtractor`. |
| `--decimate N` | (off) | Decimation ratio (`--decimate-mode ratio`) or absolute error tolerance (`--decimate-mode error`). |
| `--decimate-mode ratio\|error` | `ratio` | `ratio` uses `bpy.ops.graph.decimate` (fragile headless); `error` uses manual Ramer-Douglas-Peucker (headless-safe). |
| `--no-decimate` | (off) | Disable decimation; overrides `--decimate`. |
| `--no-flip-uv` | (off) | Disable UV V-coordinate flipping (on by default — DirectX uses V=0 at top, Blender uses V=0 at bottom). |
| `--emissive-strength N` | `0.0` | Emission Strength for materials. Old anime games use high Emissive values as "baked lighting"; default 0.0 preserves the color but doesn't apply it as light (matches Solid mode). Set to 1.0 for the original game's bright look in Material Preview. |
| `--visual-tails` | (off) | Use child-directed bone tails (visually correct, **breaks skinning**). See "Bone tail direction" below. |
| `--bone-axis <auto\|x\|y\|z>` | `auto` | Which local axis the .X file uses for bone direction. `auto` detects from hierarchy (3ds Max Biped=X, Maya=Y). See "Bone axis correction" below. |
| `--log-level <level>` | `INFO` | One of `DEBUG`, `INFO`, `WARN`, `ERROR`. Use `DEBUG` to see bone axis-alignment diagnostics. |

#### Recommended headless invocation

```bash
./x2blend.sh input.x output.blend \
    --bake-fps 60 \
    -- --root-scale 0.01 \
       --decimate-mode error --decimate 1e-4 \
       --log-level INFO
```

### 4. `--no-bake` mode (sparse key-time baking)

When Stage 1 is run with `--no-bake`, the C++ side collects the union
of all keyframe times from `ID3DXKeyframedAnimationSet` and evaluates
the D3DX animation controller at each of those sparse times only
(typically 20–50 key times vs 300+ at 60 FPS).  This produces the same
`bakedKeys` (world matrices) as the default dense path — the Python
importer applies the same mathematically-exact formula — but at the
original keyframe times instead of fixed 60 FPS intervals.

Benefits:

- **Preserves original keyframe timing**: the source's keyframe times
  are retained, keeping the Blender action sparse and editable.
- **Much smaller JSON/bake size**: 20–50 key times vs 300+ at 60 FPS,
  plus the static-bone reduction collapses non-animated channels to 2
  keyframes.
- **Mathematically exact**: uses the same world-matrix formula as the
  dense path (`M_rest⁻¹ · M_rest_parent · M_world_parent⁻¹ · M_world`),
  so there is no accuracy trade-off.

Trade-offs:

- **Only works for keyframed sets.** Compressed / procedural animation
  sets don't expose key times via `ID3DXKeyframedAnimationSet`; the
  `--no-bake` path detects this and automatically falls back to dense
  baking with a warning.
- **All bones get keys at every union time.** Each bone gets keys at
  the union of ALL bones' key times, not just its own. A per-bone
  approach (which would require Python-side parent interpolation) is
  deferred. The static-bone reduction mitigates this for non-animated
  bones.

**Why not direct TRS-remap?** The original codebase had a component-wise
TRS-remap path (`t_rel = R_rest⁻¹ ⊗ (t_key − t_rest)`, etc.) that tried
to avoid world-matrix sampling entirely. This formula is mathematically
wrong — it treats D3DX keys (which are LOCAL / parent-relative) as if
they're in the same space as the rest pose, and misses the
`M_rest_parent · M_world_parent⁻¹` term in Blender's `chan_mat` formula.
The error accumulates down the bone chain, producing disfigured models.
See `docs/X_FORMAT_RESEARCH.md §4` and `docs/ANALYSIS.md §3.3` for the
full derivation.

Use `--no-bake` for smaller, sparser actions when the source is a
keyframed animation set. Use the default (dense baking) when you want
maximum robustness or the source uses compressed/procedural sets.

### 5. F-curve decimation

Baking at 60 FPS produces a key per frame per channel.  For long
animations on dense skeletons this can bloat the `.blend` action data.
The `--decimate` flag runs a decimation pass after keying:

- `--decimate-mode ratio --decimate 0.5` removes 50% of keyframes via
  `bpy.ops.graph.decimate`.  **Requires a graph editor context**, so
  this is fragile under `blender --background` (the operator may raise
  `RuntimeError`; the importer logs a warning and continues without
  decimating).  Use this only in interactive Blender sessions.
- `--decimate-mode error --decimate 1e-4` runs a manual
  Ramer-Douglas-Peucker pass on each F-curve with the given absolute
  error tolerance on the value axis.  Headless-safe and the
  **recommended mode for `blender --background` invocations**.
  `1e-4` is a good default (preserves sub-millimeter accuracy on most
  skeletons); raise to `1e-3` for more aggressive reduction.

Decimation runs **after** keyframe writing but **before** the NLA push,
so the action is still active when context-dependent operators run.

### 6. Bone axis correction (solves "bones point backwards")

**The problem:** Different 3D authoring tools use different local axes as
the bone "direction":

| Authoring tool | Bone axis | Example .X files |
|---|---|---|
| **3ds Max Biped** | **X** | Higurashi Daybreak (Twilight Frontier), most Japanese game assets |
| **Maya** | **Y** | Matches Blender's convention |
| Some custom rigs | **Z** | Less common |

Blender **requires** the bone's local Y axis = head→tail direction. This
is baked into `bone.matrix_local` and used by the armature modifier for
skinning.

When a .X file uses X as the bone axis (3ds Max Biped), the importer's
default tail direction (`bind_mat.col[1]` = Y) produces bones with correct
head positions but wrong tail directions — arms pointing backward, spines
pointing sideways, etc.

**The solution — `--bone-axis auto` (default):**

The importer auto-detects the bone axis by checking, for each bone with
children, which local axis (X/Y/Z) best aligns with the direction to the
nearest child bone. The axis with the most votes wins.

If the detected axis is not Y, a **consistent axis permutation** is
applied to ALL data in the JSON model before importing:

| Data | Permutation |
|---|---|
| Bone local transforms (4x4) | Swap rows + columns i↔j |
| Inverse bind matrices (4x4) | Swap rows + columns i↔j |
| Baked animation matrices (4x4) | Swap rows + columns i↔j |
| Vertex positions (3-vectors) | Swap components i↔j |
| Vertex normals (3-vectors) | Swap components i↔j |
| Triangle winding | Toggle index swap (two reflections cancel) |

Because the permutation is applied **uniformly** to all bones and all
vertices, the skinning formula is preserved:

```
deformed_new = Σ(wᵢ · P·poseᵢ · inv(P·restᵢ) · P·vertex)
             = P · Σ(wᵢ · poseᵢ · inv(restᵢ) · vertex)
             = P · deformed_old
```

The deformed vertex is in "permuted space," but since vertices and
armature are all permuted consistently, the result is visually correct in
Blender's coordinate system.

After permutation, `bind_mat.col[1]` (Y) is the original bone direction
axis (was X), so the existing tail-direction logic produces
visually-correct bones AND correct skinning — **both at the same time**.

**CLI flags:**

```bash
# Auto-detect (default, recommended):
./x2blend.sh model.x output.blend

# Force a specific axis (if auto-detection is wrong):
./x2blend.sh model.x output.blend -- --bone-axis x

# Skip permutation (for .X files that already use Y, e.g. Maya):
./x2blend.sh model.x output.blend -- --bone-axis y
```

**Detection diagnostics:** Run with `--log-level INFO` (or higher) to see
the detection results:

```
Bone axis detection: X=14 (70%), Y=2 (10%), Z=4 (20%) → 'x'
Applying axis permutation: x → y (swapping x and y)
```

### 7. Bone visual tails

The `.X` file format does not store bone lengths, heads, or tails — it
only stores transformation matrices (`Frame` + `FrameTransformMatrix`).
The bone's visual length in 3ds Max (the typical authoring tool via the
Panda exporter) is a property of the 3ds Max bone object and is lost on
export.  The importer must reconstruct visual bone tails from the
transform hierarchy alone.

#### Tail LENGTH (always hierarchy-based)

Blender bones require a non-zero tail (Blender auto-removes zero-length
bones).  The original X2Blend hardcoded a fixed `0.05` tail length,
which made all bones look like spheres/dots when the skeleton was at a
realistic scale.  The refactor computes tail lengths from the bone
hierarchy instead:

| Bone type | Tail length |
|---|---|
| Has children | 80% of the distance to the nearest child bone |
| Leaf (no children) | 50% of the parent's tail length |
| Root leaf (no parent, no children) | Fallback `--bone-tail-length` (default 0.05) |

Tail length does NOT affect `bone.matrix_local` (Blender derives the
rest matrix from head position, tail **direction**, and roll — not
length), so length computation is always safe.

#### Tail DIRECTION — the fundamental trade-off

This is where .X → Blender conversion hits a fundamental limitation.
The `.X` format (like FBX) does **not** store a "bone direction" — only
frame transforms.  In 3ds Max / Maya, bones visually point towards their
child, but that's a display property, not part of the transform matrix.

Blender **requires** the bone's local Y axis = head→tail direction, and
this is baked into `bone.matrix_local` (the rest matrix).  The armature
modifier uses `inv(bone.matrix_local)` for skinning.

This creates an unavoidable conflict:

| Mode | Tail direction | Skinning | Visual |
|---|---|---|---|
| **Default** (skinning-accurate) | `bind_mat.col[1]` (bind matrix local Y) | ✅ Correct — `matrix_local == bind_mat` | ❌ Tails may point oddly (backward, sideways) |
| `--visual-tails` (child-directed) | Towards nearest child bone | ❌ **Broken** — `matrix_local != bind_mat` | ✅ Tails follow limbs |

**Why skinning breaks in visual mode:** The armature modifier computes
`deformed_vertex = pose_world @ inv(rest_world) @ vertex`.  When the
tail direction changes, `rest_world` changes, so `inv(rest_world)`
changes, but the vertex positions (geometry) are fixed — they were
authored for the original bind pose.  The result is incorrect
deformation.  (Animation F-curves are unaffected because the importer
reads `bone.matrix_local` from Blender at import time, which
auto-compensates.)

**Why this can't be fixed trivially:** For soft-skinned vertices (multiple
bone influences), there's no single transform that can re-bake the
vertex positions to a new bind pose — each influencing bone would
require a different transform, and a vertex can only have one position.

**Recommendation:**
- Use the **default** (skinning-accurate) for production — the bones
  look odd but everything works.  Switch the armature display to
  **OCTAHEDRON** mode (the importer does this automatically) to better
  see the 3D orientation of each bone's local axes.
- Use **`--visual-tails`** for hierarchy debugging — to verify the bone
  structure is correct, or to inspect the rig without caring about
  skinning.  Re-import without the flag for production.
- Run with **`--log-level DEBUG`** to see a diagnostic showing which
  local axis (X/Y/Z) of each bind matrix best aligns with the child
  direction.  If most bones' best axis is NOT Y, the .X authoring tool
  used a non-Y bone axis convention (common with 3ds Max, which uses X
  for bones), and skinning-accurate mode will produce odd-looking tails
  by design.

This is the same limitation documented in Blender's own FBX importer
(bug #53620): "each FBX bone can point in a different direction, there
is no single uniform swapping of bone orientations that will ever work."

### 8. Manual stages

You can run the two stages separately if needed:

```bash
# Stage 1: .x -> JSON
wine build/x2blend.exe model.x model.json --bake-fps 60

# Stage 2: JSON -> .blend (using a venv bpy)
.venv/bin/python -m blend_importer model.json output.blend \
    --root-scale 0.01 --decimate-mode error --decimate 1e-4

# Or using system Blender
blender --background --python scripts/blend_importer/main.py -- \
    model.json output.blend --root-scale 0.01
```

(Note: when invoking `main.py` directly via `--python`, Blender leaves
the `--` separator inside `sys.argv`; the importer's
`_strip_blender_separator` handles this.  When invoking via
`python -m blend_importer`, no `--` is needed.)

### 9. View models

```bash
wine build/viewer.exe model.x
```

Controls:

- **Orbit**: Left mouse drag
- **Zoom**: Mouse wheel
- **Cycle Animations**: Spacebar or left/right arrows
- **Reset Camera**: `R`

### 10. Textures

The `.X` file references textures by filename (often just the bare name,
e.g. `服.bmp` for Higurashi Daybreak assets — Shift-JIS decoded to UTF-8
by the C++ side).  The importer resolves texture paths automatically:

1. **Absolute path** — used directly if the file exists.
2. **Relative to the .X source directory** — if the .X file was at
   `/path/to/assets/00.X`, a texture `服.bmp` is looked up at
   `/path/to/assets/服.bmp`.  This matches how D3DX itself resolves
   textures at load time, and is the most common case (textures placed
   alongside the .X file).
3. **Current working directory** — fallback if not found in the .X
   directory (preserves backward compatibility).

**Supported formats:** Blender loads BMP, PNG, JPEG, TIFF, TGA, and DDS
natively.  The Higurashi Daybreak assets use **BMP**, which works out of
the box — no conversion needed.

**Logging:** The importer reports each texture load (or failure) at INFO
level:

```
[blend_importer] INFO   Material 'Material__133_headSub8': loaded texture '服.bmp' from /path/to/assets/服.bmp
[blend_importer] INFO   Materials: 5 textures loaded, 0 missing
```

If a texture is not found, the importer logs where it looked and creates
a placeholder image-texture node (with the filename as a label) so the
material slot exists:

```
[blend_importer] WARNING  Material 'Material__133_headSub8': texture '服.bmp' not found.
                          Looked in: '/path/to/assets' (X source dir) and '/cwd' (cwd).
                          Creating placeholder node.
```

**No `--texture-dir` flag is needed** — just place the textures in the
same folder as the .X file (the standard layout), and the importer finds
them automatically.

**UV V-coordinate flip:** DirectX uses V=0 at the TOP of the texture
(like image coordinates), while Blender uses V=0 at the BOTTOM (OpenGL
convention).  The importer flips V by default (`v_blender = 1.0 - v_d3dx`)
so textures appear right-side up on the model.  This is the standard fix
for all DirectX-to-Blender conversions.  Use `--no-flip-uv` to disable
it if your .X file already uses the OpenGL convention (rare).

**Image packing:** All loaded textures are **packed into the .blend file**
at import time (`image.pack()`).  This makes the .blend self-contained —
you can move or share it without losing the textures.  Without packing,
Blender only stores a filepath reference (e.g. `//assets/mion/服.bmp`),
and when the .blend is opened on another machine or the textures are
moved, the images show as "missing" and materials appear empty in the
Properties panel.

### 11. Validation

The verification scripts numerically compare the matrices in a `.blend`
against the source `.x` file's D3DX-computed reference matrices.

```bash
# Bone rest-pose accuracy (< 1e-4 units, < 0.5 degrees)
blender --background --python scripts/verify/verify_bones.py -- \
    model.json output.blend

# Animation pose accuracy (< 1e-3 units, < 0.1 degrees, first 3 anims x first 10 frames)
blender --background --python scripts/verify/verify_animation_poses.py -- \
    model.json output.blend
```

Both scripts read `bake_fps` from the JSON `meta` block (falling back
to 60.0 with a warning if the meta block is missing).

## Tests

See [`tests/README.md`](tests/README.md) for the full instructions.
Short version:

```bash
# C++ unit tests (MinGW cross-compile for the D3D one)
BUILD_TESTS=1 ./build.sh
# Or, natively on a Linux host with a C++17 compiler (only the non-D3D tests):
mkdir build && cd build && cmake .. -DBUILD_TESTS=ON && make && ctest

# Python unit tests (no Blender required, only pytest + mathutils)
pip install pytest mathutils
pytest tests/python/
```

The C++ tests cover the pure-math pieces (`XMath::conjugate` /
`multiply` / `rotate`), the D3DX coordinate conversion
(`convertMatrixToBlender`), and the JSON exporter's `meta` block.  The
Python tests cover `math_utils` (matrix conversion, pose-local
derivation, bone-roll round-trip) and the pure helpers in `animation`
(RDP decimation, quaternion reorder, static-channel detection).  None
of the unit tests require Wine or Blender; only the integration-level
verification scripts do.

## Changes from the original

A full before/after table is in
[`docs/ANALYSIS.md`](docs/ANALYSIS.md) section 4.2.  Summary:

- **Modular source tree**: the 919-line `x_loader.cpp` god class is
  split into `d3d/`, `loader/`, `io/`, and `core/` modules with single
  responsibilities.  The 735-line `viewer.cpp` no longer duplicates the
  preprocessor / codec / template-injection helpers; it links against
  the shared `core/codec`, `core/log`, and `io/x_file_preprocessor`.
- **The 590-line `blend_importer.py` monolith is now a package**
  (`scripts/blend_importer/`) with one module per concern (config,
  scene, materials, armature, mesh, animation, math_utils, main).
- **Dead code revived**: the original extracted legacy TRS keys but
  the Python importer only consumed `bakedKeys`.  The refactor revives
  the TRS-remap path as the `--no-bake` mode; the C++ side always
  collects TRS keys so the JSON carries both paths, and the Python
  side picks per channel.
- **Magic numbers extracted to `config.py`** and exposed as CLI flags
  (`--root-scale`, `--bone-tail-length`, `--max-influences`,
  `--bake-fps`).  The Higurashi-specific `0.01` root scale is no longer
  hardcoded — pass it explicitly for Higurashi assets.
- **`meta` block in the JSON** carries pipeline configuration from
  the C++ side to the Python side, replacing the `detect_fps`
  heuristic that read dead data.
- **Static-bone optimization** on the C++ side (collapse channels
  whose baked matrices are constant within `1e-7`), with
  defense-in-depth on the Python side.
- **F-curve decimation** (`--decimate`, `--decimate-mode`).
- **Idempotent template injection**: only the missing templates are
  injected.
- **Leveled logger** (`LOG_DEBUG` / `LOG_INFO` / `LOG_WARN` /
  `LOG_ERROR` on the C++ side; Python `logging` on the Python side),
  replacing the original's bare `std::cerr` / `print`.
- **Unit tests** for the pure-math and serialization pieces (C++ and
  Python).  The original had zero unit coverage.

What stays the same: the two-stage architecture, the Middleman data
model (extended, not replaced), the mathematical F-curve keying, the
inverse-bind-matrix-first armature construction, the Shift-JIS texture
handling, the validation scripts (lightly refactored), and the
MinGW cross-compile + static-link build strategy.

## Notes

- Tested with Wine 9.0, Blender 5.1, and `x86_64-w64-mingw32-g++`
  14.2.0.
- Generated `.blend` files include NLA tracks for easy animation
  switching.  The armature uses Stick display mode for bone visibility.
- Static-link flags (`-static-libgcc -static-libstdc++ -static`) make
  `x2blend.exe` and `viewer.exe` self-contained under Wine; no MinGW
  runtime DLLs need to be present in the Wine prefix.
- This refactor was produced by parallel subagents (C++ core/io, C++
  d3d/loader, Python blend_importer, build/scripts/docs/tests) and
  was not build-validated in the sandbox it was written in.  Build and
  run validation should be performed on the user's MinGW + Wine +
  Blender machine.
