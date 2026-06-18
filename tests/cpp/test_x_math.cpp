// test_x_math.cpp — Unit tests for core/x_math.h (conjugate, multiply, rotate).
//
// No GoogleTest.  Uses <cassert> + a small main() that prints PASS/FAIL
// and returns 0 on success, non-zero on any failure.
//
// Build (standalone, native C++17):
//   g++ -std=c++17 -Wall -Wextra -Isrc tests/cpp/test_x_math.cpp 
//       src/core/x_math.cpp -o test_x_math
//   ./test_x_math
//
// Or via CMake (BUILD_TESTS=ON):  ctest -R test_x_math
#include <cassert>
#include <cmath>
#include <cstdio>
#include <iostream>
#include <string>

#include "core/middleman.h"
#include "core/x_math.h"

// ---------------------------------------------------------------------------
// Tiny floating-point comparison helper (the values we test are exact
// multiples of 0, 1, 0.5, and sqrt(2)/2, so a 1e-6 tolerance is plenty).
// ---------------------------------------------------------------------------
static bool approx(float a, float b, float tol = 1e-6f) {
    return std::fabs(a - b) <= tol;
}

static bool vec_eq(const XVector3& a, float x, float y, float z, float tol = 1e-6f) {
    return approx(a.x, x, tol) && approx(a.y, y, tol) && approx(a.z, z, tol);
}

static bool quat_eq(const XQuaternion& q, float x, float y, float z, float w,
                    float tol = 1e-6f) {
    return approx(q.x, x, tol) && approx(q.y, y, tol)
        && approx(q.z, z, tol) && approx(q.w, w, tol);
}

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

// ---------------------------------------------------------------------------
// conjugate
// ---------------------------------------------------------------------------
static void test_conjugate_identity() {
    XQuaternion id{0.0f, 0.0f, 0.0f, 1.0f};
    XQuaternion r = XMath::conjugate(id);
    CHECK(quat_eq(r, 0.0f, 0.0f, 0.0f, 1.0f),
          "conjugate(identity) == identity");
}

static void test_conjugate_pure_imaginary() {
    XQuaternion q{1.0f, 2.0f, 3.0f, 4.0f};
    XQuaternion r = XMath::conjugate(q);
    CHECK(quat_eq(r, -1.0f, -2.0f, -3.0f, 4.0f),
          "conjugate negates the imaginary part, keeps w");
}

// ---------------------------------------------------------------------------
// multiply
// ---------------------------------------------------------------------------
static void test_multiply_identity() {
    XQuaternion id{0.0f, 0.0f, 0.0f, 1.0f};
    XQuaternion q{1.0f, 2.0f, 3.0f, 4.0f};
    XQuaternion r = XMath::multiply(id, q);
    CHECK(quat_eq(r, 1.0f, 2.0f, 3.0f, 4.0f),
          "multiply(identity, q) == q");

    XQuaternion r2 = XMath::multiply(q, id);
    CHECK(quat_eq(r2, 1.0f, 2.0f, 3.0f, 4.0f),
          "multiply(q, identity) == q");
}

static void test_multiply_two_90deg_Y_is_180deg_Y() {
    // A 90-degree rotation around Y:  q = (0, sin(45), 0, cos(45))
    const float s = std::sqrt(2.0f) / 2.0f;
    XQuaternion q{0.0f, s, 0.0f, s};
    XQuaternion r = XMath::multiply(q, q);

    // Result should be a 180-degree rotation around Y:
    // (0, sin(90), 0, cos(90)) = (0, 1, 0, 0)
    CHECK(quat_eq(r, 0.0f, 1.0f, 0.0f, 0.0f),
          "multiply(90Y, 90Y) == 180Y");
}

