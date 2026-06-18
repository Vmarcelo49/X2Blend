// log.cpp — Minimal leveled logger implementation.
//
// Holds the current threshold in a mutex-protected global so the logger is
// thread-safe (the loader and viewer may both call log() concurrently in
// future refactors).  Output format is `[LEVEL] message\n` on std::cerr.
// The default level is Info per the contract.
#include "core/log.h"

#include <iostream>
#include <mutex>

namespace {

std::mutex g_logMutex;
LogLevel   g_logLevel = LogLevel::Info;

const char* levelTag(LogLevel level) {
    switch (level) {
        case LogLevel::Debug: return "DEBUG";
        case LogLevel::Info:  return "INFO";
        case LogLevel::Warn:  return "WARN";
        case LogLevel::Error: return "ERROR";
    }
    return "?";
}

} // namespace

void setLogLevel(LogLevel level) {
    std::lock_guard<std::mutex> lk(g_logMutex);
    g_logLevel = level;
}

LogLevel currentLogLevel() {
    std::lock_guard<std::mutex> lk(g_logMutex);
    return g_logLevel;
}

void log(LogLevel level, const std::string& msg) {
    std::lock_guard<std::mutex> lk(g_logMutex);
    // Defensive double-check: callers using the LOG_* macros already gated
    // on the level, but a direct log() call should also respect the
    // threshold.  We compare against the in-mutex copy g_logLevel.
    if (static_cast<int>(level) < static_cast<int>(g_logLevel)) return;
    std::cerr << "[" << levelTag(level) << "] " << msg << "\n";
}
