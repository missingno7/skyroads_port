"""The complete native gameplay FRAME — every stage pure recovered Python.

Composes the proven pieces in `2D1F`'s exact order (call-boundary capture of
demo_e2e frame 571, 2026-07-13 — see run_status.md):

    compute_render_params (0C98, 40/40)        -- sim state -> 8 params + skip
      -> params stored to [0E28..0E36]         -- 2D1F prologue
      -> run_34ae(ax=0) + 39D4                 -- COMPOSITE: bg->offscreen
         (rebuild copy / delta gating / variant-A columns, then ship blit)
      -> render_tile_passes (+ ship-row tile)  -- rasterize road + ship tile
      -> run_34ae(ax=1) + 39D4                 -- PRESENT: offscreen->VGA A000
         (variant-B columns blit the road band to the screen + ship to VGA)
      -> post_frame                            -- rotation + mask copy

[asm 1010:2D1F frame driver; 34AE two-mode render; 39D4 sprite dispatcher;
2E43-2E6B post-steps]
"""
from __future__ import annotations

from typing import Optional

from skyroads.native.image import NativeGameImage
from skyroads.native.render_frame import run_34ae
from skyroads.native.render_params import RenderDecision, compute_render_params
from skyroads.native.tile_dispatch import render_tile_passes
from skyroads.handrecovered.present import sprite_blit
from skyroads.handrecovered.tile_raster import tile_rasterize

BACKGROUND_SRC = 0x5170     # [5170] = the 320x138 background bank segment
BACKGROUND_LEN = 44160      # 320 * 138


def ship_sprites(img: NativeGameImage, dg: int) -> None:
    """`1010:39D4` — the ship sprite erase/redraw dispatcher: four
    `sprite_blit` flips from `[0E66]` into `[0E68]`. The first pair (previous
    frame's offsets `[0E6E]`/`[0E72]`, the COPIED masks at `0x1243`/`0x14FB`)
    erases the old ship; the second pair (current `[0E6C]`/`[0E70]`, live
    masks `0x0E86`/`0x113E`) draws the new — only when compositing straight
    onto the VGA plane (`[0E68] == 0xA000`)."""
    src = img.rw(dg, 0x0E66)
    dest = img.rw(dg, 0x0E68)
    sprite_blit(img.rb, img.wb, dest, src, dg, img.rw(dg, 0x0E6E), 0x1243, 0x18)
    sprite_blit(img.rb, img.wb, dest, src, dg, img.rw(dg, 0x0E72), 0x14FB, 0x09)
    if dest == 0xA000:
        sprite_blit(img.rb, img.wb, dest, src, dg, img.rw(dg, 0x0E6C), 0x0E86, 0x18)
        sprite_blit(img.rb, img.wb, dest, src, dg, img.rw(dg, 0x0E70), 0x113E, 0x09)


def post_frame(img: NativeGameImage, dg: int) -> None:
    """`2D1F`'s post-steps (`2E49-2E64`): rotate the frame position + ship
    offsets into their previous-frame slots, and copy the live occlusion mask
    (`0x0E86`, 0x3BC bytes) to the previous-frame mask at `0x1243`."""
    img.ww(dg, 0x0E6A, img.rw(dg, 0x0E2A))
    img.ww(dg, 0x0E6E, img.rw(dg, 0x0E6C))
    img.ww(dg, 0x0E72, img.rw(dg, 0x0E70))
    for i in range(0x3BC):                       # rep movsw cx=0x1DE
        img.wb(dg, (0x1243 + i) & 0xFFFF, img.rb(dg, (0x0E86 + i) & 0xFFFF))


def compose_frame(img: NativeGameImage, dg: int, params, *, rebuild: bool = False) -> None:
    """Render one frame from explicit params (already-verified 8-tuple in
    `[0E28..0E36]` order) — the capture-verified pipeline.

    ``rebuild=True`` forces `[0E32]` != 0, which routes BOTH `34AE` calls
    through their full-copy path (bg -> offscreen, then offscreen -> VGA)."""
    p = list(params)
    if rebuild:
        p[5] = 1
    for k, v in enumerate(p):
        img.ww(dg, 0x0E28 + 2 * k, v)
    run_34ae(img, dg, 0, ship_sprites)           # composite: bg -> offscreen

    def _ship_row(_ctx) -> None:
        tile_rasterize(img.rb, img.wb,
                       lambda o: img.rw(dg, o),
                       lambda o, v: img.ww(dg, o, v), dg)

    render_tile_passes(img, dg, on_ship_row=_ship_row)
    run_34ae(img, dg, 1, ship_sprites)           # present: offscreen -> VGA
    post_frame(img, dg)


def render_native_frame(img: NativeGameImage, dg: int, *, offscreen: int = 1,
                        rebuild: bool = False) -> Optional[RenderDecision]:
    """The full per-frame render: derive the params from sim state
    (`compute_render_params`, 40/40 vs VM) and compose. Returns the decision
    (``None`` params + ``skipped`` when the dirty-cache says nothing changed;
    pass ``rebuild=True`` for the first frame to force the background copy)."""
    dec = compute_render_params(lambda o: img.rw(dg, o),
                                lambda o, v: img.ww(dg, o, v), offscreen)
    if dec.skipped and not rebuild:
        return dec
    params = dec.params
    if params is None:      # skipped but rebuild forced: recompute unconditionally
        img.ww(dg, 0x0E1C, 0xFFFF)               # poison the cache and retry
        dec = compute_render_params(lambda o: img.rw(dg, o),
                                    lambda o, v: img.ww(dg, o, v), offscreen)
        params = dec.params
    compose_frame(img, dg, params, rebuild=rebuild)
    return dec
