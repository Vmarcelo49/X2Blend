#define INITGUID
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <d3d9.h>
#include <d3dx9.h>
#include <d3dx9anim.h>
#include <string>
#include <vector>
#include <map>
#include <iostream>
#include <fstream>
#include <sstream>
#include <algorithm>
#include <cstring>
#include <cmath>

// Mesh-extension templates that Wine's built-in d3dxof does not pre-register.
// These are needed so that Wine's D3DX parser doesn't fail when encountering them in .x files.
static const char* k_missingTemplates = R"x(
template XSkinMeshHeader {
  <3CF169CE-FF7C-44AB-93C0-F78F62D172E2>
  WORD nMaxSkinWeightsPerVertex;
  WORD nMaxSkinWeightsPerFace;
  WORD nBones;
}

template VertexDuplicationIndices {
  <B8D65549-D7C9-4995-89CF-53A9A8B031E3>
  DWORD nIndices;
  DWORD nOriginalVertices;
  array DWORD indices[nIndices];
}

template SkinWeights {
  <6F0D123B-BAD2-4167-A0D0-80224F25FABB>
  STRING transformNodeName;
  DWORD nWeights;
  array DWORD vertexIndices[nWeights];
  array float weights[nWeights];
  Matrix4x4 matrixOffset;
}
)x";

// Read a .x file from disk and, for text-format files, inject any missing mesh-extension
// template definitions right after the file header so that Wine's d3dxof parser registers
// them before encountering the corresponding objects.
static bool readAndPreprocessXFile(const std::string& filepath, std::string& content) {
    std::ifstream file(filepath, std::ios::binary);
    if (!file.is_open()) return false;

    std::ostringstream oss;
    oss << file.rdbuf();
    content = oss.str();
    file.close();

    // Only text-format .x files begin with "xof 0303txt" or "xof 0302txt".
    // Binary files already carry the template GUIDs in their header section.
    bool isTextFormat = (content.size() >= 11 &&
                         content.compare(0, 7, "xof 030") == 0 &&
                         (content[10] == 't' || content[10] == 'T'));

    if (isTextFormat) {
        // Find the end of the first line (the magic header line).
        size_t headerEnd = content.find('\n');
        if (headerEnd == std::string::npos) headerEnd = content.size();
        else ++headerEnd; // include the newline

        // Inject template declarations immediately after the header line.
        content.insert(headerEnd, k_missingTemplates);
    }

    return true;
}

// Helpers for Shift-JIS (MS932) conversion to Wide/UTF-8
static std::string convertShiftJisToUtf8(const std::string& sjisStr) {
    if (sjisStr.empty()) return "";
    int wideLen = MultiByteToWideChar(932, 0, sjisStr.c_str(), -1, nullptr, 0);
    if (wideLen <= 0) return sjisStr;

    std::vector<wchar_t> wideBuf(wideLen);
    MultiByteToWideChar(932, 0, sjisStr.c_str(), -1, wideBuf.data(), wideLen);

    int utf8Len = WideCharToMultiByte(CP_UTF8, 0, wideBuf.data(), -1, nullptr, 0, nullptr, nullptr);
    if (utf8Len <= 0) return sjisStr;

    std::vector<char> utf8Buf(utf8Len);
    WideCharToMultiByte(CP_UTF8, 0, wideBuf.data(), -1, utf8Buf.data(), utf8Len, nullptr, nullptr);

    return std::string(utf8Buf.data());
}

static std::wstring convertShiftJisToWString(const std::string& sjisStr) {
    if (sjisStr.empty()) return L"";
    int wideLen = MultiByteToWideChar(932, 0, sjisStr.c_str(), -1, nullptr, 0);
    if (wideLen <= 0) return L"";

    std::vector<wchar_t> wideBuf(wideLen);
    MultiByteToWideChar(932, 0, sjisStr.c_str(), -1, wideBuf.data(), wideLen);

    return std::wstring(wideBuf.data());
}

static std::wstring convertUtf8ToWString(const std::string& utf8Str) {
    if (utf8Str.empty()) return L"";
    int wideLen = MultiByteToWideChar(CP_UTF8, 0, utf8Str.c_str(), -1, nullptr, 0);
    if (wideLen <= 0) return L"";

    std::vector<wchar_t> wideBuf(wideLen);
    MultiByteToWideChar(CP_UTF8, 0, utf8Str.c_str(), -1, wideBuf.data(), wideLen);

    return std::wstring(wideBuf.data());
}

