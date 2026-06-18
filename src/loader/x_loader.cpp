// loader/x_loader.cpp — Facade implementation.
//
// Orchestrates D3DContext + XFilePreprocessor + HierarchyAllocator +
// HierarchyBuilder + MeshExtractor + AnimationBaker into a single
// loadModel() call.  The pipeline order matches the original
// XLoader::loadModel from x_loader.cpp (lines 834-919); the only
// changes are:
//
//   - Each phase is delegated to its own module instead of being a
//     private method on a god class.
//   - std::cerr -> LOG_ERROR / LOG_INFO.
//   - outModel.meta is populated (new in the refactor — analysis
//     section 4.2 row "FPS detection").
//   - sourceTicksPerSecond is queried from the first keyframed anim
//     set (default 4800 if there are no keyframed sets).
//
// Cleanup is balanced: on any failure path, the temp file is deleted,
// the hierarchy is destroyed (if it was created), the anim controller
// is released (if it was created), and the D3D context is cleaned up.
#define INITGUID
#define WIN32_LEAN_AND_MEAN

#include "loader/x_loader.h"

#include <windows.h>

#include <fstream>
#include <map>
#include <string>

#include "core/log.h"
#include "d3d/d3d_context.h"
#include "d3d/hierarchy_allocator.h"
#include "io/x_file_preprocessor.h"
#include "loader/animation_baker.h"
#include "loader/hierarchy_builder.h"
#include "loader/mesh_extractor.h"

// D3DXLoadMeshHierarchyFromXA / D3DXFrameDestroy live in d3dx9anim.h,
// which is pulled in transitively via d3d/hierarchy_allocator.h.
#include <d3dx9.h>
#include <d3dx9anim.h>

namespace {

// Query the animation controller for the source ticks-per-second of
// the first keyframed animation set.  Returns 4800 if there are no
// keyframed sets (4800 is the D3DX default used by the original
// exporter when the controller has no keyframed sets).
double querySourceTicksPerSecond(ID3DXAnimationController* pAnimController) {
    if (!pAnimController) return 4800.0;

    DWORD numSets = pAnimController->GetNumAnimationSets();
    for (DWORD s = 0; s < numSets; ++s) {
        ID3DXAnimationSet* pSet = nullptr;
        if (FAILED(pAnimController->GetAnimationSet(s, &pSet)) || !pSet) continue;

        ID3DXKeyframedAnimationSet* pKeySet = nullptr;
        double ticks = 4800.0;
        if (SUCCEEDED(pSet->QueryInterface(IID_ID3DXKeyframedAnimationSet, (void**)&pKeySet)) && pKeySet) {
            ticks = pKeySet->GetSourceTicksPerSecond();
            pKeySet->Release();
            pSet->Release();
            return (ticks > 0.0) ? ticks : 4800.0;
        }
        pSet->Release();
    }
    return 4800.0;
}

} // namespace

