// x_file_preprocessor.cpp — .x file reader with idempotent template injection.
//
// Port of the original `readAndPreprocessXFile` helper from x_loader.cpp
// (lines 10-73), refactored so the template injection is idempotent: each
// of the three mesh-extension templates (XSkinMeshHeader,
// VertexDuplicationIndices, SkinWeights) is checked independently, and
// only the ones whose `template <Name>` marker is NOT already present in
// the file content are injected.  This avoids "duplicate template" errors
// under strict parsers when the file already declares one or more of them.
//
// The original `k_missingTemplates` constant is preserved verbatim (it is
// still a single string containing all three template blocks back-to-back,
// as in the original).  Per-template extraction is done at runtime by
// slicing k_missingTemplates on each `template <Name>` marker.
#include "io/x_file_preprocessor.h"

#include "core/log.h"

#include <fstream>
#include <sstream>
#include <string>

namespace {

// The mesh-extension templates that Wine's d3dxof parser does not
// pre-register.  Injected as inline template definitions right after the
// .x file header so d3dxof registers them during the parse pass and can
// then handle the corresponding SkinWeights / XSkinMeshHeader objects
// without errors.  GUIDs and field layouts are verbatim from the DirectX 9
// SDK (rmxftmpl.x).
//
// Idempotency: the original loader injected this whole block
// unconditionally, which produced "duplicate template" errors under strict
// parsers if the file already declared any of these templates.  The
// refactored version checks each template's `template <Name>` marker
// against the file content and injects only the missing ones, so repeated
// preprocessing of the same file is a no-op.
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

// Markers used to detect existing declarations in the file content.  Each
// marker is the `template <Name>` keyword sequence that begins the
// corresponding template block inside k_missingTemplates; scanning the file
// for the marker tells us whether that template is already declared.
static const char* const k_templateMarkers[] = {
    "template XSkinMeshHeader",
    "template VertexDuplicationIndices",
    "template SkinWeights",
};

// Extracts the body of one template (identified by its marker) from
// k_missingTemplates.  The body starts at the marker and ends just before
// the next `template ` keyword (or at the end of k_missingTemplates for
// the last template).  Returns an empty string if the marker is somehow
// not found in the constant (should not happen).
static std::string extractTemplateBody(const std::string& marker) {
    static const std::string src = k_missingTemplates;
    auto start = src.find(marker);
    if (start == std::string::npos) return std::string();
    auto next = src.find("template ", start + 1);
    return (next == std::string::npos) ? src.substr(start)
                                        : src.substr(start, next - start);
}

} // namespace

bool XFilePreprocessor::readAndPreprocess(const std::string& filepath,
                                           std::string& outContent) {
    std::ifstream file(filepath, std::ios::binary);
    if (!file.is_open()) {
        LOG_ERROR("[XFilePreprocessor] Cannot open input file: " + filepath);
        return false;
    }

    std::ostringstream oss;
    oss << file.rdbuf();
    outContent = oss.str();
    file.close();

    // Only text-format .x files begin with "xof 0303txt" or "xof 0302txt".
    // Binary files already carry the template GUIDs in their header section.
    bool isTextFormat = (outContent.size() >= 11 &&
                         outContent.compare(0, 7, "xof 030") == 0 &&
                         (outContent[10] == 't' || outContent[10] == 'T'));

    if (isTextFormat) {
        // Find the end of the first line (the magic header line).
        size_t headerEnd = outContent.find('\n');
        if (headerEnd == std::string::npos) {
            headerEnd = outContent.size();
        } else {
            ++headerEnd; // include the newline
        }

        // Build the injection string by appending only the templates whose
        // markers are not already present in outContent.  This keeps the
        // operation idempotent: a second pass over the same file finds all
        // three markers already present and injects nothing, so the file's
        // byte content is stable across repeated preprocessing.
        std::string injection;
        for (const char* marker : k_templateMarkers) {
            if (outContent.find(marker) == std::string::npos) {
                injection += extractTemplateBody(marker);
            }
        }

        if (!injection.empty()) {
            // Prepend a single newline to preserve the leading blank line
            // that the original k_missingTemplates constant contributed
            // (its first character is '\n').  When all three templates are
            // missing, the injected bytes are byte-identical to the
            // original unconditional injection.
            outContent.insert(headerEnd, std::string("\n") + injection);
        }
    }

    return true;
}