static std::wstring getDirectoryW(const std::wstring& filepath) {
    size_t lastSlash = filepath.find_last_of(L"\\/");
    if (lastSlash == std::wstring::npos) return L"";
    return filepath.substr(0, lastSlash + 1);
}

// Viewer specific custom D3DX structures
struct ViewerFrame : public D3DXFRAME {
    D3DXMATRIX worldMatrix;
};

struct ViewerMeshContainer : public D3DXMESHCONTAINER {
    ID3DXMesh* pOriginalMesh;
    ID3DXMesh* pSkinnedMesh;
    std::vector<IDirect3DTexture9*> textures;
    std::vector<D3DMATERIAL9> d3dMaterials;
};

// Hierarchy Allocator implementation
class ViewerAllocateHierarchy : public ID3DXAllocateHierarchy {
private:
    IDirect3DDevice9* m_pDevice;
    std::wstring m_modelDir;

public:
    ViewerAllocateHierarchy(IDirect3DDevice9* pDevice, const std::wstring& modelDir)
        : m_pDevice(pDevice), m_modelDir(modelDir) {}

    STDMETHOD(CreateFrame)(LPCSTR Name, LPD3DXFRAME* ppNewFrame) override {
        *ppNewFrame = nullptr;
        ViewerFrame* pFrame = new ViewerFrame();
        ZeroMemory(pFrame, sizeof(ViewerFrame));

        if (Name) {
            size_t len = strlen(Name) + 1;
            pFrame->Name = new char[len];
            strcpy(pFrame->Name, Name);
        }

        D3DXMatrixIdentity(&pFrame->TransformationMatrix);
        D3DXMatrixIdentity(&pFrame->worldMatrix);
        *ppNewFrame = pFrame;
        return S_OK;
    }

    STDMETHOD(CreateMeshContainer)(
        LPCSTR Name,
        const D3DXMESHDATA* pMeshData,
        const D3DXMATERIAL* pMaterials,
        const D3DXEFFECTINSTANCE* pEffectInstances,
        DWORD NumMaterials,
        const DWORD* pAdjacency,
        LPD3DXSKININFO pSkinInfo,
        LPD3DXMESHCONTAINER* ppNewMeshContainer
    ) override {
        *ppNewMeshContainer = nullptr;

        if (pMeshData->Type != D3DXMESHTYPE_MESH) {
            return E_FAIL;
        }

        ViewerMeshContainer* pMeshContainer = new ViewerMeshContainer();
        ZeroMemory(pMeshContainer, sizeof(ViewerMeshContainer));

        if (Name) {
            size_t len = strlen(Name) + 1;
            pMeshContainer->Name = new char[len];
            strcpy(pMeshContainer->Name, Name);
        }

        pMeshContainer->MeshData.Type = D3DXMESHTYPE_MESH;
        pMeshContainer->MeshData.pMesh = pMeshData->pMesh;
        pMeshContainer->MeshData.pMesh->AddRef();
        pMeshContainer->pOriginalMesh = pMeshData->pMesh;
        pMeshContainer->pOriginalMesh->AddRef();

        pMeshContainer->NumMaterials = NumMaterials;
        if (NumMaterials > 0) {
            pMeshContainer->d3dMaterials.resize(NumMaterials);
            pMeshContainer->textures.resize(NumMaterials, nullptr);

            for (DWORD i = 0; i < NumMaterials; ++i) {
                if (pMaterials) {
                    pMeshContainer->d3dMaterials[i] = pMaterials[i].MatD3D;
                    pMeshContainer->d3dMaterials[i].Ambient = pMeshContainer->d3dMaterials[i].Diffuse;
                } else {
                    ZeroMemory(&pMeshContainer->d3dMaterials[i], sizeof(D3DMATERIAL9));
                    pMeshContainer->d3dMaterials[i].Diffuse = { 1.0f, 1.0f, 1.0f, 1.0f };
                    pMeshContainer->d3dMaterials[i].Ambient = { 1.0f, 1.0f, 1.0f, 1.0f };
                }

                if (pMaterials && pMaterials[i].pTextureFilename) {
                    std::string texNameSjis = pMaterials[i].pTextureFilename;
                    std::wstring texNameW = convertShiftJisToWString(texNameSjis);
                    std::wstring fullPathW = m_modelDir + texNameW;

                    IDirect3DTexture9* pTexture = nullptr;
                    HRESULT hr = D3DXCreateTextureFromFileW(m_pDevice, fullPathW.c_str(), &pTexture);
                    if (SUCCEEDED(hr)) {
                        pMeshContainer->textures[i] = pTexture;
                    }
                }
            }
        }

        if (pSkinInfo) {
            pMeshContainer->pSkinInfo = pSkinInfo;
            pMeshContainer->pSkinInfo->AddRef();

            ID3DXMesh* pSkinnedMesh = nullptr;
            HRESULT hr = pMeshContainer->pOriginalMesh->CloneMeshFVF(
                D3DXMESH_MANAGED,
                pMeshContainer->pOriginalMesh->GetFVF(),
                m_pDevice,
                &pSkinnedMesh
            );
            if (SUCCEEDED(hr)) {
                pMeshContainer->pSkinnedMesh = pSkinnedMesh;
                pMeshContainer->MeshData.pMesh->Release();
                pMeshContainer->MeshData.pMesh = pSkinnedMesh;
                pMeshContainer->MeshData.pMesh->AddRef();
            }
        }

        *ppNewMeshContainer = pMeshContainer;
        return S_OK;
    }

