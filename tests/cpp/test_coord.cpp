// test_coord.cpp — Unit test for core/coord.h (convertMatrixToBlender).
//
// The function under test takes a D3DXMATRIX (row-major, left-handed,
// Y-up) and returns an XMatrix4x4 with the Y and Z axes swapped (so
// the result is in Blender's right-handed Z-up space).  The mapping
// table is `int map[4] = {0, 2, 1, 3}` — rows/cols 1 and 2 are swapped,
// the W row/col (index 3) is untouched.
//
// This test only compiles and runs when `<d3dx9.h>` is available
// (i.e. under the MinGW + DX9 SDK toolchain).  On other toolchains
// the test prints "SKIPPED (no D3DX)" and exits 0 so that ctest can
// run on native Linux without breaking.
//
// Build (MinGW):
//   x86_64-w64-mingw32-g++ -std=c++17 -Wall -Wextra -Isrc 
//       -DX2BLEND_HAVE_D3DX=1 
//       tests/cpp/test_coord.cpp src/core/coord.cpp 
//       -o test_coord.exe -ld3d9 -ld3dx9 -ldxguid
//   wine ./test_coord.exe
//
// Or via CMake (BUILD_TESTS=ON under MinGW):  ctest -R test_coord
#include <cmath>
#include <iostream>
#include <string>

#include "core/middleman.h"

#if defined(X2BLEND_HAVE_D3DX)
#  include <d3dx9.h>
#  include "core/coord.h"
#endif

#if defined(X2BLEND_HAVE_D3DX)
static int g_failures = 0;

#define CHECK(cond, msg) \
    do { \
        if (!(cond)) { \
            std::cerr << "FAIL: " << (msg) << " (line " << __LINE__ << ")" << std::endl; \
            ++g_failures; \
        } else { \
            std::cout << "ok:   " << (msg) << std::endl; \
        } \
    } while (0)
#endif

#if defined(X2BLEND_HAVE_D3DX)

// ---------------------------------------------------------------------------
// Helpers — D3DXMATRIX is a thin wrapper around a 16-float array; we
// build the test matrices by hand to keep the assertions readable.
// ---------------------------------------------------------------------------
static D3DXMATRIX make_identity() {
    D3DXMATRIX m;
    for (int r = 0; r < 4; ++r)
        for (int c = 0; c < 4; ++c)
            m.m[r][c] = (r == c) ? 1.0f : 0.0f;
    return m;
}

// Build a D3DXMATRIX from a 4x4 C-array of floats (row-major).
static D3DXMATRIX from_rows(const float rows[4][4]) {
    D3DXMATRIX m;
    for (int r = 0; r < 4; ++r)
        for (int c = 0; c < 4; ++c)
            m.m[r][c] = rows[r][c];
    return m;
}

