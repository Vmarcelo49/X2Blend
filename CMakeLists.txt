cmake_minimum_required(VERSION 3.15)
project(x2blend CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# Add compiler flags for warnings and static linking
if(MSVC)
    add_compile_options(/W4)
else()
    add_compile_options(-Wall -Wextra)
    # Enable static linking of libstdc++ and libgcc so the executable is self-contained under Wine
    set(CMAKE_EXE_LINKER_FLAGS "${CMAKE_EXE_LINKER_FLAGS} -static-libgcc -static-libstdc++ -static")
endif()

# Shared object / loader sources used by both targets
set(COMMON_SOURCES
    src/middleman.h
    src/middleman.cpp
    src/x_loader.h
    src/x_loader.cpp
)

# ---------------------------------------------------------------------------
# Target: x2blend  (JSON exporter → Python bpy pipeline)
# ---------------------------------------------------------------------------
set(BLEND_SOURCES
    src/main.cpp
    src/json_exporter.h
    src/json_exporter.cpp
    ${COMMON_SOURCES}
)
add_executable(x2blend ${BLEND_SOURCES})

# Target libraries
# d3d9: Direct3D 9 core API
# d3dx9: Direct3D 9 Extension library (mesh loading, skinning, animations)
# dxguid: DirectX GUID definitions for COM interfaces
target_link_libraries(x2blend d3d9 d3dx9 dxguid)

# ---------------------------------------------------------------------------
# Target: viewer (DirectX 9 model and animation viewer)
# ---------------------------------------------------------------------------
add_executable(viewer src/viewer.cpp)
target_link_libraries(viewer d3d9 d3dx9 dxguid)