    STDMETHOD(DestroyFrame)(LPD3DXFRAME pFrameToFree) override {
        if (!pFrameToFree) return S_OK;
        delete[] pFrameToFree->Name;
        delete pFrameToFree;
        return S_OK;
    }

    STDMETHOD(DestroyMeshContainer)(LPD3DXMESHCONTAINER pMeshContainerToFree) override {
        if (!pMeshContainerToFree) return S_OK;
        ViewerMeshContainer* pCustom = static_cast<ViewerMeshContainer*>(pMeshContainerToFree);

        delete[] pCustom->Name;
        if (pCustom->pOriginalMesh) pCustom->pOriginalMesh->Release();
        if (pCustom->pSkinnedMesh) pCustom->pSkinnedMesh->Release();
        if (pCustom->MeshData.pMesh) pCustom->MeshData.pMesh->Release();

        for (auto pTex : pCustom->textures) {
            if (pTex) pTex->Release();
        }

        if (pCustom->pSkinInfo) pCustom->pSkinInfo->Release();

        delete pCustom;
        return S_OK;
    }
};

// Global variables for camera & input controls
float g_yaw = 0.0f;
float g_pitch = 0.0f;
float g_distance = 3.0f;
D3DXVECTOR3 g_target(0.0f, 1.0f, 0.0f);

bool g_isDragging = false;
POINT g_lastMousePos;

ID3DXAnimationController* g_pAnimController = nullptr;
std::vector<std::string> g_animNames;
int g_activeAnimIdx = 0;
HWND g_hwnd = nullptr;
std::string g_modelName;

void UpdateWindowTitle() {
    std::string title = "DirectX 9 Model Viewer - " + g_modelName;
    if (!g_animNames.empty() && g_activeAnimIdx >= 0 && g_activeAnimIdx < (int)g_animNames.size()) {
        title += " | Anim: [" + g_animNames[g_activeAnimIdx] + "] (" + std::to_string(g_activeAnimIdx + 1) + "/" + std::to_string(g_animNames.size()) + ")";
    } else {
        title += " | No Animations";
    }
    title += " | Left/Right/Space: Switch | Drag Mouse: Orbit | Scroll: Zoom";
    SetWindowTextA(g_hwnd, title.c_str());
}

void SwitchToAnimation(int index) {
    if (!g_pAnimController) return;
    DWORD numSets = g_pAnimController->GetNumAnimationSets();
    if (numSets == 0) return;

    g_activeAnimIdx = index;
    if (g_activeAnimIdx < 0) g_activeAnimIdx = (int)numSets - 1;
    if (g_activeAnimIdx >= (int)numSets) g_activeAnimIdx = 0;

    ID3DXAnimationSet* pSet = nullptr;
    if (SUCCEEDED(g_pAnimController->GetAnimationSet((DWORD)g_activeAnimIdx, &pSet)) && pSet) {
        g_pAnimController->SetTrackAnimationSet(0, pSet);
        g_pAnimController->SetTrackEnable(0, TRUE);
        g_pAnimController->SetTrackWeight(0, 1.0f);
        g_pAnimController->SetTrackSpeed(0, 1.0f);
        g_pAnimController->SetTrackPosition(0, 0.0);
        pSet->Release();
    }
    UpdateWindowTitle();
}

