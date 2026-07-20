"""The whole-road-tile rasterizer chain — `1010:325B` (drive), `32C1` (clip
mask), `33FD` (shade) — as pure functions.

This is the ship-row tile draw (`2D1F`'s `call ss:[0E3E]` between the ship
row's two runs): build a 29-wide clip/coverage mask at DGROUP `[0E86]` from the
road position fields, blit the 29×24 tile bitmap (`lds si,[0E2E]`, column-major
source) into the destination wherever the mask permits (marking covered cells
2), then shade — remap the covered screen pixels through the tile pattern at
`[0x68E + idx*0x105]` (`0x3D`→`0x40`; 1..0xF → `+0x2D` ramp).

Promoted from `skyroads/hooks.py`'s `_tile_mask_build`/`_tile_rasterizer_hook`/
`_tile_shade_build` — pure logic already, differential-verified in situ; only
the register/flag exit bookkeeping stays behind in the hooks.

[asm 1010:325B-32C0 drive; 32C1-3368 mask; 33FD-347D shade]
"""
from __future__ import annotations

from typing import Callable


MASK_BUF = 0x0E86          # DGROUP coverage/clip mask, 29 bytes/row x 33 rows
MASK_ROWS = 0x21
MASK_ROW_STRIDE = 0x1D
SHADE_PATTERN_BASE = 0x068E
SHADE_MASK_BASE = 0x113E
TILE_BITMAP_PTR = 0x0E2E   # far pointer (offset, then segment at +2)


def _sgn16(v: int) -> int:
    v &= 0xFFFF
    return v - 0x10000 if v >= 0x8000 else v


def tile_mask_build(rw: Callable[[int], int], ww: Callable[[int, int], None],
                    wb: Callable[[int, int], None]) -> None:
    """Build the 29x33 coverage mask at DGROUP `[0E86]` (`1010:32C1`).
    ``rw``/``ww``/``wb`` are DGROUP word/word-write/byte-write accessors."""
    for i in range(0x01DE):
        ww((MASK_BUF + 2 * i) & 0xFFFF, 0)

    di = MASK_BUF
    bx = rw(0x0E2C)
    si = ((0x009D - rw(0x0E2C)) << 1) & 0xFFFF
    dx = MASK_ROWS
    while True:
        if not (bx > 0x009D or bx < 0x0014):
            saved_bx, saved_di = bx, di
            bx = 0x006E
            cx = 0x01AE
            ext = rw((si + 0x047A) & 0xFFFF)
            if ext != 0:
                ax = (0x010E - ext) & 0xFFFF
                center = rw(0x0E28)
                if not (_sgn16(center) < _sgn16(ax)):
                    bx = (0x010E + ext) & 0xFFFF
                if _sgn16(center) < _sgn16(ax):
                    cx = ax
            ax = cx
            cx = MASK_ROW_STRIDE
            bx = (bx - rw(0x0E28)) & 0xFFFF
            if _sgn16(bx) < 0:
                bx = 0
            if bx < 0x001D:
                di = (di + bx) & 0xFFFF
                cx = (cx - bx) & 0xFFFF
                bx = (rw(0x0E28) + 0x1D - ax) & 0xFFFF
                if _sgn16(bx) < 0:
                    bx = 0
                old_cx = cx
                cx = (cx - bx) & 0xFFFF
                if not (old_cx <= bx):
                    for _ in range(cx):
                        wb(di, 0x01)
                        di = (di + 1) & 0xFFFF
            bx, di = saved_bx, saved_di

        di = (di + MASK_ROW_STRIDE) & 0xFFFF
        bx = (bx - 1) & 0xFFFF
        si = (si + 2) & 0xFFFF
        if dx == 0x0A:
            skip = (rw(0x0E34) - 0x0008) & 0xFFFF
            bx = (bx - skip) & 0xFFFF
            si = (si + ((skip << 1) & 0xFFFF)) & 0xFFFF
        dx = (dx - 1) & 0xFFFF
        if dx == 0:
            return


def tile_shade(rb: Callable[[int, int], int], wb: Callable[[int, int, int], None],
               rw: Callable[[int], int], ww: Callable[[int, int], None],
               dgroup_seg: int, dest_seg: int) -> None:
    """The road-tile shader (`1010:33FD`), over seg-style ``rb``/``wb`` plus
    DGROUP word accessors ``rw``/``ww``."""
    v34 = rw(0x0E34)
    idx = v34 // 5
    if idx >= 5:
        return
    si = (SHADE_PATTERN_BASE + idx * 0x0105) & 0xFFFF
    row = (0x009D - rw(0x0E2C) + 0x0010 + v34) & 0xFFFF
    di = (_sgn16(row) * 0x0140) & 0xFFFF
    di = (di + rw(0x0E28) - 0x006E) & 0xFFFF
    ww(0x0E70, di)

    bx = 0
    for _col in range(0x1D):
        for _row in range(9):
            if (rb(dgroup_seg, (bx + si) & 0xFFFF) != 0
                    and rb(dgroup_seg, (bx + SHADE_MASK_BASE) & 0xFFFF) != 0):
                wb(dgroup_seg, (bx + SHADE_MASK_BASE) & 0xFFFF, 0x02)
                al = rb(dest_seg, di)
                if al == 0x3D:
                    al = 0x40
                if al != 0 and al < 0x10:
                    al = (al + 0x2D) & 0xFF
                wb(dest_seg, di, al)
            di = (di + 0x0140) & 0xFFFF
            bx = (bx + MASK_ROW_STRIDE) & 0xFFFF
        di = (di - 0x0B3F) & 0xFFFF
        bx = (bx - 0x0104) & 0xFFFF


def tile_rasterize(rb: Callable[[int, int], int], wb: Callable[[int, int, int], None],
                   rw: Callable[[int], int], ww: Callable[[int, int], None],
                   dgroup_seg: int) -> None:
    """The full `1010:325B` chain (mask -> blit -> shade), pure."""
    dest_seg = rw(0x0E36)

    def wb_dg(off: int, v: int) -> None:
        wb(dgroup_seg, off, v)

    tile_mask_build(rw, ww, wb_dg)

    di = (_sgn16((0x009D - rw(0x0E2C)) & 0xFFFF) * 0x0140) & 0xFFFF
    di = (di + rw(0x0E28) - 0x006E) & 0xFFFF
    ww(0x0E6C, di)
    si = rw(TILE_BITMAP_PTR)
    tile_seg = rw(TILE_BITMAP_PTR + 2)

    bx = 0
    for _row in range(0x1D):
        for _col in range(0x18):
            al = rb(tile_seg, si)
            si = (si + 1) & 0xFFFF
            if al != 0 and rb(dgroup_seg, (MASK_BUF + bx) & 0xFFFF) != 0:
                wb(dgroup_seg, (MASK_BUF + bx) & 0xFFFF, 0x02)
                wb(dest_seg, di, al)
            di = (di + 0x0140) & 0xFFFF
            bx = (bx + MASK_ROW_STRIDE) & 0xFFFF
        di = (di - 0x1DFF) & 0xFFFF
        bx = (bx - 0x02B7) & 0xFFFF

    tile_shade(rb, wb, rw, ww, dgroup_seg, dest_seg)
