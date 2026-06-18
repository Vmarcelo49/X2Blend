// loader/x_loader.h — Facade for the .x -> XModel pipeline.
//
// XLoader::loadModel orchestrates the full Stage-1 pipeline:
//
//   1. D3DContext::init() — headless D3D9 device.
//   2. XFilePreprocessor::readAndPreprocess() — read the .x file and
//      inject missing mesh-extension templates.
//   3. Write preprocessed content to a temp .x file (Wine's in-memory
//      loader mishandles inline templates; the file-based loader works).
//   4. D3DXLoadMeshHierarchyFromXA() with HierarchyAllocator.
//   5. Populate outModel.meta (source file, bake mode, bake FPS, source
//      ticks-per-second, max influences, version string).
//   6. HierarchyBuilder::build() — D3DXFRAME tree -> model.nodes.
//   7. MeshExtractor::extract() — meshes + skinning + bone flags.
//   8. AnimationBaker::bake() — produces baked keys (dense 60 FPS or sparse
//      key-time, depending on opts.bake).
//   9. D3DXFrameDestroy() + release the anim controller + D3DContext::cleanup().
//
// On any failure, the loader logs the cause via LOG_ERROR and returns
// false after cleaning up whatever it had allocated so far.
#pragma once

#include <string>

#include "core/middleman.h"

// CLI-facing options forwarded into BakeOptions for the AnimationBaker
// and into XModelMeta for the JSON output.
struct LoaderOptions {
    bool  bake          = true;
    float bakeFps       = 60.0f;
    int   maxInfluences = 4;
};

class XLoader {
public:
    // Loads a .x model from `filepath` into `outModel`.  Returns true on
    // success.  On failure, logs the cause and returns false; `outModel`
    // may be partially populated but should be considered invalid.
    bool loadModel(const std::string& filepath, XModel& outModel, const LoaderOptions& opts);
};
