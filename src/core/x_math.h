// x_math.h — Pure quaternion / vector math, no D3D dependency.
//
// Declares the `XMath` namespace with the three operations the refactored
// loader needs for relative-keyframe computation: quaternion conjugate,
// quaternion product, and quaternion-vector rotation.  The implementations
// in x_math.cpp are line-for-line ports of the original `AnimMath` struct
// (x_loader.cpp lines 620-642) — only the spelling changed (static methods
// on a struct -> free functions in a namespace).
#pragma once

#include "core/middleman.h"

namespace XMath {

// Quaternion conjugate: negates the imaginary part, keeps w.
XQuaternion conjugate(const XQuaternion& q);

// Hamilton product q1 * q2.
XQuaternion multiply(const XQuaternion& q1, const XQuaternion& q2);

// Rotate vector v by quaternion q via v' = q * (0,v) * q*.
XVector3 rotate(const XQuaternion& q, const XVector3& v);

} // namespace XMath
