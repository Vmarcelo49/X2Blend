// loader/hierarchy_builder.cpp — D3DXFRAME -> XModel node array implementation.
//
// Line-for-line port of XLoader::processFrameHierarchy from the original
// x_loader.cpp (lines 318-375).  The Shift-JIS -> UTF-8 name conversion
// is delegated to core/codec.h::shiftJisToUtf8, the matrix-to-Blender
// conversion to core/coord.h::convertMatrixToBlender, and the recursive
// walk is structurally identical to the original.  The D3DXMatrixDecompose
// + axis-swap TRS decomposition is preserved exactly:
//
//   node.translation = { t.x, t.z, t.y };
//   node.rotation    = { -r.x, -r.z, -r.y, r.w };
//   node.scale       = { s.x, s.z, s.y };
//
// The only delta vs the original is the absence of the
// `node.useTRS = true;` line — that field was removed from XNode.
#include "loader/hierarchy_builder.h"

#include "core/codec.h"
#include "core/coord.h"

int HierarchyBuilder::build(D3DXFRAME* pRootFrame, XModel& model,
                            std::map<D3DXFRAME*, int>& frameToIndexMap,
                            std::map<std::string, int>& nameToIndexMap,
                            std::map<D3DXFRAME*, D3DXMATRIX>& frameToWorldMap) {
    D3DXMATRIX identity;
    D3DXMatrixIdentity(&identity);
    return processFrame(pRootFrame, -1, identity, model,
                        frameToIndexMap, nameToIndexMap, frameToWorldMap);
}

int HierarchyBuilder::processFrame(
    D3DXFRAME* pFrame, int parentIdx, const D3DXMATRIX& parentWorld,
    XModel& model,
    std::map<D3DXFRAME*, int>& frameToIndexMap,
    std::map<std::string, int>& nameToIndexMap,
    std::map<D3DXFRAME*, D3DXMATRIX>& frameToWorldMap
) {
    if (!pFrame) return -1;

    int currentIdx = static_cast<int>(model.nodes.size());
    XNode node;

    // Convert name from Shift-JIS to UTF-8 (synthetic name if unnamed).
    std::string utf8Name = pFrame->Name
        ? shiftJisToUtf8(pFrame->Name)
        : "Frame_" + std::to_string(currentIdx);
    node.name = utf8Name;

    // Convert local transform matrix to Blender coordinate space.
    node.localTransform = convertMatrixToBlender(pFrame->TransformationMatrix);
    node.parentIndex = parentIdx;

    // Decompose the transform matrix into translation / rotation / scale
    // so the Python importer can use the rest-pose TRS when computing
    // relative keyframes (analysis section 3.3).
    //
    // The axis swap (t.x, t.z, t.y) / (-r.x, -r.z, -r.y, r.w) /
    // (s.x, s.z, s.y) is the left-handed-Y-up -> right-handed-Z-up
    // remap.  Math preserved verbatim from the original.
    D3DXVECTOR3       t, s;
    D3DXQUATERNION    r;
    if (SUCCEEDED(D3DXMatrixDecompose(&s, &r, &t, &pFrame->TransformationMatrix))) {
        node.translation = { t.x, t.z, t.y };
        node.rotation    = { -r.x, -r.z, -r.y, r.w };
        node.scale       = { s.x, s.z, s.y };
        // (useTRS field removed in refactor — was set but never read.)
    }

    // Compute the frame's world transform (Local * ParentWorld, row-major).
    D3DXMATRIX worldMat;
    if (parentIdx == -1) {
        worldMat = pFrame->TransformationMatrix;
    } else {
        D3DXMatrixMultiply(&worldMat, &pFrame->TransformationMatrix, &parentWorld);
    }
    frameToWorldMap[pFrame] = worldMat;

    frameToIndexMap[pFrame]    = currentIdx;
    nameToIndexMap[utf8Name]   = currentIdx; // Map the UTF-8 name (not raw SJIS).

    model.nodes.push_back(node);

    // Recursively process child frames.
    D3DXFRAME* pChild = pFrame->pFrameFirstChild;
    while (pChild) {
        int childIdx = processFrame(pChild, currentIdx, worldMat, model,
                                    frameToIndexMap, nameToIndexMap,
                                    frameToWorldMap);
        if (childIdx != -1) {
            model.nodes[currentIdx].childrenIndices.push_back(childIdx);
        }
        pChild = pChild->pFrameSibling;
    }

    return currentIdx;
}
