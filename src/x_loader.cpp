#define INITGUID
#include "x_loader.h"
#include <iostream>
#include <fstream>
#include <sstream>
#include <algorithm>
#include <cstring>
#include <vector>

// Mesh-extension templates that Wine's built-in d3dxof does not pre-register.
// By injecting them as inline template definitions right after the .x file header,
// d3dxof registers them during the parse pass and can then handle the corresponding
// objects (SkinWeights, XSkinMeshHeader) without errors.
//
// GUIDs and field layouts are taken verbatim from the DirectX 9 SDK (rmxftmpl.x).
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
        // d3dxof processes template blocks before object blocks, so inserting them
        // here ensures they are registered before any SkinWeights / XSkinMeshHeader
        // objects are encountered.
        content.insert(headerEnd, k_missingTemplates);
    }

    return true;
}

// Helper to convert Shift-JIS strings (CP_932) to UTF-8 using standard Win32 APIs
static std::string convertShiftJisToUtf8(const std::string& sjisStr) {
    if (sjisStr.empty()) return "";

    // CP_932 is the Code Page for Microsoft's Shift-JIS extension (MS Kanji)
    int wideLen = MultiByteToWideChar(932, 0, sjisStr.c_str(), -1, nullptr, 0);
    if (wideLen <= 0) return sjisStr; // Return original as fallback

    std::vector<wchar_t> wideBuf(wideLen);
    MultiByteToWideChar(932, 0, sjisStr.c_str(), -1, wideBuf.data(), wideLen);

    int utf8Len = WideCharToMultiByte(CP_UTF8, 0, wideBuf.data(), -1, nullptr, 0, nullptr, nullptr);
    if (utf8Len <= 0) return sjisStr;

    std::vector<char> utf8Buf(utf8Len);
    WideCharToMultiByte(CP_UTF8, 0, wideBuf.data(), -1, utf8Buf.data(), utf8Len, nullptr, nullptr);

    return std::string(utf8Buf.data());
}

static XMatrix4x4 convertMatrixToBlender(const D3DXMATRIX& mat) {
    XMatrix4x4 out;
    int map[4] = {0, 2, 1, 3}; // Swap Y (1) and Z (2) for row-major matrix
    for (int r = 0; r < 4; ++r) {
        for (int c = 0; c < 4; ++c) {
            out.m[map[r]][map[c]] = mat.m[r][c];
        }
    }
    return out;
}

static void markNodeAndAncestorsAsBones(XModel& model, int nodeIndex) {
    while (nodeIndex >= 0 && nodeIndex < static_cast<int>(model.nodes.size())) {
        XNode& node = model.nodes[static_cast<size_t>(nodeIndex)];
        node.isBone = true;
        nodeIndex = node.parentIndex;
    }
}

// Custom structures extending D3DXFRAME and D3DXMESHCONTAINER for clean memory management
struct CustomFrame : public D3DXFRAME {
    // Sibling and FirstChild are managed recursively, no extra fields needed
};

struct CustomMeshContainer : public D3DXMESHCONTAINER {
    std::vector<D3DXMATERIAL> materials;
    std::vector<DWORD> adjacency;
};