// Win32 Window Procedure
LRESULT CALLBACK WndProc(HWND hwnd, UINT msg, WPARAM wParam, LPARAM lParam) {
    switch (msg) {
    case WM_DESTROY:
        PostQuitMessage(0);
        return 0;

    case WM_LBUTTONDOWN:
        g_isDragging = true;
        g_lastMousePos.x = (short)LOWORD(lParam);
        g_lastMousePos.y = (short)HIWORD(lParam);
        SetCapture(hwnd);
        return 0;

    case WM_LBUTTONUP:
        g_isDragging = false;
        ReleaseCapture();
        return 0;

    case WM_MOUSEMOVE:
        if (g_isDragging) {
            int x = (short)LOWORD(lParam);
            int y = (short)HIWORD(lParam);

            float dx = (float)(x - g_lastMousePos.x);
            float dy = (float)(y - g_lastMousePos.y);

            g_yaw += dx * 0.005f;
            g_pitch += dy * 0.005f;

            // Clamp pitch to avoid flipping over the top
            const float k_maxPitch = 85.0f * (3.14159265f / 180.0f);
            if (g_pitch > k_maxPitch) g_pitch = k_maxPitch;
            if (g_pitch < -k_maxPitch) g_pitch = -k_maxPitch;

            g_lastMousePos.x = x;
            g_lastMousePos.y = y;
        }
        return 0;

    case WM_MOUSEWHEEL: {
        short zDelta = (short)HIWORD(wParam);
        g_distance -= (float)zDelta * 0.002f;
        if (g_distance < 0.5f) g_distance = 0.5f;
        if (g_distance > 50.0f) g_distance = 50.0f;
        return 0;
    }

    case WM_KEYDOWN:
        if (wParam == VK_SPACE || wParam == VK_RIGHT) {
            SwitchToAnimation(g_activeAnimIdx + 1);
        } else if (wParam == VK_LEFT) {
            SwitchToAnimation(g_activeAnimIdx - 1);
        }
        return 0;
    }
    return DefWindowProcA(hwnd, msg, wParam, lParam);
}

// Recursive matrix updating
void UpdateFrameMatrices(D3DXFRAME* pFrame, const D3DXMATRIX* pParentMatrix) {
    ViewerFrame* pViewerFrame = static_cast<ViewerFrame*>(pFrame);
    if (pParentMatrix) {
        D3DXMatrixMultiply(&pViewerFrame->worldMatrix, &pViewerFrame->TransformationMatrix, pParentMatrix);
    } else {
        pViewerFrame->worldMatrix = pViewerFrame->TransformationMatrix;
    }

    if (pViewerFrame->pFrameFirstChild) {
        UpdateFrameMatrices(pViewerFrame->pFrameFirstChild, &pViewerFrame->worldMatrix);
    }
    if (pViewerFrame->pFrameSibling) {
        UpdateFrameMatrices(pViewerFrame->pFrameSibling, pParentMatrix);
    }
}

// Recursive skinned mesh update
void UpdateSkinnedMeshes(D3DXFRAME* pFrame, D3DXFRAME* pRootFrame) {
    if (!pFrame) return;

    D3DXMESHCONTAINER* pMC = pFrame->pMeshContainer;
    while (pMC) {
        ViewerMeshContainer* pVMC = static_cast<ViewerMeshContainer*>(pMC);
        if (pVMC->pSkinInfo && pVMC->pSkinnedMesh) {
            DWORD numBones = pVMC->pSkinInfo->GetNumBones();
            std::vector<D3DXMATRIX> boneTransforms(numBones);

            for (DWORD i = 0; i < numBones; ++i) {
                LPCSTR boneName = pVMC->pSkinInfo->GetBoneName(i);
                D3DXFRAME* pBoneFrame = D3DXFrameFind(pRootFrame, boneName);
                if (pBoneFrame) {
                    ViewerFrame* pVF = static_cast<ViewerFrame*>(pBoneFrame);
                    D3DXMatrixMultiply(&boneTransforms[i], pVMC->pSkinInfo->GetBoneOffsetMatrix(i), &pVF->worldMatrix);
                } else {
                    D3DXMatrixIdentity(&boneTransforms[i]);
                }
            }

            void* pSrc = nullptr;
            void* pDst = nullptr;
            if (SUCCEEDED(pVMC->pOriginalMesh->LockVertexBuffer(D3DLOCK_READONLY, &pSrc))) {
                if (SUCCEEDED(pVMC->pSkinnedMesh->LockVertexBuffer(0, &pDst))) {
                    pVMC->pSkinInfo->UpdateSkinnedMesh(boneTransforms.data(), nullptr, pSrc, pDst);
                    pVMC->pSkinnedMesh->UnlockVertexBuffer();
                }
                pVMC->pOriginalMesh->UnlockVertexBuffer();
            }
        }
        pMC = pMC->pNextMeshContainer;
    }

    if (pFrame->pFrameFirstChild) {
        UpdateSkinnedMeshes(pFrame->pFrameFirstChild, pRootFrame);
    }
    if (pFrame->pFrameSibling) {
        UpdateSkinnedMeshes(pFrame->pFrameSibling, pRootFrame);
    }
}

