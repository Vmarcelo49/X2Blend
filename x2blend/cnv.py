"""The game's .cnv image container. No Blender dependency.

Layout: u8 declared bpp, u32 ignored, u32 height, u32 width, u32 compressed size, pixels.
Pixels are BGRA or BGR with rows top-down. The declared bpp is unreliable (most files
say 24 and store 32), so the payload size decides.
"""

import struct

_HEADER = 17


def read_cnv(path):
    """Return (width, height, RGBA bytes with rows top-down)."""
    with open(path, "rb") as f:
        data = f.read()
    if len(data) < _HEADER:
        raise ValueError(f"{path}: too short for a .cnv")
    height, width, compressed = struct.unpack_from("<III", data, 5)
    if compressed:
        raise ValueError(f"{path}: compressed .cnv is not supported")
    payload = data[_HEADER:]
    pixels = width * height
    if pixels and len(payload) == pixels * 4:
        rgba = bytearray(payload)
        rgba[0::4], rgba[2::4] = payload[2::4], payload[0::4]
    elif pixels and len(payload) == pixels * 3:
        rgba = bytearray(b"\xff" * (pixels * 4))
        rgba[0::4], rgba[1::4], rgba[2::4] = payload[2::3], payload[1::3], payload[0::3]
    else:
        raise ValueError(f"{path}: not an image (audio also ships as .cnv)")
    return width, height, bytes(rgba)
