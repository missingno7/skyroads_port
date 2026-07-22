"""The RLE sprite rasterizer pair — `1010:3153` (forward) / `1010:3190`
(backward) — as pure functions.

The dominant in-game render cost (5,884 calls / ~41K inner iterations in the
e2e replay): each call paints one vertical strip of horizontal spans from an RLE
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

from dataclasses import dataclass
from typing import Callable


FILL_TABLE_FWD = 0x0352   # DGROUP fill-colour table, indexed by stream index*4
FILL_TABLE_BWD = 0x0353   # the odd-parity companion the backward twin reads
ROW_STRIDE = 0x0140       # 320 bytes -> one scanline down
TERMINATOR = 0xFF

# Recovered initialized-DGROUP constants used by TREKDAT selectors 0..73.
# Keeping these data-only tables in the recovered layer lets an EXE-free
# native carrier construct the same faceted road palette without inventing a
# live guest data segment.  Values beyond the selector domain are zero.
RECOVERED_FILL_FORWARD = (
    tuple(range(68)) + (71, 70, 69, 68, 69, 70) + (0,) * (256 - 74)
)
RECOVERED_FILL_BACKWARD = (
    tuple(range(31)) + tuple(range(46, 61)) + (0,) * 15
    + (61, 62, 64, 0, 65, 66, 67, 70, 69, 68, 69, 70, 71)
    + (0,) * (256 - 74)
)


@dataclass(frozen=True)
class RleSpan:
    """One exact horizontal span emitted by an original display-list strip.

    ``offset`` is relative to the destination segment.  Keeping the recovered
    16-bit coordinate here (instead of prematurely calling it x/y) makes the
    decoder useful both for the original rasterizer and for presentation
    instrumentation whose destination segment may have a scanline bias.
    """

    offset: int
    length: int


@dataclass(frozen=True)
class DecodedRleStrip:
    """Read-only description of one 3153/3190 display-list primitive."""

    next_si: int
    palette_selector: int
    palette_index: int
    destination_offset: int
    spans: tuple[RleSpan, ...]
    backward: bool


def decode_rle_strip(
    rb: Callable[[int, int], int], dgroup_seg: int, list_seg: int, si: int,
    *, backward: bool = False,
) -> DecodedRleStrip:
    """Decode, but do not rasterize, one original road-display-list strip.

    This follows the same cursor and 16-bit offset arithmetic as
    :func:`rle_sprite_forward` / :func:`rle_sprite_backward`.  It is the
    instrumentation boundary used by the high-resolution renderer: the
    geometry comes from the game's own TREKDAT data, not a fitted camera.
    """
    selector = rb(list_seg, si)
    si = (si + 1) & 0xFFFF
    table = FILL_TABLE_BWD if backward else FILL_TABLE_FWD
    palette_index = rb(
        dgroup_seg, (table + ((selector << 2) & 0xFFFF)) & 0xFFFF,
    )
    destination = rb(list_seg, si) | (
        rb(list_seg, (si + 1) & 0xFFFF) << 8
    )
    si = (si + 2) & 0xFFFF
    anchor = (destination - 1) & 0xFFFF if backward else destination
    spans: list[RleSpan] = []
    while True:
        ctrl = rb(list_seg, si)
        si = (si + 1) & 0xFFFF
        if ctrl == TERMINATOR:
            return DecodedRleStrip(
                si, selector, palette_index, destination,
                tuple(spans), backward,
            )
        runlen = rb(list_seg, si)
        si = (si + 2) & 0xFFFF  # run length plus the unused third byte
        if backward:
            end = (anchor + ctrl) & 0xFFFF
            start = (end - runlen + 1) & 0xFFFF
        else:
            start = (anchor - ctrl) & 0xFFFF
        if runlen:
            spans.append(RleSpan(start, runlen))
        anchor = (anchor + ROW_STRIDE) & 0xFFFF


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
