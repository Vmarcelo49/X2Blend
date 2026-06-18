cmake_minimum_required(VERSION 3.15)
project(x2blend CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# ---------------------------------------------------------------------------
# Compiler flags: warnings everywhere, static-linked MinGW runtime so the
# resulting .exe files are self-contained under Wine.
# ---------------------------------------------------------------------------
if(MSVC)
    add_compile_options(/W4)
else()
    add_compile_options(-Wall -Wextra)
    # Static-link libstdc++ and libgcc so the executable is self-contained
    # under Wine (no MinGW runtime DLLs needed on the Wine prefix).
    set(CMAKE_EXE_LINKER_FLAGS
        "${CMAKE_EXE_LINKER_FLAGS} -static-libgcc -static-libstdc++ -static")
endif()

# ---------------------------------------------------------------------------
# Source groups.  Kept as flat lists so the dependency structure is
# obvious:  core <- io, d3d <- loader, all <- main.
# ---------------------------------------------------------------------------

set(CORE_SOURCES
    src/core/middleman.cpp
    src/core/x_math.cpp
    src/core/coord.cpp
    src/core/codec.cpp
    src/core/log.cpp
)

set(IO_SOURCES
    src/io/x_file_preprocessor.cpp
    src/io/json_exporter.cpp
)

set(D3D_SOURCES
    src/d3d/d3d_context.cpp
    src/d3d/hierarchy_allocator.cpp
)

set(LOADER_SOURCES
    src/loader/hierarchy_builder.cpp
    src/loader/mesh_extractor.cpp
    src/loader/animation_baker.cpp
    src/loader/x_loader.cpp
)

# Target libraries:
#   d3d9   — Direct3D 9 core API
#   d3dx9  — Direct3D 9 Extension library (mesh loading, skinning, anim)
#   dxguid — DirectX GUID definitions for COM interfaces

# ---------------------------------------------------------------------------
# Target: x2blend  (Stage 1 — .x -> JSON)
# ---------------------------------------------------------------------------
add_executable(x2blend
    src/main.cpp
    ${CORE_SOURCES}
    ${IO_SOURCES}
    ${D3D_SOURCES}
    ${LOADER_SOURCES}
)
target_include_directories(x2blend PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/src)
target_link_libraries(x2blend d3d9 d3dx9 dxguid)

# ---------------------------------------------------------------------------
# Target: viewer  (standalone DX9 previewer)
#
# The viewer is intentionally standalone-ish: it owns its own windowed D3D9
# init (HAL->REF fallback, depth-stencil, presentation swap chain) and its
# own ViewerAllocateHierarchy (which carries textures and skinned-mesh
# clones the headless HierarchyAllocator doesn't).  It reuses only the
# shared preprocessor (io/x_file_preprocessor), the Shift-JIS codec
# (core/codec), the leveled logger (core/log), and the pure-data model
# (core/middleman) from the refactor's shared modules.
#
# It does NOT link against loader/ or d3d/ modules.  Per Task 2's handoff
# notes, both viewer.cpp and loader/x_loader.cpp #define INITGUID at the
# top to instantiate IID_ID3DXKeyframedAnimationSet; linking both into the
# same executable would produce a duplicate-symbol error.  Keeping the
# viewer's D3D init inline (rather than reusing D3DContext) is also a
# deliberate choice: the viewer needs a real presentation swap chain,
# which the headless D3DContext cannot provide.  See docs/ARCHITECTURE.md
# for the module dependency graph.
# ---------------------------------------------------------------------------
add_executable(viewer
    src/viewer/viewer.cpp
    ${CORE_SOURCES}
    ${IO_SOURCES}
)
target_include_directories(viewer PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/src)
target_link_libraries(viewer d3d9 d3dx9 dxguid)

# ---------------------------------------------------------------------------
# Tests
#
# The non-D3D unit tests (x_math, json_exporter) can build natively on any
# C++17 toolchain.  The D3D test (test_coord) requires <d3dx9.h> and is
# therefore only built when the MinGW + DX9 SDK toolchain is in use; it
# is guarded by X2BLEND_HAVE_D3DX so the file still compiles for syntax
# checks on non-Windows hosts.
# ---------------------------------------------------------------------------
option(BUILD_TESTS "Build unit tests" OFF)

if(BUILD_TESTS)
    enable_testing()

    # test_x_math — pure quaternion/vector math, no D3DX dependency.
    add_executable(test_x_math
        tests/cpp/test_x_math.cpp
        src/core/x_math.cpp
    )
    target_include_directories(test_x_math PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/src)
    add_test(NAME test_x_math COMMAND test_x_math)

    # test_json_exporter — JSON serialization round-trip.  Links the
    # CORE + IO sources (minus coord.cpp / codec.cpp, which pull in
    # windows.h / d3dx9.h) so the test can build on any C++17 host.
    add_executable(test_json_exporter
        tests/cpp/test_json_exporter.cpp
        src/core/middleman.cpp
        src/core/x_math.cpp
        src/core/log.cpp
        src/io/json_exporter.cpp
    )
    target_include_directories(test_json_exporter PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/src)
    add_test(NAME test_json_exporter COMMAND test_json_exporter)

    # test_coord — coordinate-system conversion; requires <d3dx9.h>.
    # Skip on toolchains where d3dx9.h is not available (e.g. native
    # Linux GCC).  Detected by trying to compile a tiny #include probe.
    include(CheckIncludeFileCXX)
    set(CMAKE_REQUIRED_INCLUDES "")
    check_include_file_cxx(d3dx9.h X2BLEND_HAVE_D3DX)
    if(X2BLEND_HAVE_D3DX)
        add_executable(test_coord
            tests/cpp/test_coord.cpp
            src/core/coord.cpp
        )
        target_include_directories(test_coord PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/src)
        target_compile_definitions(test_coord PRIVATE X2BLEND_HAVE_D3DX=1)
        target_link_libraries(test_coord d3d9 d3dx9 dxguid)
        add_test(NAME test_coord COMMAND test_coord)
    else()
        message(STATUS "test_coord: d3dx9.h not found; test skipped (build with MinGW + DX9 SDK to enable).")
    endif()
endif()
