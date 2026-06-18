# X2Blend — Architecture

> High-level pipeline and module graph for the refactored tree.  See
> [`ANALYSIS.md`](ANALYSIS.md) for the codebase analysis and the
> refactoring plan that drove this structure.

## 1. Two-stage pipeline

The pipeline is forced by the fact that DirectX 9 / D3DX is
Windows-only and requires an active Direct3D device:

```
                                            Stage 1                       Stage 2
                                            (C++ / Wine)                  (Python / bpy)

  .x file ─────▶ x2blend.exe ──────────▶ model.json ─────▶ blend_importer ─────▶ .blend file
                 (headless D3D9 +           (compact JSON      (Blender Python:
                  D3DXLoadMeshHierarchy      + meta block)       armature, mesh,
                  + bake / TRS-remap)                            F-curves, NLA)
```

Stage 1 (`x2blend.exe`) creates a headless Direct3D 9 device, loads
the `.x` file via `D3DXLoadMeshHierarchyFromX`, flattens the frame
hierarchy, extracts meshes / materials / skinning, bakes animations
(default) or extracts rest-relative TRS keys (`--no-bake`), and
serializes the result to JSON.

Stage 2 (`blend_importer`) runs inside Blender (via `pip install bpy`
or `blender --background`), reads the JSON, builds the armature from
inverse-bind matrices, constructs mesh objects with vertex groups +
armature modifier, and writes F-curves by mathematically deriving
local pose matrices from the baked world matrices.

A standalone `viewer.exe` (not shown) provides an interactive DX9
preview.

## 2. Module dependency graph

```
                  ┌───────────────────────────────────────────────┐
                  │                  src/main.cpp                  │
                  │            (Stage 1 CLI entry point)           │
                  └─────┬──────────┬──────────┬──────────────┬─────┘
                        │          │          │              │
                        v          v          v              v
                  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌──────────┐
                  │  core/  │ │   io/   │ │  d3d/   │ │ loader/  │
                  │ middle- │ │ json_   │ │ d3d_    │ │ x_loader │
                  │ man,    │ │exporter │ │context, │ │ (facade) │
                  │ x_math, │ │ x_file_ │ │ hierar- │ │          │
                  │ coord,  │ │preproc  │ │ chy_al- │ │          │
                  │ codec,  │ │         │ │ locator │ │          │
                  │ log     │ │         │ │         │ │          │
                  └─────────┘ └─────────┘ └─────────┘ └──────────┘
                       │          ▲          ▲              │
                       │          │          │              ├──▶ hierarchy_builder
                       │          │          │              ├──▶ mesh_extractor
                       │          │          │              └──▶ animation_baker
                       │          │          │
                       └──────────┴──────────┘
                  (io/ and loader/ both consume core/; loader/
                   also consumes io/ for the preprocessor and
                   d3d/ for the device + hierarchy allocator)


   ┌───────────────────────────────────────────────┐
   │              src/viewer/viewer.cpp             │
   │            (standalone DX9 previewer)          │
   └──────┬──────────────────────────────┬──────────┘
          │                              │
          v                              v
    ┌──────────┐                   ┌──────────┐
    │  core/   │                   │   io/    │
    │ codec,   │                   │ x_file_  │
    │ log,     │                   │preproc   │
    │ middle-  │                   │          │
    │ man      │                   │          │
    └──────────┘                   └──────────┘
```

Layering rules:

- `core/` is pure C++17 with no Windows or D3DX dependency (except
  `coord.cpp`, which takes a `D3DXMATRIX` as input).  It can be unit-
  tested on any C++17 host.
- `io/` depends on `core/` (for the data model + logger).  Only
  `x_file_preprocessor.cpp` pulls in `<windows.h>` (for Shift-JIS
  decoding via the Win32 `MultiByteToWideChar` API).
- `d3d/` depends on `core/` (for the logger).  Pulls in `<d3d9.h>`,
  `<d3dx9.h>`, `<d3dx9anim.h>`.
- `loader/` depends on `core/`, `io/`, and `d3d/`.  `x_loader.cpp`
  defines `INITGUID` so `IID_ID3DXKeyframedAnimationSet` is
  instantiated in this TU; the other loader TUs declare it `extern`
  and link the symbol from `dxguid.lib`.
