"""Native HUD gauges — `1010:12F8` (the per-frame updater) + `1010:0F8C`
(the widget drawer), decoded live (2026-07-13, see run_status.md) and wired
onto the pure `stencil_blit` (`skyroads/recovered/blit.py`) primitive.

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

from skyroads.recovered.blit import stencil_blit

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
    cached and new value, then update the cache."""
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
    """The always-visible HUD gauges: speed dial, oxygen bar, fuel bar.
    Call once per rendered frame, after the road/background render (so the
    dashboard's bezel doesn't need re-painting first) and after
    `paint_dashboard` (the gauges sit ON the dashboard art)."""
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
