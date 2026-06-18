// middleman.cpp — Implementation file for the Middleman data model.
//
// The data model in middleman.h is pure struct declarations with no
// out-of-line methods, so there is nothing to compile here.  This file
// exists for two reasons: (1) the original project shipped an empty
// middleman.cpp and CMake still lists it in COMMON_SOURCES, and (2) it
// gives future maintainers an obvious place to add free-helper functions
// that operate directly on the Middleman structs (e.g. traversal helpers).
//
// The quaternion / vector math helpers that used to live next to the
// Middleman types in the original project (the `AnimMath` struct in
// x_loader.cpp) have been relocated to core/x_math.{h,cpp} so that the
// math has no D3D dependency and can be unit-tested in isolation.
#include "core/middleman.h"
