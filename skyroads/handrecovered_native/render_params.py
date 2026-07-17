"""The per-frame RENDER ORCHESTRATOR (`1010:0C98`) as a pure function — the last
node between the native sim and a visible frame.

`0C98(offscreen_flag)` is what the game's frame loop calls to render gameplay:
it derives the road renderer's 8 parameters (`2D1F`'s `[0E28..0E36]`) purely
from sim state (lateral `[9618/961A]`, vertical `[AF1C]`/`[AF2C]`, air counter
`[456A]`, game_state `[456E]`, tick `[1600]`, bounce `[9336]`), keeps a frame
dirty-cache (`[0E1C..0E26]` — skip the render when nothing visible changed),
selects the destination (off-screen `[5478]` when `[003C]!=0`, else the VGA
page-flip pair `A000`/`A200` by `[9334]`), calls `2D1F`, and flips the page.

Every callee was already recovered/lifted; this module just composes them:
  * `0533` fall predicate → `skyroads.handrecovered_native.collision.ship_fell_off`
  * `04C0` perspective    → `skyroads.handrecovered.renderer.perspective_row_offset`
  * `0BAF` pitch selector / `0BE9` row band — tiny leaves, ported inline below
  * `5D8C` ulong_div      → a plain 32-bit divide here
  * `0C26` cell classifier → `_cell_value` below (04C0 word → tiny table map)

[asm 1010:0C98-0ECF orchestrator; 0BAF/0BE9/0C26 leaves; disassembled 2026-07-12
from gameplay_f640 — see run_status.md "render orchestrator 0C98 decoded"]
"""
from __future__ import annotations

from typing import Callable, NamedTuple, Optional

from skyroads.handrecovered_native.collision import ship_fell_off
from skyroads.handrecovered.renderer import perspective_row_offset

# DGROUP offsets (documented layout; see dgroup_view / run_status.md)
LATERAL_LO, LATERAL_HI = 0x9618, 0x961A
AF1C, AF2C = 0xAF1C, 0xAF2C
AIR_COUNTER = 0x456A          # [456A]: airborne/crash animation counter
GAME_STATE = 0x456E
TICK = 0x1600
BOUNCE_VVEL = 0x9336
PAGE_FLAG = 0x9334            # front/back page selector (xor'd 1 after render)
OFFSCREEN_MODE = 0x003C       # !=0 -> render to [5478] instead of VGA pages
OFFSCREEN_SEG = 0x5478
SPRITE_BANK_LO, SPRITE_BANK_HI = 0xAF34, 0xAF36
#: dirty-cache of the last-rendered (lateral, af1c, af2c_eff, sprite idx, page)
CACHE_BASE = 0x0E1C           # 0E1C/0E1E lat, 0E20 af1c, 0E22 af2c_eff, 0E24 si, 0E26 page
#: small DGROUP tables the orchestrator indexes (displacements from the ASM)
ROW_BASE_TABLE = 0x3E         # word[0x3E + row*2]: per-band projection row base
CELL_MAP_TABLE = 0xE4         # word[0xE4 + hi_nibble*2]: cell class -> value
WOBBLE_TABLE = 0x11A          # word[0x11A + ((tick/2)%4)*2]: idle wobble frames
SPRITE_STRIDE = 0x2D0         # bytes per ship-sprite frame in the [AF34:AF36] bank


def pitch_selector(vvel: int, af2c: int) -> int:
    """`1010:0BAF(vvel, af2c)` — ship pitch frame 0/1/2 from vertical velocity
    + height: 2 when diving fast or low (`vvel <= -0x163` signed, or
    `af2c < 0x2800`), 1 when climbing (`vvel >= 0x163`), else 0."""
    v = vvel - 0x10000 if vvel & 0x8000 else vvel
    if v <= -0x163 or (af2c & 0xFFFF) < 0x2800:
        return 2
    return 1 if v >= 0x163 else 0


