"""SkyRoads screen-present masked blits — `1010:41A0` (`masked_blit`),
`1010:4201` (`present_rect`), and `1010:3A22` (`sprite_blit`).

These general masked/color-keyed blits serve the HUD/dashboard present path.
The road and ship use the separate ``road_column_strip`` and ``sprite_blit``
paths. ``masked_blit`` copies a background buffer verbatim in top and bottom
bands, and composites a foreground buffer over it in the middle band with a
two-threshold color key.

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

from typing import Callable, NamedTuple



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


def sprite_blit(
    rb: Callable[[int, int], int], wb: Callable[[int, int, int], None],
    dest_seg: int, src_seg: int, mask_seg: int,
    src_off: int, mask_off: int, rows: int,
) -> None:
    """Reproduce `1010:3A22`'s masked sprite flip. Copies from ``src_seg`` to
    ``dest_seg`` at the SAME offset (dest cursor resets to the source cursor at
    the top of every row), gated by a packed ``mask_seg`` table where a byte of
    ``2`` means opaque. Width is a fixed 29 columns; ``rows`` rows are drawn."""
    sprite_blit_detail(rb, wb, dest_seg, src_seg, mask_seg, src_off, mask_off, rows)


class SpriteBlitResult(NamedTuple):
    """Where the three cursors ended up, and what the copy left behind.

    All of it is state the pure blit discards and the original does not. ``si``,
    ``di`` and ``bx`` are live at ``ret`` -- 3A22 pushes nothing and its caller
    reloads them, so they are outputs, not scratch.

    ``last_value`` is the last byte an OPAQUE column copied, or None if the mask
    made every column transparent. It matters because ``mov al,[si]`` at 3A2D is
    the only thing that ever writes AL: a fully transparent sprite returns the
    caller's AX untouched, and taking the last source byte instead would be
    wrong for every mask that ends in transparent columns.

    ``hits`` is the opaque-column count, and it is the only variable in the
    cost: 3A2D/3A30 are the two instructions the mask test skips.
    """

    si: int
    di: int
    bx: int
    hits: int
    last_value: "int | None"


def sprite_blit_detail(
    rb: Callable[[int, int], int], wb: Callable[[int, int, int], None],
    dest_seg: int, src_seg: int, mask_seg: int,
    src_off: int, mask_off: int, rows: int,
) -> SpriteBlitResult:
    """:func:`sprite_blit` with the exit cursors and the opaque count reported.

    ``rb``/``wb`` are called exactly where the ASM reads and writes, and in its
    order, so an adapter reproducing the memory trace can pass them straight
    through: the mask read, the source read and the destination write of one
    column all happen before the next column's mask read.
    """
    si = src_off & 0xFFFF
    bx = mask_off & 0xFFFF
    di = si
    hits = 0
    last_value = None
    for _ in range(rows):
        di = si                                       # 3A22 mov di,si
        for _ in range(SPRITE_BLIT_WIDTH):
            if rb(mask_seg, bx) == 0x02:              # 3A27 cmp byte ss:[bx],2
                last_value = rb(src_seg, si)          # 3A2D mov al,[si]
                wb(dest_seg, di, last_value)          # 3A30 mov es:[di],al
                hits += 1
            si = (si + 1) & 0xFFFF
            di = (di + 1) & 0xFFFF
            bx = (bx + 1) & 0xFFFF
        si = (si + _SPRITE_BLIT_ROW_SKIP) & 0xFFFF
    return SpriteBlitResult(si, di, bx, hits, last_value)
