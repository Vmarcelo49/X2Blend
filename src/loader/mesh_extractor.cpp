// loader/mesh_extractor.cpp — Mesh + skinning extraction implementation.
//
// Line-for-line port of XLoader::processMeshContainers from the original
// x_loader.cpp (lines 377-617).  The recursive frame-walk is implemented
// as a member function (processFrameMeshes) instead of a recursive
// lambda, but the body is preserved exactly:
//
//   - Clone the mesh to XYZ|NORMAL|TEX1 FVF via the device.
//   - Lock the vertex buffer; for each vertex transform position and
//     normal by the frame's world matrix, then swap Y/Z to Blender space.
//   - Lock the index buffer; for each face swap indices [1] and [2] to
//     flip winding (mirrors the Z reflection of the coord conversion).
//   - Extract materials + Shift-JIS texture filenames.
//   - Lock the attribute buffer for per-face material indices.
//   - If the container has skin info:
//       * For each bone, get its name, mark that node and all its
//         ancestors as bones (file-local markNodeAndAncestorsAsBones),
//         remap the offset matrix to world space (invWorld * offset),
//         and convert it to Blender space.
//       * Gather all per-vertex influences, sort descending, cap to 4,
//         and normalize the weights per vertex.
//   - Append the XMesh to model.meshes and update the owning node's
//     meshIndex.
//
// The markNodeAndAncestorsAsBones helper is preserved as a file-local
// static (was x_loader.cpp lines 106-112).
#include "loader/mesh_extractor.h"

#include <algorithm>
#include <cstdint>
#include <vector>

#include "core/codec.h"
#include "core/coord.h"
#include "core/log.h"

// FVF layout for the cloned mesh: position, normal, one set of UVs.
// Matches D3DFVF_XYZ | D3DFVF_NORMAL | D3DFVF_TEX1 from the original.
namespace {
struct D3DXVERTEX_FVF {
    D3DXVECTOR3 Position;
    D3DXVECTOR3 Normal;
    float       tu, tv;
};

// Mark `nodeIndex` and every ancestor as a bone.  Preserved verbatim
// from x_loader.cpp (lines 106-112) — file-local per the spec.
void markNodeAndAncestorsAsBones(XModel& model, int nodeIndex) {
    while (nodeIndex >= 0 && nodeIndex < static_cast<int>(model.nodes.size())) {
        XNode& node = model.nodes[static_cast<size_t>(nodeIndex)];
        node.isBone = true;
        nodeIndex = node.parentIndex;
    }
}
} // namespace

MeshExtractor::MeshExtractor(IDirect3DDevice9* device, int maxInfluences)
    : m_pDevice(device) {
    // The XVertex data model stores a fixed std::array of size 4, so we
    // cannot represent more than 4 influences per vertex.  Clamp and warn.
    if (maxInfluences < 1) {
        LOG_WARN("[MeshExtractor] max_influences=" + std::to_string(maxInfluences)
                 + " out of range; clamping to 1.");
        m_maxInfluences = 1;
    } else if (maxInfluences > 4) {
        LOG_WARN("[MeshExtractor] max_influences=" + std::to_string(maxInfluences)
                 + " exceeds the data-model cap of 4; clamping to 4.");
        m_maxInfluences = 4;
    } else {
        m_maxInfluences = maxInfluences;
    }
}

void MeshExtractor::extract(D3DXFRAME* pRootFrame, XModel& model,
                            const std::map<D3DXFRAME*, int>& frameToIndexMap,
                            const std::map<std::string, int>& nameToIndexMap,
                            const std::map<D3DXFRAME*, D3DXMATRIX>& frameToWorldMap) {
    processFrameMeshes(pRootFrame, model, frameToIndexMap, nameToIndexMap, frameToWorldMap);
}

