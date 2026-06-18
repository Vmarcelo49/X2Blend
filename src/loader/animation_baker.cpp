// loader/animation_baker.cpp — Animation baking (dense + sparse) implementation.
//
// Two paths, both producing `bakedKeys` (world matrices in Blender space):
//
//   1. extractKeyTimeBakedKeys (--no-bake):
//      Collects the UNION of all keyframe times from the keyframed set,
//      evaluates the controller at each sparse time, and records world
//      matrices for every node.  This preserves the original keyframe
//      timing while being mathematically exact — the Python importer
//      applies the same chan_mat formula as the dense path.
//
//      Why this replaced the old component-wise TRS-remap:
//      The old code did `tRel = R_rest⁻¹ ⊗ (t_key − t_rest)` per channel,
//      treating D3DX keys as world-space values.  But D3DX keys are LOCAL
//      (parent-relative) and Blender's chan_mat requires the parent's
//      ACTUAL posed world matrix at each time.  The component-wise formula
//      misses the `parent.matrix_local × parent.pose.matrix⁻¹` term,
//      causing accumulating errors down the bone chain → disfigured models.
//      See docs/X_FORMAT_RESEARCH.md §4 for the full derivation.
//
//   2. sampleBakedKeys (default):
//      Samples at a fixed rate (default 60 FPS) across the full duration.
//      Preserved verbatim from the original x_loader.cpp (lines 747-826),
//      plus the static-bone optimization that collapses constant channels
//      to 2 keyframes.
//
// Both paths share the FrameMatrixUpdater (verbatim from the original)
// and the static-bone reduction pass.
#include "loader/animation_baker.h"

#include <algorithm>
#include <cmath>
#include <set>
#include <vector>

#include "core/codec.h"
#include "core/coord.h"
#include "core/log.h"
#include "core/x_math.h"

namespace {

// Recursive world-matrix updater.  Verbatim from the original
// FrameMatrixUpdater nested struct (x_loader.cpp lines 777-791).
// Walks the frame tree multiplying TransformationMatrix * parentWorld,
// and writes each named frame's world matrix into outWorldMap keyed by
// the raw pFrame->Name (NOT the UTF-8 conversion — preserved exactly
// from the original, which means SJIS frame names won't be found by the
// UTF-8 lookup below and will fall back to identity, matching the
// original's behavior).
struct FrameMatrixUpdater {
    static void update(D3DXFRAME* pFrame, const D3DXMATRIX& parentWorld,
                       std::map<std::string, D3DXMATRIX>& outWorldMap) {
        if (!pFrame) return;
        D3DXMATRIX world;
        D3DXMatrixMultiply(&world, &pFrame->TransformationMatrix, &parentWorld);
        if (pFrame->Name) {
            outWorldMap[pFrame->Name] = world;
        }
        D3DXFRAME* pChild = pFrame->pFrameFirstChild;
        while (pChild) {
            update(pChild, world, outWorldMap);
            pChild = pChild->pFrameSibling;
        }
    }
};

// Returns true if every float in `b` is within `tol` of the corresponding
// float in `a`.  Used by the static-bone optimization to decide whether
// a channel's baked keys are all identical.
bool matricesEqual(const XMatrix4x4& a, const XMatrix4x4& b, float tol) {
    for (int r = 0; r < 4; ++r) {
        for (int c = 0; c < 4; ++c) {
            if (std::fabs(a.m[r][c] - b.m[r][c]) > tol) {
                return false;
            }
        }
    }
    return true;
}

// Runs the static-bone reduction on a channel's bakedKeys.  If every
// matrix is identical to the first (within k_staticTol), collapse to
// just the first and last keyframe.  Returns the number of keys remaining.
size_t reduceStaticChannel(std::vector<XKeyframeMatrix>& bakedKeys) {
    if (bakedKeys.size() <= 2) return bakedKeys.size();

    const float k_staticTol = 1e-7f;
    const XMatrix4x4& first = bakedKeys.front().value;
    bool allSame = true;
    for (size_t k = 1; k < bakedKeys.size() && allSame; ++k) {
        if (!matricesEqual(first, bakedKeys[k].value, k_staticTol)) {
            allSame = false;
        }
    }
    if (allSame) {
        XKeyframeMatrix firstKf = bakedKeys.front();
        XKeyframeMatrix lastKf  = bakedKeys.back();
        bakedKeys.clear();
        bakedKeys.push_back(firstKf);
        bakedKeys.push_back(lastKf);
    }
    return bakedKeys.size();
}

// Ensure a channel exists for every node in the model, so the Python
// importer gets a complete F-curve set.  Returns a map from node name
// to channel index.  Preserved from the original (lines 759-774).
std::map<std::string, size_t> ensureChannelsForAllNodes(XAnimation& xanim,
                                                         const XModel& model) {
    std::map<std::string, size_t> nodeNameToChannelIdx;
    for (size_t i = 0; i < xanim.channels.size(); ++i) {
        nodeNameToChannelIdx[xanim.channels[i].targetNodeName] = i;
    }
    for (size_t i = 0; i < model.nodes.size(); ++i) {
        const std::string& name = model.nodes[i].name;
        if (nodeNameToChannelIdx.find(name) == nodeNameToChannelIdx.end()) {
            XAnimationChannel newChannel;
            newChannel.targetNodeName  = name;
            newChannel.targetNodeIndex = static_cast<int>(i);
            xanim.channels.push_back(newChannel);
            nodeNameToChannelIdx[name] = xanim.channels.size() - 1;
        }
    }
    return nodeNameToChannelIdx;
}

// Reset the animation controller to a clean state before baking each
// animation set.  The D3DX controller can carry over state from the .X
// file's original track setup (multiple tracks enabled, blended weights,
// accumulated global time) which causes subsequent animation sets to be
// sampled incorrectly — often producing identical output for all sets.
//
// This function:
//   1. Disables ALL tracks except track 0.
//   2. Resets track 0's speed/weight/position to defaults.
//   3. Advances time by 0 to flush any pending state.
//
// Must be called before SetTrackAnimationSet for each animation set.
void resetControllerForBaking(ID3DXAnimationController* pAnimController) {
    DWORD maxTracks = pAnimController->GetMaxNumAnimationSets();
    for (DWORD t = 0; t < maxTracks; ++t) {
        pAnimController->SetTrackEnable(t, FALSE);
    }
    // Re-enable only track 0 with clean settings.
    pAnimController->SetTrackEnable(0, TRUE);
    pAnimController->SetTrackWeight(0, 1.0f);
    pAnimController->SetTrackSpeed(0, 1.0f);
    pAnimController->SetTrackPosition(0, 0.0);
    // Flush any accumulated global time / pending evaluations.
    pAnimController->AdvanceTime(0.0, nullptr);
}

} // namespace

