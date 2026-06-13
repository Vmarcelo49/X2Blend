#include <iostream>
#include <string>
#include "x_loader.h"
#include "json_exporter.h"
#include "middleman.h"

static void printUsage() {
    std::cout << "=======================================================\n";
    std::cout << " x2blend — DirectX .x Model to Blender .blend Converter\n";
    std::cout << "=======================================================\n\n";
    std::cout << "Usage: x2blend.exe <input.x> <output.json>\n\n";
    std::cout << "The JSON output is consumed by scripts/blend_importer.py\n";
    std::cout << "to produce the final .blend file via Blender headless bpy.\n\n";
    std::cout << "Run the full pipeline with:\n";
    std::cout << "  ./x2blend.sh input.x output.blend\n\n";
}

int main(int argc, char* argv[]) {
    if (argc < 3) {
        printUsage();
        return 1;
    }

    std::string inputPath  = argv[1];
    std::string outputJson = argv[2];

    XLoader loader;
    XModel  model;

    if (!loader.loadModel(inputPath, model)) {
        std::cerr << "[Fatal] Failed to load model: " << inputPath << "\n";
        return 2;
    }

    JsonExporter exporter;
    if (!exporter.exportToFile(model, outputJson)) {
        std::cerr << "[Fatal] Failed to write JSON: " << outputJson << "\n";
        return 3;
    }

    return 0;
}
