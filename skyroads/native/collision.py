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

from skyroads.recovered.collision_response import (
    FELL_SEG_HIGH_TABLE,
    FELL_SEG_LOW_TABLE,
    fell_off_segment,
    ship_fell_off as _ship_fell_off_pure,
)
from skyroads.recovered.renderer import (
    SEG_BOUND_HIGH_TABLE,
    SEG_BOUND_LOW_TABLE,
    perspective_row_offset,
    road_object_visible,
    road_segment_clip,
)


def ship_fell_off(rw: Callable[[int], int], lateral: int, af1c: int, af2c: int) -> int:
    """Whether the ship has fallen off the road (`1010:0533`), reading the
    perspective + per-segment clip tables through ``rw``. Returns 1 (fell) / 0."""
    r = perspective_row_offset(lateral & 0xFFFF, (lateral >> 16) & 0xFFFF, af1c)
    persp_word = rw(r.offset) if r.in_range else 0
    seg = fell_off_segment(af1c)
    if seg < 0:
        # segment out of range -> can't have fallen (the pure fn returns 0 too,
        # but avoid a bogus table read).
        return _ship_fell_off_pure(persp_word, af1c, af2c, 0, 0)
    seg_low = rw((FELL_SEG_LOW_TABLE + 2 * seg) & 0xFFFF)
    seg_high = rw((FELL_SEG_HIGH_TABLE + 2 * seg) & 0xFFFF)
    return _ship_fell_off_pure(persp_word, af1c, af2c, seg_low, seg_high)


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