// Custom implementation of ID3DXAllocateHierarchy
class AnimateAllocateHierarchy : public ID3DXAllocateHierarchy {
public:
    STDMETHOD(CreateFrame)(LPCSTR Name, LPD3DXFRAME *ppNewFrame) override {
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

    STDMETHOD(CreateMeshContainer)(
        LPCSTR Name,
        const D3DXMESHDATA *pMeshData,
        const D3DXMATERIAL *pMaterials,
        const D3DXEFFECTINSTANCE *pEffectInstances,
        DWORD NumMaterials,
        const DWORD *pAdjacency,
        LPD3DXSKININFO pSkinInfo,
        LPD3DXMESHCONTAINER *ppNewMeshContainer
    ) override {
        *ppNewMeshContainer = nullptr;

        // We only support standard triangle meshes
        if (pMeshData->Type != D3DXMESHTYPE_MESH) {
            return E_FAIL;
        }

        CustomMeshContainer* pMeshContainer = new CustomMeshContainer();

        if (Name) {
            size_t len = std::strlen(Name) + 1;
            pMeshContainer->Name = new char[len];
            std::strcpy(pMeshContainer->Name, Name);
        }

        // Copy and Reference the ID3DXMesh
        pMeshContainer->MeshData.Type = D3DXMESHTYPE_MESH;
        pMeshContainer->MeshData.pMesh = pMeshData->pMesh;
        pMeshContainer->MeshData.pMesh->AddRef();

        // Copy materials & texture names
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

        // Copy adjacency
        if (pAdjacency) {
            DWORD numFaces = pMeshData->pMesh->GetNumFaces();
            pMeshContainer->adjacency.assign(pAdjacency, pAdjacency + (3 * numFaces));
            pMeshContainer->pAdjacency = pMeshContainer->adjacency.data();
        }

        // Copy and Reference SkinInfo
        if (pSkinInfo) {
            pMeshContainer->pSkinInfo = pSkinInfo;
            pMeshContainer->pSkinInfo->AddRef();
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
};

// XLoader constructor
XLoader::XLoader() {}

// XLoader destructor
XLoader::~XLoader() {
    cleanupD3D9();
}

bool XLoader::initD3D9() {
    // Register standard Win32 class and create a dummy hidden window
    HINSTANCE hInst = GetModuleHandle(nullptr);
    WNDCLASSA wc = {};
    wc.lpfnWndProc = DefWindowProcA;
    wc.hInstance = hInst;
    wc.lpszClassName = "X2GltfDummyWindow";
    RegisterClassA(&wc);

    HWND hwnd = CreateWindowA(
        "X2GltfDummyWindow", "Headless",
        WS_POPUP, 0, 0, 640, 480,
        nullptr, nullptr, hInst, nullptr
    );
    if (!hwnd) {
        std::cerr << "[Error] Failed to create dummy window.\n";
        return false;
    }
    m_hwnd = hwnd;

    // Create D3D9 core instance
    m_pD3D = Direct3DCreate9(D3D_SDK_VERSION);
    if (!m_pD3D) {
        std::cerr << "[Error] Failed to initialize Direct3D 9.\n";
        return false;
    }

    D3DPRESENT_PARAMETERS d3dpp = {};
    d3dpp.Windowed = TRUE;
    d3dpp.SwapEffect = D3DSWAPEFFECT_DISCARD;
    d3dpp.hDeviceWindow = hwnd;

    // Try high-performance hardware device first
    HRESULT hr = m_pD3D->CreateDevice(
        D3DADAPTER_DEFAULT,
        D3DDEVTYPE_HAL,
        hwnd,
        D3DCREATE_SOFTWARE_VERTEXPROCESSING,
        &d3dpp,
        &m_pDevice
    );

    // Fallback to Null Reference device (ideal for headless conversion tools)
    if (FAILED(hr)) {
        hr = m_pD3D->CreateDevice(
            D3DADAPTER_DEFAULT,
            D3DDEVTYPE_NULLREF,
            hwnd,
            D3DCREATE_SOFTWARE_VERTEXPROCESSING,
            &d3dpp,
            &m_pDevice
        );
    }

    if (FAILED(hr)) {
        std::cerr << "[Error] Failed to create a Direct3D 9 device (hr = " << hr << ").\n";
        return false;
    }

    return true;
}

void XLoader::cleanupD3D9() {
    if (m_pDevice) {
        m_pDevice->Release();
        m_pDevice = nullptr;
    }
    if (m_pD3D) {
        m_pD3D->Release();
        m_pD3D = nullptr;
    }
    if (m_hwnd) {
        DestroyWindow(m_hwnd);
        UnregisterClassA("X2GltfDummyWindow", GetModuleHandle(nullptr));
        m_hwnd = nullptr;
    }
}

int XLoader::processFrameHierarchy(
    D3DXFRAME* pFrame,
    int parentIdx,
    const D3DXMATRIX& parentWorld,
    XModel& model,
    std::map<D3DXFRAME*, int>& frameToIndexMap,
    std::map<std::string, int>& nameToIndexMap,
    std::map<D3DXFRAME*, D3DXMATRIX>& frameToWorldMap
) {
    if (!pFrame) return -1;

    int currentIdx = static_cast<int>(model.nodes.size());
    XNode node;
    
    // Convert name from Shift-JIS to UTF-8
    std::string utf8Name = pFrame->Name ? convertShiftJisToUtf8(pFrame->Name) : "Frame_" + std::to_string(currentIdx);
    node.name = utf8Name;

    // Convert local transform matrix to Blender coordinate space
    node.localTransform = convertMatrixToBlender(pFrame->TransformationMatrix);
    node.parentIndex = parentIdx;

    // Decompose transform matrix into translation, rotation, scale to prevent animation matrix conflicts in Blender
    D3DXVECTOR3 t, s;
    D3DXQUATERNION r;
    if (SUCCEEDED(D3DXMatrixDecompose(&s, &r, &t, &pFrame->TransformationMatrix))) {
        node.translation = { t.x, t.z, t.y };
        node.rotation    = { -r.x, -r.z, -r.y, r.w };
        node.scale       = { s.x, s.z, s.y };
        node.useTRS      = true;
    }

    // Compute frame world transform matrix (Local * ParentWorld in row-major)
    D3DXMATRIX worldMat;
    if (parentIdx == -1) {
        worldMat = pFrame->TransformationMatrix;
    } else {
        D3DXMatrixMultiply(&worldMat, &pFrame->TransformationMatrix, &parentWorld);
    }
    frameToWorldMap[pFrame] = worldMat;

    frameToIndexMap[pFrame] = currentIdx;
    nameToIndexMap[utf8Name] = currentIdx; // Map the UTF-8 name!

    model.nodes.push_back(node);

    // Recursively process child frames
    D3DXFRAME* pChild = pFrame->pFrameFirstChild;
    while (pChild) {
        int childIdx = processFrameHierarchy(pChild, currentIdx, worldMat, model, frameToIndexMap, nameToIndexMap, frameToWorldMap);
        if (childIdx != -1) {
            model.nodes[currentIdx].childrenIndices.push_back(childIdx);
        }
        pChild = pChild->pFrameSibling;
    }

    return currentIdx;
}

struct D3DXVERTEX_FVF {
    D3DXVECTOR3 Position;
    D3DXVECTOR3 Normal;
    float tu, tv;
};

void XLoader::processMeshContainers(
    D3DXFRAME* pRootFrame,
    XModel& model,
    const std::map<D3DXFRAME*, int>& frameToIndexMap,
    const std::map<std::string, int>& nameToIndexMap,
    const std::map<D3DXFRAME*, D3DXMATRIX>& frameToWorldMap
) {
    // Traverse the D3DXFRAME hierarchy recursively to extract meshes
    auto processFrameMeshes = [&](auto& self, D3DXFRAME* pFrame) -> void {
        if (!pFrame) return;

        int nodeIdx = -1;
        auto fit = frameToIndexMap.find(pFrame);
        if (fit != frameToIndexMap.end()) {
            nodeIdx = fit->second;
        }

        // Get the world matrix of this frame
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

                // Clone the mesh to a known FVF format (XYZ, Normal, UV0)
                ID3DXMesh* pCleanMesh = nullptr;
                HRESULT hr = pOriginalMesh->CloneMeshFVF(
                    D3DXMESH_MANAGED,
                    D3DFVF_XYZ | D3DFVF_NORMAL | D3DFVF_TEX1,
                    m_pDevice,
                    &pCleanMesh
                );

                if (SUCCEEDED(hr) && pCleanMesh) {
                    DWORD numVertices = pCleanMesh->GetNumVertices();
                    DWORD numFaces = pCleanMesh->GetNumFaces();

                    XMesh xmesh;
                    // Convert mesh name from Shift-JIS to UTF-8
                    xmesh.name = pMC->Name ? convertShiftJisToUtf8(pMC->Name) : "Mesh_" + std::to_string(model.meshes.size());

                    // 1. Lock and Extract Vertices (Transform to world space and then to Blender space)
                    xmesh.vertices.resize(numVertices);
                    D3DXVERTEX_FVF* pRawVerts = nullptr;
                    pCleanMesh->LockVertexBuffer(D3DLOCK_READONLY, (void**)&pRawVerts);
                    if (pRawVerts) {
                        for (DWORD i = 0; i < numVertices; ++i) {
                            D3DXVECTOR3 pos = pRawVerts[i].Position;
                            D3DXVECTOR3 norm = pRawVerts[i].Normal;
                            
                            // Transform position and normal by the frame's world matrix
                            D3DXVECTOR3 posWorld, normWorld;
                            D3DXVec3TransformCoord(&posWorld, &pos, &worldMat);
                            D3DXVec3TransformNormal(&normWorld, &norm, &worldMat);

                            // Convert from left-handed Y-up to right-handed Z-up (Blender space)
                            xmesh.vertices[i].position = { posWorld.x, posWorld.z, posWorld.y };
                            xmesh.vertices[i].normal   = { normWorld.x, normWorld.z, normWorld.y };
                            xmesh.vertices[i].texCoord = { pRawVerts[i].tu,         pRawVerts[i].tv,         0.0f };
                        }
                        pCleanMesh->UnlockVertexBuffer();
                    }

                    // 2. Lock and Extract Indices (winding order reversed to account for Z reflection)
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

                    // 3. Extract Materials (including Shift-JIS to UTF-8 texture filename conversions)
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
                                // Convert texture path from Shift-JIS to UTF-8!
                                xmesh.materials[i].textureFilename = convertShiftJisToUtf8(mat.pTextureFilename);
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

                    // 4. Extract Skinning and Bone weights
                    if (pMC->pSkinInfo) {
                        LPD3DXSKININFO pSkin = pMC->pSkinInfo;
                        DWORD numBones = pSkin->GetNumBones();

                        struct Influence {
                            int jointIdx;
                            float weight;
                        };
                        std::vector<std::vector<Influence>> rawInfluences(numVertices);

                        xmesh.boneNames.resize(numBones);
                        xmesh.inverseBindMatrices.resize(numBones);

                        // Transform offset matrix by the inverse of the frame's world matrix
                        D3DXMATRIX invWorldMat;
                        D3DXMatrixInverse(&invWorldMat, nullptr, &worldMat);

                        for (DWORD b = 0; b < numBones; ++b) {
                            LPCSTR boneName = pSkin->GetBoneName(b);
                            // Convert bone name from Shift-JIS to UTF-8
                            std::string utf8BoneName = boneName ? convertShiftJisToUtf8(boneName) : "Bone_" + std::to_string(b);
                            xmesh.boneNames[b] = utf8BoneName;

                            // Preserve the full skeleton chain
                            auto bit = nameToIndexMap.find(utf8BoneName);
                            if (bit != nameToIndexMap.end()) {
                                markNodeAndAncestorsAsBones(model, bit->second);
                            }

                            // Transform Offset Matrix to world space: offsetWorld = invWorldMat * offsetMatrix
                            const D3DXMATRIX* pOffsetMatrix = pSkin->GetBoneOffsetMatrix(b);
                            D3DXMATRIX offsetWorld;
                            D3DXMatrixMultiply(&offsetWorld, &invWorldMat, pOffsetMatrix);

                            // Convert offsetWorld to Blender space and save
                            xmesh.inverseBindMatrices[b] = convertMatrixToBlender(offsetWorld);

                            // Gather Bone Influences
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

                        // Cap influences to 4 and normalize weights per-vertex
                        for (DWORD vIdx = 0; vIdx < numVertices; ++vIdx) {
                            auto& infList = rawInfluences[vIdx];

                            // Sort descending
                            std::sort(infList.begin(), infList.end(), [](const Influence& a, const Influence& b) {
                                return a.weight > b.weight;
                            });

                            if (infList.size() > 4) {
                                infList.resize(4);
                            }

                            float sum = 0.0f;
                            for (const auto& inf : infList) {
                                sum += inf.weight;
                            }

                            for (size_t i = 0; i < 4; ++i) {
                                if (i < infList.size() && sum > 0.0f) {
                                    xmesh.vertices[vIdx].jointIndices[i]  = infList[i].jointIdx;
                                    xmesh.vertices[vIdx].jointWeights[i]  = infList[i].weight / sum;
                                } else {
                                    xmesh.vertices[vIdx].jointIndices[i]  = 0;
                                    xmesh.vertices[vIdx].jointWeights[i]  = 0.0f;
                                }
                            }
                        }

                        xmesh.hasSkin = true;
                    }

                    // Store mesh and update node's mesh attachment index
                    int middlemanMeshIdx = static_cast<int>(model.meshes.size());
                    model.meshes.push_back(xmesh);

                    if (nodeIdx != -1) {
                        model.nodes[nodeIdx].meshIndex = middlemanMeshIdx;
                    }
                    
                    pCleanMesh->Release();
                }
            }
            pMC = pMC->pNextMeshContainer;
        }

        // Traverse children
        D3DXFRAME* pChild = pFrame->pFrameFirstChild;
        while (pChild) {
            self(self, pChild);
            pChild = pChild->pFrameSibling;
        }
    };

    processFrameMeshes(processFrameMeshes, pRootFrame);
}

// Helper struct for quaternion and vector operations in relative keyframe computation
struct AnimMath {
    static XQuaternion conjugate(const XQuaternion& q) {
        return { -q.x, -q.y, -q.z, q.w };
    }

    static XQuaternion multiply(const XQuaternion& q1, const XQuaternion& q2) {
        XQuaternion out;
        out.w = q1.w * q2.w - q1.x * q2.x - q1.y * q2.y - q1.z * q2.z;
        out.x = q1.w * q2.x + q1.x * q2.w + q1.y * q2.z - q1.z * q2.y;
        out.y = q1.w * q2.y - q1.x * q2.z + q1.y * q2.w + q1.z * q2.x;
        out.z = q1.w * q2.z + q1.x * q2.y - q1.y * q2.x + q1.z * q2.w;
        return out;
    }

    static XVector3 rotate(const XQuaternion& q, const XVector3& v) {
        // Rotate vector v by quaternion q: v' = q * (0, v) * q*
        XQuaternion qv = { v.x, v.y, v.z, 0.0f };
        XQuaternion q_conj = conjugate(q);
        XQuaternion temp = multiply(q, qv);
        XQuaternion rotated = multiply(temp, q_conj);
        return { rotated.x, rotated.y, rotated.z };
    }
};

void XLoader::processAnimations(
    ID3DXAnimationController* pAnimController,
    XModel& model,
    const std::map<std::string, int>& nameToIndexMap,
    D3DXFRAME* pRootFrame
) {
    if (!pAnimController || !pRootFrame) return;

    DWORD numAnimSets = pAnimController->GetNumAnimationSets();
    for (DWORD s = 0; s < numAnimSets; ++s) {
        ID3DXAnimationSet* pSet = nullptr;
        if (SUCCEEDED(pAnimController->GetAnimationSet(s, &pSet)) && pSet) {
            XAnimation xanim;
            LPCSTR pSetName = pSet->GetName();
            xanim.name = pSetName ? convertShiftJisToUtf8(pSetName) : "Animation_" + std::to_string(s);
            xanim.duration = static_cast<float>(pSet->GetPeriod());

            // 1. (Optional) Parse legacy keyframes if it's a keyframed animation set
            ID3DXKeyframedAnimationSet* pKeySet = nullptr;
            if (SUCCEEDED(pSet->QueryInterface(IID_ID3DXKeyframedAnimationSet, (void**)&pKeySet)) && pKeySet) {
                const double sourceTicksPerSecond = pKeySet->GetSourceTicksPerSecond();
                const float ticksToSeconds = (sourceTicksPerSecond > 0.0) ? static_cast<float>(1.0 / sourceTicksPerSecond) : 1.0f;

                DWORD numAnimations = pKeySet->GetNumAnimations();
                for (DWORD a = 0; a < numAnimations; ++a) {
                    LPCSTR targetFrameName = nullptr;
                    if (SUCCEEDED(pKeySet->GetAnimationNameByIndex(a, &targetFrameName)) && targetFrameName) {
                        std::string utf8TargetName = convertShiftJisToUtf8(targetFrameName);
                        XAnimationChannel channel;
                        channel.targetNodeName = utf8TargetName;

                        auto nit = nameToIndexMap.find(utf8TargetName);
                        channel.targetNodeIndex = (nit != nameToIndexMap.end()) ? nit->second : -1;

                        XVector3 restTranslation = {0.0f, 0.0f, 0.0f};
                        XQuaternion restRotation = {0.0f, 0.0f, 0.0f, 1.0f};
                        XVector3 restScale = {1.0f, 1.0f, 1.0f};
                        if (channel.targetNodeIndex >= 0 && channel.targetNodeIndex < static_cast<int>(model.nodes.size())) {
                            const XNode& node = model.nodes[static_cast<size_t>(channel.targetNodeIndex)];
                            restTranslation = node.translation;
                            restRotation = node.rotation;
                            restScale = node.scale;
                        }
                        XQuaternion invRestRot = AnimMath::conjugate(restRotation);

                        // Translation Keys
                        DWORD numTranslationKeys = pKeySet->GetNumTranslationKeys(a);
                        if (numTranslationKeys > 0) {
                            std::vector<D3DXKEY_VECTOR3> transKeys(numTranslationKeys);
                            if (SUCCEEDED(pKeySet->GetTranslationKeys(a, transKeys.data()))) {
                                for (const auto& k : transKeys) {
                                    float time = static_cast<float>(k.Time) * ticksToSeconds;
                                    XVector3 tBlend = { k.Value.x, k.Value.z, k.Value.y };
                                    XVector3 tDiff = {
                                        tBlend.x - restTranslation.x,
                                        tBlend.y - restTranslation.y,
                                        tBlend.z - restTranslation.z
                                    };
                                    XVector3 tRel = AnimMath::rotate(invRestRot, tDiff);
                                    channel.translationKeys.push_back({ time, tRel });
                                }
                            }
                        }

                        // Rotation Keys
                        DWORD numRotationKeys = pKeySet->GetNumRotationKeys(a);
                        if (numRotationKeys > 0) {
                            std::vector<D3DXKEY_QUATERNION> rotKeys(numRotationKeys);
                            if (SUCCEEDED(pKeySet->GetRotationKeys(a, rotKeys.data()))) {
                                for (const auto& k : rotKeys) {
                                    float time = static_cast<float>(k.Time) * ticksToSeconds;
                                    XQuaternion rBlend = { -k.Value.x, -k.Value.z, -k.Value.y, k.Value.w };
                                    XQuaternion rRel = AnimMath::multiply(invRestRot, rBlend);
                                    channel.rotationKeys.push_back({ time, rRel });
                                }
                            }
                        }

                        // Scale Keys
                        DWORD numScaleKeys = pKeySet->GetNumScaleKeys(a);
                        if (numScaleKeys > 0) {
                            std::vector<D3DXKEY_VECTOR3> scaleKeys(numScaleKeys);
                            if (SUCCEEDED(pKeySet->GetScaleKeys(a, scaleKeys.data()))) {
                                for (const auto& k : scaleKeys) {
                                    float time = static_cast<float>(k.Time) * ticksToSeconds;
                                    XVector3 sBlend = { k.Value.x, k.Value.z, k.Value.y };
                                    XVector3 sRel;
                                    sRel.x = (restScale.x != 0.0f) ? (sBlend.x / restScale.x) : sBlend.x;
                                    sRel.y = (restScale.y != 0.0f) ? (sBlend.y / restScale.y) : sBlend.y;
                                    sRel.z = (restScale.z != 0.0f) ? (sBlend.z / restScale.z) : sBlend.z;
                                    channel.scaleKeys.push_back({ time, sRel });
                                }
                            }
                        }

                        if (!channel.translationKeys.empty() || !channel.rotationKeys.empty() || !channel.scaleKeys.empty()) {
                            xanim.channels.push_back(channel);
                        }
                    }
                }
                pKeySet->Release();
            }

            // Set up track 0 to play our animation set
            pAnimController->SetTrackAnimationSet(0, pSet);
            pAnimController->SetTrackEnable(0, TRUE);
            pAnimController->SetTrackWeight(0, 1.0f);
            pAnimController->SetTrackSpeed(0, 1.0f);
            pAnimController->SetTrackPosition(0, 0.0);

            float fps = 60.0f;
            float timeStep = 1.0f / fps;
            int numFrames = static_cast<int>(std::ceil(xanim.duration * fps)) + 1;

            // Map target bone name to index in xanim.channels
            std::map<std::string, size_t> nodeNameToChannelIdx;
            for (size_t i = 0; i < xanim.channels.size(); ++i) {
                nodeNameToChannelIdx[xanim.channels[i].targetNodeName] = i;
            }

            // Ensure a channel exists for every node in the model
            for (size_t i = 0; i < model.nodes.size(); ++i) {
                const std::string& name = model.nodes[i].name;
                if (nodeNameToChannelIdx.find(name) == nodeNameToChannelIdx.end()) {
                    XAnimationChannel newChannel;
                    newChannel.targetNodeName = name;
                    newChannel.targetNodeIndex = static_cast<int>(i);
                    xanim.channels.push_back(newChannel);
                    nodeNameToChannelIdx[name] = xanim.channels.size() - 1;
                }
            }

            // Helper struct to recursively update world matrices
            struct FrameMatrixUpdater {
                static void update(D3DXFRAME* pFrame, const D3DXMATRIX& parentWorld, std::map<std::string, D3DXMATRIX>& outWorldMap) {
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

            D3DXMATRIX identity;
            D3DXMatrixIdentity(&identity);

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
                    kf.time = t;
                    kf.value = blendMat;

                    size_t chIdx = nodeNameToChannelIdx[name];
                    xanim.channels[chIdx].bakedKeys.push_back(kf);
                }
            }

            model.animations.push_back(xanim);
            pSet->Release();
        }
    }
}

bool XLoader::loadModel(const std::string& filepath, XModel& outModel) {
    // 1. Initialize Direct3D 9 headless context
    if (!initD3D9()) {
        std::cerr << "[Error] Failed to initialize headless D3D9 context for file: " << filepath << "\n";
        return false;
    }


    // 2. Read the .x file to memory and strip templates that Wine's D3DX may not recognise
    std::string xFileContent;
    if (!readAndPreprocessXFile(filepath, xFileContent)) {
        std::cerr << "[Error] Failed to read .x file from disk: " << filepath << "\n";
        cleanupD3D9();
        return false;
    }

    AnimateAllocateHierarchy allocHierarchy;
    LPD3DXFRAME pRootFrame = nullptr;
    ID3DXAnimationController* pAnimController = nullptr;

    // 3. Write the preprocessed content to a temp file, then use the file-based loader.
    //    Wine's D3DXLoadMeshHierarchyFromXInMemory has a known issue where it doesn't
    //    properly handle inline template definitions; the file-based variant works correctly.
    char tempPath[MAX_PATH];
    char tempFile[MAX_PATH];
    GetTempPathA(MAX_PATH, tempPath);
    GetTempFileNameA(tempPath, "x2g", 0, tempFile);
    // Give it a .x extension so d3dxof uses the text parser
    std::string tempXFile = std::string(tempFile) + ".x";
    DeleteFileA(tempFile); // remove the placeholder file created by GetTempFileName

    {
        std::ofstream tmpOut(tempXFile, std::ios::binary);
        if (!tmpOut.is_open()) {
            std::cerr << "[Error] Failed to write temp .x file: " << tempXFile << "\n";
            cleanupD3D9();
            return false;
        }
        tmpOut.write(xFileContent.data(), static_cast<std::streamsize>(xFileContent.size()));
    }

    HRESULT hr = D3DXLoadMeshHierarchyFromXA(
        tempXFile.c_str(),
        D3DXMESH_SYSTEMMEM,
        m_pDevice,
        &allocHierarchy,
        nullptr,
        &pRootFrame,
        &pAnimController
    );

    DeleteFileA(tempXFile.c_str()); // clean up temp file regardless of result

    if (FAILED(hr)) {
        std::cerr << "[Error] D3DXLoadMeshHierarchyFromXA failed with HRESULT " << std::hex << hr << "\n";
        cleanupD3D9();
        return false;
    }


    // 4. Map frame hierarchy to flat middleman representation
    std::map<D3DXFRAME*, int> frameToIndexMap;
    std::map<std::string, int> nameToIndexMap;
    std::map<D3DXFRAME*, D3DXMATRIX> frameToWorldMap;
    
    D3DXMATRIX identity;
    D3DXMatrixIdentity(&identity);

    int rootIdx = processFrameHierarchy(pRootFrame, -1, identity, outModel, frameToIndexMap, nameToIndexMap, frameToWorldMap);
    outModel.rootNodeIndex = rootIdx;

    // 5. Extract mesh geometries and skeletal skin weights
    processMeshContainers(pRootFrame, outModel, frameToIndexMap, nameToIndexMap, frameToWorldMap);

    // 6. Extract animation keyframes
    processAnimations(pAnimController, outModel, nameToIndexMap, pRootFrame);

    // 7. Cleanup native D3DX objects
    D3DXFrameDestroy(pRootFrame, &allocHierarchy);
    if (pAnimController) {
        pAnimController->Release();
    }

    cleanupD3D9();
    return true;
}