- `viewer/` is intentionally standalone-ish: it depends only on
  `core/codec`, `core/log`, `core/middleman`, and
  `io/x_file_preprocessor`.  It does **not** link against `loader/`
  or `d3d/` (it owns its own windowed device init and its own
  `ViewerAllocateHierarchy`, because the viewer needs a presentation
  swap chain and textures / skinned-mesh clones the headless
  allocator doesn't carry).  It also defines `INITGUID` itself, so
  linking it together with `loader/x_loader.cpp` would cause a
  duplicate-symbol error; the CMakeLists.txt therefore keeps the
  viewer's source list narrow.

## 3. The JSON `meta` block

The JSON emitted by Stage 1 starts with a `meta` object that carries
pipeline configuration to Stage 2.  This replaces the original's
`detect_fps` heuristic, which read TRS keys that the original Python
importer never actually consumed (dead data).

```json
{
  "meta": {
    "source_file":               "C:\\assets\\model.x",
    "bake_mode":                 "baked",
    "bake_fps":                  60,
    "source_ticks_per_second":   4800,
    "max_influences":            4,
    "x2blend_version":           "2.0.0-refactor"
  },
  "root_node_index": 0,
  "nodes":  [ ... ],
  "meshes": [ ... ],
  "animations": [ ... ]
}
```

Field meanings:

| Field | Type | Meaning |
|---|---|---|
| `source_file` | string | Path of the `.x` file the model came from. |
| `bake_mode` | `"baked"` or `"keyframed"` | Whether `animations[].channels[].baked_keys` (baked) or `translation_keys` / `rotation_keys` / `scale_keys` (keyframed) are populated. |
| `bake_fps` | number | Sample rate used by the C++ baker, in Hz.  Stage 2 uses this to convert keyframe times to Blender frame numbers. |
| `source_ticks_per_second` | integer | D3DX animation-set ticks-per-second from `ID3DXKeyframedAnimationSet::GetSourceTicksPerSecond()`.  Default `4800` (the D3DX default). |
| `max_influences` | integer | Cap on bone weights per vertex applied C++-side by `MeshExtractor`. |
| `x2blend_version` | string | Exporter version string (`"2.0.0-refactor"` for this tree). |

When `bake_mode == "keyframed"`, the Python importer uses the per-channel
TRS-remap path; when `bake_mode == "baked"`, it uses the baked world-matrix
path.  Channels can mix modes if `baked_keys` is non-empty on some channels
and empty on others (rare edge case).

## 4. The baking decision

**Baking is the default.**  Sampling the D3DX animation controller at a
fixed rate (default 60 FPS) and recording world matrices per bone per
frame sidesteps the three problems with direct TRS extraction:

1. **Parent-space ambiguity.**  D3DX keys are in each bone's parent
   space, but Blender's pose-bone matrices are relative to the bone's
   rest pose in armature space.  Intermediate non-bone "helper" frames
   that Blender collapses make the parent chains not match 1:1.
2. **Scale composition.**  D3DX allows non-uniform scale in keys that
   composes with rest scale; getting this exactly right in Blender's
   TRS pipeline is fiddly.
3. **Non-keyframed sets.**  `ID3DXKeyframedAnimationSet` only exposes
   keys for the keyframed variant; baking via
   `ID3DXAnimationController` samples the *evaluated* result and works
   for compressed / procedural variants too.

Baking to world space produces unambiguous ground truth, and the
formula `M_pose_local = M_rest⁻¹ · M_rest_parent · M_world_parent⁻¹ · M_world`
produces exactly the F-curve values Blender expects.

The costs (storage bloat, loss of keyframe structure, fixed sample
rate, single-track sampling) are mitigated by the post-bake
optimizations in this refactor:

- **Static-bone collapse** (C++ side, tolerance 1e-7): channels whose
  baked matrices are constant collapse to first-and-last keyframes,
  cutting bake size by 50-80% on typical skeletons.
- **F-curve decimation** (`--decimate-mode error --decimate 1e-4`):
  manual Ramer-Douglas-Peucker on each F-curve with an absolute error
  bound; headless-safe.
- **Parameterized sample rate** (`--bake-fps N`): pick 30 for 30 FPS
  sources, 120 for 120 FPS sources.

The TRS-remap path (`--no-bake`) is preserved as an opt-in alternative
for users who need lossless keyframe preservation and are confident
the source's parent chain maps cleanly.  See `ANALYSIS.md` section 3
for the full study.

## 5. Static-bone optimization

For each animation channel, after the bake loop completes, the C++
side iterates the baked matrices and checks whether they are all
identical to the first within `1e-7f` across all 16 floats.  If so,
the channel is collapsed to just the first and last keyframe (one
keyframe would be enough mathematically, but two preserves the
animation's time bounds for Blender's action frame range).

The Python side repeats this check as defense in depth (same `1e-7`
tolerance, in `blend_importer.config.STATIC_CHANNEL_TOLERANCE`), so
channels that arrive un-collapsed from C++ (e.g. from compressed
animation sets that bypassed the C++ optimization) are still
collapsed on the Python side.  In practice, both checks fire on the
same channels; the Python check is a safety net.

Typical impact: a 50-bone skeleton with 10 animated bones baking at
60 FPS for 5 seconds goes from 15,000 keyframes per animation to
~6,000 (after both optimizations and `--decimate 1e-4`), without
losing any visual fidelity.

## 6. TRS-remap fallback path

When Stage 1 is run with `--no-bake`, the C++ side skips the D3DX
animation-controller sampling loop and instead extracts the
rest-relative TRS keys directly from
`ID3DXKeyframedAnimationSet::GetTranslationKey` /
`GetRotationKey` / `GetScaleKey`.  The derivation (preserved verbatim
from the original `AnimMath` struct, now in `core/x_math.cpp`):

- **Translation**: `t_rel = R_rest⁻¹ ⊗ (t_blend − t_rest)`
- **Rotation**:    `r_rel = R_rest⁻¹ ⊗ r_blend`
- **Scale**:       `s_rel = s_blend ÷ s_rest`

where `R_rest` is the rest-pose rotation extracted from the frame's
combined transformation matrix.

The Python side writes these keys directly to F-curves, with two
adjustments:

1. **Time-to-frame conversion**: `f_num = int(round(t * bake_fps))`,
   where `bake_fps` comes from the JSON `meta` block.
2. **Quaternion component reorder**: the C++ `XQuaternion` is
   `{x, y, z, w}` and `io/json_exporter.cpp` emits it as
   `[x, y, z, w]` in the JSON.  Blender's `rotation_quaternion`
   F-curve indices are 0=w, 1=x, 2=y, 3=z, so the importer remaps:

   ```
   F-curve index 0  <-  w  (JSON index 3)
   F-curve index 1  <-  x  (JSON index 0)
   F-curve index 2  <-  y  (JSON index 1)
   F-curve index 3  <-  z  (JSON index 2)
   ```

This path is **experimental**.  It is faster and produces smaller
actions, but is sensitive to the D3DX-to-Blender parent-chain mapping
(intermediate helper frames break it) and fails on compressed /
procedural animation sets.  See `ANALYSIS.md` section 3.3 for the
trade-offs.

## 7. Module-to-file map

| Module | Files | Responsibility |
|---|---|---|
| **core/** | `middleman.{h,cpp}`, `x_math.{h,cpp}`, `coord.{h,cpp}`, `codec.{h,cpp}`, `log.{h,cpp}` | Pure-data model + dependency-free math/codec/log helpers. |
| **io/** | `x_file_preprocessor.{h,cpp}`, `json_exporter.{h,cpp}` | `.x` template injection (idempotent) + JSON serializer with `meta` block. |
| **d3d/** | `d3d_context.{h,cpp}`, `hierarchy_allocator.{h,cpp}` | Headless D3D9 device lifecycle + `ID3DXAllocateHierarchy` impl. |
| **loader/** | `x_loader.{h,cpp}` (facade), `hierarchy_builder.{h,cpp}`, `mesh_extractor.{h,cpp}`, `animation_baker.{h,cpp}` | D3DXFRAME → `XModel` flattening, mesh + skinning extraction, animation baking + TRS-remap. |
| **viewer/** | `viewer.cpp` | Standalone windowed DX9 previewer.  Reuses `core/codec`, `core/log`, `io/x_file_preprocessor`; owns its own D3D init + allocator. |
| **scripts/blend_importer/** | `__init__.py`, `__main__.py`, `main.py`, `config.py`, `scene.py`, `materials.py`, `armature.py`, `mesh.py`, `animation.py`, `math_utils.py` | Stage 2: JSON → `.blend`. |
| **scripts/verify/** | `verify_bones.py`, `verify_animation_poses.py` | Numerical validation against the source `.x` (require Blender). |
| **tests/** | `cpp/test_x_math.cpp`, `cpp/test_coord.cpp`, `cpp/test_json_exporter.cpp`, `python/test_math_utils.py`, `python/test_animation.py`, `fixtures/minimal.x` | Unit tests; pure-math / serialization only (no Wine / Blender needed). |
