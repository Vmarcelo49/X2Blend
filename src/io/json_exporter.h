// json_exporter.h — XModel -> JSON serializer.
//
// Declares the JsonExporter class, which writes an XModel to a UTF-8 JSON
// file consumable by scripts/blend_importer.  The serialization logic in
// json_exporter.cpp is a port of the original json_exporter.cpp with two
// changes: (1) a `meta` block is emitted at the top of the JSON carrying
// pipeline configuration (bake mode, bake FPS, source ticks-per-second,
// max influences, version), and (2) error reporting goes through the
// LOG_ERROR macro from core/log.h instead of raw std::cerr.
#pragma once

#include <string>

#include "core/middleman.h"

class JsonExporter {
public:
    // Writes the XModel to `filepath` as UTF-8 JSON.  Returns true on
    // success, false if the file cannot be opened or a write error occurs.
    bool exportToFile(const XModel& model, const std::string& filepath);
};
