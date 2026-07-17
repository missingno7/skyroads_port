"""SkyRoads screen-present masked blits — `1010:41A0` (`masked_blit`),
`1010:4201` (`present_rect`), and `1010:3A22` (`sprite_blit`).

General masked/color-keyed blits that put composited pixels onto the screen.
`masked_blit` copies a BACKGROUND buffer (source A) verbatim in a top and bottom
band, and in the middle band composites a foreground buffer (source B) over the
background with a two-threshold color key.

ROLE CORRECTION (2026-07-12, see run_status.md): `masked_blit`/`present_rect`
are the **HUD/dashboard** present path, NOT the gameplay road. A definitive
`write_watchers` scan of VGA (`0xA000`) over steady-gameplay frames shows the
road and ship draw DIRECTLY to VGA via `road_column_strip` (`1010:38BF`,
`recovered/road_column.py`) and `sprite_blit` (`1010:3A22`, below);
`masked_blit` (`1010:41f1`) contributes only ~2.4 KB/100 frames — the small
dashboard widgets, not the full-screen road. The functions here remain
byte-exact against their real ASM calls (19/20 and 12/12); only the earlier
"this flushes the road" framing was wrong.

Decoded from the disassembly (`1010:41A0-4200`):

    es = ds:[961C]                 ; dest segment (VGA 0xA000 for the screen flush)
    ds_a = ds:[4512]               ; source A (background)
    ds_b = ds:[4514]               ; source B (foreground / road)
    di = dest_off (param bp+4);  si = di;  ds = ds_a
    rep movsb (cx = top_count, bp+8)          ; TOP: dest[di..] = A[di..], verbatim
    si = srcB_off (bp+6);  ds = ds_b
    cx = ds:[9612] - top_count - bottom_count  ; MIDDLE pixel count
    for each middle pixel:                     ; 41E7-41F1
        al = B[si]; si += 1
        if al <  thresh_lo (ds:[9614]):  di += 1          ; TRANSPARENT: leave dest as-is
        elif al < thresh_hi (ds:[AF3A]): dest[di]=A[di]; di+=1   ; substitute background
        else:                            dest[di]=al;   di+=1   ; foreground (road) pixel
    ds = ds_a;  si = di
    rep movsb (cx = bottom_count, bp+10)       ; BOTTOM: dest[di..] = A[di..], verbatim

Note source A is read at the SAME offset as the destination (`di`) — background
aligned to screen position — while source B is read sequentially from its own
`srcB_off`. Both threshold compares are unsigned `jb` (`al < t`), so a threshold
of 0 disables that branch (nothing is below 0 unsigned).
"""
from __future__ import annotations

from typing import Callable

from skyroads.islands import oracle_link


@oracle_link(
    boundary="1010:41A0",
    contract="masked_blit(rb, wb, dest_seg, srcA_seg, srcB_seg, dest_off, "
             "srcB_off, top_count, bottom_count, total, thresh_lo, thresh_hi): "
             "the screen-present masked blit. TOP top_count bytes: dest[off..] = "
             "A[off..]. MIDDLE (total-top-bottom) bytes, per source-B pixel p at "
             "srcB_off++ (dest cursor di from dest_off+top_count): p<thresh_lo -> "
             "leave dest (transparent); p<thresh_hi -> dest[di]=A[di]; else "
             "dest[di]=p. BOTTOM bottom_count bytes: dest[di..] = A[di..]. Source "
             "A is read at the dest offset; source B sequentially.",
    status="ASM_MATCHED",  # full-memory-diff verified over real 1010:41A0 calls
    merge_target="skyroads.recovered_native.present (future)",
)
def masked_blit(
    rb: Callable[[int, int], int], wb: Callable[[int, int, int], None],
    dest_seg: int, srcA_seg: int, srcB_seg: int,
    dest_off: int, srcB_off: int,
    top_count: int, bottom_count: int, total: int,
    thresh_lo: int, thresh_hi: int,
) -> None:
    """Reproduce `1010:41A0`'s masked screen blit. ``rb(seg, off)`` reads a
    byte, ``wb(seg, off, v)`` writes one. Offsets wrap at 0xFFFF within a
    segment (real-mode string ops)."""
    di = dest_off & 0xFFFF

    # TOP band: verbatim copy from source A at the destination offset.
    for _ in range(top_count):
        wb(dest_seg, di, rb(srcA_seg, di))
        di = (di + 1) & 0xFFFF

    # MIDDLE band: color-keyed composite of source B over source A.
    si = srcB_off & 0xFFFF
    middle = (total - top_count - bottom_count) & 0xFFFF
    for _ in range(middle):
        p = rb(srcB_seg, si)
        si = (si + 1) & 0xFFFF
        if p < thresh_lo:
            pass  # transparent: leave the destination pixel untouched
        elif p < thresh_hi:
            wb(dest_seg, di, rb(srcA_seg, di))  # substitute background
        else:
            wb(dest_seg, di, p)                 # foreground (road) pixel
        di = (di + 1) & 0xFFFF

    # BOTTOM band: verbatim copy from source A at the (advanced) offset.
    for _ in range(bottom_count):
        wb(dest_seg, di, rb(srcA_seg, di))
        di = (di + 1) & 0xFFFF


