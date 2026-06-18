# X2Blend — Codebase Analysis & Refactoring Plan

> Analysis of `https://github.com/Vmarcelo49/X2Blend` (commit `f4d7c9d`)
> by the refactor orchestrator. This document drives the refactor in
> `X2Blend-refactored/`.

---

## 1. What the project does

X2Blend converts DirectX 9 `.x` 3D model files (specifically from the 2008
game *Higurashi Daybreak kai*) into Blender `.blend` files with frame-perfect
animation fidelity.

The pipeline is **two-stage**, forced by the fact that DirectX 9 / D3DX is
Windows-only and requires an active Direct3D device:

```
 .x file ──(Wine)──▶ x2blend.exe ──▶ model.json ──(bpy)──▶ blend_importer.py ──▶ .blend
            Stage 1: C++/D3DX        intermediate         Stage 2: Blender Python
```

- **Stage 1** (`x2blend.exe`, ~1.3k LoC C++): creates a headless D3D9 device,
  loads the `.x` file via `D3DXLoadMeshHierarchyFromX`, flattens the frame
  hierarchy, extracts meshes/materials/skinning, **bakes animations at 60 FPS**
  by advancing the D3DX animation controller and recording world matrices, and
  serialises everything to JSON.
- **Stage 2** (`blend_importer.py`, ~590 LoC Python): reads the JSON inside
  Blender, builds an armature from inverse-bind matrices, constructs mesh
  objects with vertex groups + armature modifier, and writes F-curves by
  **mathematically deriving local pose matrices** from the baked world
  matrices (`M_rest⁻¹ · M_rest_parent · M_world_parent⁻¹ · M_world`).

A standalone `viewer.exe` (~735 LoC, largely duplicated from the loader)
provides an interactive DX9 preview.

---

## 2. Architecture assessment

### 2.1 What's genuinely good

1. **The two-stage split is correct.** D3DX demands a live Direct3D device and
   is Windows-only; keeping that poison isolated from the Blender side is the
   right call. The JSON boundary is a clean contract.
2. **The "Middleman" data model** (`middleman.h`) is dependency-free pure
   data. It could target glTF/FBX/etc. without touching the loader.
3. **Mathematical F-curve keying** in `blend_importer.py` is a real
   optimization — bypassing `view_layer.update()` in the frame loop gives a
   ~100× speedup and eliminates depgraph drift. The formula
   `M_pose_local = M_rest⁻¹ · M_rest_parent · M_world_parent⁻¹ · M_world`
   is the textbook derivation and is provably correct.
4. **Inverse-bind-matrix-first armature construction** is the right primary
   path: the skin's offset matrix is the authoritative rest pose, with the
   frame hierarchy as a fallback.
5. **Shift-JIS (CP932) texture-path handling** is essential for the target
   assets and correctly threaded through every name-bearing field.
6. **Numeric verification scripts** (`verify_bones.py`,
   `verify_animation_poses.py`) are excellent engineering — they prove
   correctness against thresholds (1e-4 units / 0.5°) rather than eyeballing.

### 2.2 Issues found

#### A. Massive code duplication between `x_loader.cpp` and `viewer.cpp`
- `k_missingTemplates` (the template-injection string) is duplicated verbatim.
- `readAndPreprocessXFile` is duplicated.
- The headless-D3D9 init pattern is duplicated.
- The frame-hierarchy traversal is duplicated.

`CMakeLists.txt` even defines a `COMMON_SOURCES` variable — but only includes
`middleman` + `x_loader`, and the `viewer` target ignores it. The duplication
is unnecessary.

#### B. `x_loader.cpp` is a 919-line god class
`XLoader` does D3D init, file preprocessing, hierarchy traversal, mesh
extraction, skinning, **and** animation baking in one class. These are five
distinct responsibilities.

#### C. `blend_importer.py` is a 590-line monolith
One file does scene reset, materials, armature, meshes, animations, scale
adjustment, NLA packaging, and CLI. No package structure.

#### D. Dead / vestigial code
- `XNode::useTRS` is set but never read by the Python importer.
- The C++ side extracts legacy TRS keys (`translationKeys`, `rotationKeys`,
  `scaleKeys`) into a rest-relative form — but the Python importer **only
  consumes `bakedKeys`**. The entire TRS-remap path is dead code.
