#pragma once

#include <string>
#include "middleman.h"

// Serialises an XModel to a JSON file that blend_importer.py can consume.
class JsonExporter {
public:
    // Writes model data to filepath. Returns true on success.
    bool exportToFile(const XModel& model, const std::string& filepath);
};
