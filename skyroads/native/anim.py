"""Decode the intro's ``ANIM.LZS`` dirty-rectangle animation.

The file contains 220 LZS-compressed tile records in presentation order. Each
record supplies ``(dest, height, width, pixels)`` for a row-major VGA blit.
``REVEAL_PACE`` preserves the one observed idle-intro schedule as evidence; it
is not claimed as a recovered general timing rule. The table-walking driver
around ``1010:42AF`` remains outside this focused candidate.
"""
from __future__ import annotations

import struct
from pathlib import Path
from typing import List, NamedTuple

from skyroads.codecs.lzs import LzsWidths, _BitReader

VGA_WIDTH = 320
VGA_HEIGHT = 200


class AnimTile(NamedTuple):
    dest: int          # VGA byte offset (0xA000:dest)
    h: int
    w: int
    pixels: bytes       # h*w, row-major, palette-relative (0..ncolours-1)


def _decompress_tracked(payload: bytes, widths: LzsWidths, out_size: int):
    r = _BitReader(payload)
    out = bytearray()
    while len(out) < out_size:
        if r.get_bit() == 0:
            dist = r.get_bits(widths.width_dist_long) + 2
        else:
            if r.get_bit() == 1:
                out.append(r.get_bits(8))
                continue
            dist = r.get_bits(widths.width_dist_short) + (1 << widths.width_dist_long) + 2
        length = r.get_bits(widths.width_len) + 2
        src = len(out) - dist
        for _ in range(length):
            out.append(out[src] if 0 <= src < len(out) else 0)
            src += 1
    consumed = r._pos - (1 if r._bits_left == 8 else 0)
    return bytes(out), consumed


def load_anim(path: "str | Path") -> "tuple[list, list]":
    """Parse ANIM.LZS: ``"ANIM" + u16`` outer wrapper, one shared CMAP, then
    tiles back-to-back (search-resynced past small inter-tile gaps — 0, 2,
    or occasionally more padding bytes were observed, never a real desync).
    Returns (cmap_rgb6_bytes, [AnimTile, ...]) in file order."""
    data = Path(path).read_bytes()
    if data[:4] != b"ANIM" or data[6:10] != b"CMAP":
        raise ValueError("not an ANIM.LZS-shaped file")
    ncolours = data[10]
    cmap = data[11:11 + 3 * ncolours]
    pos = data.find(b"PICT", 11 + 3 * ncolours)
    tiles: List[AnimTile] = []
    while pos >= 0 and pos < len(data) - 4:
        dest, h, w = struct.unpack_from("<3H", data, pos + 4)
        p = pos + 10
        widths = LzsWidths(data[p], data[p + 1], data[p + 2])
        pixels, consumed = _decompress_tracked(data[p + 3:], widths, h * w)
        tiles.append(AnimTile(dest, h, w, pixels))
        pos = data.find(b"PICT", p + 3 + consumed)
    return cmap, tiles


def paint_tile(canvas: bytearray, tile: AnimTile) -> None:
    """Blit one tile onto a 320x200 (64,000-byte) canvas, row by row --
    exactly `1010:42AF`'s row loop (dest advances by VGA_WIDTH per row)."""
    for row in range(tile.h):
        o = tile.dest + row * VGA_WIDTH
        if 0 <= o and o + tile.w <= VGA_WIDTH * VGA_HEIGHT:
            canvas[o:o + tile.w] = tile.pixels[row * tile.w:(row + 1) * tile.w]


#: Observed idle-intro tiles-to-reveal schedule, in file order. It sums to 219
#: against 221 real tiles (2 short because the observation
#: window's tail wasn't fully bracketed); `iter_reveal_counts` appends the
#: remainder to the final tick rather than silently dropping tiles. A
#: retained observation, not a recovered general timing rule.
REVEAL_PACE = (
    22, 27, 14, 11, 8, 7, 4, 4, 3, 2, 2, 2, 2, 1, 1, 1, 1, 1, 1, 2, 1, 1, 1,
    1, 2, 1, 1, 2, 1, 4, 4, 1, 2, 3, 3, 1, 2, 3, 3, 1, 2, 3, 3, 2, 2, 1, 2,
    2, 2, 2, 2, 2, 2, 1, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 1, 1, 1, 1, 1, 1, 1,
    1, 1, 1, 1, 1, 1, 1, 1, 1, 1,
)


def iter_reveal_counts(total_tiles: int):
    """Yield per-tick reveal counts covering ALL `total_tiles` -- REVEAL_PACE
    plus any remainder on the final tick (never silently drops tiles)."""
    yield from REVEAL_PACE
    remainder = total_tiles - sum(REVEAL_PACE)
    if remainder > 0:
        yield remainder
