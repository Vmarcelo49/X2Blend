#!/bin/bash
set -e

# Define directories
BUILD_DIR="build"

echo "=== Creating Build Directory ==="
mkdir -p "$BUILD_DIR"
cd "$BUILD_DIR"

echo "=== Running CMake for Windows Target (MinGW Cross-Compilation) ==="
# Configure CMake using llvm-mingw compilers
cmake .. \
    -DCMAKE_SYSTEM_NAME=Windows \
    -DCMAKE_CXX_COMPILER=x86_64-w64-mingw32-g++ \
    -DCMAKE_C_COMPILER=x86_64-w64-mingw32-gcc \
    -DCMAKE_BUILD_TYPE=Release

echo "=== Compiling Project ==="
make VERBOSE=1

echo "=== Build Successful! x2blend.exe is ready ==="
ls -la x2blend.exe
