"""Semantics tests for the promoted RLE sprite rasterizer pair
(`skyroads.handrecovered.rle_sprite`, from the differential-verified hook bodies).

Byte-exactness against the game comes from the frame-level assembly test (the
`artifacts/frame_2d1f` capture); these tests pin the stream/paint contract:
span placement, row stride, mirroring, fill-table indexing, cursor return.
"""
from __future__ import annotations

from skyroads.handrecovered.rle_sprite import (FILL_TABLE_BWD, FILL_TABLE_FWD,
                                           ROW_STRIDE, decode_rle_strip,
                                           rle_sprite_backward,
                                           rle_sprite_forward)

DG, LS, DEST = 0x1686, 0x2B12, 0x8116


def _env(stream: bytes, index: int, fwd_fill: int, bwd_fill: int):
    mem = {}
    for i, b in enumerate(stream):
        mem[(LS, i)] = b
    mem[(DG, (FILL_TABLE_FWD + index * 4) & 0xFFFF)] = fwd_fill
    mem[(DG, (FILL_TABLE_BWD + index * 4) & 0xFFFF)] = bwd_fill
    dest = {}

    def rb(seg, off):
        return mem.get((seg, off & 0xFFFF), 0)

    def wb(seg, off, v):
        assert seg == DEST
        dest[off & 0xFFFF] = v & 0xFF

    return rb, wb, dest


def test_forward_paints_spans_left_of_anchor_with_row_stride() -> None:
    # index=2, dest=0x1000; two rows: (ctrl=3, run=2), (ctrl=5, run=3); term
    stream = bytes([2, 0x00, 0x10, 3, 2, 0, 5, 3, 0, 0xFF])
    rb, wb, dest = _env(stream, index=2, fwd_fill=0xAB, bwd_fill=0xCD)
    si = rle_sprite_forward(rb, wb, DG, LS, DEST, 0)
    assert si == len(stream)                       # cursor past the terminator
    row0 = 0x1000 - 3                              # di -= ctrl, paint forward
    assert dest == {row0: 0xAB, row0 + 1: 0xAB,
                    **{0x1000 + ROW_STRIDE - 5 + k: 0xAB for k in range(3)}}


def test_backward_mirrors_and_uses_odd_fill_table() -> None:
    stream = bytes([2, 0x00, 0x10, 3, 2, 0, 0xFF])
    rb, wb, dest = _env(stream, index=2, fwd_fill=0xAB, bwd_fill=0xCD)
    si = rle_sprite_backward(rb, wb, DG, LS, DEST, 0)
    assert si == len(stream)
    # di starts at 0x0FFF (dest-1), += ctrl -> 0x1002; run=2 ends AT di
    assert dest == {0x1002: 0xCD, 0x1001: 0xCD}


def test_empty_strip_paints_nothing() -> None:
    stream = bytes([1, 0x34, 0x12, 0xFF])
    rb, wb, dest = _env(stream, index=1, fwd_fill=7, bwd_fill=9)
    assert rle_sprite_forward(rb, wb, DG, LS, DEST, 0) == 4
    assert dest == {}


def test_read_only_decoder_reports_the_exact_forward_and_backward_coverage() -> None:
    stream = bytes([2, 0x00, 0x10, 3, 2, 0, 5, 3, 0, 0xFF])
    rb, _wb, _dest = _env(stream, index=2, fwd_fill=0xAB, bwd_fill=0xCD)

    forward = decode_rle_strip(rb, DG, LS, 0)
    backward = decode_rle_strip(rb, DG, LS, 0, backward=True)

    assert forward.next_si == backward.next_si == len(stream)
    assert forward.palette_selector == backward.palette_selector == 2
    assert (forward.palette_index, backward.palette_index) == (0xAB, 0xCD)
    assert [(span.offset, span.length) for span in forward.spans] == [
        (0x1000 - 3, 2), (0x1000 + ROW_STRIDE - 5, 3),
    ]
    assert [(span.offset, span.length) for span in backward.spans] == [
        (0x1001, 2), (0x1000 + ROW_STRIDE + 2, 3),
    ]
