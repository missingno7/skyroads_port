"""SkyRoads screen-present masked blit — `1010:41A0`.

The routine that puts a composited frame onto the screen: a general
masked/color-keyed blit that copies a BACKGROUND buffer (source A) verbatim in a
top and bottom band, and in the middle band composites a foreground buffer
(source B — e.g. the off-screen road buffer `34AE` fills) over the background
with a two-threshold color key. This is what actually flushes the road to VGA
each frame (found 2026-07-12 by tracing what writes `0xA000`; see run_status.md
-- it is NOT `34AE` "mode-1", which only fills the off-screen buffer).

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
    merge_target="skyroads.native.present (future)",
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
