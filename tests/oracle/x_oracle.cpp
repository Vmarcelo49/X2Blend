// Dumps a .X as seen by daybreak-decomp's src/assets/xmodel.h, a parser written
// independently of x2blend/xfile.py, so the tests compare two readers of one file.
// Names are hex because the shipped ones are raw Shift-JIS. Matrices are row-major
// in xm's column-vector form. Sets are in the game's index order, not file order.
#define XMODEL_IMPLEMENTATION
#include "xmodel.h"

#include <cstdio>

static void hex(FILE* f, const std::string& s) {
    fputc('"', f);
    for (unsigned char c : s) fprintf(f, "%02x", c);
    fputc('"', f);
}

static void matrix(FILE* f, const glm::mat4& m) {
    fputc('[', f);
    for (int r = 0; r < 4; ++r)
        for (int c = 0; c < 4; ++c) fprintf(f, "%s%.9g", (r || c) ? "," : "", m[c][r]);
    fputc(']', f);
}

int main(int argc, char** argv) {
    if (argc != 3) {
        fprintf(stderr, "usage: x_oracle input.X output.json\n");
        return 2;
    }
    const xm::Model m = xm::loadFile(argv[1]);
    FILE* f = fopen(argv[2], "w");
    if (!f) return 1;

    fprintf(f, "{\"ticks_per_second\":%g,\"frames\":[", m.ticksPerSecond);
    for (size_t i = 0; i < m.frames.size(); ++i) {
        fprintf(f, "%s{\"name\":", i ? "," : "");
        hex(f, m.frames[i].name);
        fprintf(f, ",\"parent\":%d,\"local\":", m.frames[i].parent);
        matrix(f, m.frames[i].bindLocal);
        fputc('}', f);
    }

    fprintf(f, "],\"meshes\":[");
    for (size_t i = 0; i < m.meshes.size(); ++i) {
        const xm::Mesh& me = m.meshes[i];
        fprintf(f, "%s{\"frame\":%d,\"indices\":[", i ? "," : "", me.frame);
        for (size_t k = 0; k < me.indices.size(); ++k) fprintf(f, "%s%u", k ? "," : "", me.indices[k]);
        fprintf(f, "],\"vertices\":[");
        for (size_t k = 0; k < me.vertices.size(); ++k) {
            const xm::Vertex& v = me.vertices[k];
            fprintf(f, "%s[%.9g,%.9g,%.9g,%.9g,%.9g,%u,%u,%u,%u,%.9g,%.9g,%.9g,%.9g]", k ? "," : "",
                    v.pos.x, v.pos.y, v.pos.z, v.uv.x, v.uv.y,
                    v.bones.x, v.bones.y, v.bones.z, v.bones.w,
                    v.weights.x, v.weights.y, v.weights.z, v.weights.w);
        }
        fprintf(f, "],\"bones\":[");
        for (size_t k = 0; k < me.bones.size(); ++k) {
            fprintf(f, "%s{\"frame\":%d,\"offset\":", k ? "," : "", me.bones[k].frame);
            matrix(f, me.bones[k].offset);
            fputc('}', f);
        }
        fprintf(f, "],\"textures\":[");
        for (size_t k = 0; k < me.materials.size(); ++k) {
            fprintf(f, "%s", k ? "," : "");
            hex(f, me.materials[k].texture);
        }
        fprintf(f, "]}");
    }

    fprintf(f, "],\"sets\":[");
    for (size_t i = 0; i < m.anims.size(); ++i) {
        const xm::AnimSet& a = m.anims[i];
        fprintf(f, "%s{\"name\":", i ? "," : "");
        hex(f, a.name);
        fprintf(f, ",\"channels\":[");
        for (size_t c = 0; c < a.channels.size(); ++c) {
            const xm::Channel& ch = a.channels[c];
            fprintf(f, "%s{\"frame\":%d,\"keys\":[", c ? "," : "", ch.frame);
            // Matrix keys decompose to three equal-timed runs, the only form shipped.
            for (size_t k = 0; k < ch.rot.size(); ++k) {
                const auto& q = ch.rot[k].v;
                const auto& t = ch.pos[k].v;
                const auto& s = ch.scale[k].v;
                fprintf(f, "%s[%.9g,%.9g,%.9g,%.9g,%.9g,%.9g,%.9g,%.9g,%.9g,%.9g,%.9g]", k ? "," : "",
                        ch.rot[k].t, q.w, q.x, q.y, q.z, t.x, t.y, t.z, s.x, s.y, s.z);
            }
            fprintf(f, "]}");
        }
        fprintf(f, "]}");
    }
    fprintf(f, "]}\n");
    fclose(f);
    return 0;
}