static bool approx(float a, float b, float tol = 1e-6f) {
    return std::fabs(a - b) <= tol;
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

// Identity matrix: the Y<->Z swap is a no-op on the identity, so the
// output should still be the identity.
static void test_identity_is_unchanged() {
    D3DXMATRIX in = make_identity();
    XMatrix4x4 out = convertMatrixToBlender(in);
    int identity_ok = 1;
    for (int r = 0; r < 4; ++r) {
        for (int c = 0; c < 4; ++c) {
            float expected = (r == c) ? 1.0f : 0.0f;
            if (!approx(out.m[r][c], expected)) {
                identity_ok = 0;
                std::cerr << "  identity mismatch at [" << r << "][" << c
                          << "]: got " << out.m[r][c]
                          << ", expected " << expected << std::endl;
            }
        }
    }
    CHECK(identity_ok, "identity matrix is unchanged by the Y<->Z swap");
}

// Pure translation: translation lives in the last ROW of a D3DXMATRIX
// (row-vector convention, v * M).  After the Y<->Z swap, rows 1 and 2
// of the translation row swap, so a translation of (tx, ty, tz) becomes
// (tx, tz, ty) in the output.  (Equivalently: in Blender's Z-up space,
// what D3DX calls +Y is +Z and vice versa.)
static void test_translation_swaps_y_and_z() {
    float rows[4][4] = {
        {1, 0, 0, 0},
        {0, 1, 0, 0},
        {0, 0, 1, 0},
        {10, 20, 30, 1}     // translation row: (tx, ty, tz) = (10, 20, 30)
    };
    D3DXMATRIX in = from_rows(rows);
    XMatrix4x4 out = convertMatrixToBlender(in);

    // Output translation row (index 3): (tx, tz, ty, 1) = (10, 30, 20, 1).
    CHECK(approx(out.m[3][0], 10.0f), "translation: out.m[3][0] == tx");
    CHECK(approx(out.m[3][1], 30.0f), "translation: out.m[3][1] == tz (swapped from row 2)");
    CHECK(approx(out.m[3][2], 20.0f), "translation: out.m[3][2] == ty (swapped from row 1)");
    CHECK(approx(out.m[3][3],  1.0f), "translation: out.m[3][3] == 1 (W row preserved)");
}

// Pure scale: same idea — the diagonal entries at (1,1) and (2,2) swap.
static void test_scale_swaps_y_and_z() {
    float rows[4][4] = {
        {2, 0, 0, 0},
        {0, 3, 0, 0},      // Y scale = 3
        {0, 0, 5, 0},      // Z scale = 5
        {0, 0, 0, 1}
    };
    D3DXMATRIX in = from_rows(rows);
    XMatrix4x4 out = convertMatrixToBlender(in);

    CHECK(approx(out.m[0][0], 2.0f), "scale: out.m[0][0] == 2 (X preserved)");
    CHECK(approx(out.m[1][1], 5.0f), "scale: out.m[1][1] == 5 (was Z, now Y)");
    CHECK(approx(out.m[2][2], 3.0f), "scale: out.m[2][2] == 3 (was Y, now Z)");
    CHECK(approx(out.m[3][3], 1.0f), "scale: out.m[3][3] == 1 (W preserved)");
}

// Off-diagonal rotation: a 90-degree rotation around Y in D3DX
// (left-handed Y-up) has the form:
//      [ cos  0  sin  0 ]
//      [  0   1   0   0 ]
//      [ -sin 0  cos  0 ]
//      [  0   0   0   1 ]
// After the Y<->Z swap, the entries at positions (0,2) and (2,0) move
// to (0,1) and (1,0) respectively, and the entries at (1,*) / (*,1)
// move to (2,*) / (*,2).  We just check the swap pattern, not the
// geometric interpretation.
static void test_off_diagonal_swaps_correctly() {
    float rows[4][4] = {
        { 0,  0,  1, 0},   // row 0
        { 0,  1,  0, 0},   // row 1 (Y axis)
        {-1,  0,  0, 0},   // row 2 (Z axis)
        { 0,  0,  0, 1}
    };
    D3DXMATRIX in = from_rows(rows);
    XMatrix4x4 out = convertMatrixToBlender(in);

    // map[r][c] == out.m[map[r]][map[c]] == in.m[r][c], so:
    //   in.m[0][2] == 1 -> out.m[map[0]][map[2]] = out.m[0][1] == 1
    //   in.m[2][0] == -1 -> out.m[map[2]][map[0]] = out.m[1][0] == -1
    //   in.m[1][1] == 1 -> out.m[map[1]][map[1]] = out.m[2][2] == 1
    CHECK(approx(out.m[0][1],  1.0f), "off-diag: in.m[0][2] -> out.m[0][1]");
    CHECK(approx(out.m[1][0], -1.0f), "off-diag: in.m[2][0] -> out.m[1][0]");
    CHECK(approx(out.m[2][2],  1.0f), "off-diag: in.m[1][1] -> out.m[2][2]");
}

int main() {
    std::cout << "=== test_coord (D3DX path) ===" << std::endl;
    test_identity_is_unchanged();
    test_translation_swaps_y_and_z();
    test_scale_swaps_y_and_z();
    test_off_diagonal_swaps_correctly();

    if (g_failures == 0) {
        std::cout << "PASS: all assertions held" << std::endl;
        return 0;
    }
    std::cout << "FAIL: " << g_failures << " assertion(s) failed" << std::endl;
    return 1;
}

#else  // !X2BLEND_HAVE_D3DX

// ---------------------------------------------------------------------------
// Stub fallback: the file compiles for syntax checks on hosts without
// <d3dx9.h>, but the test is a no-op (ctest will see exit code 0 and
// "SKIPPED (no D3DX)" in the output).  The CMakeLists.txt only adds
// this test to ctest when X2BLEND_HAVE_D3DX is set, so this branch is
// only reached when invoking the compiler directly without the macro.
// ---------------------------------------------------------------------------
int main() {
    std::cout << "=== test_coord ===" << std::endl;
    std::cout << "SKIPPED (no D3DX): <d3dx9.h> not available. "
                 "Build under MinGW + DX9 SDK to enable this test."
              << std::endl;
    return 0;
}

#endif  // X2BLEND_HAVE_D3DX
