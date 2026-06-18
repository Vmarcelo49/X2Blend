// coord.cpp — Coordinate-system conversion implementation.
//
// Line-for-line port of the original `convertMatrixToBlender` helper from
// x_loader.cpp (lines 95-104).  The mapping table `int map[4] = {0, 2, 1,
// 3}` swaps rows/cols 1 and 2 (the Y and Z axes) while leaving the W row
// and column (index 3) untouched.  The math is preserved exactly so the
// refactored pipeline produces byte-identical matrix bytes for any given
// D3DXMATRIX input.
#include "core/coord.h"

XMatrix4x4 convertMatrixToBlender(const D3DXMATRIX& mat) {
    XMatrix4x4 out;
    int map[4] = {0, 2, 1, 3}; // Swap Y (1) and Z (2) for row-major matrix
    for (int r = 0; r < 4; ++r) {
        for (int c = 0; c < 4; ++c) {
            out.m[map[r]][map[c]] = mat.m[r][c];
        }
    }
    return out;
}