- `middleman.cpp` is empty (just includes the header).
- `detect_fps()` in the Python inspects the (dead) TRS keys to guess FPS,
  while the baked keys are always 60 FPS by construction — the heuristic
  is misleading and reaches into dead data.

#### E. Hardcoded magic numbers (leakage of game-specific assumptions)
| Value | Where | Problem |
|---|---|---|
| `0.01` root scale | `blend_importer.py:567-569` | Higurashi-specific, baked into a "general" importer |
| `60.0` FPS baking | `x_loader.cpp:754` | Not parameterized; wrong for 30 FPS or 120 FPS sources |
| `0.05` bone tail | `blend_importer.py:211` + docs | Magic, undocumented |
| `4` max influences | `x_loader.cpp:571` | Reasonable but unnamed |
| `128` spec-power max | `blend_importer.py:122` | D3DX constant, undocumented |

#### F. No tests
The only "tests" are the two Blender verification scripts, which need a full
Blender install and real `.x` fixtures. The pure-math pieces (`AnimMath`,
`convertMatrixToBlender`, the pose-local derivation, weight normalization)
have zero unit coverage.

#### G. Verbose JSON intermediate
Every matrix is `[[a,b,c,d],[e,f,g,h],...]` at 7-digit precision. For a
baked 5-second / 50-bone animation that's ~240k floats — tens of MB of JSON.
Flat arrays would be dramatically smaller; a binary format (CBOR/MessagePack)
smaller still.

#### H. Static-bone bloat in baking
`processAnimations` creates a channel for **every node** (line 765-774) and
the bake loop writes a key for every node at every frame — including
non-animated nodes that never move. A 50-bone skeleton where only 10 bones
animate still produces 50 × 300 = 15,000 keyframes per animation, of which
12,000 are identical copies of the bind pose.

#### I. Fragile template injection
The `.x` template injection assumes text format, doesn't check for existing
declarations (risk of "duplicate template" errors under strict parsers), and
silently does nothing for binary `.x` files with custom templates.

#### J. No structured logging / weak error recovery
`std::cerr` and `print` everywhere. No log levels. Temp files can leak on
mid-write failures. Partial `.blend` files are left on disk on exception.

---

## 3. Animation baking — is it the right call?

This is the core question. The current pipeline **bakes** animations: it
samples the D3DX animation controller at 60 FPS, records world matrices for
every bone at every frame, and reconstructs local pose matrices on the Blender
side. Let me weigh this honestly.

### 3.1 Why baking exists (the problem it solves)

D3DX exposes per-channel TRS keys via `ID3DXKeyframedAnimationSet`, but
mapping them directly to Blender is non-trivial:

1. **Parent-space ambiguity.** D3DX keys are in each bone's *parent space*.
   Blender pose-bone matrices are relative to the bone's *rest pose in
   armature space*. The D3DX frame hierarchy contains intermediate non-bone
   "helper" frames that Blender collapses; the parent chains don't match 1:1.
2. **Scale composition.** D3DX allows non-uniform scale in keys that composes
   with rest scale; getting this exactly right in Blender's TRS pipeline is
   fiddly.
3. **Non-keyframed sets.** D3DX supports compressed and procedural animation
   sets. `ID3DXKeyframedAnimationSet` only exposes keys for the keyframed
   variant; baking via `ID3DXAnimationController` works for all variants
   because it samples the *evaluated* result.

Baking to **world space** sidesteps all three: world matrices are
unambiguous, and the formula
`M_pose_local = M_rest⁻¹ · M_rest_parent · M_world_parent⁻¹ · M_world`
produces exactly the F-curve values Blender expects.

### 3.2 Costs of baking

1. **Storage bloat.** At 60 FPS, a 5-second animation × 50 bones = 15,000
   baked matrix keyframes. The JSON can easily reach tens of MB.
2. **Loss of keyframe structure.** The source `.x` may have only a handful of
   keyframes per bone. Baking replaces them with one key per frame, making
   the Blender action heavier and harder to edit by hand.
3. **Loss of compression.** Compressed D3DX sets are decompressed.
4. **Fixed sample rate.** 60 FPS may not match the source's intended rate.
   `detect_fps` on the Python side tries to compensate but reads dead data.
5. **Track compositing lost.** Only track 0 is sampled; if the game blends
   multiple tracks (upper-body + lower-body), the blend isn't reproduced.

