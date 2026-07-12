"""Verify skyroads.recovered.present.present_rect against real 1010:4201 calls.

The road-present scanline loop: 34AE fills the off-screen road buffer, then
1010:4201 flushes a rows x width rectangle of it to VGA row-by-row (dest cursor
+= 0x140 per row) via masked_blit. Against real VM row-loop invocations from
demo_e2e_20260710_132930, present_rect reproduces every VGA byte written:
12/12 calls byte-exact (see run_status.md). This closes the road render->screen
present pipeline: 34AE composite -> present_rect -> masked_blit, all VM-verified.
"""
from __future__ import annotations

import json
from pathlib import Path

from skyroads.recovered.present import present_rect

_CASES = json.loads((Path(__file__).parent / "fixtures" / "present_rect_trace.json").read_text())


def _run(case: dict) -> None:
    mem = bytearray(0x100000)
    for addr_hex, val in case["seed"].items():
        mem[int(addr_hex, 16)] = val

    def rb(seg: int, off: int) -> int:
        return mem[((seg & 0xFFFF) << 4) + (off & 0xFFFF)]

    def wb(seg: int, off: int, v: int) -> None:
        mem[((seg & 0xFFFF) << 4) + (off & 0xFFFF)] = v & 0xFF

    present_rect(rb, wb, case["dest_seg"], case["srcA"], case["srcB"],
                 case["dest_off"], case["rows"], case["width"],
                 case["tlo"], case["thi"])

    for addr_hex, expected in case["expected"].items():
        assert mem[int(addr_hex, 16)] == expected, (addr_hex, case["rows"], case["width"])


def test_present_rect_matches_real_4201_calls() -> None:
    assert _CASES, "fixture empty"
    for case in _CASES:
        _run(case)


def test_present_rect_advances_dest_by_scanline_and_src_by_width() -> None:
    """The two cursor strides: dest += 0x140 per row (VGA scanline), src += width
    per row. Verify by presenting 3 rows of distinct source data with an
    all-foreground key and checking each row lands one scanline apart."""
    mem = bytearray(0x100000)
    B, D = 0x2000, 0x3000
    W = 4
    # source B: rows of 0x40+r+i (all >= threshold => foreground copy)
    for r in range(3):
        for i in range(W):
            mem[(B << 4) + r * W + i] = 0x40 + r * 0x10 + i

    def rb(seg, off): return mem[((seg & 0xFFFF) << 4) + (off & 0xFFFF)]
    def wb(seg, off, v): mem[((seg & 0xFFFF) << 4) + (off & 0xFFFF)] = v & 0xFF

    present_rect(rb, wb, D, 0, B, dest_off=0, rows=3, width=W, thresh_lo=1, thresh_hi=1)

    for r in range(3):
        base = (D << 4) + r * 0x140
        assert list(mem[base:base + W]) == [0x40 + r * 0x10 + i for i in range(W)]
