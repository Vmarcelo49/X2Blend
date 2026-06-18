// test_json_exporter.cpp — Unit test for io/json_exporter.
//
// Builds a minimal XModel in memory (one root node, one mesh, no
// animations), populates the `meta` block, exports it to a temp file,
// reads the file back, and asserts the `meta` and `bake_fps` keys are
// present (plus a few specific field checks).
//
// No GoogleTest.  Uses a custom CHECK macro + a main() that prints
// PASS/FAIL and returns 0 / non-zero.
//
// Build (standalone, native C++17 — no D3DX needed):
//   g++ -std=c++17 -Wall -Wextra -Isrc 
//       tests/cpp/test_json_exporter.cpp 
//       src/core/middleman.cpp src/core/x_math.cpp src/core/log.cpp 
//       src/io/json_exporter.cpp 
//       -o test_json_exporter
//   ./test_json_exporter
//
// Or via CMake (BUILD_TESTS=ON):  ctest -R test_json_exporter
#include <cassert>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iostream>
#include <sstream>
#include <string>

#if defined(_WIN32)
#  include <io.h>   // close() on Windows / MinGW
#else
#  include <unistd.h>  // close() on POSIX
#endif

#include "core/middleman.h"
#include "io/json_exporter.h"

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

// Read an entire file into a std::string.  Returns empty string on error.
static std::string read_file(const std::string& path) {
    std::ifstream f(path, std::ios::binary);
    std::ostringstream ss;
    ss << f.rdbuf();
    return ss.str();
}

// Returns true if `haystack` contains `needle`.
static bool contains(const std::string& haystack, const std::string& needle) {
    return haystack.find(needle) != std::string::npos;
}

// Build a minimal XModel with one root node and one tiny mesh.  The
// point of this test is the JSON shape, not the geometry.
static XModel make_minimal_model() {
    XModel model;

    // Meta block — matches the contract documented in docs/ARCHITECTURE.md.
    model.meta.sourceFile             = "tests/fixtures/minimal.x";
    model.meta.bakeMode               = "baked";
    model.meta.bakeFps                = 60.0f;
    model.meta.sourceTicksPerSecond   = 4800.0;
    model.meta.maxInfluences          = 4;
    model.meta.x2blendVersion         = "2.0.0-refactor";

    // One root node: identity transform, no children, no mesh attachment.
    XNode root;
    root.name             = "Root";
    root.parentIndex      = -1;
    root.meshIndex        = -1;
    root.isBone           = false;
    // localTransform defaults to identity; translation/rotation/scale
    // default to {0,0,0}, {0,0,0,1}, {1,1,1} respectively.
    model.nodes.push_back(root);
    model.rootNodeIndex = 0;

    // One mesh: a quad (4 vertices, 2 triangles, 1 material).
    XMesh mesh;
    mesh.name = "Quad";
    XVertex v0; v0.position = {0.0f, 0.0f, 0.0f}; v0.normal = {0.0f, 0.0f, 1.0f}; v0.texCoord = {0.0f, 0.0f, 0.0f};
    XVertex v1; v1.position = {1.0f, 0.0f, 0.0f}; v1.normal = {0.0f, 0.0f, 1.0f}; v1.texCoord = {1.0f, 0.0f, 0.0f};
    XVertex v2; v2.position = {1.0f, 1.0f, 0.0f}; v2.normal = {0.0f, 0.0f, 1.0f}; v2.texCoord = {1.0f, 1.0f, 0.0f};
    XVertex v3; v3.position = {0.0f, 1.0f, 0.0f}; v3.normal = {0.0f, 0.0f, 1.0f}; v3.texCoord = {0.0f, 1.0f, 0.0f};
    mesh.vertices = {v0, v1, v2, v3};
    mesh.indices  = {0, 1, 2, 0, 2, 3};
    XMaterial mat;
    mat.name             = "Mat0";
    mat.textureFilename  = "texture.png";
    mesh.materials.push_back(mat);
    mesh.faceMaterialIndices = {0, 0};
    mesh.hasSkin = false;
    model.meshes.push_back(mesh);

    // No animations for this fixture.
    return model;
}

int main() {
    std::cout << "=== test_json_exporter ===" << std::endl;

    XModel model = make_minimal_model();

    // Pick a temp output path.  Use mkstemp on Linux / tmpnam fallback.
    char tmpl[] = "/tmp/x2blend_json_test_XXXXXX";
    int fd = mkstemp(tmpl);
    CHECK(fd >= 0, "mkstemp created a temp file");
    if (fd < 0) {
        std::cout << "FAIL: cannot create temp file" << std::endl;
        return 1;
    }
    close(fd);  // we just want the path; JsonExporter opens it itself
    std::string out_path(tmpl);

    JsonExporter exporter;
    bool ok = exporter.exportToFile(model, out_path);
    CHECK(ok, "JsonExporter::exportToFile returned true");

    std::string content = read_file(out_path);
    CHECK(!content.empty(), "output file is non-empty");

    // --- Top-level shape ---
    CHECK(contains(content, "\"meta\":"),         "JSON contains a top-level \"meta\" key");
    CHECK(contains(content, "\"root_node_index\":"), "JSON contains a top-level \"root_node_index\" key");
    CHECK(contains(content, "\"nodes\":"),        "JSON contains a top-level \"nodes\" array");
    CHECK(contains(content, "\"meshes\":"),       "JSON contains a top-level \"meshes\" array");
    CHECK(contains(content, "\"animations\":"),   "JSON contains a top-level \"animations\" array");

    // --- meta block fields ---
    CHECK(contains(content, "\"source_file\":"),  "meta contains source_file");
    CHECK(contains(content, "\"bake_mode\":\"baked\""), "meta bake_mode == \"baked\"");
    CHECK(contains(content, "\"bake_fps\":60"),    "meta bake_fps == 60");
    CHECK(contains(content, "\"source_ticks_per_second\":4800"),
          "meta source_ticks_per_second == 4800 (emitted as integer)");
    CHECK(contains(content, "\"max_influences\":4"), "meta max_influences == 4");
    CHECK(contains(content, "\"x2blend_version\":\"2.0.0-refactor\""),
          "meta x2blend_version == \"2.0.0-refactor\"");

    // --- meta must appear before root_node_index (the contract) ---
    {
        size_t meta_pos     = content.find("\"meta\":");
        size_t root_idx_pos = content.find("\"root_node_index\":");
        CHECK(meta_pos != std::string::npos && root_idx_pos != std::string::npos
                  && meta_pos < root_idx_pos,
              "meta block appears before root_node_index");
    }

    // --- dead-code check: the old `use_trs` field must NOT be emitted ---
    CHECK(!contains(content, "use_trs"),
          "use_trs field is NOT emitted (XNode::useTRS was removed)");

    // --- round-trip smoke: the texture filename we set must appear ---
    CHECK(contains(content, "\"texture.png\""),
          "material texture filename appears in the JSON");

    // Clean up.
    std::remove(out_path.c_str());

    if (g_failures == 0) {
        std::cout << "PASS: all assertions held" << std::endl;
        return 0;
    }
    std::cout << "FAIL: " << g_failures << " assertion(s) failed" << std::endl;
    return 1;
}
