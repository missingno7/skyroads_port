"""Native binding for the perspective classification
(``skyroads.recovered.classify.classify_perspective``): computes the ship's
own perspective word from a DGROUP word-reader and feeds the recovered pure
logic, mirroring ``skyroads.native.collision.make_visible``.

VM-free: ``rw`` is an injected ``Callable[[int], int]`` DGROUP word-read (a
``NativeGameState.rw`` or a VM ``mem.rw`` bound to ``ds``).
"""
from __future__ import annotations

from typing import Callable

from skyroads.recovered.classify import (
    SEG_CLASS_TABLE,
    ClassifyResult,
    classify_perspective,
)
from skyroads.recovered.renderer import perspective_row_offset


def perspective_word(rw: Callable[[int], int], lateral: int, af1c: int) -> int:
    """The ``04C0`` result for the ship's own ``(lateral, af1c)``: the
    perspective-table word at the projected offset, or 0 if out of range
    (same rule as ``collision``'s internal ``persp_word``)."""
    r = perspective_row_offset(lateral & 0xFFFF, (lateral >> 16) & 0xFFFF, af1c)
    return rw(r.offset) if r.in_range else 0


def classify_ship(
    rw: Callable[[int], int], lateral: int, af1c: int, af2c: int,
    bp12: int, class_skip_prev: int,
) -> ClassifyResult:
    """Run the ``1010:2324-23BF`` classification for the ship's own position,
    reading the perspective and per-segment-class tables through ``rw``."""
    word = perspective_word(rw, lateral, af1c)

    def read_seg_table(idx: int) -> int:
        return rw((SEG_CLASS_TABLE + ((idx & 0xFFFF) << 1)) & 0xFFFF)

    return classify_perspective(word, af2c, bp12, class_skip_prev, read_seg_table)