void MeshExtractor::processFrameMeshes(
    D3DXFRAME* pFrame, XModel& model,
    const std::map<D3DXFRAME*, int>& frameToIndexMap,
    const std::map<std::string, int>& nameToIndexMap,
    const std::map<D3DXFRAME*, D3DXMATRIX>& frameToWorldMap
) {
    if (!pFrame) return;

    int nodeIdx = -1;
    auto fit = frameToIndexMap.find(pFrame);
    if (fit != frameToIndexMap.end()) {
        nodeIdx = fit->second;
    }

    // Get the world matrix of this frame (identity if not in the map).
    D3DXMATRIX worldMat;
    auto wit = frameToWorldMap.find(pFrame);
    if (wit != frameToWorldMap.end()) {
        worldMat = wit->second;
    } else {
        D3DXMatrixIdentity(&worldMat);
    }

    D3DXMESHCONTAINER* pMC = pFrame->pMeshContainer;
    while (pMC) {
        if (pMC->MeshData.Type == D3DXMESHTYPE_MESH && pMC->MeshData.pMesh) {
            ID3DXMesh* pOriginalMesh = pMC->MeshData.pMesh;

            // Clone the mesh to a known FVF (XYZ | NORMAL | TEX1).
            ID3DXMesh* pCleanMesh = nullptr;
            HRESULT hr = pOriginalMesh->CloneMeshFVF(
                D3DXMESH_MANAGED,
                D3DFVF_XYZ | D3DFVF_NORMAL | D3DFVF_TEX1,
                m_pDevice,
                &pCleanMesh
            );

            if (SUCCEEDED(hr) && pCleanMesh) {
                DWORD numVertices = pCleanMesh->GetNumVertices();
                DWORD numFaces    = pCleanMesh->GetNumFaces();

                XMesh xmesh;
                xmesh.name = pMC->Name
                    ? shiftJisToUtf8(pMC->Name)
                    : "Mesh_" + std::to_string(model.meshes.size());

                // 1. Lock and extract vertices (transform to world space,
                //    then to Blender space).
                xmesh.vertices.resize(numVertices);
                D3DXVERTEX_FVF* pRawVerts = nullptr;
                pCleanMesh->LockVertexBuffer(D3DLOCK_READONLY, (void**)&pRawVerts);
                if (pRawVerts) {
                    for (DWORD i = 0; i < numVertices; ++i) {
                        D3DXVECTOR3 pos  = pRawVerts[i].Position;
                        D3DXVECTOR3 norm = pRawVerts[i].Normal;

                        // Transform position and normal by the frame's world matrix.
                        D3DXVECTOR3 posWorld, normWorld;
                        D3DXVec3TransformCoord(&posWorld,  &pos,  &worldMat);
                        D3DXVec3TransformNormal(&normWorld, &norm, &worldMat);

                        // Convert from left-handed Y-up to right-handed Z-up (Blender space).
                        xmesh.vertices[i].position = { posWorld.x, posWorld.z, posWorld.y };
                        xmesh.vertices[i].normal   = { normWorld.x, normWorld.z, normWorld.y };
                        xmesh.vertices[i].texCoord = { pRawVerts[i].tu, pRawVerts[i].tv, 0.0f };
                    }
                    pCleanMesh->UnlockVertexBuffer();
                }

                // 2. Lock and extract indices (winding order reversed to
                //    account for the Z reflection of the coord conversion).
                bool is32Bit = (pCleanMesh->GetOptions() & D3DXMESH_32BIT) != 0;
                xmesh.indices.resize(numFaces * 3);
                void* pRawIndices = nullptr;
                pCleanMesh->LockIndexBuffer(D3DLOCK_READONLY, &pRawIndices);
                if (pRawIndices) {
                    if (is32Bit) {
                        uint32_t* p32 = static_cast<uint32_t*>(pRawIndices);
                        for (DWORD i = 0; i < numFaces; ++i) {
                            xmesh.indices[i * 3 + 0] = p32[i * 3 + 0];
                            xmesh.indices[i * 3 + 1] = p32[i * 3 + 2]; // swap 1 and 2
                            xmesh.indices[i * 3 + 2] = p32[i * 3 + 1];
                        }
                    } else {
                        uint16_t* p16 = static_cast<uint16_t*>(pRawIndices);
                        for (DWORD i = 0; i < numFaces; ++i) {
                            xmesh.indices[i * 3 + 0] = p16[i * 3 + 0];
                            xmesh.indices[i * 3 + 1] = p16[i * 3 + 2]; // swap 1 and 2
                            xmesh.indices[i * 3 + 2] = p16[i * 3 + 1];
                        }
                    }
                    pCleanMesh->UnlockIndexBuffer();
                }

                // 3. Extract materials (with Shift-JIS -> UTF-8 texture filenames).
                if (pMC->NumMaterials > 0 && pMC->pMaterials) {
                    xmesh.materials.resize(pMC->NumMaterials);
                    for (DWORD i = 0; i < pMC->NumMaterials; ++i) {
                        const auto& mat = pMC->pMaterials[i];
                        xmesh.materials[i].name = "Material_" + std::to_string(i);
                        xmesh.materials[i].diffuseColor  = { mat.MatD3D.Diffuse.r, mat.MatD3D.Diffuse.g, mat.MatD3D.Diffuse.b };
                        xmesh.materials[i].diffuseAlpha  = mat.MatD3D.Diffuse.a;
                        xmesh.materials[i].specularColor = { mat.MatD3D.Specular.r, mat.MatD3D.Specular.g, mat.MatD3D.Specular.b };
                        xmesh.materials[i].specularPower = mat.MatD3D.Power;
                        xmesh.materials[i].emissiveColor = { mat.MatD3D.Emissive.r, mat.MatD3D.Emissive.g, mat.MatD3D.Emissive.b };

                        if (mat.pTextureFilename) {
                            xmesh.materials[i].textureFilename = shiftJisToUtf8(mat.pTextureFilename);
                        }
                    }
                }

                // Attribute buffer stores the material/subset index per face.
                if (numFaces > 0) {
                    xmesh.faceMaterialIndices.resize(numFaces, 0);
                    DWORD* pAttribs = nullptr;
                    if (SUCCEEDED(pCleanMesh->LockAttributeBuffer(D3DLOCK_READONLY, &pAttribs)) && pAttribs) {
                        for (DWORD f = 0; f < numFaces; ++f) {
                            xmesh.faceMaterialIndices[f] = pAttribs[f];
                        }
                        pCleanMesh->UnlockAttributeBuffer();
                    }
                }

                // 4. Extract skinning and bone weights.
                if (pMC->pSkinInfo) {
                    LPD3DXSKININFO pSkin = pMC->pSkinInfo;
                    DWORD numBones = pSkin->GetNumBones();

                    struct Influence {
                        int   jointIdx;
                        float weight;
                    };
                    std::vector<std::vector<Influence>> rawInfluences(numVertices);

                    xmesh.boneNames.resize(numBones);
                    xmesh.inverseBindMatrices.resize(numBones);

                    // Transform offset matrix by the inverse of the frame's world matrix.
                    D3DXMATRIX invWorldMat;
                    D3DXMatrixInverse(&invWorldMat, nullptr, &worldMat);

                    for (DWORD b = 0; b < numBones; ++b) {
                        LPCSTR boneName = pSkin->GetBoneName(b);
                        std::string utf8BoneName = boneName
                            ? shiftJisToUtf8(boneName)
                            : "Bone_" + std::to_string(b);
                        xmesh.boneNames[b] = utf8BoneName;

                        // Preserve the full skeleton chain: mark this bone
                        // and all its ancestors as bones in the node array.
                        auto bit = nameToIndexMap.find(utf8BoneName);
                        if (bit != nameToIndexMap.end()) {
                            markNodeAndAncestorsAsBones(model, bit->second);
                        }

                        // Transform offset matrix to world space:
                        //   offsetWorld = invWorldMat * offsetMatrix
                        const D3DXMATRIX* pOffsetMatrix = pSkin->GetBoneOffsetMatrix(b);
                        D3DXMATRIX offsetWorld;
                        D3DXMatrixMultiply(&offsetWorld, &invWorldMat, pOffsetMatrix);

                        // Convert offsetWorld to Blender space and store.
                        xmesh.inverseBindMatrices[b] = convertMatrixToBlender(offsetWorld);

                        // Gather bone influences.
                        DWORD numInfluences = pSkin->GetNumBoneInfluences(b);
                        if (numInfluences > 0) {
                            std::vector<DWORD> infVertices(numInfluences);
                            std::vector<float> infWeights(numInfluences);
                            pSkin->GetBoneInfluence(b, infVertices.data(), infWeights.data());

                            for (DWORD i = 0; i < numInfluences; ++i) {
                                DWORD vIdx = infVertices[i];
                                if (vIdx < numVertices) {
                                    rawInfluences[vIdx].push_back({ static_cast<int>(b), infWeights[i] });
                                }
                            }
                        }
                    }

                    // Cap influences to m_maxInfluences and normalize weights per vertex.
                    const int cap = m_maxInfluences;
                    for (DWORD vIdx = 0; vIdx < numVertices; ++vIdx) {
                        auto& infList = rawInfluences[vIdx];

                        // Sort descending by weight.
                        std::sort(infList.begin(), infList.end(),
                                  [](const Influence& a, const Influence& b) {
                                      return a.weight > b.weight;
                                  });

                        if (static_cast<int>(infList.size()) > cap) {
                            infList.resize(static_cast<size_t>(cap));
                        }

                        float sum = 0.0f;
                        for (const auto& inf : infList) {
                            sum += inf.weight;
                        }

                        for (size_t i = 0; i < 4; ++i) {
                            if (static_cast<int>(i) < cap && i < infList.size() && sum > 0.0f) {
                                xmesh.vertices[vIdx].jointIndices[i] = infList[i].jointIdx;
                                xmesh.vertices[vIdx].jointWeights[i] = infList[i].weight / sum;
                            } else {
                                xmesh.vertices[vIdx].jointIndices[i] = 0;
                                xmesh.vertices[vIdx].jointWeights[i] = 0.0f;
                            }
                        }
                    }

                    xmesh.hasSkin = true;
                }

                // Store mesh and update the node's mesh attachment index.
                int middlemanMeshIdx = static_cast<int>(model.meshes.size());
                model.meshes.push_back(xmesh);

                if (nodeIdx != -1) {
                    model.nodes[nodeIdx].meshIndex = middlemanMeshIdx;
                }

                pCleanMesh->Release();
            } else if (pCleanMesh) {
                // CloneMeshFVF returned success but a non-null mesh we
                // didn't take — release it to avoid a leak.  (Defensive;
                // the original code didn't have this branch either.)
                pCleanMesh->Release();
            }
        }
        pMC = pMC->pNextMeshContainer;
    }

    // Traverse children.
    D3DXFRAME* pChild = pFrame->pFrameFirstChild;
    while (pChild) {
        processFrameMeshes(pChild, model, frameToIndexMap, nameToIndexMap, frameToWorldMap);
        pChild = pChild->pFrameSibling;
    }
}
