// x_math.cpp — Pure quaternion / vector math, no D3D dependency.
//
// Line-for-line port of the original `AnimMath` struct in x_loader.cpp
// (lines 620-642).  The math is preserved exactly: same formulas, same
// operation order, same float intermediates.  Only the spelling changed
// (struct static methods -> free functions in the XMath namespace).
#include "core/x_math.h"

namespace XMath {

XQuaternion conjugate(const XQuaternion& q) {
    return { -q.x, -q.y, -q.z, q.w };
}

XQuaternion multiply(const XQuaternion& q1, const XQuaternion& q2) {
    XQuaternion out;
    out.w = q1.w * q2.w - q1.x * q2.x - q1.y * q2.y - q1.z * q2.z;
    out.x = q1.w * q2.x + q1.x * q2.w + q1.y * q2.z - q1.z * q2.y;
    out.y = q1.w * q2.y - q1.x * q2.z + q1.y * q2.w + q1.z * q2.x;
    out.z = q1.w * q2.z + q1.x * q2.y - q1.y * q2.x + q1.z * q2.w;
    return out;
}

XVector3 rotate(const XQuaternion& q, const XVector3& v) {
    // Rotate vector v by quaternion q: v' = q * (0, v) * q*
    XQuaternion qv = { v.x, v.y, v.z, 0.0f };
    XQuaternion q_conj = conjugate(q);
    XQuaternion temp = multiply(q, qv);
    XQuaternion rotated = multiply(temp, q_conj);
    return { rotated.x, rotated.y, rotated.z };
}

} // namespace XMath
