// loader/animation_baker.h — Animation baking (dense + sparse).
//
// Takes the ID3DXAnimationController produced by D3DXLoadMeshHierarchyFromX
// and emits one XAnimation per animation set into model.animations.
//
// Two baking strategies:
//
//   1. sampleBakedKeys (opts.bake == true, the default):
//      Sample the controller at a fixed rate (opts.bakeFps, default 60)
//      across the animation's full duration.  Produces one world-matrix
//      keyframe per node per frame.  The static-bone optimization then
//      collapses channels whose matrices are all identical to 2 keyframes.
//      This is the most robust path and the one recommended for general use.
//
//   2. extractKeyTimeBakedKeys (opts.bake == false, the --no-bake path):
//      Collect the UNION of all keyframe times from the keyframed animation
//      set, then evaluate the controller at each of those sparse times only.
//      Produces one world-matrix keyframe per node per union-time.  This
//      preserves the original keyframe timing (typically 20-50 key times
//      vs 300+ at 60 FPS) while being mathematically exact — it uses the
//      SAME world-matrix formula as the dense path, just at sparse times.
//
// Both paths output `bakedKeys` (world matrices in Blender space).  The
// Python importer consumes `bakedKeys` with the formula:
//
//   chan_mat = M_rest⁻¹ · M_rest_parent · M_world_parent⁻¹ · M_world
//
// which correctly accounts for parent animation and the rest-pose offset.
// See docs/X_FORMAT_RESEARCH.md §4 for why the old component-wise TRS-remap
// (which tried to avoid this formula) produced disfigured models.
//
// Ported from x_loader.cpp (lines 644-832): processAnimations.  The dense
// bake loop is preserved verbatim; the sparse path is new.
#pragma once

#include <d3dx9.h>
#include <d3dx9anim.h>

#include <map>
#include <string>

#include "core/middleman.h"

// Options carried from the CLI through XLoader::loadModel into the baker.
struct BakeOptions {
    bool  bake        = true;     // true = dense 60-FPS bake; false = sparse key-time bake
    float bakeFps     = 60.0f;    // sample rate for the dense path (informational for sparse)
    int   maxInfluences = 4;      // informational; actual capping is in MeshExtractor
};

class AnimationBaker {
public:
    void bake(ID3DXAnimationController* pAnimController, XModel& model,
              const std::map<std::string, int>& nameToIndexMap,
              D3DXFRAME* pRootFrame, const BakeOptions& opts);

private:
    // Sparse key-time baking (--no-bake path).  Collects the union of all
    // keyframe times from the keyframed set, evaluates the controller at
    // each, and records per-node world matrices.  Falls back to a warning
    // (no keys produced) if the set isn't queryable as ID3DXKeyframedAnimationSet.
    void extractKeyTimeBakedKeys(ID3DXAnimationController* pAnimController,
                                 ID3DXKeyframedAnimationSet* pKeySet,
                                 ID3DXAnimationSet* pSet,
                                 XAnimation& xanim,
                                 D3DXFRAME* pRootFrame,
                                 XModel& model);

    // Dense 60-FPS baking (default path).  Sets up track 0, advances the
    // controller across the animation duration at `fps`, records per-node
    // world matrices, then runs the static-bone optimization.
    void sampleBakedKeys(ID3DXAnimationController* pAnimController,
                         ID3DXAnimationSet* pSet,
                         XAnimation& xanim, D3DXFRAME* pRootFrame, float fps,
                         XModel& model);
};
