"""Verify the recovered buffer-relocation patch (skyroads.recovered.relocate)
against real ASM I/O captured over the E2E demo (1010:4052/4062-4069).

Recovered via lift-then-refactor: `dos_re.tools.liftverify` first proved a
literal transcription byte-exact (ORACLE_PASSING, a bounded-count sample, 8/9
blocks) — see run_status.md — and this pure function + the VM hook
(``skyroads/hooks.py::buffer_relocate_hook``) were written from that proven
block structure rather than derived by reading the disassembly alone. The full
register-exact hook was then proven against real gameplay with the project's
strict differential verifier: 252/252 calls over the E2E demo + 230/230 over
a cold-sound-demo window, zero divergences, on the first attempt (no
correction rounds needed, unlike the earlier stencil-blit hook that skipped
the lift step).

Coverage note: neither demo happens to exercise a call whose scan crosses a
64K segment boundary or arms the "extra full-pass" counter (`ss:[bp+0xA]`) —
those branches are mechanically proven by the lift's own bounded sample but
not exercised end-to-end against real gameplay. See run_status.md.
"""
from __future__ import annotations

import json
from pathlib import Path

from skyroads.recovered.relocate import patch_nonzero_bytes

_CASES = json.loads((Path(__file__).parent / "fixtures" / "relocate_trace.json").read_text())


def test_patch_nonzero_bytes_matches_asm() -> None:
    assert _CASES, "fixture empty"
    for case in _CASES:
        src = bytes.fromhex(case["src"])
        got = patch_nonzero_bytes(src, case["delta"])
        assert got == bytes.fromhex(case["expected"]), case


def test_patch_nonzero_bytes_leaves_zero_alone() -> None:
    assert patch_nonzero_bytes(b"\x00\x01\x02\xff\x00", 0x0A) == bytes([0, 0x0B, 0x0C, 0x09, 0])


def test_patch_nonzero_bytes_wraps_mod_256() -> None:
    assert patch_nonzero_bytes(b"\xff", 1) == b"\x00"
    assert patch_nonzero_bytes(b"\x01", 0x1FF) == b"\x00"  # delta masked to its low byte
