"""Check the pure recovered buffer relocation against captured oracle cases.

The lift-then-refactor path first proved a literal transcription byte-exact;
the retained semantic function was derived from that block structure. The
fixtures cover the observed 1010:4052/4062-4069 behavior.
"""
from __future__ import annotations

import json
from pathlib import Path

from skyroads.handrecovered.relocate import patch_nonzero_bytes

_CASES = json.loads(
    (Path(__file__).parent / "fixtures" / "relocate_trace.json").read_text()
)


def test_patch_nonzero_bytes_matches_asm() -> None:
    assert _CASES, "fixture empty"
    for case in _CASES:
        src = bytes.fromhex(case["src"])
        got = patch_nonzero_bytes(src, case["delta"])
        assert got == bytes.fromhex(case["expected"]), case


def test_patch_nonzero_bytes_leaves_zero_alone() -> None:
    assert patch_nonzero_bytes(
        b"\x00\x01\x02\xff\x00", 0x0A
    ) == bytes([0, 0x0B, 0x0C, 0x09, 0])


def test_patch_nonzero_bytes_wraps_mod_256() -> None:
    assert patch_nonzero_bytes(b"\xff", 1) == b"\x00"
    assert patch_nonzero_bytes(b"\x01", 0x1FF) == b"\x00"
