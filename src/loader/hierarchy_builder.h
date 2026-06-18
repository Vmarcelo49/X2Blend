// loader/hierarchy_builder.h — D3DXFRAME -> XModel node array.
//
// Walks the D3DX frame tree produced by D3DXLoadMeshHierarchyFromX and
// appends one XNode per D3DXFRAME to model.nodes.  Also fills three
// lookup maps the downstream mesh extractor and animation baker need:
//
//   frameToIndexMap   — D3DXFRAME*        -> node index in model.nodes
//   nameToIndexMap    — UTF-8 frame name  -> node index in model.nodes
//   frameToWorldMap   — D3DXFRAME*        -> world (root-relative) matrix
//
// Returns the root node index, or -1 if pRootFrame is null.
//
// Ported from x_loader.cpp (lines 318-375): processFrameHierarchy.
// The math is preserved verbatim (D3DXMatrixDecompose + the
// t.x/t.z/t.y, -r.x/-r.z/-r.y, s.x/s.z/s.y axis-swap) so the TRS
// decomposition produces byte-identical values to the original.  The
// only behavioral change is dropping the `node.useTRS = true` line:
// the field was removed from XNode in the refactor (it was set but
// never read by the Python importer — dead code per analysis section
// 2.2.D).
#pragma once

#include <d3dx9.h>

#include <map>
#include <string>

#include "core/middleman.h"

class HierarchyBuilder {
public:
    // Recursively walks the D3DXFRAME tree and appends XNode entries to
    // model.nodes.  Fills frameToIndexMap, nameToIndexMap, and
    // frameToWorldMap.  Returns the root node index, or -1 on null root.
    int build(D3DXFRAME* pRootFrame, XModel& model,
              std::map<D3DXFRAME*, int>& frameToIndexMap,
              std::map<std::string, int>& nameToIndexMap,
              std::map<D3DXFRAME*, D3DXMATRIX>& frameToWorldMap);

private:
    int processFrame(D3DXFRAME* pFrame, int parentIdx,
                     const D3DXMATRIX& parentWorld,
                     XModel& model,
                     std::map<D3DXFRAME*, int>& frameToIndexMap,
                     std::map<std::string, int>& nameToIndexMap,
                     std::map<D3DXFRAME*, D3DXMATRIX>& frameToWorldMap);
};
