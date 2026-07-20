"""Native HUD gauges — `1010:12F8` (the per-frame updater) + `1010:0F8C`
(the widget drawer), decoded live (2026-07-13, see run_status.md) and wired
onto the pure `stencil_blit` (`skyroads/handrecovered/blit.py`) primitive.

## `0F8C` — draw one widget cell (VM-VERIFIED 28/28 bytes exact)

A widget record is 4 bytes (`dest_off` word, `w` byte, `h` byte) + `w*h`
palette-relative stencil bytes, addressed by a DGROUP `(offset, segment)`
far pointer + a per-cell word from a 20/68-byte DGROUP cell table (loaded
from the `*_DISP.DAT`/`SPEED.DAT` files — see `skyroads/native/boot.py`).
`0F8C(widget_ptr, flag)`:

1. picks `(template_color, other_color)` from `flag` (offscreen mode, the
   only path this native renderer uses — the `[003C]==0` fast-VGA branch is
   dead code here): `flag!=0 -> (0x5E, 0x5F)`, `flag==0 -> (0x5C, 0x5D)`.
2. `stencil_blit`s the record's stencil bytes through those colours (`0`
   stays `0`).
3. paints the result onto VGA row-major at `dest_off + row*320 + col`,
   **skipping zero bytes** (transparent) — verified against a live capture:
   a real 7x4 widget draw matched the VM's VGA bytes 28/28 exactly with this
   rule (masked_blit's own `[9614]`/`[AF3A]` thresholds were both 0 in the
   capture, which naively reads as "no masking", but the empirical result is
   unambiguous: zero pixels are never written).

## `12F8` — the per-frame updater (three gauges, each identical in shape)

Each gauge: compute `new` from live sim state, clamp, compare to a cached
"last drawn" value; if `new != cache`, walk cells `[min(new,cache),
max(new,cache))` calling `0F8C` on each with `flag = (new > cache)` (a
SINGLE flag for the whole walk — increasing the value redraws the newly
covered cells "on", decreasing redraws the vacated cells "off"), then
`cache := new`. This is a delta/dirty-cell update, not a full redraw.

============  ======================  ==========  =========  ============
gauge         formula                  cache       cell tbl   widget base
============  ======================  ==========  =========  ============
speed         (ship_pos-[AF2E:30])     [41BE]      0x4572     [54A4:54A6]
              /0x141, clamp 0x22
oxygen        ([B13C]+0xBB7)/0xBB8,    [456C]      0x95F8     [5474:5476]
              clamp 10
fuel          ([5494]+0xBB7)/0xBB8,    [960C]      0x5480     [960E:9610]
              clamp 10
============  ======================  ==========  =========  ============

(`[B13C]`/`[5494]` -> oxygen/fuel confirmed by CELL TABLE content, not by
name: the `[B13C]` gauge's table is populated from `OXY_DISP.DAT`, the
`[5494]` gauge's from `FUL_DISP.DAT` — see `boot.py`'s `OXY_CELLS_OFF`/
`FUL_CELLS_OFF`.)

**Not ported**: the lamp-blink digit-pair readout (`game_state in (4,5)`,
helper `1282` + SFX id 3) — a rare one-shot display on fuel/oxygen timeout,
not the always-visible gauges this module covers.
"""
from __future__ import annotations

from skyroads.handrecovered.blit import stencil_blit

# -- per-gauge DGROUP offsets --------------------------------------------
SPEED_CACHE = 0x41BE
SPEED_WIDGET_OFF = 0x54A4
SPEED_WIDGET_SEG = 0x54A6
SPEED_CELL_TABLE = 0x4572
SPEED_CLAMP = 0x22
SPEED_ANCHOR = 0xAF2E          # dword: ship_pos anchor at level start
SPEED_DIVISOR = 0x141

OXYGEN_CACHE = 0x456C
OXYGEN_WIDGET_OFF = 0x5474
OXYGEN_WIDGET_SEG = 0x5476
OXYGEN_CELL_TABLE = 0x95F8
OXYGEN_TIMER = 0xB13C

