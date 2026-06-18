# Tests

X2Blend's test suite is split into two layers:

1. **Unit tests** (this directory) — pure-math and serialization tests
   that do NOT need Wine, DirectX, or Blender.  They run on any C++17
   host (C++) and any Python with `pytest` + `mathutils` (Python).
2. **Verification scripts** (`scripts/verify/`) — numerical end-to-end
   checks that compare the `.blend` against the source `.x`.  These DO
   require a Blender install (they use `bpy`).

The unit tests cover the pieces of the pipeline that have well-defined
mathematical behavior: the quaternion / vector ops, the D3DX → Blender
coordinate conversion, the JSON `meta` block, the pose-local matrix
derivation, the bone-roll round-trip, the RDP decimation, the
quaternion component reorder, and the static-channel detection.

## Layout

```
tests/
├── cpp/
│   ├── test_x_math.cpp          XMath::conjugate / multiply / rotate
│   ├── test_coord.cpp           convertMatrixToBlender (D3DX required)
│   └── test_json_exporter.cpp   JSON meta block + round-trip
├── python/
│   ├── test_math_utils.py       mat4_to_mathutils, pose_local_matrix, roll round-trip
│   └── test_animation.py        RDP, perpendicular distance, static-channel, quaternion reorder
└── fixtures/
    └── minimal.x                synthetic .x for round-trip smoke tests
```

## C++ unit tests

### Prerequisites

- A C++17 compiler (g++ 8+ or clang++ 7+).
- The MinGW + DirectX 9 SDK toolchain is only required for
  `test_coord.cpp`; the other two tests build natively on Linux.

### Running

The tests are wired into CMake behind the `BUILD_TESTS` option.

```bash
# Build + run all C++ tests (native Linux, no D3DX):
mkdir build && cd build
cmake .. -DBUILD_TESTS=ON
make
ctest --output-on-failure

# Build + run all C++ tests under MinGW cross-compile (includes test_coord):
BUILD_TESTS=1 ./build.sh
# (the script invokes ctest automatically when BUILD_TESTS=1)
```

Under the MinGW cross-compile, `test_coord` is added to ctest only when
`<d3dx9.h>` is found by CMake's `check_include_file_cxx` probe.  On a
native Linux host without the DirectX headers, `test_coord` is silently
skipped (the file still compiles via an `#ifdef X2BLEND_HAVE_D3DX`
stub that prints `SKIPPED (no D3DX)`).

### Running a single C++ test manually

```bash
# test_x_math (no D3D deps):
g++ -std=c++17 -Wall -Wextra -Isrc \
    tests/cpp/test_x_math.cpp src/core/x_math.cpp \
    -o test_x_math
./test_x_math

# test_json_exporter (no D3D deps):
g++ -std=c++17 -Wall -Wextra -Isrc \
    tests/cpp/test_json_exporter.cpp \
    src/core/middleman.cpp src/core/x_math.cpp src/core/log.cpp \
    src/io/json_exporter.cpp \
    -o test_json_exporter
./test_json_exporter

# test_coord (MinGW + DX9 SDK only):
x86_64-w64-mingw32-g++ -std=c++17 -Wall -Wextra -Isrc -DX2BLEND_HAVE_D3DX=1 \
    tests/cpp/test_coord.cpp src/core/coord.cpp \
    -o test_coord.exe -ld3d9 -ld3dx9 -ldxguid
wine ./test_coord.exe
```

Each test binary prints `ok:` lines for passing assertions, `FAIL:`
lines for failures, and a final `PASS: all assertions held` (exit 0) or
`FAIL: N assertion(s) failed` (exit non-zero).

## Python unit tests

### Prerequisites

- Python 3.8 or newer.
- `pytest` (any modern version; 7.x or 8.x recommended).
- `mathutils` (the standalone Python wheel; `pip install mathutils`).
  This is the **only** third-party dependency.  `bpy` is NOT required —
  the Python tests stub out `bpy` so they can run anywhere.

### Running

```bash
pip install pytest mathutils
pytest tests/python/ -v
```

Or, to run a single file:

```bash
pytest tests/python/test_math_utils.py -v
pytest tests/python/test_animation.py -v
```

### What the Python tests cover

- `test_math_utils.py`:
  - `mat4_to_mathutils` identity, translation transpose, and
    round-trip with `mathutils.Matrix`.
  - `pose_local_matrix` with / without parent (identity, pure
    translation, relative translation, singular-input fallback).
  - `vec_roll_to_mat3` default-axis and antiparallel-axis paths.
  - `mat3_to_roll` zero-roll and nonzero-roll round-trips.
- `test_animation.py`:
  - `_rdp` short inputs, collinear collapse, outlier preservation,
    endpoint preservation, strict-subsequence property.
  - `_perp_distance` horizontal / vertical / degenerate / on-line.
  - `_matrices_equal` identical / within-tolerance / outside-tolerance.
  - `_is_static_channel` identical / differing / single-key / empty /
    config-tolerance.
  - `_write_trs_channel` quaternion component reorder (identity and
    90° Y rotation), time-to-frame conversion, translation-key
    writing, scale-key writing.

## What is NOT covered by unit tests

- The full C++/Wine → JSON → bpy pipeline (requires Wine + Blender +
  real `.x` fixtures).
- D3DX mesh hierarchy loading (requires a Windows D3D9 device).
- bpy-side armature / mesh / animation building (requires a Blender
  install).

For end-to-end validation, use the verification scripts under
`scripts/verify/` against a real `.x` file:

```bash
./x2blend.sh model.x output.blend
blender --background --python scripts/verify/verify_bones.py -- model.json output.blend
blender --background --python scripts/verify/verify_animation_poses.py -- model.json output.blend
```

These scripts numerically compare bone rest-pose matrices and animation
pose matrices against the source `.x` (1e-4 units / 0.5° for bones,
1e-3 units / 0.1° for animations).
