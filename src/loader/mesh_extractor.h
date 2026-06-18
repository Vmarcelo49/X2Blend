// loader/mesh_extractor.h — Mesh + skinning extraction.
//
// Walks the D3DX frame tree a second time (after the HierarchyBuilder
// has populated the frameToIndexMap / nameToIndexMap / frameToWorldMap
// lookups) and, for every mesh container attached to every frame,
// extracts vertices / normals / UVs / indices / materials / skin
// weights into a fresh XMesh appended to model.meshes.  The owning
// frame's meshIndex is set to the new mesh's position in the list.
//
// Ported from x_loader.cpp (lines 377-617): processMeshContainers.
// All extraction logic — vertex/normal world-space transform, index
// winding flip, material extraction, the influence cap with weight
// normalization, and the inverse-bind-matrix remap — is preserved
// byte-for-byte.  The MeshExtractor constructor takes the device
// because CloneMeshFVF requires one.
//
// `maxInfluences` caps the per-vertex bone-influence count.  The XVertex
// data model stores a fixed std::array of size 4, so values > 4 are
// clamped to 4 with a LOG_WARN at construction time; values in [1,4]
// are honored exactly (useful for mobile/low-end targets that only
// skin to 2 bones per vertex).
#pragma once

#include <d3d9.h>
#include <d3dx9.h>

#include <map>
#include <string>

#include "core/middleman.h"

class MeshExtractor {
public:
    // `device` is required for CloneMeshFVF when normalizing the mesh's
    // FVF to XYZ | NORMAL | TEX1.  Must outlive the extractor.
    // `maxInfluences` caps per-vertex influences (clamped to [1,4]).
    explicit MeshExtractor(IDirect3DDevice9* device, int maxInfluences = 4);

    // Walks the frame tree and appends one XMesh per mesh container.
    void extract(D3DXFRAME* pRootFrame, XModel& model,
                 const std::map<D3DXFRAME*, int>& frameToIndexMap,
                 const std::map<std::string, int>& nameToIndexMap,
                 const std::map<D3DXFRAME*, D3DXMATRIX>& frameToWorldMap);

private:
    IDirect3DDevice9* m_pDevice;
    int m_maxInfluences;  // clamped to [1,4]

    void processFrameMeshes(
        D3DXFRAME* pFrame, XModel& model,
        const std::map<D3DXFRAME*, int>& frameToIndexMap,
        const std::map<std::string, int>& nameToIndexMap,
        const std::map<D3DXFRAME*, D3DXMATRIX>& frameToWorldMap);
};