### 3.3 The alternative: direct TRS-remap (BROKEN — do not use)

The original C++ code extracted legacy TRS keys and converted them to a
rest-relative form using a **component-wise** formula:
- translation: `t_rel = R_rest⁻¹ ⊗ (t_blend − t_rest)`
- rotation:    `r_rel = R_rest⁻¹ ⊗ r_blend`
- scale:       `s_rel = s_blend ÷ s_rest`

**This formula is mathematically wrong.** It treats the D3DX keyframe
values as if they're in the same space as the rest pose and can be
independently converted to Blender's `chan_mat`. But:

1. **D3DX keys are LOCAL (parent-relative)**, not world-space. The `.X`
   file's `AnimationKey` template stores per-bone S/R/T keys that
   REPLACE the frame's `TransformationMatrix` during playback. The
   frame's world matrix is `local × parent_world`.

2. **Blender's `chan_mat` is NOT a simple rest-relative TRS delta.** The
   correct formula (derived in `docs/X_FORMAT_RESEARCH.md §4`) is:
   ```
   chan_mat = M_rest⁻¹ · M_rest_parent · M_world_parent⁻¹ · M_world
   ```
   This requires the parent's ACTUAL posed world matrix at each keyframe
   time — not just the key value. The component-wise formula misses the
   `M_rest_parent · M_world_parent⁻¹` term entirely.

3. **The error accumulates down the bone chain.** Root bones may look
   approximately correct (if their rest local is near-identity), but
   child bones inherit growing errors, producing the "totally
   disfigured" models the user observed.

The Python importer never consumed these TRS keys (the path was dead
code in the original), so the bug was never caught. When the refactor
revived the path as `--no-bake`, the disfigurement became visible.

### 3.4 Verdict

**Baking (world-space sampling) is the correct approach.** The formula
`M_rest⁻¹ · M_rest_parent · M_world_parent⁻¹ · M_world` is the only
way to correctly derive Blender F-curve values from D3DX animation
data. Direct TRS-remap cannot work without full hierarchy
reconstruction at each keyframe time.

**But 60 FPS dense baking is wasteful.** The refactor offers two
correct paths:

1. **Dense bake (default, `--bake`):** Sample at fixed FPS (default 60)
   across the full duration. Most robust; works for all animation set
   types (keyframed, compressed, procedural). Storage-heavy.

