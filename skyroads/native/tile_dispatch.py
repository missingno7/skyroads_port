"""The `1010:2D1F` road-tile dispatch loop as a pure transcription — the heart
of the live gameplay frame.

Walks the road records (DGROUP, 7 words per row, stride 0xE) in an 11-row ×
(2-pass × 4-column) grid; per cell dispatches on the record's type nibble
(`[bp+1] & 0xF`) through the `[0BAF]` handler table; each handler PATCHES the
first byte of one or more RLE strips in the current DISPLAY-LIST segment
(rotating `[0E76]` buffer selected by `[0E2A] & 7`) and rasterizes them with
the pass's rasterizer — forward RLE on pass 0, backward (mirror) on pass 1 —
with `31D1`-style strip SKIPS selecting which strips of a slot appear.
Neighbor gating: `[bp-13]` (row above's type byte) and `[bp+bx+1]` (adjacent
column, `bx = 2 − 4·pass`) choose edge tiles (`+0x1E`/`+0x0F`/`0x41`/`0x43`)
and block-face pieces (hi nibble, default `0x3D`). Row `[0E44] == 4` is the
SHIP row: it runs twice (display-list `di` overridden to 0x210 then 0x240)
with `[0E3E]` (`325B`, the 29×24 ship-tile rasterizer chain) between runs.

Everything here is an exact transcription of the `2D1F` disassembly
(run_status.md 2026-07-12 "tile-dispatch fully decoded") over the pure
`skyroads.recovered.rle_sprite` pair. The `[0E38]` pre-pass (`34AE` mode-0
composite), the `34AE(1)` finalize, the `[0E6A]` frame rotation and the
occlusion-mask copy are the FRAME assembler's job, not this loop's.

[asm 1010:2D9F-2E3C loop; 2E6C/2EBB/2EFD/2F58/2FCC/3059 handlers; 31D1 skip]
"""
from __future__ import annotations

from typing import Callable, Optional

from skyroads.native.image import NativeGameImage
from skyroads.recovered.rle_sprite import rle_sprite_backward, rle_sprite_forward

HANDLER_TABLE = 0x0BAF     # DGROUP word table: tile type -> handler (16 entries)
BUFFER_SEG_TABLE = 0x0E76  # the 8 rotating display-list segments
RECORD_BASE = 0x162C       # road records (= road[] from ROADS.LZS)
RECORD_STEP = 0xE
RECORD_BIAS = 0x62
SHIP_ROW_E44 = 4           # the [0E44] value of the twice-rendered ship row
SHIP_ROW_DI = (0x210, 0x240)


class _Ctx:
    """Register state the handlers share (SI flows through draw/skip calls)."""
    __slots__ = ("img", "dg", "ls", "dest", "di", "bp", "pass_idx")

    def __init__(self, img: NativeGameImage, dg: int, ls: int, dest: int):
        self.img, self.dg, self.ls, self.dest = img, dg, ls, dest
        self.di = 0
        self.bp = 0
        self.pass_idx = 0

    # DGROUP / display-list accessors ---------------------------------------
    def rbg(self, off: int) -> int:
        return self.img.rb(self.dg, off & 0xFFFF)

    def rwl(self, off: int) -> int:
        return self.img.rw(self.ls, off & 0xFFFF)

    def wbl(self, off: int, v: int) -> None:
        self.img.wb(self.ls, off & 0xFFFF, v)

    # the [0E40] rasterizer: forward on pass 0, backward on pass 1 ----------
    def draw(self, si: int) -> int:
        fn = rle_sprite_forward if self.pass_idx == 0 else rle_sprite_backward
        return fn(self.img.rb, self.img.wb, self.dg, self.ls, self.dest, si)

    def skip(self, si: int) -> int:
        """`1010:31D1`: advance past one strip without drawing."""
        si = (si + 3) & 0xFFFF
        while self.img.rb(self.ls, si) != 0xFF:
            si = (si + 3) & 0xFFFF
        return (si + 1) & 0xFFFF

    # neighbor record fields -------------------------------------------------
    def side_type(self) -> int:
        """`[bp+bx+1]` with `bx = 2 - 4*pass` — the adjacent cell's type byte."""
        bx = 2 - 4 * self.pass_idx
        return self.rbg(self.bp + bx + 1)

    def side_height_empty(self) -> bool:
        """`[bp+bx] & 0xF == 0` — adjacent cell's height nibble empty."""
        bx = 2 - 4 * self.pass_idx
        return (self.rbg(self.bp + bx) & 0xF) == 0

    def above_type(self) -> int:
        return self.rbg(self.bp - 13)      # row above's type byte

    def above_height_empty(self) -> bool:
        return (self.rbg(self.bp - 14) & 0xF) == 0