#: VGA mode-13h scanline stride (bytes per row) — the dest cursor advances by
#: this per presented row (`1010:426A`).
VGA_SCANLINE = 0x140


@oracle_link(
    boundary="1010:4201",
    contract="present_rect(rb, wb, dest_seg, srcA_seg, srcB_seg, dest_off, rows, "
             "width, thresh_lo, thresh_hi): present a rows x width rectangle from "
             "source B onto the destination, one scanline per row via masked_blit "
             "(top=bottom=0, total=width). Per row: dest cursor advances by "
             "VGA_SCANLINE (0x140), source-B cursor advances by width. This is "
             "34AE's off-screen road buffer -> VGA scanline flush (the row-loop "
             "path of 1010:4201; the [003C]==0 fast-VGA branch to 1010:3D18 is a "
             "separate path not modelled here).",
    status="ASM_MATCHED",  # full-memory-diff verified over real 1010:4201 row-loop calls
    merge_target="skyroads.recovered_native.present (future)",
)
def present_rect(
    rb: Callable[[int, int], int], wb: Callable[[int, int, int], None],
    dest_seg: int, srcA_seg: int, srcB_seg: int,
    dest_off: int, rows: int, width: int,
    thresh_lo: int, thresh_hi: int,
) -> None:
    """Reproduce `1010:4201`'s scanline present loop: blit ``rows`` scanlines of
    ``width`` color-keyed pixels from ``srcB_seg`` onto ``dest_seg`` starting at
    ``dest_off``, calling :func:`masked_blit` per row. The destination cursor
    steps by :data:`VGA_SCANLINE` each row; the source-B cursor steps by
    ``width``."""
    dst = dest_off & 0xFFFF
    src = 0
    for _ in range(rows):
        masked_blit(rb, wb, dest_seg, srcA_seg, srcB_seg, dst, src,
                    top_count=0, bottom_count=0, total=width,
                    thresh_lo=thresh_lo, thresh_hi=thresh_hi)
        dst = (dst + VGA_SCANLINE) & 0xFFFF
        src = (src + width) & 0xFFFF


#: sprite_blit fixed geometry (baked into the routine, not passed in):
#: a 29-column-wide masked flip; the source/dest advance 320 (0x140) bytes per
#: row (0x1D consumed in the inner loop + 0x123 added after), the mask is packed
#: 29 bytes/row with no padding.
SPRITE_BLIT_WIDTH = 0x1D
_SPRITE_BLIT_ROW_SKIP = 0x0123  # 0x1D + 0x123 == 0x140 (one screen row)


@oracle_link(
    boundary="1010:3A22",
    contract="sprite_blit(rb, wb, dest_seg, src_seg, mask_seg, src_off, "
             "mask_off, rows): a 29-wide masked flip of a sprite/overlay onto "
             "the visible buffer. For each of `rows` rows: the dest cursor di "
             "starts at the CURRENT source offset si (dest and source share "
             "row*0x140+col, different segments); for the 29 columns copy "
             "src[si] -> dest[di] ONLY where mask[bx]==2 (opaque), advancing "
             "si/di/bx by 1 each; after the row si += 0x123 (so si advances a "
             "full 0x140 screen row) while the packed mask bx just continues "
             "(29 bytes/row, no padding). This is the gameplay ship/object "
             "compositor that draws DIRECTLY to VGA (see run_status.md).",
    status="ASM_MATCHED",  # full-memory-diff verified over real 1010:3A22 calls
    merge_target="skyroads.recovered_native.present (future)",
)
def sprite_blit(
    rb: Callable[[int, int], int], wb: Callable[[int, int, int], None],
    dest_seg: int, src_seg: int, mask_seg: int,
    src_off: int, mask_off: int, rows: int,
) -> None:
    """Reproduce `1010:3A22`'s masked sprite flip. Copies from ``src_seg`` to
    ``dest_seg`` at the SAME offset (dest cursor resets to the source cursor at
    the top of every row), gated by a packed ``mask_seg`` table where a byte of
    ``2`` means opaque. Width is a fixed 29 columns; ``rows`` rows are drawn."""
    si = src_off & 0xFFFF
    bx = mask_off & 0xFFFF
    for _ in range(rows):
        di = si
        for _ in range(SPRITE_BLIT_WIDTH):
            if rb(mask_seg, bx) == 0x02:
                wb(dest_seg, di, rb(src_seg, si))
            si = (si + 1) & 0xFFFF
            di = (di + 1) & 0xFFFF
            bx = (bx + 1) & 0xFFFF
        si = (si + _SPRITE_BLIT_ROW_SKIP) & 0xFFFF