def row_band(af1c: int) -> int:
    """`1010:0BE9(af1c)` — the 7-band projection row index:
    clamp((af1c/0x80 - 0x5F) idiv 0x2E, 0, 6)."""
    a = (((af1c & 0xFFFF) // 0x80) + 0xFFA1) & 0xFFFF
    if a & 0x8000:
        a -= 0x10000
    # 8086 idiv truncates toward zero
    q = a // 0x2E if a >= 0 else -((-a) // 0x2E)
    return 0 if q < 0 else 6 if q > 6 else q


def _cell_value(rw: Callable[[int], int], x_lo: int, x_hi: int, y: int, flag: int) -> int:
    """`1010:0C26(x_lo, x_hi, y, flag)` — classify the road cell at (x, y):
    the `04C0` perspective word's high nibble maps through `word[0xE4 + hi*2]`
    (0x100 → 0), unless `flag` (ship-fell) forces the low-nibble path:
    0x2800 for any low-nibble content, else 0."""
    r = perspective_row_offset(x_lo, x_hi, y & 0xFFFF)
    persp = rw(r.offset) if r.in_range else 0
    hi = persp & 0x0F00
    if hi != 0 and (flag & 0xFFFF) == 0:
        if hi == 0x100:
            return 0
        return rw((CELL_MAP_TABLE + ((hi >> 8) << 1)) & 0xFFFF)
    return 0x2800 if (persp & 0x000F) else 0


class RenderParams(NamedTuple):
    """`2D1F`'s 8 parameters, in `[0E28..0E36]` order."""
    row_base: int       # [0E28] word[0x3E + row_band*2] + af1c/0x80
    lateral_col: int    # [0E2A] low word of lateral32 / 0x2000
    screen_row: int     # [0E2C] af2c_eff/0x80 (0 when sprite idx == -1)
    sprite_lo: int      # [0E2E] [AF34] + 0x2D0*sprite_idx (16-bit, no carry out)
    sprite_hi: int      # [0E30] [AF36] (unchanged -- the ASM adds no carry)
    zero: int           # [0E32] constant 0
    height_clip: int    # [0E34] 0x7FFF airborne, else (af2c - max(cellA,cellB))/0x80
    dest_seg: int       # [0E36] render destination segment


class RenderDecision(NamedTuple):
    skipped: bool                     # dirty-cache hit: nothing changed, no render
    params: Optional[RenderParams]    # the 8 params when not skipped
    sprite_idx: int                   # `si` -- ship sprite frame (0xFFFF when hidden)
    flip_page: bool                   # whether the ASM would xor [9334] after


def compute_render_params(
    rw: Callable[[int], int], ww: Callable[[int, int], None], offscreen: int,
) -> RenderDecision:
    """The pure `1010:0C98` orchestration: derive the road renderer's 8 params
    from sim state, maintain the `[0E1C..0E26]` dirty-cache through ``ww``, and
    report whether the frame should render at all. ``rw``/``ww`` are DGROUP
    word accessors (`NativeGameState.rw/ww` or a VM's, bound to ds)."""
    lat_lo, lat_hi = rw(LATERAL_LO), rw(LATERAL_HI)
    af1c, af2c = rw(AF1C), rw(AF2C)
    air = rw(AIR_COUNTER)
    lateral32 = ((lat_hi << 16) | lat_lo) & 0xFFFFFFFF

    fall = ship_fell_off(rw, lateral32, af1c, af2c)                    # bp-6 [asm 0CAE]

    if air != 0:                                                       # [asm 0CB7]
        si = air // 3
        if si >= 0x0E:
            si = 0xFFFF
    else:
        if rw(GAME_STATE) == 4:                                        # [asm 0CDB]
            wobble = 0
        else:                                                          # [asm 0CEB]
            wobble = rw((WOBBLE_TABLE + (((rw(TICK) // 2) % 4) << 1)) & 0xFFFF)
        pitch = 0 if fall else pitch_selector(rw(BOUNCE_VVEL), af2c)   # [asm 0D07]
        si = ((row_band(af1c) * 3 + pitch) * 3 + 0x0E + wobble) & 0xFFFF  # [asm 0D2B-0D48]

    cell_a = _cell_value(rw, lat_lo, lat_hi, (af1c - 0x380) & 0xFFFF, fall)  # [asm 0D4A]
    cell_b = _cell_value(rw, lat_lo, lat_hi, (af1c + 0x380) & 0xFFFF, fall)  # [asm 0D65]

    # Dirty-cache: compare against the LAST-RENDERED values (note: RAW af2c is
    # compared against the cached af2c_eff, exactly as the ASM does). [asm 0D80]
    page = rw(PAGE_FLAG)
    if (rw(CACHE_BASE) == lat_lo and rw(CACHE_BASE + 2) == lat_hi
            and rw(CACHE_BASE + 4) == af1c and rw(CACHE_BASE + 6) == af2c
            and rw(CACHE_BASE + 8) == si and rw(CACHE_BASE + 10) == page):
        return RenderDecision(skipped=True, params=None, sprite_idx=si, flip_page=False)

    af2c_eff = (af2c - 0x80) & 0xFFFF if offscreen else af2c           # [asm 0DDA]
    ww(CACHE_BASE, lat_lo); ww(CACHE_BASE + 2, lat_hi)                 # [asm 0DC6]
    ww(CACHE_BASE + 4, af1c); ww(CACHE_BASE + 6, af2c_eff)
    ww(CACHE_BASE + 8, si); ww(CACHE_BASE + 10, page)

    if rw(OFFSCREEN_MODE) != 0:                                        # [asm 0DFC]
        dest = rw(OFFSCREEN_SEG)
    else:
        dest = (0xA000 + ((0 if page else 1) << 9)) & 0xFFFF           # A000/A200 flip

    if air != 0:                                                       # [asm 0E26]
        height_clip = 0x7FFF
    else:
        cell_max = cell_a if cell_a >= cell_b else cell_b              # unsigned max
        height_clip = ((af2c - cell_max) & 0xFFFF) // 0x80

    sprite_lo = (rw(SPRITE_BANK_LO) + SPRITE_STRIDE * si) & 0xFFFF     # [asm 0E5C-0E6C]
    sprite_hi = rw(SPRITE_BANK_HI)                                     # (no carry: 16-bit add)

    # screen_row uses RAW af2c, NOT af2c_eff: the 0x80 subtraction (0DDA) feeds
    # only the dirty-cache slot; the ship-row projection (0E6D-0E85) reads the
    # unadjusted af2c. This only shows up in OFFSCREEN mode (af2c_eff = af2c-0x80
    # there), which is the path play_native uses -- with af2c_eff the ship drew
    # exactly one row (0x140) too low. VM-verified 2026-07-13 on
    # demo_skyroads_20260713_103107 (3 frames: af2c 12800/18100/13603 ->
    # screen_row 100/141/106 == af2c//0x80, was 99/140/105 with af2c_eff).
    screen_row = 0 if si == 0xFFFF else (af2c // 0x80)                 # [asm 0E6D-0E85]
    lateral_col = (lateral32 // 0x2000) & 0xFFFF                       # [asm 0E86-0E98] 5D8C
    row_base = (rw((ROW_BASE_TABLE + (row_band(af1c) << 1)) & 0xFFFF)
                + (af1c & 0xFFFF) // 0x80) & 0xFFFF                    # [asm 0E9C-0EBE]

    return RenderDecision(
        skipped=False,
        params=RenderParams(row_base, lateral_col, screen_row,
                            sprite_lo, sprite_hi, 0, height_clip, dest),
        sprite_idx=si, flip_page=True)
