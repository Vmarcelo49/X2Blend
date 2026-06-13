#pragma once

#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <d3d9.h>
#include <d3dx9.h>
#include <d3dx9anim.h>

#include <string>
#include <map>
#include "middleman.h"

class XLoader {
public:
    XLoader();
    ~XLoader();

    // Loads a .x model from file path and converts it to Middleman representation (XModel)
    bool loadModel(const std::string& filepath, XModel& outModel);

private:
    // Core Direct3D 9 headless setup
    bool initD3D9();
    void cleanupD3D9();

    // Headless Window handle and D3D9 pointers
    HWND m_hwnd = nullptr; 
    IDirect3D9* m_pD3D = nullptr;
    IDirect3DDevice9* m_pDevice = nullptr;

    // Helper to recursively process loaded D3DXFRAME hierarchy and build flat node array
    int processFrameHierarchy(
        D3DXFRAME* pFrame,
        int parentIdx,
        const D3DXMATRIX& parentWorld,
        XModel& model,
        std::map<D3DXFRAME*, int>& frameToIndexMap,
        std::map<std::string, int>& nameToIndexMap,
        std::map<D3DXFRAME*, D3DXMATRIX>& frameToWorldMap
    );

    // Helper to process mesh containers attached to frames
    void processMeshContainers(
        D3DXFRAME* pRootFrame,
        XModel& model,
        const std::map<D3DXFRAME*, int>& frameToIndexMap,
        const std::map<std::string, int>& nameToIndexMap,
        const std::map<D3DXFRAME*, D3DXMATRIX>& frameToWorldMap
    );

    // Helper to process animations from animation controller
    void processAnimations(
        ID3DXAnimationController* pAnimController,
        XModel& model,
        const std::map<std::string, int>& nameToIndexMap,
        D3DXFRAME* pRootFrame
    );
};
