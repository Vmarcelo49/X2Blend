// codec.h — Shift-JIS (CP932) <-> UTF-8 string codec.
//
// Declares the single helper that converts a Shift-JIS-encoded string
// (as commonly found in Higurashi asset texture paths and material names)
// into UTF-8 for the JSON intermediate.  The implementation lives in
// codec.cpp and uses the Win32 MultiByteToWideChar / WideCharToMultiByte
// pair, so codec.cpp includes <windows.h>; this header does not.
#pragma once

#include <string>

// Converts a Shift-JIS (CP932) string to UTF-8 using Win32
// MultiByteToWideChar.  Returns the input unchanged on failure (e.g. if
// the Win32 conversion reports zero output length).  Empty input returns
// empty output without calling into Win32.
std::string shiftJisToUtf8(const std::string& sjis);