FUEL_CACHE = 0x960C
FUEL_WIDGET_OFF = 0x960E
FUEL_WIDGET_SEG = 0x9610
FUEL_CELL_TABLE = 0x5480
FUEL_TIMER = 0x5494

BAR_ADD = 0x0BB7
BAR_DIV = 0x0BB8
BAR_CLAMP = 10

VGA_SEG = 0xA000
VGA_SCANLINE = 320


def hud_widget_draw(img, dg: int, widget_seg: int, widget_off: int, flag: int) -> None:
    """`1010:0F8C`: draw one widget record onto the live VGA plane."""
    template, other = (0x5E, 0x5F) if flag else (0x5C, 0x5D)
    dest_off = img.rw(widget_seg, widget_off)
    w = img.rb(widget_seg, (widget_off + 2) & 0xFFFF)
    h = img.rb(widget_seg, (widget_off + 3) & 0xFFFF)
    src = bytes(img.rb(widget_seg, (widget_off + 4 + i) & 0xFFFF) for i in range(w * h))
    out = stencil_blit(src, template, other)
    for row in range(h):
        base = (dest_off + row * VGA_SCANLINE) & 0xFFFF
        for col in range(w):
            p = out[row * w + col]
            if p:
                img.wb(VGA_SEG, (base + col) & 0xFFFF, p)


def _update_gauge(img, dg: int, new: int, cache_off: int,
                  widget_off_off: int, widget_seg_off: int, cell_table: int) -> None:
    """`1010:12F8`'s per-gauge delta walk: redraw only the cells between the
    cached and new value, then update the cache. Increasing the value redraws
    the newly-covered cells "on" (``flag=1``); DECREASING redraws the vacated
    cells "off" (``flag=0``) -- the unfill path. Because it is a pure delta,
    the caller must NOT repaint the dashboard over the gauge region between
    frames, or the standing fill is lost (see :func:`update_hud`)."""
    cache = img.rw(dg, cache_off)
    if new == cache:
        return
    lo, hi = (new, cache) if new < cache else (cache, new)
    flag = 1 if new > cache else 0
    widget_seg = img.rw(dg, widget_seg_off)
    widget_base = img.rw(dg, widget_off_off)
    for cell in range(lo, hi):
        cell_word = img.rw(dg, (cell_table + 2 * cell) & 0xFFFF)
        widget_off = (widget_base + cell_word) & 0xFFFF
        hud_widget_draw(img, dg, widget_seg, widget_off, flag)
    img.ww(dg, cache_off, new & 0xFFFF)


