"""ANIM.LZS — the intro's dirty-rectangle ship/tunnel animation, decoded.

Traced live (2026-07-13, see run_status.md): the blit function `1010:42AF`
takes a pointer to a DGROUP record `(src_seg, dest_off, h, w)` and row-copies
``h`` rows of ``w`` bytes from ``src_seg:0`` onto VGA (`0xA000:dest_off`),
one row per call to the C-runtime `_fmemmove` helper (`1010:6053`,
`dest_off += 0x140` each row). Called from a table-walking loop this session
did not further isolate, but its INPUT is fully recovered: at boot frame 9,
every tile in `ANIM.LZS` is individually LZS-decompressed into its own tiny
allocated segment (confirmed: the very first tile's VM-observed
``(src_seg, dest_off, h, w)`` at frame 94 was `(0x78df, 0xb43a, 20, 26)`,
matching this module's tile[0] exactly), and the DGROUP table walked by the
caller is simply those tiles IN FILE ORDER, one 10-byte record apiece.

**What this recovers**: the tile data (dest/h/w/pixels, exact — full-file
byte consumption confirmed, 220 tiles, 44,808/44,808 bytes) and the exact
per-native-tick REVEAL COUNT the real boot replay used (`REVEAL_PACE`, VM-
traced from `replay_cold_20260711_201855` run blind with zero input — the
default/idle intro pacing). **What is NOT recovered**: the generic driver
loop at whatever address calls `42AF` in a table walk (not isolated), and
whether the real pacing is TIME-budgeted (would vary by host speed) or a
fixed per-tick schedule (this trace suggests fixed, since the counts settle
into a stable 1-2-3 repeating pattern independent of frame content) — so
`REVEAL_PACE` is a faithful REPLAY of one real trace, not a general rule.

Compositing all 220 tiles onto one canvas (ignoring the reveal order) shows
this is NOT one static picture — early tiles paint a close-up ship (engine
glow visible), later tiles paint a receding checkered-floor perspective at
OVERLAPPING screen coordinates: a genuine multi-frame animation using
dirty-rectangle updates directly on the visible framebuffer (matches the
game's whole rendering philosophy — see rendering_architecture.md), not a
single dissolve-in image.
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


#: VM-traced reveal pacing (replay_cold_20260711_201855, zero input, boot
#: frames 94-230): tiles-to-reveal at each successive native intro tick, in
#: FILE ORDER. Sums to 219 against 221 real tiles (2 short -- the capture
#: window's tail wasn't fully bracketed); `iter_reveal_counts` appends the
#: remainder to the final tick rather than silently dropping tiles. A
#: faithful replay of one real trace, not a rederived timing rule (see
#: module docstring).
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