def _patch_draw(ctx: _Ctx, si: int, tile: int) -> int:
    ctx.wbl(si, tile)
    return ctx.draw(si)


def _hi_or_default(ctx: _Ctx) -> int:
    hi = (ctx.rbg(ctx.bp) >> 4) & 0xF
    return hi if hi else 0x3D


def _h0(ctx: _Ctx) -> None:
    """type 0 (`2E6C`) — the base flat-tile handler every other type calls."""
    tid = ctx.rbg(ctx.bp) & 0xF
    if tid == 0:
        return
    si = ctx.rwl(ctx.di)
    si = _patch_draw(ctx, si, tid)
    if ctx.side_height_empty():
        si = _patch_draw(ctx, si, (tid + 0x1E) & 0xFF)
    else:
        si = ctx.skip(si)
    if ctx.above_height_empty():
        _patch_draw(ctx, si, (tid + 0x0F) & 0xFF)


def _h1(ctx: _Ctx) -> None:
    """type 1 (`3059`)."""
    _h0(ctx)
    if ctx.above_type() < 1:
        _patch_draw(ctx, ctx.rwl(ctx.di + 2), 0x43)
    si = ctx.rwl(ctx.di + 8)
    for _ in range(6):
        si = ctx.draw(si)
    if ctx.above_type() < 1:
        si = ctx.draw(si)
        ctx.draw(si)


def _h2(ctx: _Ctx) -> None:
    """type 2 (`2EBB`)."""
    _h0(ctx)
    if ctx.above_type() < 2:
        ctx.draw(ctx.rwl(ctx.di + 6))
    si = ctx.rwl(ctx.di + 4)
    si = _patch_draw(ctx, si, _hi_or_default(ctx))
    if ctx.side_type() < 2:
        ctx.draw(si)


def _h3(ctx: _Ctx) -> None:
    """type 3 (`2EFD`)."""
    _h0(ctx)
    if ctx.above_type() < 2:
        _patch_draw(ctx, ctx.rwl(ctx.di + 2), 0x41)
    si = ctx.rwl(ctx.di + 4)
    si = _patch_draw(ctx, si, _hi_or_default(ctx))
    if ctx.side_type() < 2:
        ctx.draw(si)
    if ctx.above_type() < 2:
        si = ctx.rwl(ctx.di + 6)
        si = ctx.skip(si)
        si = ctx.draw(si)
        ctx.draw(si)


def _h4(ctx: _Ctx) -> None:
    """type 4 (`2F58`)."""
    _h0(ctx)
    if ctx.above_type() < 2:
        ctx.draw(ctx.rwl(ctx.di + 6))
    si = ctx.rwl(ctx.di + 4)
    si = ctx.skip(si)
    if ctx.side_type() < 2:
        si = ctx.draw(si)
    si = ctx.rwl(ctx.di + 10)
    si = _patch_draw(ctx, si, _hi_or_default(ctx))
    if ctx.side_type() < 4:
        si = ctx.draw(si)
    else:
        si = ctx.skip(si)
    if ctx.above_type() < 4:
        ctx.draw(si)


def _h5(ctx: _Ctx) -> None:
    """type 5 (`2FCC`)."""
    _h0(ctx)
    if ctx.above_type() < 2:
        _patch_draw(ctx, ctx.rwl(ctx.di + 2), 0x41)
    si = ctx.rwl(ctx.di + 4)
    si = ctx.skip(si)
    if ctx.side_type() < 2:
        si = ctx.draw(si)
    if ctx.above_type() < 2:
        si = ctx.rwl(ctx.di + 6)
        si = ctx.skip(si)
        si = ctx.draw(si)
        ctx.draw(si)
    si = ctx.rwl(ctx.di + 10)
    si = _patch_draw(ctx, si, _hi_or_default(ctx))
    if ctx.side_type() < 4:
        si = ctx.draw(si)
    else:
        si = ctx.skip(si)
    if ctx.above_type() < 4:
        ctx.draw(si)


