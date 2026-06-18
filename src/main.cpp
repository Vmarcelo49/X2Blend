// main.cpp — Slim CLI entry point for x2blend.exe.
//
// Usage:
//   x2blend.exe <input.x> <output.json>
//               [--no-bake]
//               [--bake-fps N]
//               [--max-influences N]
//               [--log-level <debug|info|warn|error>]
//
// Defaults: --bake-fps 60, --max-influences 4, --log-level info,
// baking enabled.  The JSON output is consumed by
// scripts/blend_importer to produce the final .blend file via Blender
// headless bpy.
//
// Exit codes:
//   0  success
//   1  usage error (missing / invalid arguments)
//   2  load failure (D3D / D3DX / file I/O)
//   3  JSON write failure
#include <cmath>
#include <iostream>
#include <limits>
#include <string>

#include "core/log.h"
#include "io/json_exporter.h"
#include "loader/x_loader.h"

static void printUsage() {
    std::cout << "=======================================================\n";
    std::cout << " x2blend — DirectX .x Model to Blender JSON Converter\n";
    std::cout << "=======================================================\n\n";
    std::cout << "Usage: x2blend.exe <input.x> <output.json>\n";
    std::cout << "                        [--no-bake]\n";
    std::cout << "                        [--bake-fps N]\n";
    std::cout << "                        [--max-influences N]\n";
    std::cout << "                        [--log-level <debug|info|warn|error>]\n\n";
    std::cout << "Defaults: baking ON at 60 FPS, max 4 influences, log level info.\n";
    std::cout << "The JSON output is consumed by scripts/blend_importer\n";
    std::cout << "to produce the final .blend file via Blender headless bpy.\n\n";
    std::cout << "Run the full pipeline with:\n";
    std::cout << "  ./x2blend.sh input.x output.blend\n\n";
}

// Parses a non-negative integer from `s` into `out`.  Returns false on
// malformed input or values that don't fit in an int.
static bool parseInt(const std::string& s, int& out) {
    if (s.empty()) return false;
    try {
        size_t pos = 0;
        long v = std::stol(s, &pos, 10);
        if (pos != s.size()) return false;
        if (v < 0 || v > static_cast<long>(std::numeric_limits<int>::max())) return false;
        out = static_cast<int>(v);
        return true;
    } catch (...) {
        return false;
    }
}

// Parses a positive float from `s` into `out`.
static bool parseFloat(const std::string& s, float& out) {
    if (s.empty()) return false;
    try {
        size_t pos = 0;
        float v = std::stof(s, &pos);
        if (pos != s.size()) return false;
        if (v <= 0.0f || !std::isfinite(v)) return false;
        out = v;
        return true;
    } catch (...) {
        return false;
    }
}

// Maps a log-level token to a LogLevel.  Returns false on unknown.
static bool parseLogLevel(const std::string& s, LogLevel& out) {
    if (s == "debug") { out = LogLevel::Debug; return true; }
    if (s == "info")  { out = LogLevel::Info;  return true; }
    if (s == "warn")  { out = LogLevel::Warn;  return true; }
    if (s == "error") { out = LogLevel::Error; return true; }
    return false;
}

int main(int argc, char* argv[]) {
    if (argc < 3) {
        printUsage();
        return 1;
    }

    std::string inputPath  = argv[1];
    std::string outputJson = argv[2];

    LoaderOptions opts;            // defaults: bake=true, fps=60, max=4
    LogLevel      level = LogLevel::Info;

    // Parse optional flags from index 3 onward.
    for (int i = 3; i < argc; ++i) {
        std::string arg = argv[i];
        if (arg == "--no-bake") {
            opts.bake = false;
        } else if (arg == "--bake-fps") {
            if (i + 1 >= argc) {
                std::cerr << "[Error] --bake-fps requires a value.\n";
                printUsage();
                return 1;
            }
            float v;
            if (!parseFloat(argv[++i], v)) {
                std::cerr << "[Error] Invalid --bake-fps value: " << argv[i] << "\n";
                return 1;
            }
            opts.bakeFps = v;
        } else if (arg == "--max-influences") {
            if (i + 1 >= argc) {
                std::cerr << "[Error] --max-influences requires a value.\n";
                printUsage();
                return 1;
            }
            int v;
            if (!parseInt(argv[++i], v)) {
                std::cerr << "[Error] Invalid --max-influences value: " << argv[i] << "\n";
                return 1;
            }
            if (v < 1 || v > 8) {
                std::cerr << "[Error] --max-influences must be in [1, 8].\n";
                return 1;
            }
            opts.maxInfluences = v;
        } else if (arg == "--log-level") {
            if (i + 1 >= argc) {
                std::cerr << "[Error] --log-level requires a value.\n";
                printUsage();
                return 1;
            }
            if (!parseLogLevel(argv[++i], level)) {
                std::cerr << "[Error] Invalid --log-level value: " << argv[i]
                          << " (expected debug|info|warn|error).\n";
                return 1;
            }
        } else if (arg == "--help" || arg == "-h") {
            printUsage();
            return 0;
        } else {
            std::cerr << "[Error] Unknown argument: " << arg << "\n";
            printUsage();
            return 1;
        }
    }

    setLogLevel(level);

    XLoader loader;
    XModel  model;

    if (!loader.loadModel(inputPath, model, opts)) {
        LOG_ERROR("[Fatal] Failed to load model: " + inputPath);
        return 2;
    }

    JsonExporter exporter;
    if (!exporter.exportToFile(model, outputJson)) {
        LOG_ERROR("[Fatal] Failed to write JSON: " + outputJson);
        return 3;
    }

    LOG_INFO("[OK] Wrote " + outputJson);
    return 0;
}
