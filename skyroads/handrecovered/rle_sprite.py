"""The RLE sprite rasterizer pair — `1010:3153` (forward) / `1010:3190`
(backward) — as pure functions.

The dominant in-game render cost (5,884 calls / ~41K inner iterations in the
e2e demo): each call paints one vertical strip of horizontal spans from an RLE
control stream living in the current DISPLAY-LIST segment. Stream layout:

    byte  index      -> fill colour = DGROUP[0x352 + index*4]   (fwd)
                                      DGROUP[0x353 + index*4]   (bwd)
    word  dest_off   -> starting destination offset (bwd: minus one)
    then per row:  byte ctrl  (0xFF terminates)
                   byte runlen
                   byte (skipped)
      forward:  di -= ctrl, paint `runlen` bytes of fill FORWARD from di
      backward: di += ctrl, paint `runlen` bytes of fill ending AT di
      then di = row_anchor + 0x140 (next scanline)

The pair renders mirror halves of the road tiles (left/right edges). Promoted
from the ASM-verified `skyroads/hooks.py` bodies (strict differential verifier,
thousands of calls) exactly like `sprite_blit` was — the register/flag exit
bookkeeping stays in the hooks; only the pixel/stream semantics live here.

[asm 1010:3153-318F fwd; 1010:3190-31D0 bwd]
"""
from __future__ import annotations

from typing import Callable

from skyroads.islands import oracle_link

FILL_TABLE_FWD = 0x0352   # DGROUP fill-colour table, indexed by stream index*4
FILL_TABLE_BWD = 0x0353   # the odd-parity companion the backward twin reads
ROW_STRIDE = 0x0140       # 320 bytes -> one scanline down
TERMINATOR = 0xFF


@oracle_link(
    boundary="1010:3153",
    contract="rle_sprite_forward(rb, wb, dgroup_seg, list_seg, dest_seg, si): "
             "decode one RLE strip: index byte -> fill colour "
             "DGROUP[0x352+index*4]; word dest offset; then per control byte "
             "(0xFF ends) di -= ctrl, paint runlen bytes of fill forward from "
             "es:di (one stream byte skipped after runlen), next row at "
             "anchor+0x140. Returns si past the terminator.",
    status="ASM_MATCHED",  # the hook body this was promoted from ran under the
    # strict differential verifier for every rasterizer call in the suites
    merge_target="skyroads.handrecovered_native.tile_dispatch",
)
def rle_sprite_forward(
    rb: Callable[[int, int], int], wb: Callable[[int, int, int], None],
    dgroup_seg: int, list_seg: int, dest_seg: int, si: int,
) -> int:
    """Paint one forward RLE strip; returns the stream cursor past the 0xFF."""
    index = rb(list_seg, si); si = (si + 1) & 0xFFFF
    fill = rb(dgroup_seg, (FILL_TABLE_FWD + ((index << 2) & 0xFFFF)) & 0xFFFF)
    di = rb(list_seg, si) | (rb(list_seg, (si + 1) & 0xFFFF) << 8)
    si = (si + 2) & 0xFFFF
    while True:
        ctrl = rb(list_seg, si); si = (si + 1) & 0xFFFF
        if ctrl == TERMINATOR:
            return si
        anchor = di
        di = (di - ctrl) & 0xFFFF
        runlen = rb(list_seg, si); si = (si + 2) & 0xFFFF  # runlen byte + 1 skipped
        for _ in range(runlen):
            wb(dest_seg, di, fill); di = (di + 1) & 0xFFFF
        di = (anchor + ROW_STRIDE) & 0xFFFF


@oracle_link(
    boundary="1010:3190",
    contract="rle_sprite_backward(rb, wb, dgroup_seg, list_seg, dest_seg, si): "
             "the mirror twin: fill colour DGROUP[0x353+index*4]; dest offset "
             "word minus one; per control byte di += ctrl and paint runlen "
             "bytes ENDING at es:di (written downward in the ASM -- same "
             "bytes), next row at anchor+0x140. Returns si past the 0xFF.",
    status="ASM_MATCHED",  # promoted from the differential-verified hook body
    merge_target="skyroads.handrecovered_native.tile_dispatch",
)
def rle_sprite_backward(
    rb: Callable[[int, int], int], wb: Callable[[int, int, int], None],
    dgroup_seg: int, list_seg: int, dest_seg: int, si: int,
) -> int:
    """Paint one backward (mirrored) RLE strip; returns the cursor past 0xFF."""
    index = rb(list_seg, si); si = (si + 1) & 0xFFFF
    fill = rb(dgroup_seg, (FILL_TABLE_BWD + ((index << 2) & 0xFFFF)) & 0xFFFF)
    di = ((rb(list_seg, si) | (rb(list_seg, (si + 1) & 0xFFFF) << 8)) - 1) & 0xFFFF
    si = (si + 2) & 0xFFFF
    while True:
        ctrl = rb(list_seg, si); si = (si + 1) & 0xFFFF
        if ctrl == TERMINATOR:
            return si
        anchor = di
        di = (di + ctrl) & 0xFFFF
        runlen = rb(list_seg, si); si = (si + 2) & 0xFFFF
        p = di
        for _ in range(runlen):
            wb(dest_seg, p, fill); p = (p - 1) & 0xFFFF
        di = (anchor + ROW_STRIDE) & 0xFFFF