// Recursive rendering traversal
void RenderFrame(D3DXFRAME* pFrame, IDirect3DDevice9* pDevice) {
    if (!pFrame) return;

    ViewerFrame* pViewerFrame = static_cast<ViewerFrame*>(pFrame);
    D3DXMESHCONTAINER* pMC = pViewerFrame->pMeshContainer;
    while (pMC) {
        ViewerMeshContainer* pVMC = static_cast<ViewerMeshContainer*>(pMC);

        // Skinned vertices are already calculated in the model's root coordinate space.
        // Unskinned meshes require their node frame's transformation matrix.
        D3DXMATRIX globalScale;
        D3DXMatrixScaling(&globalScale, 0.01f, 0.01f, 0.01f);

        if (pVMC->pSkinInfo) {
            pDevice->SetTransform(D3DTS_WORLD, &globalScale);
        } else {
            D3DXMATRIX drawMat;
            D3DXMatrixMultiply(&drawMat, &pViewerFrame->worldMatrix, &globalScale);
            pDevice->SetTransform(D3DTS_WORLD, &drawMat);
        }

        ID3DXMesh* pMesh = pVMC->MeshData.pMesh;
        for (DWORD i = 0; i < pVMC->NumMaterials; ++i) {
            if (i < pVMC->d3dMaterials.size()) {
                pDevice->SetMaterial(&pVMC->d3dMaterials[i]);
            }
            if (i < pVMC->textures.size() && pVMC->textures[i]) {
                pDevice->SetTexture(0, pVMC->textures[i]);
            } else {
                pDevice->SetTexture(0, nullptr);
            }
            pMesh->DrawSubset(i);
        }

        pMC = pMC->pNextMeshContainer;
    }

    if (pFrame->pFrameFirstChild) {
        RenderFrame(pFrame->pFrameFirstChild, pDevice);
    }
    if (pFrame->pFrameSibling) {
        RenderFrame(pFrame->pFrameSibling, pDevice);
    }
}

