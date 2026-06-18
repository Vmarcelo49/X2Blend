// d3d/hierarchy_allocator.h — ID3DXAllocateHierarchy implementation.
//
// The D3DXLoadMeshHierarchyFromX API takes a caller-supplied
// ID3DXAllocateHierarchy so the loader can hook the construction and
// destruction of every D3DXFRAME and D3DXMESHCONTAINER the parser
// produces.  This header declares the HierarchyAllocator class that
// D3DX calls back into; the implementation (and the file-local
// CustomFrame / CustomMeshContainer subclasses that carry the
// per-frame / per-mesh-container bookkeeping vectors) live in the .cpp.
//
// Ported verbatim from x_loader.cpp (lines 114-230):
// AnimateAllocateHierarchy + CustomFrame + CustomMeshContainer.
// The CustomFrame / CustomMeshContainer structs stay file-local in
// the .cpp so the header's only public surface is the allocator class
// itself.
#pragma once

#include <d3dx9.h>
#include <d3dx9anim.h>

class HierarchyAllocator : public ID3DXAllocateHierarchy {
public:
    STDMETHOD(CreateFrame)(LPCSTR Name, LPD3DXFRAME* ppNewFrame) override;
    STDMETHOD(CreateMeshContainer)(
        LPCSTR Name,
        const D3DXMESHDATA* pMeshData,
        const D3DXMATERIAL* pMaterials,
        const D3DXEFFECTINSTANCE* pEffectInstances,
        DWORD NumMaterials,
        const DWORD* pAdjacency,
        LPD3DXSKININFO pSkinInfo,
        LPD3DXMESHCONTAINER* ppNewMeshContainer) override;
    STDMETHOD(DestroyFrame)(LPD3DXFRAME pFrameToFree) override;
    STDMETHOD(DestroyMeshContainer)(LPD3DXMESHCONTAINER pMeshContainerToFree) override;
};
