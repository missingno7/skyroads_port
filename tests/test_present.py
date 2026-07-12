"""Verify skyroads.recovered.present.masked_blit against real 1010:41A0 calls.

The screen-present masked blit (the routine that flushes composited frames to
VGA -- see run_status.md's "FOUND the real screen present" entry). Verified by
full-memory diff against real 41A0 invocations from demo_e2e_20260710_132930:
masked_blit reproduces every byte 41A0 writes to its destination segment,
exactly. (The captured calls are small UI blits exercising the MIDDLE
color-keyed band, top/bottom verbatim counts 0; the top/bottom bands are plain
rep-movsb, trivially matching the disassembly.)
"""
from __future__ import annotations

import json
from pathlib import Path

from skyroads.recovered.present import masked_blit

_CASES = json.loads((Path(__file__).parent / "fixtures" / "present_masked_blit_trace.json").read_text())


def _run(case: dict) -> None:
    mem = bytearray(0x100000)
    for addr_hex, val in case["seed"].items():
        mem[int(addr_hex, 16)] = val

    def rb(seg: int, off: int) -> int:
        return mem[((seg & 0xFFFF) << 4) + (off & 0xFFFF)]

    def wb(seg: int, off: int, v: int) -> None:
        mem[((seg & 0xFFFF) << 4) + (off & 0xFFFF)] = v & 0xFF

    masked_blit(rb, wb, case["dest_seg"], case["srcA"], case["srcB"],
                case["dest_off"], case["srcB_off"], case["top"], case["bot"],
                case["total"], case["tlo"], case["thi"])

    # the whole dest blit window must match the VM's post-call bytes.
    lo = ((case["dest_seg"] & 0xFFFF) << 4) + case["dest_off"]
    expected = bytes.fromhex(case["after_window"])
    assert bytes(mem[lo:lo + len(expected)]) == expected, case


def test_masked_blit_matches_real_41a0_calls() -> None:
    assert _CASES, "fixture empty"
    for case in _CASES:
        _run(case)


def test_masked_blit_color_key_semantics() -> None:
    """Directly exercise the three middle-band outcomes: transparent (p<lo,
    leave dest), substitute-background (lo<=p<hi, copy source A), foreground
    (p>=hi, copy source B)."""
    mem = bytearray(0x100000)
    A, B, D = 0x1000, 0x2000, 0x3000
    # source B pixels: 0 (transparent, lo=1), 5 (substitute, hi=10), 20 (fg)
    for i, p in enumerate((0, 5, 20)):
        mem[(B << 4) + i] = p
    # source A background bytes at the dest offsets
    for i, bg in enumerate((0x11, 0x22, 0x33)):
        mem[(A << 4) + i] = bg
    mem[(D << 4) + 0] = 0xEE  # pre-existing dest pixel (should survive transparent)

    def rb(seg, off): return mem[((seg & 0xFFFF) << 4) + (off & 0xFFFF)]
    def wb(seg, off, v): mem[((seg & 0xFFFF) << 4) + (off & 0xFFFF)] = v & 0xFF

    masked_blit(rb, wb, D, A, B, 0, 0, top_count=0, bottom_count=0, total=3,
                thresh_lo=1, thresh_hi=10)

    assert mem[(D << 4) + 0] == 0xEE   # transparent: dest untouched
    assert mem[(D << 4) + 1] == 0x22   # substitute: source-A background
    assert mem[(D << 4) + 2] == 20     # foreground: source-B pixel
