#include "json_exporter.h"
#include <fstream>
#include <sstream>
#include <iomanip>
#include <iostream>

// ---------------------------------------------------------------------------
// Minimal hand-written JSON helpers — no external dependency required.
// ---------------------------------------------------------------------------

namespace {

// Write a float that is valid JSON (no "inf" / "nan")
static std::string jf(float v) {
    if (v != v || v == v + 1.0f) return "0"; // nan / inf guard
    std::ostringstream o;
    o << std::setprecision(7) << v;
    return o.str();
}

static std::string jvec3(const XVector3& v) {
    return "[" + jf(v.x) + "," + jf(v.y) + "," + jf(v.z) + "]";
}

static std::string jvec4(float x, float y, float z, float w) {
    return "[" + jf(x) + "," + jf(y) + "," + jf(z) + "," + jf(w) + "]";
}

static std::string jmat4(const XMatrix4x4& m) {
    std::ostringstream o;
    o << "[[";
    for (int r = 0; r < 4; ++r) {
        if (r) o << ",[";
        for (int c = 0; c < 4; ++c) {
            if (c) o << ",";
            o << jf(m.m[r][c]);
        }
        o << "]";
    }
    o << "]";
    return o.str();
}

// Escape a raw string for JSON — handles backslashes, quotes, and common
// control characters so the output is always valid JSON.
static std::string jstr(const std::string& s) {
    std::string out;
    out.reserve(s.size() + 2);
    out += '"';
    for (unsigned char c : s) {
        switch (c) {
            case '"':  out += "\\\""; break;
            case '\\': out += "\\\\"; break;
            case '\n': out += "\\n";  break;
            case '\r': out += "\\r";  break;
            case '\t': out += "\\t";  break;
            default:
                if (c < 0x20) {
                    char buf[8];
                    snprintf(buf, sizeof(buf), "\\u%04x", c);
                    out += buf;
                } else {
                    out += static_cast<char>(c);
                }
        }
    }
    out += '"';
    return out;
}

// ---------------------------------------------------------------------------
// Section writers — each appends to `out`
// ---------------------------------------------------------------------------

static void writeMaterials(std::ostream& out, const std::vector<XMaterial>& mats) {
    out << "[";
    for (size_t i = 0; i < mats.size(); ++i) {
        const auto& m = mats[i];
        if (i) out << ",";
        out << "{"
            << "\"name\":" << jstr(m.name) << ","
            << "\"diffuse\":" << jvec3(m.diffuseColor) << ","
            << "\"alpha\":" << jf(m.diffuseAlpha) << ","
            << "\"specular\":" << jvec3(m.specularColor) << ","
            << "\"specular_power\":" << jf(m.specularPower) << ","
            << "\"emissive\":" << jvec3(m.emissiveColor) << ","
            << "\"texture\":" << jstr(m.textureFilename)
            << "}";
    }
    out << "]";
}

static void writeVertices(std::ostream& out, const std::vector<XVertex>& verts) {
    out << "[";
    for (size_t i = 0; i < verts.size(); ++i) {
        const auto& v = verts[i];
        if (i) out << ",";
        out << "{"
            << "\"p\":" << jvec3(v.position) << ","
            << "\"n\":" << jvec3(v.normal) << ","
            << "\"uv\":[" << jf(v.texCoord.x) << "," << jf(v.texCoord.y) << "],"
            << "\"ji\":[" << v.jointIndices[0] << "," << v.jointIndices[1] << ","
                          << v.jointIndices[2] << "," << v.jointIndices[3] << "],"
            << "\"jw\":[" << jf(v.jointWeights[0]) << "," << jf(v.jointWeights[1]) << ","
                          << jf(v.jointWeights[2]) << "," << jf(v.jointWeights[3]) << "]"
            << "}";
    }
    out << "]";
}

static void writeIndices(std::ostream& out, const std::vector<uint32_t>& idx) {
    out << "[";
    for (size_t i = 0; i < idx.size(); ++i) {
        if (i) out << ",";
        out << idx[i];
    }
    out << "]";
}

static void writeMeshes(std::ostream& out, const std::vector<XMesh>& meshes) {
    out << "[";
    for (size_t mi = 0; mi < meshes.size(); ++mi) {
        const auto& mesh = meshes[mi];
        if (mi) out << ",";
        out << "{"
            << "\"name\":" << jstr(mesh.name) << ","
            << "\"vertices\":";
        writeVertices(out, mesh.vertices);
        out << ",\"indices\":";
        writeIndices(out, mesh.indices);
        out << ",\"materials\":";
        writeMaterials(out, mesh.materials);
        out << ",\"face_material_indices\":";
        writeIndices(out, mesh.faceMaterialIndices);
        out << ",\"has_skin\":" << (mesh.hasSkin ? "true" : "false");

        // Bone names
        out << ",\"bone_names\":[";
        for (size_t b = 0; b < mesh.boneNames.size(); ++b) {
            if (b) out << ",";
            out << jstr(mesh.boneNames[b]);
        }
        out << "]";

        // Inverse bind matrices
        out << ",\"inverse_bind_matrices\":[";
        for (size_t b = 0; b < mesh.inverseBindMatrices.size(); ++b) {
            if (b) out << ",";
            out << jmat4(mesh.inverseBindMatrices[b]);
        }
        out << "]}";
    }
    out << "]";
}

static void writeNodes(std::ostream& out, const std::vector<XNode>& nodes) {
    out << "[";
    for (size_t ni = 0; ni < nodes.size(); ++ni) {
        const auto& n = nodes[ni];
        if (ni) out << ",";

        // Children array
        std::string children = "[";
        for (size_t c = 0; c < n.childrenIndices.size(); ++c) {
            if (c) children += ",";
            children += std::to_string(n.childrenIndices[c]);
        }
        children += "]";

        out << "{"
            << "\"name\":" << jstr(n.name) << ","
            << "\"parent_index\":" << n.parentIndex << ","
            << "\"children\":" << children << ","
            << "\"mesh_index\":" << n.meshIndex << ","
            << "\"is_bone\":" << (n.isBone ? "true" : "false") << ","
            << "\"local_transform\":" << jmat4(n.localTransform) << ","
            << "\"use_trs\":" << (n.useTRS ? "true" : "false") << ","
            << "\"translation\":" << jvec3(n.translation) << ","
            << "\"rotation\":" << jvec4(n.rotation.x, n.rotation.y, n.rotation.z, n.rotation.w) << ","
            << "\"scale\":" << jvec3(n.scale)
            << "}";
    }
    out << "]";
}

static void writeAnimations(std::ostream& out, const std::vector<XAnimation>& anims) {
    out << "[";
    for (size_t ai = 0; ai < anims.size(); ++ai) {
        const auto& anim = anims[ai];
        if (ai) out << ",";
        out << "{"
            << "\"name\":" << jstr(anim.name) << ","
            << "\"duration\":" << jf(anim.duration) << ","
            << "\"channels\":[";
        for (size_t ci = 0; ci < anim.channels.size(); ++ci) {
            const auto& ch = anim.channels[ci];
            if (ci) out << ",";
            out << "{"
                << "\"target_node\":" << jstr(ch.targetNodeName) << ","
                << "\"target_index\":" << ch.targetNodeIndex << ","
                << "\"translation_keys\":[";
            for (size_t k = 0; k < ch.translationKeys.size(); ++k) {
                if (k) out << ",";
                out << "{\"t\":" << jf(ch.translationKeys[k].time)
                    << ",\"v\":" << jvec3(ch.translationKeys[k].value) << "}";
            }
            out << "],\"rotation_keys\":[";
            for (size_t k = 0; k < ch.rotationKeys.size(); ++k) {
                if (k) out << ",";
                const auto& q = ch.rotationKeys[k].value;
                out << "{\"t\":" << jf(ch.rotationKeys[k].time)
                    << ",\"v\":" << jvec4(q.x, q.y, q.z, q.w) << "}";
            }
            out << "],\"scale_keys\":[";
            for (size_t k = 0; k < ch.scaleKeys.size(); ++k) {
                if (k) out << ",";
                out << "{\"t\":" << jf(ch.scaleKeys[k].time)
                    << ",\"v\":" << jvec3(ch.scaleKeys[k].value) << "}";
            }
            out << "],\"baked_keys\":[";
            for (size_t k = 0; k < ch.bakedKeys.size(); ++k) {
                if (k) out << ",";
                out << "{\"t\":" << jf(ch.bakedKeys[k].time)
                    << ",\"m\":" << jmat4(ch.bakedKeys[k].value) << "}";
            }
            out << "]}";
        }
        out << "]}";
    }
    out << "]";
}

} // anonymous namespace

// ---------------------------------------------------------------------------

bool JsonExporter::exportToFile(const XModel& model, const std::string& filepath) {
    std::ofstream out(filepath);
    if (!out.is_open()) {
        std::cerr << "[JsonExporter] Cannot open output file: " << filepath << "\n";
        return false;
    }

    out << "{";
    out << "\"root_node_index\":" << model.rootNodeIndex << ",";
    out << "\"nodes\":";
    writeNodes(out, model.nodes);
    out << ",\"meshes\":";
    writeMeshes(out, model.meshes);
    out << ",\"animations\":";
    writeAnimations(out, model.animations);
    out << "}\n";

    if (!out.good()) {
        std::cerr << "[JsonExporter] Write error for: " << filepath << "\n";
        return false;
    }

    return true;
}
