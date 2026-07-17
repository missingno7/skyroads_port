"""Verify the recovered intro animation-frame unpacker
(skyroads.handrecovered.intro_anim) against real ASM I/O captured over the E2E
demo (1010:3A96, one segment's worth of the fixed 1040-row unpack).

Recovered via lift-then-refactor: `dos_re.tools.liftverify` proved a literal
transcription byte-exact against the ASM oracle first (see run_status.md).
The full register-exact VM hook (`skyroads/hooks.py::intro_anim_unpack_hook`)
was separately proven against real gameplay with the project's strict
differential verifier: 1/1 real call (it unpacks the whole intro animation
once, not per-frame) over both the E2E and cold-sound demos, zero
divergences. Getting there caught a real bug independently of the algorithm
recovery itself: the hook read the return address off the stack but never
actually advanced SP past it (an omission caught by the SP-only divergence
report, not a game-behavior bug).
"""
from __future__ import annotations

import json
from pathlib import Path

from skyroads.handrecovered.intro_anim import unpack_animation_segment

_FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "intro_anim_trace.json").read_text())


def test_unpack_animation_segment_matches_asm() -> None:
    pre = bytearray.fromhex(_FIXTURE["pre"])
    expected = bytes.fromhex(_FIXTURE["post"])

    def rb(off: int) -> int:
        return pre[off & 0xFFFF] if (off & 0xFFFF) < len(pre) else 0

    def wb(off: int, val: int) -> None:
        pre[off & 0xFFFF] = val & 0xFF

    result = unpack_animation_segment(rb, wb)
    assert bytes(pre) == expected
    assert (result.cursor_si, result.cursor_di) == (_FIXTURE["si"], _FIXTURE["di"])


def test_unpack_animation_segment_row_structure() -> None:
    from skyroads.handrecovered.intro_anim import HEADER_BYTES, ROW_TERMINATOR

    # A minimal synthetic segment. The self-reference offset must leave a real
    # gap between si and di (di grows faster than si once tokens are present,
    # matching real data), or di catches up to si and self-overwrites the very
    # bytes still being read -- placing the row data at offset 0 (a no-op
    # header "shift") does exactly that and hangs; use a real offset instead.
    self_ref = 0x2000
    seg = bytearray(0x10000)
    seg[0] = self_ref & 0xFF
    seg[1] = (self_ref >> 8) & 0xFF
    # after the header-shift step, si continues from self_ref + HEADER_BYTES
    row_src = self_ref + HEADER_BYTES
    seg[row_src] = 0xAA          # prefix byte
    seg[row_src + 1] = 0xBB      # prefix word lo
    seg[row_src + 2] = 0xCC      # prefix word hi
    seg[row_src + 3] = 0x11      # token b1 (not terminator)
    seg[row_src + 4] = 0x22      # token b2
    seg[row_src + 5] = ROW_TERMINATOR  # ends the row after one token

    def rb(off): return seg[off & 0xFFFF]
    def wb(off, val): seg[off & 0xFFFF] = val & 0xFF

    from unittest import mock
    with mock.patch("skyroads.handrecovered.intro_anim.ROWS_PER_SEGMENT", 1):
        result = unpack_animation_segment(rb, wb)

    di = HEADER_BYTES
    assert list(seg[di:di + 7]) == [0xAA, 0xBB, 0xCC, 0x11, 0x22, 0x00, 0xFF]
    assert result.cursor_di == di + 7
