"""Verify the recovered bounce-decay gate
(skyroads.handrecovered.dynamics.gate_bounce_decay) against real ASM I/O captured
over the full E2E replay (1010:2421-24BA).

682/682 real frames matched byte-exact on ds:[9336]; the fixture keeps a spread
across every outcome bucket (unchanged / small-kill / grounded-kill / decay /
5496-kill).
"""
from __future__ import annotations

import json
from pathlib import Path

from skyroads.handrecovered.dynamics import BOUNCE_KILL_MUL, gate_bounce_decay
from skyroads.handrecovered.player import decay_bounce

_CASES = json.loads((Path(__file__).parent / "fixtures" / "decay_gate_trace.json").read_text())


def _run(c):
    return gate_bounce_decay(
        c["bounce"], c["af2c"], c["bp28"], cur_5496=c["f5496"],
        scan_cell=c["bp24"], jump_gate=c["jump_gate"], grounded=c["f456a"],
    )


def test_matches_asm() -> None:
    assert _CASES, "fixture empty"
    for c in _CASES:
        assert _run(c) == c["out_bounce"], c


def test_unchanged_at_vertical_target() -> None:
    # af2c == tgt_af2c -> bounce passes through untouched.
    assert gate_bounce_decay(0x1234, af2c=0x2800, tgt_af2c=0x2800, cur_5496=0,
                             scan_cell=5, jump_gate=8, grounded=0) == 0x1234


def test_small_bounce_is_killed() -> None:
    # |bounce| below low16(0x104*gate)//8 -> 0. For gate=8, threshold=0x104.
    assert gate_bounce_decay(0x0080, af2c=0x2000, tgt_af2c=0x2800, cur_5496=0,
                             scan_cell=5, jump_gate=8, grounded=0) == 0


def test_large_bounce_decays() -> None:
    # |bounce| above threshold, grounded 0, af2c != tgt -> decay_bounce.
    b = 0x0400
    assert gate_bounce_decay(b, af2c=0x2000, tgt_af2c=0x2800, cur_5496=0,
                             scan_cell=5, jump_gate=8, grounded=0) == decay_bounce(b)


def test_grounded_kills_bounce() -> None:
    assert gate_bounce_decay(0x0400, af2c=0x2000, tgt_af2c=0x2800, cur_5496=0,
                             scan_cell=5, jump_gate=8, grounded=1) == 0


def test_5496_with_low_scan_cell_kills_bounce() -> None:
    assert gate_bounce_decay(0x0400, af2c=0x2000, tgt_af2c=0x2800, cur_5496=7,
                             scan_cell=1, jump_gate=8, grounded=0) == 0


def test_threshold_constant() -> None:
    assert BOUNCE_KILL_MUL == 0x104
