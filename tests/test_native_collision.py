"""Unit tests for skyroads.handrecovered_native.collision.make_visible -- the wiring between
renderer.road_object_visible and its two DGROUP table lookups (the ``04C0``
perspective table, the per-segment clip bound tables), mirroring
skyroads/hooks.py's ``_persp_exit``/``_clip_exit`` (see that module's comment
at the ``1732`` hook for the register-exact original this reproduces the pure
decision of).
"""
from __future__ import annotations

import random

from skyroads.handrecovered_native.collision import make_visible
from skyroads.handrecovered.renderer import (
    SEG_BOUND_HIGH_TABLE,
    SEG_BOUND_LOW_TABLE,
    perspective_row_offset,
    road_object_visible,
    road_segment_clip,
)


def _table_rw(words: dict[int, int]):
    def rw(off: int) -> int:
        return words.get(off & 0xFFFF, 0)
    return rw


def _reference_visible(words: dict[int, int], lateral32: int, depth: int, screen_y: int) -> int:
    """An independent reimplementation of the SAME wiring (not calling
    make_visible), built directly from renderer.py's documented contracts --
    the cross-check make_visible must match."""
    x_lo, x_hi = lateral32 & 0xFFFF, (lateral32 >> 16) & 0xFFFF
    rw = _table_rw(words)

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


def test_make_visible_matches_a_random_table_over_many_samples() -> None:
    rnd = random.Random(20260711)
    words = {off: rnd.randrange(0x10000) for off in range(0x0000, 0x2000, 2)}
    visible = make_visible(_table_rw(words))
    for _ in range(500):
        lateral32 = rnd.randrange(0x100000000)
        depth = rnd.randrange(0x10000)
        screen_y = rnd.randrange(0x10000)
        expected = _reference_visible(words, lateral32, depth, screen_y)
        got = visible(lateral32, depth, screen_y)
        assert got == expected, (lateral32, depth, screen_y)


def test_make_visible_near_band_short_circuits_visible() -> None:
    # depth chosen so both (depth+-0x700) land in range at table offset 0x1630/0x162E
    # (see the module comment above for the index arithmetic); screen_y picked to
    # satisfy road_object_visible's "straddles the near band" branch outright.
    words = {0x1630: 0x0001, 0x162E: 0x0001}
    visible = make_visible(_table_rw(words))
    assert visible(0, 0x6400, 0x2000) == 1


def test_make_visible_far_screen_y_culls_without_touching_clip_tables() -> None:
    words = {}  # clip tables deliberately absent -- must not be consulted
    visible = make_visible(_table_rw(words))
    assert visible(0, 0x6400, 0x1000) == 0


def test_make_visible_falls_through_to_clip_table() -> None:
    words = {
        0x1630: 0x0100,  # r1/r3: 0xF00 nibble set, 0xF nibble clear
        0x162E: 0x0000,  # r2: irrelevant (first branch already false via screen_y)
        (SEG_BOUND_LOW_TABLE + 2 * 10) & 0xFFFF: 0,
        (SEG_BOUND_HIGH_TABLE + 2 * 10) & 0xFFFF: 100,
    }
    visible = make_visible(_table_rw(words))
    # screen_y == 0x2800 makes the first branch's `screen_y < 0x2800` false and
    # the second branch's `far <= 0x2800` false, reaching the clip call with
    # seg=10 (derived from depth) and di=12 -- inside [0, 100).
    assert visible(0, 0x6400, 0x2800) == 1