2. **Sparse key-time bake (`--no-bake`):** Collect the UNION of all
   keyframe times from the keyframed set, evaluate the controller at
   each of those sparse times only (typically 20–50 vs 300+ at 60 FPS),
   and record the same world matrices. Mathematically identical to
   dense baking — uses the same formula — just at the original
   keyframe times. Falls back to dense baking for compressed/procedural
   sets (which don't expose key times).

Both paths produce `bakedKeys` (world matrices) and use the same Python
importer formula. The `--no-bake` flag is now a misnomer (it still
"bakes" in the sense of sampling the controller), but it preserves the
original keyframe timing and is much sparser.

**Additional optimizations (both paths):**
- **Static-bone reduction.** Channels whose world matrices don't vary
  across sampled times collapse to 2 keyframes (first + last).
- **Post-import decimation.** `--decimate-mode error --decimate 1e-4`
  runs RDP simplification on the F-curves after keying, further
  reducing keyframe counts within a tolerance.

**In short: baking (world-space sampling) is the only correct approach.
The `--no-bake` flag now selects sparse key-time baking instead of the
broken TRS-remap.**

---

## 4. Refactoring plan

### 4.1 Target structure

```
X2Blend-refactored/
├── README.md
├── CMakeLists.txt
├── build.sh
├── x2blend.sh
├── docs/
│   ├── ANALYSIS.md            ← this file
│   └── ARCHITECTURE.md        ← high-level pipeline doc
├── src/
│   ├── main.cpp               ← slim CLI with flags
│   ├── core/
│   │   ├── middleman.h        ← data model (cleaned)
│   │   ├── middleman.cpp      ← pure helpers (AnimMath, etc.)
│   │   ├── x_math.h/.cpp      ← quaternion / vector ops (no D3DX dep)
│   │   ├── coord.h/.cpp       ← LH↔RH / Y-up↔Z-up conversions
│   │   ├── codec.h/.cpp       ← Shift-JIS ↔ UTF-8
│   │   └── log.h/.cpp         ← structured logger
│   ├── io/
│   │   ├── x_file_preprocessor.h/.cpp   ← template injection (idempotent)
│   │   └── json_exporter.h/.cpp         ← compact JSON + meta block
│   ├── d3d/
│   │   ├── d3d_context.h/.cpp            ← headless device lifecycle
│   │   └── hierarchy_allocator.h/.cpp    ← ID3DXAllocateHierarchy impl
│   ├── loader/
│   │   ├── x_loader.h/.cpp               ← facade
│   │   ├── hierarchy_builder.h/.cpp      ← D3DXFRAME → XModel nodes
│   │   ├── mesh_extractor.h/.cpp         ← mesh + skinning
│   │   └── animation_baker.h/.cpp        ← baking + TRS-remap (both)
│   └── viewer/
│       └── viewer.cpp                    ← reuses core/io/d3d/loader
├── scripts/
│   ├── blend_importer/
│   │   ├── __init__.py        ← CLI entry
│   │   ├── main.py
│   │   ├── config.py          ← named constants
│   │   ├── scene.py
│   │   ├── materials.py
│   │   ├── armature.py
│   │   ├── mesh.py
│   │   ├── animation.py       ← baked + TRS paths + decimation
│   │   └── math_utils.py
│   └── verify/
│       ├── verify_bones.py
│       └── verify_animation_poses.py
└── tests/
    ├── cpp/
    │   ├── test_x_math.cpp
    │   ├── test_coord.cpp
    │   └── test_json_exporter.cpp
    ├── python/
    │   ├── test_math_utils.py
    │   └── test_animation.py
    └── fixtures/
        └── minimal.x          ← tiny synthetic .x for round-trip tests
```

### 4.2 Key behavioral changes

| Change | Original | Refactored |
|---|---|---|
| Baking mode | Always bake at 60 FPS | Bake by default; `--no-bake` uses TRS-remap; `--bake-fps N` sets rate |
| Static-bone handling | Bake every frame for every node | Skip nodes whose world matrix is constant; emit 1 keyframe |
| F-curve decimation | None | `--decimate <tol>` runs `bpy.ops.graph.decimate` after keying |
| Root scale | Hardcoded 0.01 (Higurashi) | `--root-scale 0.01` flag; default 1.0 |
| Bone tail length | Hardcoded 0.05 | `--bone-tail-length 0.05` flag; default 0.05 |
| Max influences | Hardcoded 4 | `--max-influences 4` flag |
| FPS detection | Heuristic on dead TRS keys | Read from JSON `meta.bake_fps` |
| JSON size | Nested objects, 7-digit precision | Flat arrays for vertices/baked keys; `meta` block |
| Logging | `std::cerr` / `print` | Leveled logger (`LOG_*` macros / `logging` module) |
| Template injection | Always injects | Idempotent: skip if already declared |
| Dead code | `useTRS`, unused TRS keys, empty `.cpp` | TRS keys revived as `--no-bake` path; `useTRS` removed; `.cpp` filled |
| Viewer duplication | ~700 lines copied | Links against shared `core/io/d3d/loader` |
| Tests | None | Unit tests for pure-math pieces in C++ and Python |

### 4.3 What stays the same

- The two-stage architecture (C++/Wine → JSON → bpy).
- The Middleman data model (extended with a `meta` block, not replaced).
- The mathematical F-curve keying approach (it's correct and fast).
- The inverse-bind-matrix-first armature construction.
- The Shift-JIS texture-path handling.
- The validation scripts (kept, lightly refactored for the new JSON shape).
- The CMake MinGW cross-compilation + static-link strategy.

---

## 5. Risk & honesty notes

- **I cannot build or run the refactor here.** This sandbox has no MinGW, no
  Wine, no DirectX 9 SDK, and no Blender. The refactor is a code-level
  reorganization that preserves the original's mathematical behavior; the
  build/run validation must happen on the user's machine.
- **Behavioral fidelity is the priority.** Where the original produced a
  specific matrix value, the refactor must produce the same value. The
  refactor's `convertMatrixToBlender`, `AnimMath`, and the pose-local
  derivation are line-for-line equivalent to the originals (just relocated).
- **The `--no-bake` TRS path is new surface area.** It revives dead code
  into a live alternative; it should be considered experimental and is
  documented as such. The baking path remains the default and the
  recommended path.
