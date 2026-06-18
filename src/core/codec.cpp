// codec.cpp — Shift-JIS (CP932) -> UTF-8 conversion via Win32.
//
// Line-for-line port of the original `convertShiftJisToUtf8` helper from
// x_loader.cpp (lines 76-93).  The only change is the rename to
// `shiftJisToUtf8` to match the refactored naming convention.  The
// conversion path (SJIS -> wide via CP_932 -> UTF-8 via CP_UTF8) and the
// "return the original on failure" fallback are preserved exactly.
#define NOMINMAX
#include <windows.h>

#include <vector>

#include "core/codec.h"

std::string shiftJisToUtf8(const std::string& sjis) {
    if (sjis.empty()) return "";

    // CP_932 is the Code Page for Microsoft's Shift-JIS extension (MS Kanji)
    int wideLen = MultiByteToWideChar(932, 0, sjis.c_str(), -1, nullptr, 0);
    if (wideLen <= 0) return sjis; // Return original as fallback

    std::vector<wchar_t> wideBuf(wideLen);
    MultiByteToWideChar(932, 0, sjis.c_str(), -1, wideBuf.data(), wideLen);

    int utf8Len = WideCharToMultiByte(CP_UTF8, 0, wideBuf.data(), -1, nullptr, 0, nullptr, nullptr);
    if (utf8Len <= 0) return sjis;

    std::vector<char> utf8Buf(utf8Len);
    WideCharToMultiByte(CP_UTF8, 0, wideBuf.data(), -1, utf8Buf.data(), utf8Len, nullptr, nullptr);

    return std::string(utf8Buf.data());
}
