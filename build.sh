#!/usr/bin/env bash
# build.sh — MinGW cross-compile x2blend.exe and viewer.exe.
#
# Adapted from the original X2Blend/build.sh.  Produces two Windows
# executables in build/ that are self-contained (statically linked
# against libstdc++ / libgcc) so they run cleanly under Wine 9.0+.
#
# Environment:
#   BUILD_TESTS=1   also build the C++ unit tests (off by default).
#
# Toolchain assumed on PATH:
#   x86_64-w64-mingw32-gcc / x86_64-w64-mingw32-g++  (MinGW 14.2.0 or newer)
#   cmake (>= 3.15)
#   make
set -euo pipefail

BUILD_DIR="build"

echo "=== Creating build directory: ${BUILD_DIR} ==="
mkdir -p "${BUILD_DIR}"
cd "${BUILD_DIR}"

CMAKE_ARGS=(
    -DCMAKE_SYSTEM_NAME=Windows
    -DCMAKE_CXX_COMPILER=x86_64-w64-mingw32-g++
    -DCMAKE_C_COMPILER=x86_64-w64-mingw32-gcc
    -DCMAKE_BUILD_TYPE=Release
)

if [[ "${BUILD_TESTS:-0}" == "1" ]]; then
    echo "=== BUILD_TESTS=1: enabling unit test build ==="
    CMAKE_ARGS+=("-DBUILD_TESTS=ON")
else
    CMAKE_ARGS+=("-DBUILD_TESTS=OFF")
fi

echo "=== Running CMake for Windows target (MinGW cross-compilation) ==="
cmake .. "${CMAKE_ARGS[@]}"

echo "=== Compiling project ==="
make VERBOSE=1

echo "=== Build successful ==="
ls -la x2blend.exe viewer.exe

if [[ "${BUILD_TESTS:-0}" == "1" ]]; then
    echo "=== Running unit tests via ctest ==="
    ctest --output-on-failure
fi
