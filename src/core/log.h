// log.h — Minimal leveled logger.
//
// Provides four log levels (Debug, Info, Warn, Error), a global threshold
// settable via setLogLevel(), and four short-circuiting LOG_* macros.
// Output goes to std::cerr in the form `[LEVEL] message`.  The default
// threshold is Info, so Debug messages are suppressed unless the caller
// explicitly lowers the level.
//
// The macros check the current level *before* evaluating their message
// expression, so cheaply-skipped log calls stay cheap (no string building
// when the level is filtered out).  The message expression is wrapped in
// std::string(...) so callers can pass string literals, std::string
// temporaries, or any expression that concatenates into a std::string.
#pragma once

#include <string>

enum class LogLevel { Debug, Info, Warn, Error };

// Sets the global log threshold.  Messages at or above the threshold are
// emitted; messages below are suppressed.
void setLogLevel(LogLevel level);

// Returns the current global log threshold.
LogLevel currentLogLevel();

// Emits `msg` to std::cerr with a `[LEVEL]` prefix iff `level` is at or
// above the current threshold.  Safe to call directly, but the LOG_* macros
// below are the preferred entry points because they short-circuit.
void log(LogLevel level, const std::string& msg);

// Short-circuiting macros.  Each evaluates `msg` only when the current
// threshold permits the call, then forwards the stringified message to
// log().  Use these instead of calling log() directly.
#define LOG_DEBUG(msg) \
    do { if (static_cast<int>(::currentLogLevel()) <= static_cast<int>(LogLevel::Debug)) \
         ::log(LogLevel::Debug, std::string(msg)); } while (0)

#define LOG_INFO(msg) \
    do { if (static_cast<int>(::currentLogLevel()) <= static_cast<int>(LogLevel::Info))  \
         ::log(LogLevel::Info, std::string(msg)); } while (0)

#define LOG_WARN(msg) \
    do { if (static_cast<int>(::currentLogLevel()) <= static_cast<int>(LogLevel::Warn))  \
         ::log(LogLevel::Warn, std::string(msg)); } while (0)

#define LOG_ERROR(msg) \
    do { if (static_cast<int>(::currentLogLevel()) <= static_cast<int>(LogLevel::Error)) \
         ::log(LogLevel::Error, std::string(msg)); } while (0)