void AnimationBaker::bake(ID3DXAnimationController* pAnimController, XModel& model,
                          const std::map<std::string, int>& nameToIndexMap,
                          D3DXFRAME* pRootFrame, const BakeOptions& opts) {
    if (!pAnimController || !pRootFrame) return;

    DWORD numAnimSets = pAnimController->GetNumAnimationSets();
    LOG_INFO("Found " + std::to_string(numAnimSets) + " animation set(s).");
    LOG_INFO(std::string("Bake mode: ") +
             (opts.bake ? "dense (fixed FPS)" : "sparse (key-time)"));
    if (opts.bake) {
        LOG_INFO("Bake FPS: " + std::to_string(opts.bakeFps));
    }

    for (DWORD s = 0; s < numAnimSets; ++s) {
        ID3DXAnimationSet* pSet = nullptr;
        if (FAILED(pAnimController->GetAnimationSet(s, &pSet)) || !pSet) continue;

        XAnimation xanim;
        LPCSTR pSetName = pSet->GetName();
        xanim.name     = pSetName ? shiftJisToUtf8(pSetName) : "Animation_" + std::to_string(s);
        xanim.duration = static_cast<float>(pSet->GetPeriod());

        // Reset the controller to a clean state before each animation set.
        // Without this, the controller can carry over track state from the
        // previous set (or from the .X file's original track setup), causing
        // all animations to be sampled identically.
        resetControllerForBaking(pAnimController);

        if (opts.bake) {
            // Dense path: sample at fixed FPS.
            sampleBakedKeys(pAnimController, pSet, xanim, pRootFrame, opts.bakeFps, model);
        } else {
            // Sparse path: sample at original keyframe times.
            ID3DXKeyframedAnimationSet* pKeySet = nullptr;
            if (SUCCEEDED(pSet->QueryInterface(IID_ID3DXKeyframedAnimationSet,
                                               (void**)&pKeySet)) && pKeySet) {
                extractKeyTimeBakedKeys(pAnimController, pKeySet, pSet, xanim,
                                        pRootFrame, model);
                pKeySet->Release();
            } else {
                // Compressed / procedural set — can't read key times.
                // Fall back to dense baking with a warning so the user
                // still gets a working animation.
                LOG_WARN("[" + xanim.name +
                         "] not a keyframed set; falling back to dense bake.");
                sampleBakedKeys(pAnimController, pSet, xanim, pRootFrame,
                                opts.bakeFps, model);
            }
        }

        model.animations.push_back(xanim);
        pSet->Release();
    }
}

