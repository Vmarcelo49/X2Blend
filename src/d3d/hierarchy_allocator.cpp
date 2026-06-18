// d3d/hierarchy_allocator.cpp — ID3DXAllocateHierarchy implementation.
//
// Verbatim port of the AnimateAllocateHierarchy class plus its
// CustomFrame / CustomMeshContainer helpers from x_loader.cpp
// (lines 114-230).  The two custom subclasses add std::vector-backed
// storage for materials and adjacency so the allocator can own the
// memory and free it in DestroyMeshContainer without leaking.
//
// The two subclasses are kept file-local (anonymous namespace) so the
// header only exposes the HierarchyAllocator class.
#include "d3d/hierarchy_allocator.h"

#include <cstring>
#include <vector>

namespace {

// Custom D3DXFRAME subclass.  No additional fields are needed beyond
// what the base D3DXFRAME already carries; the subclass exists only so
// the allocator can `new`/`delete` frames of a known static type (and
// so a future refactor could attach per-frame data without changing the
// public HierarchyAllocator interface).
struct CustomFrame : public D3DXFRAME {};

// Custom D3DXMESHCONTAINER subclass.  Owns the materials and adjacency
// vectors so the loader can read them after D3DXLoadMeshHierarchyFromX
// returns, and so DestroyMeshContainer can free everything cleanly.
struct CustomMeshContainer : public D3DXMESHCONTAINER {
    std::vector<D3DXMATERIAL> materials;
    std::vector<DWORD>        adjacency;
};

} // namespace

STDMETHODIMP HierarchyAllocator::CreateFrame(LPCSTR Name, LPD3DXFRAME* ppNewFrame) {
    *ppNewFrame = nullptr;
    CustomFrame* pFrame = new CustomFrame();
    std::memset(static_cast<void*>(pFrame), 0, sizeof(CustomFrame));

    if (Name) {
        size_t len = std::strlen(Name) + 1;
        pFrame->Name = new char[len];
        std::strcpy(pFrame->Name, Name);
    }

    D3DXMatrixIdentity(&pFrame->TransformationMatrix);
    *ppNewFrame = pFrame;
    return S_OK;
}

STDMETHODIMP HierarchyAllocator::CreateMeshContainer(
    LPCSTR Name,
    const D3DXMESHDATA* pMeshData,
    const D3DXMATERIAL* pMaterials,
    const D3DXEFFECTINSTANCE* /*pEffectInstances*/,
    DWORD NumMaterials,
    const DWORD* pAdjacency,
    LPD3DXSKININFO pSkinInfo,
    LPD3DXMESHCONTAINER* ppNewMeshContainer
) {
    *ppNewMeshContainer = nullptr;

    // We only support standard triangle meshes.
    if (pMeshData->Type != D3DXMESHTYPE_MESH) {
        return E_FAIL;
    }

    CustomMeshContainer* pMeshContainer = new CustomMeshContainer();

    if (Name) {
        size_t len = std::strlen(Name) + 1;
        pMeshContainer->Name = new char[len];
        std::strcpy(pMeshContainer->Name, Name);
    }

    // Copy and reference the ID3DXMesh.
    pMeshContainer->MeshData.Type    = D3DXMESHTYPE_MESH;
    pMeshContainer->MeshData.pMesh   = pMeshData->pMesh;
    pMeshContainer->MeshData.pMesh->AddRef();

    // Copy materials and texture filenames.
    pMeshContainer->NumMaterials = NumMaterials;
    if (NumMaterials > 0 && pMaterials) {
        pMeshContainer->materials.resize(NumMaterials);
        for (DWORD i = 0; i < NumMaterials; ++i) {
            pMeshContainer->materials[i] = pMaterials[i];
            if (pMaterials[i].pTextureFilename) {
                size_t texLen = std::strlen(pMaterials[i].pTextureFilename) + 1;
                char* pTexName = new char[texLen];
                std::strcpy(pTexName, pMaterials[i].pTextureFilename);
                pMeshContainer->materials[i].pTextureFilename = pTexName;
            }
        }
        pMeshContainer->pMaterials = pMeshContainer->materials.data();
    }

    // Copy adjacency (3 DWORDs per face).
    if (pAdjacency) {
        DWORD numFaces = pMeshData->pMesh->GetNumFaces();
        pMeshContainer->adjacency.assign(pAdjacency, pAdjacency + (3 * numFaces));
        pMeshContainer->pAdjacency = pMeshContainer->adjacency.data();
    }

    // Copy and reference SkinInfo.
    if (pSkinInfo) {
        pMeshContainer->pSkinInfo = pSkinInfo;
        pMeshContainer->pSkinInfo->AddRef();
    }

    *ppNewMeshContainer = pMeshContainer;
    return S_OK;
}

STDMETHODIMP HierarchyAllocator::DestroyFrame(LPD3DXFRAME pFrameToFree) {
    if (!pFrameToFree) return S_OK;
    delete[] pFrameToFree->Name;
    delete pFrameToFree;
    return S_OK;
}

STDMETHODIMP HierarchyAllocator::DestroyMeshContainer(LPD3DXMESHCONTAINER pMeshContainerToFree) {
    if (!pMeshContainerToFree) return S_OK;
    CustomMeshContainer* pCustom = static_cast<CustomMeshContainer*>(pMeshContainerToFree);

    delete[] pCustom->Name;
    if (pCustom->MeshData.pMesh) {
        pCustom->MeshData.pMesh->Release();
    }
    for (auto& mat : pCustom->materials) {
        delete[] mat.pTextureFilename;
    }
    if (pCustom->pSkinInfo) {
        pCustom->pSkinInfo->Release();
    }
    delete pCustom;
    return S_OK;
}
