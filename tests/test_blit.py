"""Verify the recovered stencil blit (skyroads.handrecovered.blit) against real ASM
I/O captured over the E2E demo's menu screens (1010:0F62).

The pure per-byte substitution is what ``stencil_blit`` recovers; the full
register-exact VM hook (``skyroads/hooks.py::stencil_blit_hook``) was proven
byte-exact against the ASM oracle with the project's strict differential
verifier over the whole E2E demo -- 213/213 calls, zero divergences (getting
there caught two real bugs: SI/DI are preserved via push/pop, not left at a
"final cursor" position, and AH/AF both thread through the loop rather than
being determined by the last byte alone -- see run_status.md).
"""
from __future__ import annotations

import json
from pathlib import Path

from skyroads.handrecovered.blit import stencil_blit

_CASES = json.loads((Path(__file__).parent / "fixtures" / "stencil_blit_trace.json").read_text())


def test_stencil_blit_matches_asm() -> None:
    assert _CASES, "fixture empty"
    for case in _CASES:
        src = bytes.fromhex(case["src"])
        got = stencil_blit(src, case["template_color"], case["other_color"])
        assert got == bytes.fromhex(case["expected"]), case


def test_stencil_blit_maps_zero_one_other() -> None:
    assert stencil_blit(b"\x00\x01\x02\xff\x01\x00", 0x1E, 0x05) == bytes([0, 0x1E, 5, 5, 0x1E, 0])


def test_stencil_blit_masks_colors_to_a_byte() -> None:
    assert stencil_blit(b"\x01", 0x1234, 0) == bytes([0x34])
    assert stencil_blit(b"\x02", 0, 0x5678) == bytes([0x78])