// ---------------------------------------------------------------------------
// Sparse key-time baking (--no-bake path).
//
// Collects the union of all keyframe times from the keyframed set,
// evaluates the controller at each, and records per-node world matrices.
// The Python importer then applies the same chan_mat formula as the dense
// path — mathematically exact, just at sparse times.
//
// This replaces the old component-wise TRS-remap which was mathematically
// wrong (treated local keys as world-space values, missed the parent-pose
// term in the chan_mat formula).  See docs/X_FORMAT_RESEARCH.md §4.
// ---------------------------------------------------------------------------
void AnimationBaker::extractKeyTimeBakedKeys(
    ID3DXAnimationController* pAnimController,
    ID3DXKeyframedAnimationSet* pKeySet,
    ID3DXAnimationSet* /*pSet*/,
    XAnimation& xanim,
    D3DXFRAME* pRootFrame,
    XModel& model) {

    const double sourceTicksPerSecond = pKeySet->GetSourceTicksPerSecond();
    const float  ticksToSeconds = (sourceTicksPerSecond > 0.0)
        ? static_cast<float>(1.0 / sourceTicksPerSecond)
        : 1.0f;

    // --- 1. Collect the union of all keyframe times across all bones. ---
    // Each bone may have translation, rotation, and scale keys at different
    // times.  We need ALL of them so the Python importer can find every
    // bone's world matrix at each key time (the chan_mat formula requires
    // the parent's world matrix at the same time).
    std::set<double> keyTimesSet;

    DWORD numAnimations = pKeySet->GetNumAnimations();
    for (DWORD a = 0; a < numAnimations; ++a) {
        // Translation keys
        DWORD nTrans = pKeySet->GetNumTranslationKeys(a);
        if (nTrans > 0) {
            std::vector<D3DXKEY_VECTOR3> keys(nTrans);
            if (SUCCEEDED(pKeySet->GetTranslationKeys(a, keys.data()))) {
                for (const auto& k : keys) {
                    keyTimesSet.insert(static_cast<double>(k.Time) * ticksToSeconds);
                }
            }
        }

        // Rotation keys
        DWORD nRot = pKeySet->GetNumRotationKeys(a);
        if (nRot > 0) {
            std::vector<D3DXKEY_QUATERNION> keys(nRot);
            if (SUCCEEDED(pKeySet->GetRotationKeys(a, keys.data()))) {
                for (const auto& k : keys) {
                    keyTimesSet.insert(static_cast<double>(k.Time) * ticksToSeconds);
                }
            }
        }

        // Scale keys
        DWORD nScale = pKeySet->GetNumScaleKeys(a);
        if (nScale > 0) {
            std::vector<D3DXKEY_VECTOR3> keys(nScale);
            if (SUCCEEDED(pKeySet->GetScaleKeys(a, keys.data()))) {
                for (const auto& k : keys) {
                    keyTimesSet.insert(static_cast<double>(k.Time) * ticksToSeconds);
                }
            }
        }
    }

    // Always include t=0 and t=duration so the animation has endpoints.
    keyTimesSet.insert(0.0);
    keyTimesSet.insert(static_cast<double>(xanim.duration));

    if (keyTimesSet.empty()) {
        LOG_WARN("[" + xanim.name + "] no keyframe times found; skipping.");
        return;
    }

    std::vector<double> keyTimes(keyTimesSet.begin(), keyTimesSet.end());
    std::sort(keyTimes.begin(), keyTimes.end());

    LOG_INFO("  [" + xanim.name + "] sparse bake: " +
             std::to_string(keyTimes.size()) + " key times (vs " +
             std::to_string(static_cast<int>(std::ceil(xanim.duration * 60.0f)) + 1) +
             " at 60 FPS).");

    // --- 2. Ensure a channel exists for every node in the model. ---
    // Same as the dense path: every bone needs a channel so the Python
    // importer can find the parent's world matrix at each key time.
    auto nodeNameToChannelIdx = ensureChannelsForAllNodes(xanim, model);

    // --- 3. Set up track 0 and evaluate at each key time. ---
    pAnimController->SetTrackAnimationSet(0, pKeySet);
    pAnimController->SetTrackEnable(0, TRUE);
    pAnimController->SetTrackWeight(0, 1.0f);
    pAnimController->SetTrackSpeed(0, 1.0f);
    pAnimController->SetTrackPosition(0, 0.0);

    D3DXMATRIX identity;
    D3DXMatrixIdentity(&identity);

    size_t keysBeforeReduction = 0;

    for (double t : keyTimes) {
        // Clamp to animation duration.
        double tc = std::min(t, static_cast<double>(xanim.duration));
        float  tf = static_cast<float>(tc);

        pAnimController->SetTrackPosition(0, tc);
        pAnimController->AdvanceTime(0.0, nullptr);

        // Walk the hierarchy to compute world matrices at this time.
        std::map<std::string, D3DXMATRIX> worldMap;
        FrameMatrixUpdater::update(pRootFrame, identity, worldMap);

        // Record a baked key for every node at this time.
        for (size_t i = 0; i < model.nodes.size(); ++i) {
            const std::string& name = model.nodes[i].name;
            auto wit = worldMap.find(name);

            D3DXMATRIX worldMat;
            if (wit != worldMap.end()) {
                worldMat = wit->second;
            } else {
                D3DXMatrixIdentity(&worldMat);
            }

            XMatrix4x4 blendMat = convertMatrixToBlender(worldMat);

            XKeyframeMatrix kf;
            kf.time  = tf;
            kf.value = blendMat;

            size_t chIdx = nodeNameToChannelIdx[name];
            xanim.channels[chIdx].bakedKeys.push_back(kf);
            ++keysBeforeReduction;
        }
    }

    // --- 4. Static-bone reduction (same as dense path). ---
    // Channels whose world matrices don't vary across key times collapse
    // to 2 keyframes (first + last), saving space without losing accuracy.
    size_t keysAfterReduction = 0;
    for (auto& channel : xanim.channels) {
        keysAfterReduction += reduceStaticChannel(channel.bakedKeys);
    }

    LOG_INFO("  [" + xanim.name + "] baked keyframes: "
             + std::to_string(keysBeforeReduction) + " before / "
             + std::to_string(keysAfterReduction)  + " after static-bone reduction.");
}

