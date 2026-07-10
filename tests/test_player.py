"""Verify the recovered ship physics (skyroads.recovered.player) against real
ASM I/O captured over the level demo.

``advance_ship`` (1010:24C4) and ``decay_bounce`` (1010:24A1) are inline in the
gameplay handler; the fixture is (input -> output) triples/pairs sampled by
watching those IPs during the demo (full run: 1610/1610 advance_ship and 63/63
decay_bounce byte-exact). The fixture deliberately includes negative-speed
cases: the ASM sign-extends speed (cwd) before multiplying by 75, so a negative
speed moves the ship *backward* — an earlier unsigned reconstruction diverged on
exactly these (33 of 1610 real calls).
"""
from __future__ import annotations

import json
from pathlib import Path

from skyroads.recovered.player import advance_ship, decay_bounce

_FIXTURE = Path(__file__).parent / "fixtures" / "physics_trace.json"
_CASES = json.loads(_FIXTURE.read_text())


def test_advance_ship_matches_asm_including_negative_speed() -> None:
    cases = _CASES["advance_ship"]
    assert cases, "fixture empty"
    for pos_in, speed, pos_out in cases:
        assert advance_ship(pos_in, speed) == pos_out, (pos_in, speed, pos_out)
    # the guard that matters: negative (sign-extended) speed must move backward
    neg = [(p, s, o) for p, s, o in cases if s & 0x8000]
    assert neg, "fixture should include negative-speed regression cases"
    for pos_in, speed, pos_out in neg:
        assert advance_ship(pos_in, speed) == pos_out


def test_advance_ship_clamps_to_road_ends() -> None:
    # negative result clamps to 0; overshoot clamps to LEVEL_END (0x2AAA)
    assert advance_ship(0, 0xFFFF) == 0            # speed -1 from start -> clamp 0
    assert advance_ship(0x2AA0, 100) == 0x2AAA     # overshoot -> road end


def test_decay_bounce_matches_asm() -> None:
    for bounce_in, bounce_out in _CASES["decay_bounce"]:
        assert decay_bounce(bounce_in) == bounce_out, (bounce_in, bounce_out)
