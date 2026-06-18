// coord.h — Coordinate-system conversion.
//
// Declares the single helper that converts a D3DX row-major left-handed
// Y-up matrix into the Middleman's XMatrix4x4 (which the downstream
// Python importer treats as Blender's right-handed Z-up space).  The
// conversion is a pure Y<->Z axis swap; the math is preserved verbatim
// from the original x_loader.cpp (lines 95-104).
//
// This header intentionally includes <d3dx9.h>: the input type is
// D3DXMATRIX, which only exists in the Direct3D 9 utility library.
#pragma once

#include <d3dx9.h>

#include "core/middleman.h"

// Converts a D3DXMATRIX (row-major, left-handed Y-up) to an XMatrix4x4
// (Blender space, right-handed Z-up) by swapping the Y and Z axes.
XMatrix4x4 convertMatrixToBlender(const D3DXMATRIX& mat);
