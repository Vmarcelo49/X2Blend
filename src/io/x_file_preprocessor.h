// x_file_preprocessor.h — .x file reader and template injector.
//
// Declares the XFilePreprocessor namespace with a single entry point,
// readAndPreprocess(), that loads a .x file from disk and (for text-format
// files) injects the mesh-extension templates Wine's d3dxof parser does
// not pre-register.  The injection is idempotent: templates that are
// already declared in the file are left alone, so repeated preprocessing
// of the same file is a no-op (see the .cpp for details).
#pragma once

#include <string>

namespace XFilePreprocessor {

// Reads a .x file from disk into `outContent`.  For text-format files,
// injects the missing mesh-extension templates (XSkinMeshHeader,
// VertexDuplicationIndices, SkinWeights) right after the header line
// IF they are not already declared in the file (idempotent).  Returns
// false on read failure (file not found / unreadable).
bool readAndPreprocess(const std::string& filepath, std::string& outContent);

} // namespace XFilePreprocessor
