#pragma once

#include <string>
#include <vector>
#include <array>
#include <cstdint>

// Simple, decoupled math structures for the Middleman representation

struct XVector3 {
    float x = 0.0f;
    float y = 0.0f;
    float z = 0.0f;
};

struct XQuaternion {
    float x = 0.0f;
    float y = 0.0f;
    float z = 0.0f;
    float w = 1.0f;
};

struct XMatrix4x4 {
    float m[4][4] = {
        {1, 0, 0, 0},
        {0, 1, 0, 0},
        {0, 0, 1, 0},
        {0, 0, 0, 1}
    };
};

struct XMaterial {
    std::string name;
    XVector3 diffuseColor{0.8f, 0.8f, 0.8f};
    float diffuseAlpha = 1.0f;
    float specularPower = 0.0f;
    XVector3 specularColor{0.0f, 0.0f, 0.0f};
    XVector3 emissiveColor{0.0f, 0.0f, 0.0f};
    std::string textureFilename;
};

struct XVertex {
    XVector3 position;
    XVector3 normal;
    XVector3 texCoord; // Only x,y used for 2D UV
    std::array<int, 4> jointIndices = {0, 0, 0, 0};
    std::array<float, 4> jointWeights = {0.0f, 0.0f, 0.0f, 0.0f};
};

struct XMesh {
    std::string name;
    std::vector<XVertex> vertices;
    std::vector<uint32_t> indices;
    std::vector<XMaterial> materials;
    std::vector<uint32_t> faceMaterialIndices; // Materials per-face (if multi-material)
    
    // Skinning data
    bool hasSkin = false;
    std::vector<std::string> boneNames;          // List of bones that influence this mesh
    std::vector<XMatrix4x4> inverseBindMatrices; // Inverse bind pose matrix for each bone
};

struct XNode {
    std::string name;
    XMatrix4x4 localTransform;
    
    // Decomposed TRS fields
    XVector3 translation{0.0f, 0.0f, 0.0f};
    XQuaternion rotation{0.0f, 0.0f, 0.0f, 1.0f};
    XVector3 scale{1.0f, 1.0f, 1.0f};
    bool useTRS = false;

    int parentIndex = -1;
    std::vector<int> childrenIndices;
    
    // Attachment indices
    int meshIndex = -1; // Index in XModel's meshes list (-1 if none)
    
    // Flag to determine if this node represents a bone in a skeleton
    bool isBone = false;
};

struct XKeyframeVector3 {
    float time = 0.0f;
    XVector3 value;
};

struct XKeyframeQuaternion {
    float time = 0.0f;
    XQuaternion value;
};

struct XKeyframeMatrix {
    float time = 0.0f;
    XMatrix4x4 value;
};

struct XAnimationChannel {
    std::string targetNodeName; // Name of the target node (bone)
    int targetNodeIndex = -1;   // Resolved index in XModel's nodes list
    
    std::vector<XKeyframeVector3> translationKeys;
    std::vector<XKeyframeQuaternion> rotationKeys;
    std::vector<XKeyframeVector3> scaleKeys;
    std::vector<XKeyframeMatrix> bakedKeys;
};


struct XAnimation {
    std::string name;
    float duration = 0.0f;
    std::vector<XAnimationChannel> channels;
};

struct XModel {
    std::vector<XNode> nodes;
    std::vector<XMesh> meshes;
    std::vector<XAnimation> animations;
    
    // Root node index
    int rootNodeIndex = -1;
};
