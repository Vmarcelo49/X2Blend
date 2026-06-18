xof 0303txt 0032

//
// minimal.x — synthetic test fixture for X2Blend round-trip smoke tests.
//
// This file is a hand-written, minimal but valid DirectX .x text-format
// model.  It is intended for round-trip smoke tests of:
//
//   - io/x_file_preprocessor (idempotent template injection)
//   - io/json_exporter       (meta block + node/mesh/material sections)
//
// It is NOT a real-world model: it has no skinning and no animation,
// and the geometry is a single 1x1 quad in the XY plane.  Use it as
// input to `x2blend.exe` and verify the resulting JSON has one node
// ("Root"), one mesh ("Quad"), and zero animations.
//
// The .x text format uses:
//   - `;`  to separate scalars,
//   - `,,` to terminate a vector / row / list,
//   - `{ ... }` for object scope,
//   - C-style `//` line comments and `/* ... */` block comments.
//
// The X2Blend preprocessor (io/x_file_preprocessor.cpp) injects the
// standard templates (Frame, FrameTransformMatrix, Mesh, MeshNormals,
// MeshTextureCoords, MeshMaterialList, Material, TextureFilename) at
// runtime if they are not already declared in the file.  This fixture
// deliberately omits the template declarations so the preprocessor's
// idempotent-injection path is exercised.
//

Header {
  1;
  0;
  1;
}

Frame Root {
  FrameTransformMatrix {
    1.0, 0.0, 0.0, 0.0,
    0.0, 1.0, 0.0, 0.0,
    0.0, 0.0, 1.0, 0.0,
    0.0, 0.0, 0.0, 1.0;;
  }

  Mesh {
    // Vertex count, then vertex list (each vertex is "x; y; z;,").
    4;
    0.0; 0.0; 0.0;,
    1.0; 0.0; 0.0;,
    1.0; 1.0; 0.0;,
    0.0; 1.0; 0.0;;

    // Face count, then face list (each face is "n; i0, i1, ..., i(n-1);").
    2;
    3; 0, 1, 2;,
    3; 0, 2, 3;;

    MeshNormals {
      // 4 normals, one per vertex, all pointing +Z (out of the screen).
      4;
      0.0; 0.0; 1.0;,
      0.0; 0.0; 1.0;,
      0.0; 0.0; 1.0;,
      0.0; 0.0; 1.0;;
      2;
      3; 0, 1, 2;,
      3; 0, 2, 3;;
    }

    MeshTextureCoords {
      // 4 UV pairs, one per vertex.
      4;
      0.0; 0.0;,
      1.0; 0.0;,
      1.0; 1.0;,
      0.0; 1.0;;
    }

    MeshMaterialList {
      // nMaterials, nFaceIndexes, then per-face material indices.
      1;
      2;
      0, 0;;
      Material {
        // FaceColor (RGBA)
        0.8; 0.8; 0.8; 1.0;;
        // Power
        16.0;
        // SpecularColor (RGB)
        0.5; 0.5; 0.5;;
        // EmissiveColor (RGB)
        0.0; 0.0; 0.0;;
        TextureFilename {
          "texture.png";
        }
      }
    }
  }
}