def update_hud(img, dg: int, ship_pos: int) -> None:
    """The always-visible HUD gauges: speed dial, oxygen bar, fuel bar. A pure
    delta updater (`1010:12F8`): each frame it redraws ONLY the gauge cells
    that changed since the last call -- filling on increase, unfilling on
    decrease. Call once per rendered frame.

    Because it is a delta, the dashboard must NOT be repainted over the gauge
    region (rows 138..199) between frames -- the native renderer paints the full
    dashboard once and thereafter only re-overlays the road-overlapping bezel
    strip (``paint_dashboard(..., byte_count=DASHBOARD_BEZEL_OVERLAP)``), so the
    gauges the VM maintains incrementally stay standing here too.
    """
    anchor = (img.rw(dg, SPEED_ANCHOR) | (img.rw(dg, (SPEED_ANCHOR + 2) & 0xFFFF) << 16))
    speed_new = min((ship_pos - anchor) & 0xFFFFFFFF, 0xFFFFFFFF) // SPEED_DIVISOR
    speed_new = min(speed_new, SPEED_CLAMP)
    _update_gauge(img, dg, speed_new, SPEED_CACHE, SPEED_WIDGET_OFF, SPEED_WIDGET_SEG,
                 SPEED_CELL_TABLE)

    oxy_new = min((img.rw(dg, OXYGEN_TIMER) + BAR_ADD) // BAR_DIV, BAR_CLAMP)
    _update_gauge(img, dg, oxy_new, OXYGEN_CACHE, OXYGEN_WIDGET_OFF, OXYGEN_WIDGET_SEG,
                 OXYGEN_CELL_TABLE)

    fuel_new = min((img.rw(dg, FUEL_TIMER) + BAR_ADD) // BAR_DIV, BAR_CLAMP)
    _update_gauge(img, dg, fuel_new, FUEL_CACHE, FUEL_WIDGET_OFF, FUEL_WIDGET_SEG,
                 FUEL_CELL_TABLE)


# ============================================================================
# Level PROGRESS BAR -- the magenta strip filling left->right as the ship
# travels the level. VM routine `1010:159C` (compute target column) + the
# per-column fill `1010:1218`->putpixel `1010:11D3`. Recovered + verified
# against replay_skyroads_L1FULL_20260713_212417 (see run_status.md 2026-07-13):
# the target-column formula matched the VM 321/321 sub-steps; one column draw
# is the 3px strip captured below.
#
#   target_col = clamp( (prog32 - 0x30000) * 30 // ((L << 16) - 0x30000), 29 )
#     prog32 = ds:[9618:961A]  -- the ship's 32-bit forward position (signed)
#     L      = ds:[41C0]       -- the level length in segments (55 for L1)
#     0x30000 = the 3-segment start offset;  30 (0x1E) = bar width;  clamp 29.
#   each new column (from the cache [455C] up to target) is filled at screen
#   column (col + 0x2A) by painting colour 0x60 DOWN from row 0x8F while the
#   pixel matches the bar's "empty" colour (the reference read at the top).
PROGRESS_SRC = 0x9618       # ship forward position, 32-bit
PROGRESS_LEN = 0x41C0       # level length (segments)
PROGRESS_CACHE = 0x455C     # last drawn column (0..29); a per-frame delta cache
PROGRESS_START = 0x30000    # 3-segment start offset (subtracted from both terms)
PROGRESS_WIDTH = 30         # 0x1E bar columns
PROGRESS_MAXCOL = 29        # 0x1D clamp
PROGRESS_COL0 = 0x2A        # 42 -- screen column of bar column 0
PROGRESS_ROW = 0x8F         # 143 -- top row of the vertical fill
PROGRESS_FILL = 0x60        # the magenta fill colour
_VGA = 0xA0000


def progress_target_col(prog32: int, level_len: int) -> int:
    """The bar's target column (0..29) for a 32-bit forward position and level
    length -- the recovered `1010:159C` formula, VM-verified 321/321."""
    if prog32 & 0x80000000:
        prog32 -= 1 << 32
    den = (level_len << 16) - PROGRESS_START
    if den == 0:
        return 0
    v = ((prog32 - PROGRESS_START) * PROGRESS_WIDTH) // den
    return max(0, min(PROGRESS_MAXCOL, v))


def update_progress_bar(img, dg: int) -> None:
    """Fill the level-progress bar up to the current position. Delta, like the
    gauges: draws only the columns newly covered since the last call (cache at
    ds:[455C]). Call once per rendered frame. Faithful to `1218`: each new
    column paints 0x60 down from row 0x8F while the pixel matches the empty-bar
    reference colour, so the fill height follows the bar art itself."""
    prog32 = img.rw(dg, PROGRESS_SRC) | (img.rw(dg, (PROGRESS_SRC + 2) & 0xFFFF) << 16)
    target = progress_target_col(prog32, img.rw(dg, PROGRESS_LEN))
    cache = img.rw(dg, PROGRESS_CACHE)
    plane = img.data
    for col in range(cache, target):
        x = PROGRESS_COL0 + col
        ref = plane[_VGA + PROGRESS_ROW * 320 + x]
        # `1218`: scan UP from row 0x8F while the pixel matches the empty-bar
        # reference colour to find the top of the run, then fill DOWN the whole
        # run with 0x60 -- so the fill follows the bar art's height at each col.
        row = PROGRESS_ROW
        while row > 0 and plane[_VGA + row * 320 + x] == ref:
            row -= 1
        row += 1
        while row < 200 and plane[_VGA + row * 320 + x] == ref:
            plane[_VGA + row * 320 + x] = PROGRESS_FILL
            row += 1
    if target != cache:
        img.ww(dg, PROGRESS_CACHE, target)


# ============================================================================
# GRAV-O-METER numeric readout -- `1010:1114` (draw_number) driving `1010:1073`
# (draw_glyph_at), called from `1010:2BC3`. A 4-digit LCD showing the level's
# gravity as `(gravity - 3) * 100`. Recovered + verified byte-exact vs the VM
# over replay_cold_20260713_213510 (see run_status.md 2026-07-13):
#
#   2BC5: ax = ds:[4562] (gravity);  ax += -3;  ax *= 100   -> value
#         call 1114(col=0x60, row=0x9C, value, width=4)
#   1114 (draw_number): for si in 0..width, breaking once the remaining value is
#         0 and si!=0 (leading-zero suppression):
#           digit = (value // pow10[si]) % 10   (pow10 = ds:[0x824+si*2] = 1,10,100,..)
#           x = col + (width-si-1)*5;  glyph = font(0x16C + digit*20)  (4 wide x 5 tall)
#           value -= digit * pow10[si]
#   1073 (draw_glyph_at): blit the 4x5 glyph to VGA row-major at (x,row), writing
#         EVERY pixel (no zero-skip): font byte b -> colour `0` if b==0 else 0x60+b
#         (the LCD's dark background + two segment shades 0x61/0x62). VM-verified:
#         the '5' font [.,2,2,1]/[1,2,2,.] wrote 0x00/0x62/0x62/0x61 exactly.
#
# gravity is fixed per level, so the value is constant -- a full redraw each call
# (cheap, <=60 px) matching the VM's per-HUD-frame redraw.
GRAV_GRAVITY = 0x4562        # per-level gravity (the offset level_load writes)
GRAV_FONT = 0x16C            # digit font base: 10 glyphs x 20 bytes (4 wide x 5 tall)
GRAV_GLYPH_W = 4
GRAV_GLYPH_H = 5
GRAV_COL = 96                # 0x60 -- col arg
GRAV_ROW = 156               # 0x9C -- row arg
GRAV_WIDTH = 4               # digit field width
GRAV_PITCH = 5               # px between digit cells
GRAV_COLOR_BASE = 0x60       # font byte b(!=0) -> colour 0x60+b; b==0 -> 0


def grav_value(gravity: int) -> int:
    """`1010:2BC5`: the LCD value = `(gravity - 3) * 100` (16-bit signed mul)."""
    v = (gravity - 3) & 0xFFFF
    if v & 0x8000:
        v -= 0x10000
    return v * 100


def draw_grav_meter(img, dg: int) -> None:
    """Draw the grav-o-meter LCD number (`1010:1114` over `1010:1073`), VM-verified
    byte-exact. Static per level (gravity is fixed) -- safe to call each rendered
    frame like the gauges; it fully redraws its own 4x5-per-digit region."""
    value = grav_value(img.rw(dg, GRAV_GRAVITY))
    v = value
    plane = img.data
    for si in range(GRAV_WIDTH):
        if v == 0 and si != 0:                       # `112D/1136`: leading-zero suppress
            break
        p = 10 ** si                                 # pow10[si] = ds:[0x824+si*2]
        digit = (v // p) % 10                         # `1147/1150`
        x0 = GRAV_COL + (GRAV_WIDTH - si - 1) * GRAV_PITCH   # `1166..1173`
        src = (GRAV_FONT + digit * (GRAV_GLYPH_W * GRAV_GLYPH_H)) & 0xFFFF
        for row in range(GRAV_GLYPH_H):
            base = _VGA + (GRAV_ROW + row) * 320 + x0
            for col in range(GRAV_GLYPH_W):
                b = img.rb(dg, (src + row * GRAV_GLYPH_W + col) & 0xFFFF)
                plane[base + col] = 0 if b == 0 else (GRAV_COLOR_BASE + b) & 0xFF
        v -= digit * p                                # `117D..1187`