_HANDLERS = {0: _h0, 1: _h1, 2: _h2, 3: _h3, 4: _h4, 5: _h5}


def render_tile_passes(
    img: NativeGameImage, dgroup_seg: int,
    on_ship_row: Optional[Callable[[_Ctx], None]] = None,
) -> None:
    """Run `2D1F`'s 11-row × 2-pass × 4-column tile dispatch over ``img`` in
    place. Expects the 8 render params already stored at `[0E28..0E36]` (see
    `native/render_params.py`). ``on_ship_row`` stands in for `call ss:[0E3E]`
    (`325B`, the ship-tile rasterizer chain) between the ship row's two runs;
    ``None`` skips it (the caller composes it separately)."""
    dg = dgroup_seg
    dest0 = img.rw(dg, 0x0E36)
    bump = 0x280 if img.rw(dg, 0x003C) != 0 else 0x50           # [asm 2D5B-2D6B]
    dest = (dest0 + bump) & 0xFFFF
    e2a = img.rw(dg, 0x0E2A)
    ls = img.rw(dg, (BUFFER_SEG_TABLE + ((e2a & 7) << 1)) & 0xFFFF)  # [asm 2D82]

    ctx = _Ctx(img, dg, ls, dest)
    ctx.bp = ((e2a >> 3) * RECORD_STEP + RECORD_BASE + RECORD_BIAS) & 0xFFFF  # [asm 2D6F]

    e44 = 0x0B                                                   # [asm 2D98]
    e4a = 0                                                      # [asm 2D91]
    while True:
        # --- 2D9F row-run start ---
        ctx.pass_idx = 0
        saved_di = None
        if e44 == SHIP_ROW_E44:                                  # [asm 2DAE]
            saved_di = ctx.di
            ctx.di = SHIP_ROW_DI[1] if e4a else SHIP_ROW_DI[0]
        while True:
            # --- 2DC5 four columns ---
            for _ in range(4):
                t = ctx.rbg(ctx.bp + 1) & 0xF                    # [asm 2DCC]
                handler = _HANDLERS.get(t)
                if handler is not None:                          # 6..15 -> 3AC9 ret
                    handler(ctx)
                ctx.di = (ctx.di + 0xC) & 0xFFFF                 # [asm 2DD9]
                ctx.bp = (ctx.bp + 2) & 0xFFFF
                if ctx.pass_idx:
                    ctx.bp = (ctx.bp - 4) & 0xFFFF               # pass 1 walks back
            ctx.di = (ctx.di - 0x30) & 0xFFFF                    # [asm 2DF1]
            ctx.bp = (ctx.bp + 4) & 0xFFFF
            ctx.pass_idx += 1                                    # [0E40] -> backward
            if ctx.pass_idx >= 2:                                # [asm 2E04]
                break
        if e44 == SHIP_ROW_E44:                                  # [asm 2E0C]
            ctx.di = saved_di
            e4a += 1
            if e4a < 2:
                ctx.bp = (ctx.bp - 8) & 0xFFFF                   # [asm 2E22]
                if on_ship_row is not None:
                    on_ship_row(ctx)                             # call ss:[0E3E]
                continue                                         # redo the row
        ctx.di = (ctx.di + 0x30) & 0xFFFF                        # [asm 2E2D]
        ctx.bp = (ctx.bp - 0x16) & 0xFFFF
        e44 -= 1
        if e44 == 0:                                             # [asm 2E33]
            # The ASM keeps these loop scratches in DGROUP; persist their
            # final values so the post-frame DGROUP is byte-identical
            # ([0E40] = backward-RLE ptr after the last pass; [0E48] pass
            # counter; [0E4A] ship-row rerun counter -- all end at 2/2).
            img.ww(dg, 0x0E40, 0x3190)
            img.ww(dg, 0x0E48, 0x2)
            img.ww(dg, 0x0E4A, 0x2)
            return