// ---------------------------------------------------------------------------
// Dense 60-FPS baking (default path).  Preserved from the original.
// ---------------------------------------------------------------------------
void AnimationBaker::sampleBakedKeys(ID3DXAnimationController* pAnimController,
                                     ID3DXAnimationSet* pSet,
                                     XAnimation& xanim, D3DXFRAME* pRootFrame, float fps,
                                     XModel& model) {
    // Set up track 0 to play our animation set.
    pAnimController->SetTrackAnimationSet(0, pSet);
    pAnimController->SetTrackEnable(0, TRUE);
    pAnimController->SetTrackWeight(0, 1.0f);
    pAnimController->SetTrackSpeed(0, 1.0f);
    pAnimController->SetTrackPosition(0, 0.0);

    float timeStep = 1.0f / fps;
    int   numFrames = static_cast<int>(std::ceil(xanim.duration * fps)) + 1;

    auto nodeNameToChannelIdx = ensureChannelsForAllNodes(xanim, model);

    D3DXMATRIX identity;
    D3DXMatrixIdentity(&identity);

    size_t keysBeforeReduction = 0;

    for (int f = 0; f < numFrames; ++f) {
        float t = f * timeStep;
        if (t > xanim.duration) t = xanim.duration;

        pAnimController->SetTrackPosition(0, static_cast<double>(t));
        pAnimController->AdvanceTime(0.0, nullptr);

        std::map<std::string, D3DXMATRIX> worldMap;
        FrameMatrixUpdater::update(pRootFrame, identity, worldMap);

        for (size_t i = 0; i < model.nodes.size(); ++i) {
            const std::string& name = model.nodes[i].name;
            auto wit = worldMap.find(name);

            D3DXMATRIX worldMat;
            if (wit != worldMap.end()) {
                worldMat = wit->second;
            } else {
                D3DXMatrixIdentity(&worldMat);
            }

            XMatrix4x4 blendMat = convertMatrixToBlender(worldMat);

            XKeyframeMatrix kf;
            kf.time  = t;
            kf.value = blendMat;

            size_t chIdx = nodeNameToChannelIdx[name];
            xanim.channels[chIdx].bakedKeys.push_back(kf);
            ++keysBeforeReduction;
        }
    }

    // Static-bone optimization (analysis section 3.4 item 2).
    size_t keysAfterReduction = 0;
    for (auto& channel : xanim.channels) {
        keysAfterReduction += reduceStaticChannel(channel.bakedKeys);
    }

    LOG_INFO("  [" + xanim.name + "] baked keyframes: "
             + std::to_string(keysBeforeReduction) + " before / "
             + std::to_string(keysAfterReduction)  + " after static-bone reduction.");
}