bool XLoader::loadModel(const std::string& filepath, XModel& outModel, const LoaderOptions& opts) {
    // 1. Initialize the headless D3D9 context.
    D3DContext d3d;
    if (!d3d.init()) {
        LOG_ERROR("[XLoader] Failed to initialize headless D3D9 context for file: " + filepath);
        return false;
    }

    // 2. Read the .x file and inject missing mesh-extension templates.
    std::string xFileContent;
    if (!XFilePreprocessor::readAndPreprocess(filepath, xFileContent)) {
        LOG_ERROR("[XLoader] Failed to read .x file from disk: " + filepath);
        return false;
    }

    // 3. Write the preprocessed content to a temp .x file, then use the
    //    file-based loader.  Wine's D3DXLoadMeshHierarchyFromXInMemory has
    //    a known issue where it doesn't properly handle inline template
    //    definitions; the file-based variant works correctly.
    char tempPath[MAX_PATH];
    char tempFile[MAX_PATH];
    GetTempPathA(MAX_PATH, tempPath);
    GetTempFileNameA(tempPath, "x2g", 0, tempFile);
    // Give it a .x extension so d3dxof uses the text parser.
    std::string tempXFile = std::string(tempFile) + ".x";
    DeleteFileA(tempFile); // remove the placeholder GetTempFileName created

    {
        std::ofstream tmpOut(tempXFile, std::ios::binary);
        if (!tmpOut.is_open()) {
            LOG_ERROR("[XLoader] Failed to write temp .x file: " + tempXFile);
            DeleteFileA(tempXFile.c_str()); // defensive: clean up any partial file
            return false;
        }
        tmpOut.write(xFileContent.data(), static_cast<std::streamsize>(xFileContent.size()));
    }

    HierarchyAllocator                allocHierarchy;
    LPD3DXFRAME                       pRootFrame      = nullptr;
    ID3DXAnimationController*         pAnimController = nullptr;

    // 4. Load the mesh hierarchy + animation controller.
    HRESULT hr = D3DXLoadMeshHierarchyFromXA(
        tempXFile.c_str(),
        D3DXMESH_SYSTEMMEM,
        d3d.device(),
        &allocHierarchy,
        nullptr,
        &pRootFrame,
        &pAnimController
    );

    DeleteFileA(tempXFile.c_str()); // clean up the temp file regardless of result

    if (FAILED(hr)) {
        LOG_ERROR("[XLoader] D3DXLoadMeshHierarchyFromXA failed (HRESULT "
                  + std::to_string(hr) + ").");
        return false;
    }

    // 5. Populate the metadata block before the builder/extractor/baker
    //    run, so downstream modules can read it from outModel.meta if
    //    they need to.
    outModel.meta.sourceFile            = filepath;
    outModel.meta.bakeMode              = opts.bake ? "baked" : "keyframed";
    outModel.meta.bakeFps               = opts.bakeFps;
    outModel.meta.maxInfluences         = opts.maxInfluences;
    outModel.meta.x2blendVersion        = "2.0.0-refactor";
    outModel.meta.sourceTicksPerSecond  = querySourceTicksPerSecond(pAnimController);

    LOG_INFO("[XLoader] Loaded: " + filepath);
    LOG_INFO("[XLoader]   bake mode:    " + outModel.meta.bakeMode);
    LOG_INFO("[XLoader]   bake fps:     " + std::to_string(outModel.meta.bakeFps));
    LOG_INFO("[XLoader]   src ticks/s:  " + std::to_string(outModel.meta.sourceTicksPerSecond));
    LOG_INFO("[XLoader]   max infl:     " + std::to_string(outModel.meta.maxInfluences));

    // 6. Map the frame hierarchy to a flat node array.
    std::map<D3DXFRAME*, int>          frameToIndexMap;
    std::map<std::string, int>         nameToIndexMap;
    std::map<D3DXFRAME*, D3DXMATRIX>   frameToWorldMap;

    HierarchyBuilder builder;
    int rootIdx = builder.build(pRootFrame, outModel,
                                frameToIndexMap, nameToIndexMap, frameToWorldMap);
    outModel.rootNodeIndex = rootIdx;

    // 7. Extract meshes + skinning.
    MeshExtractor extractor(d3d.device(), opts.maxInfluences);
    extractor.extract(pRootFrame, outModel, frameToIndexMap, nameToIndexMap, frameToWorldMap);

    LOG_INFO("[XLoader]   nodes:        " + std::to_string(outModel.nodes.size()));
    LOG_INFO("[XLoader]   meshes:       " + std::to_string(outModel.meshes.size()));

    // 8. Bake / extract animations.
    BakeOptions bakeOpts;
    bakeOpts.bake          = opts.bake;
    bakeOpts.bakeFps       = opts.bakeFps;
    bakeOpts.maxInfluences = opts.maxInfluences;

    AnimationBaker baker;
    baker.bake(pAnimController, outModel, nameToIndexMap, pRootFrame, bakeOpts);

    LOG_INFO("[XLoader]   animations:   " + std::to_string(outModel.animations.size()));

    // 9. Cleanup native D3DX objects.  D3DContext::cleanup() runs in
    //    the destructor as well, but calling it explicitly here matches
    //    the original's flow and makes the lifecycle obvious.
    D3DXFrameDestroy(pRootFrame, &allocHierarchy);
    if (pAnimController) {
        pAnimController->Release();
    }
    d3d.cleanup();

    return true;
}
