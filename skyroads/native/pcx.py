"""Minimal ZSoft PCX decoder — for ``LOGO.PCX`` (a standard third-party
format, not SkyRoads-proprietary; no VM recovery needed).

``LOGO.PCX``: 8bpp, 1-plane, RLE-encoded, 279x156, with the standard
256-colour palette in the trailing 769 bytes (marker `0x0C` + 768 RGB
bytes). Decoded natively and matches visually (a "Creative Dimensions"
splash) — see run_status.md.
"""
from __future__ import annotations

import struct
from pathlib import Path
from typing import NamedTuple


class PcxImage(NamedTuple):
    width: int
    height: int
    pixels: bytes              # width*height, row-major, palette indices
    palette: list              # 256 (r, g, b) tuples


def load_pcx(path: "str | Path") -> PcxImage:
    data = Path(path).read_bytes()
    if data[0] != 0x0A or data[2] != 1 or data[3] != 8:
        raise ValueError("only 8bpp RLE PCX is supported")
    xmin, ymin, xmax, ymax = struct.unpack_from("<4H", data, 4)
    width, height = xmax - xmin + 1, ymax - ymin + 1
    bytes_per_line = struct.unpack_from("<H", data, 66)[0]
    pos = 128
    rows = []
    for _ in range(height):
        row = bytearray()
        while len(row) < bytes_per_line:
            b = data[pos]
            pos += 1
            if (b & 0xC0) == 0xC0:
                count = b & 0x3F
                val = data[pos]
                pos += 1
                row.extend([val] * count)
            else:
                row.append(b)
        rows.append(bytes(row[:width]))
    if data[-769] != 0x0C:
        raise ValueError("missing PCX palette marker")
    pal_bytes = data[-768:]
    palette = [tuple(pal_bytes[3 * i:3 * i + 3]) for i in range(256)]
    return PcxImage(width, height, b"".join(rows), palette)