static void test_multiply_90Y_then_90Z() {
    // q1 = 90-degree Y, q2 = 90-degree Z.
    // q1 * q2 should equal a 120-degree rotation around the (1,1,1)/sqrt(3)
    // axis: q = (0.5, 0.5, 0.5, 0.5) (Hamilton product convention).
    const float s = std::sqrt(2.0f) / 2.0f;
    XQuaternion q1{0.0f, s, 0.0f, s};  // 90 Y
    XQuaternion q2{0.0f, 0.0f, s, s};  // 90 Z
    XQuaternion r = XMath::multiply(q1, q2);
    CHECK(quat_eq(r, 0.5f, 0.5f, 0.5f, 0.5f),
          "multiply(90Y, 90Z) == (0.5, 0.5, 0.5, 0.5) [120 around (1,1,1)]");
}

// ---------------------------------------------------------------------------
// rotate
// ---------------------------------------------------------------------------
static void test_rotate_identity_leaves_vector() {
    XQuaternion id{0.0f, 0.0f, 0.0f, 1.0f};
    XVector3 v{1.0f, 2.0f, 3.0f};
    XVector3 r = XMath::rotate(id, v);
    CHECK(vec_eq(r, 1.0f, 2.0f, 3.0f),
          "rotate(identity, v) == v");
}

static void test_rotate_90Y_maps_X_to_negZ() {
    // 90-degree rotation around Y maps +X to -Z (right-hand rule).
    const float s = std::sqrt(2.0f) / 2.0f;
    XQuaternion q{0.0f, s, 0.0f, s};
    XVector3 v{1.0f, 0.0f, 0.0f};
    XVector3 r = XMath::rotate(q, v);
    CHECK(vec_eq(r, 0.0f, 0.0f, -1.0f),
          "rotate(90Y, +X) == -Z");
}

static void test_rotate_90Y_maps_Z_to_X() {
    // 90-degree rotation around Y maps +Z to +X.
    const float s = std::sqrt(2.0f) / 2.0f;
    XQuaternion q{0.0f, s, 0.0f, s};
    XVector3 v{0.0f, 0.0f, 1.0f};
    XVector3 r = XMath::rotate(q, v);
    CHECK(vec_eq(r, 1.0f, 0.0f, 0.0f),
          "rotate(90Y, +Z) == +X");
}

static void test_rotate_90Y_preserves_Y() {
    // A rotation around Y preserves the Y axis.
    const float s = std::sqrt(2.0f) / 2.0f;
    XQuaternion q{0.0f, s, 0.0f, s};
    XVector3 v{0.0f, 5.0f, 0.0f};
    XVector3 r = XMath::rotate(q, v);
    CHECK(vec_eq(r, 0.0f, 5.0f, 0.0f),
          "rotate(90Y, +Y) == +Y (axis preserved)");
}

static void test_rotate_180X_flips_Y_and_Z() {
    // 180-degree rotation around X:  q = (1, 0, 0, 0).
    XQuaternion q{1.0f, 0.0f, 0.0f, 0.0f};
    XVector3 v{0.0f, 1.0f, 1.0f};
    XVector3 r = XMath::rotate(q, v);
    CHECK(vec_eq(r, 0.0f, -1.0f, -1.0f),
          "rotate(180X, (0,1,1)) == (0,-1,-1)");
}

// ---------------------------------------------------------------------------
// main
// ---------------------------------------------------------------------------
int main() {
    std::cout << "=== test_x_math ===" << std::endl;

    test_conjugate_identity();
    test_conjugate_pure_imaginary();

    test_multiply_identity();
    test_multiply_two_90deg_Y_is_180deg_Y();
    test_multiply_90Y_then_90Z();

    test_rotate_identity_leaves_vector();
    test_rotate_90Y_maps_X_to_negZ();
    test_rotate_90Y_maps_Z_to_X();
    test_rotate_90Y_preserves_Y();
    test_rotate_180X_flips_Y_and_Z();

    if (g_failures == 0) {
        std::cout << "PASS: all assertions held" << std::endl;
        return 0;
    }
    std::cout << "FAIL: " << g_failures << " assertion(s) failed" << std::endl;
    return 1;
}
