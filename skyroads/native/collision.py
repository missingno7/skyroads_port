"""Wires ``skyroads.recovered.renderer``'s pure decision functions into a single
collision predicate for ``skyroads.recovered.movement.resolve_move`` -- the
``visible(lateral32, depth, screen_y)`` callback that module's docstring
describes.

This mirrors ``skyroads/hooks.py``'s ``_persp_exit``/``_clip_exit`` (the
register-exact ``1732`` VM hook's helpers), MINUS their register-exit-state
bookkeeping (bx/cx/dx, flags) -- that plumbing exists only because the hook
stands in for the ASM subroutine and must reproduce its exact caller-visible
registers for the strict differential verifier. A pure ``visible`` callback
only needs the 0/1 decision ``road_object_visible`` already returns, so this
adapter is the pure control flow (``renderer.road_object_visible`` calling
back into ``renderer.road_segment_clip``/``renderer.perspective_row_offset``)
plus the two DGROUP table lookups those need -- nothing else.

VM-free: ``rw`` is an injected ``Callable[[int], int]`` DGROUP word-read (a
VM's ``mem.rw`` bound to ``ds``, or a ``NativeGameState.rw`` /
``GameView``'s backend), never imported here.
"""
from __future__ import annotations

from typing import Callable

from skyroads.recovered.renderer import (
    SEG_BOUND_HIGH_TABLE,
    SEG_BOUND_LOW_TABLE,
    perspective_row_offset,
    road_object_visible,
    road_segment_clip,
)


def make_visible(rw: Callable[[int], int]) -> Callable[[int, int, int], int]:
    """Build the ``visible(lateral32, depth, screen_y)`` predicate
    ``resolve_move`` calls, bound to ``rw`` for its two DGROUP table reads
    (the ``04C0`` perspective table at ``0x162C``, and the per-segment clip
    bound tables at ``0x4C``/``0x98``; see ``renderer.py``)."""

    def visible(lateral32: int, depth: int, screen_y: int) -> int:
        x_lo = lateral32 & 0xFFFF
        x_hi = (lateral32 >> 16) & 0xFFFF

        def persp_word(d: int) -> int:
            r = perspective_row_offset(x_lo, x_hi, d)
            return rw(r.offset) if r.in_range else 0

        def clip(dir_sel: int, seg: int, coord: int) -> int:
            seg &= 0xFFFF
            if seg > 0x25:
                return road_segment_clip(dir_sel, seg, coord, 0, 0)
            low = rw((SEG_BOUND_LOW_TABLE + 2 * seg) & 0xFFFF)
            high = rw((SEG_BOUND_HIGH_TABLE + 2 * seg) & 0xFFFF)
            return road_segment_clip(dir_sel, seg, coord, low, high)

        return road_object_visible(persp_word, clip, x_lo, x_hi, depth, screen_y)

    return visible