int main(int argc, char* argv[]) {
    if (argc < 2) {
        std::cout << "Usage: viewer.exe <model.x>\n";
        return 1;
    }
    std::string modelPath = argv[1];

    size_t lastSlash = modelPath.find_last_of("\\/");
    if (lastSlash != std::string::npos) {
        g_modelName = modelPath.substr(lastSlash + 1);
    } else {
        g_modelName = modelPath;
    }

    // Register Win32 class
    HINSTANCE hInst = GetModuleHandle(nullptr);
    WNDCLASSA wc = {};
    wc.lpfnWndProc = WndProc;
    wc.hInstance = hInst;
    wc.lpszClassName = "D3D9ModelViewer";
    wc.hCursor = LoadCursor(nullptr, IDC_ARROW);
    RegisterClassA(&wc);

    // Create a fixed size window (800x600 client area)
    RECT wr = { 0, 0, 800, 600 };
    AdjustWindowRect(&wr, WS_OVERLAPPED | WS_CAPTION | WS_SYSMENU | WS_MINIMIZEBOX, FALSE);

    HWND hwnd = CreateWindowA(
        "D3D9ModelViewer",
        "DirectX 9 Model Viewer",
        WS_OVERLAPPED | WS_CAPTION | WS_SYSMENU | WS_MINIMIZEBOX,
        CW_USEDEFAULT, CW_USEDEFAULT,
        wr.right - wr.left, wr.bottom - wr.top,
        nullptr, nullptr, hInst, nullptr
    );

    if (!hwnd) {
        std::cerr << "Failed to create window\n";
        return 1;
    }
    g_hwnd = hwnd;

    // Initialize D3D9
    IDirect3D9* pD3D = Direct3DCreate9(D3D_SDK_VERSION);
    if (!pD3D) {
        std::cerr << "Failed to initialize D3D9\n";
        return 1;
    }

    D3DPRESENT_PARAMETERS d3dpp = {};
    d3dpp.Windowed = TRUE;
    d3dpp.SwapEffect = D3DSWAPEFFECT_DISCARD;
    d3dpp.hDeviceWindow = hwnd;
    d3dpp.EnableAutoDepthStencil = TRUE;
    d3dpp.AutoDepthStencilFormat = D3DFMT_D16;

    IDirect3DDevice9* pDevice = nullptr;
    HRESULT hr = pD3D->CreateDevice(
        D3DADAPTER_DEFAULT,
        D3DDEVTYPE_HAL,
        hwnd,
        D3DCREATE_SOFTWARE_VERTEXPROCESSING,
        &d3dpp,
        &pDevice
    );

    if (FAILED(hr)) {
        std::cerr << "Failed to create D3D9 device. Trying fallback REF device...\n";
        hr = pD3D->CreateDevice(
            D3DADAPTER_DEFAULT,
            D3DDEVTYPE_REF,
            hwnd,
            D3DCREATE_SOFTWARE_VERTEXPROCESSING,
            &d3dpp,
            &pDevice
        );
    }

    if (FAILED(hr)) {
        std::cerr << "Failed to create D3D9 device (HRESULT " << std::hex << hr << ")\n";
        pD3D->Release();
        return 1;
    }

    // Preprocess file for templates
    std::string xFileContent;
    if (!readAndPreprocessXFile(modelPath, xFileContent)) {
        std::cerr << "Failed to read .x file: " << modelPath << "\n";
        pDevice->Release();
        pD3D->Release();
        return 1;
    }

    // Write preprocessed content to temp file
    char tempPath[MAX_PATH];
    char tempFile[MAX_PATH];
    GetTempPathA(MAX_PATH, tempPath);
    GetTempFileNameA(tempPath, "x2g", 0, tempFile);
    std::string tempXFile = std::string(tempFile) + ".x";
    DeleteFileA(tempFile);

    {
        std::ofstream tmpOut(tempXFile, std::ios::binary);
        if (!tmpOut.is_open()) {
            std::cerr << "Failed to write temp file: " << tempXFile << "\n";
            pDevice->Release();
            pD3D->Release();
            return 1;
        }
        tmpOut.write(xFileContent.data(), xFileContent.size());
    }

    // Load mesh hierarchy and animation controller
    std::wstring modelPathW = convertUtf8ToWString(modelPath);
    std::wstring modelDirW = getDirectoryW(modelPathW);

    ViewerAllocateHierarchy allocHierarchy(pDevice, modelDirW);
    LPD3DXFRAME pRootFrame = nullptr;

    hr = D3DXLoadMeshHierarchyFromXA(
        tempXFile.c_str(),
        D3DXMESH_SYSTEMMEM,
        pDevice,
        &allocHierarchy,
        nullptr,
        &pRootFrame,
        &g_pAnimController
    );

    DeleteFileA(tempXFile.c_str());

    if (FAILED(hr)) {
        std::cerr << "D3DXLoadMeshHierarchyFromXA failed with HRESULT " << std::hex << hr << "\n";
        pDevice->Release();
        pD3D->Release();
        return 1;
    }

    // Initialize animation listings
    if (g_pAnimController) {
        DWORD numSets = g_pAnimController->GetNumAnimationSets();
        for (DWORD i = 0; i < numSets; ++i) {
            ID3DXAnimationSet* pSet = nullptr;
            if (SUCCEEDED(g_pAnimController->GetAnimationSet(i, &pSet)) && pSet) {
                LPCSTR name = pSet->GetName();
                if (name) {
                    g_animNames.push_back(convertShiftJisToUtf8(name));
                } else {
                    g_animNames.push_back("Unnamed_" + std::to_string(i));
                }
                pSet->Release();
            }
        }
        if (numSets > 0) {
            SwitchToAnimation(0);
        }
    }
    UpdateWindowTitle();

    ShowWindow(hwnd, SW_SHOWDEFAULT);
    UpdateWindow(hwnd);

    // Main render loop
    DWORD lastTime = GetTickCount();
    MSG msg = {};
    while (msg.message != WM_QUIT) {
        if (PeekMessageA(&msg, nullptr, 0, 0, PM_REMOVE)) {
            TranslateMessage(&msg);
            DispatchMessageA(&msg);
        } else {
            DWORD currTime = GetTickCount();
            float deltaTime = (float)(currTime - lastTime) / 1000.0f;
            lastTime = currTime;

            if (deltaTime > 0.1f) deltaTime = 0.1f;

            if (g_pAnimController) {
                g_pAnimController->AdvanceTime((double)deltaTime, nullptr);
            }

            D3DXMATRIX identity;
            D3DXMatrixIdentity(&identity);
            if (pRootFrame) {
                UpdateFrameMatrices(pRootFrame, &identity);
                UpdateSkinnedMeshes(pRootFrame, pRootFrame);
            }

            pDevice->Clear(0, nullptr, D3DCLEAR_TARGET | D3DCLEAR_ZBUFFER, D3DCOLOR_XRGB(40, 42, 54), 1.0f, 0);

            if (SUCCEEDED(pDevice->BeginScene())) {
                // Orbit camera matrix calculation
                float theta = g_yaw;
                float phi = g_pitch;
                float x = g_target.x + g_distance * cosf(phi) * sinf(theta);
                float y = g_target.y + g_distance * sinf(phi);
                float z = g_target.z - g_distance * cosf(phi) * cosf(theta);

                D3DXMATRIX viewMat;
                D3DXVECTOR3 eye(x, y, z);
                D3DXVECTOR3 up(0.0f, 1.0f, 0.0f);
                D3DXMatrixLookAtLH(&viewMat, &eye, &g_target, &up);
                pDevice->SetTransform(D3DTS_VIEW, &viewMat);

                D3DXMATRIX projMat;
                D3DXMatrixPerspectiveFovLH(&projMat, D3DXToRadian(60.0f), 800.0f / 600.0f, 0.1f, 100.0f);
                pDevice->SetTransform(D3DTS_PROJECTION, &projMat);

                pDevice->SetRenderState(D3DRS_LIGHTING, TRUE);
                pDevice->SetRenderState(D3DRS_AMBIENT, D3DCOLOR_COLORVALUE(0.3f, 0.3f, 0.3f, 1.0f));
                pDevice->SetRenderState(D3DRS_ZENABLE, TRUE);
                pDevice->SetRenderState(D3DRS_CULLMODE, D3DCULL_CCW);

                pDevice->SetRenderState(D3DRS_ALPHABLENDENABLE, TRUE);
                pDevice->SetRenderState(D3DRS_SRCBLEND, D3DBLEND_SRCALPHA);
                pDevice->SetRenderState(D3DRS_DESTBLEND, D3DBLEND_INVSRCALPHA);
                pDevice->SetRenderState(D3DRS_ALPHATESTENABLE, TRUE);
                pDevice->SetRenderState(D3DRS_ALPHAREF, 0x08);
                pDevice->SetRenderState(D3DRS_ALPHAFUNC, D3DCMP_GREATEREQUAL);

                pDevice->SetSamplerState(0, D3DSAMP_MINFILTER, D3DTEXF_LINEAR);
                pDevice->SetSamplerState(0, D3DSAMP_MAGFILTER, D3DTEXF_LINEAR);
                pDevice->SetSamplerState(0, D3DSAMP_MIPFILTER, D3DTEXF_LINEAR);

                D3DLIGHT9 light;
                ZeroMemory(&light, sizeof(light));
                light.Type = D3DLIGHT_DIRECTIONAL;
                light.Diffuse = { 0.8f, 0.8f, 0.8f, 1.0f };
                light.Specular = { 0.3f, 0.3f, 0.3f, 1.0f };
                light.Direction = { 0.5f, -1.0f, 0.5f };
                pDevice->SetLight(0, &light);
                pDevice->LightEnable(0, TRUE);

                if (pRootFrame) {
                    RenderFrame(pRootFrame, pDevice);
                }

                pDevice->EndScene();
            }

            pDevice->Present(nullptr, nullptr, nullptr, nullptr);
        }
    }

    if (pRootFrame) {
        D3DXFrameDestroy(pRootFrame, &allocHierarchy);
    }
    if (g_pAnimController) {
        g_pAnimController->Release();
    }
    pDevice->Release();
    pD3D->Release();

    return 0;
}
