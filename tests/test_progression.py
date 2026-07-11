"""Verify the recovered level-progression state machine
(skyroads.recovered.progression.step_level_progression) against real ASM I/O
captured over the full E2E demo (1010:2A35-2AE2).

682/682 real sub-steps matched byte-exact on (game_state, level_timer_a,
level_timer_b, frame_ctr); the fixture keeps every state-transition sub-step
plus a spread of transitional (game_state==0) and in-gameplay ones.
"""
from __future__ import annotations

import json
from pathlib import Path

from skyroads.recovered.progression import (
    STATE_GAMEPLAY,
    STATE_TRANSITIONAL,
    step_level_progression,
)

_CASES = json.loads((Path(__file__).parent / "fixtures" / "progression_trace.json").read_text())


def _run(c):
    return step_level_progression(
        c["game_state"], c["af2c"], c["fuel"], c["oxy"],
        timer_a_param=c["f54a2"], timer_b_param=c["f4566"],
        ship_pos=c["ship_pos"], frame_ctr=c["f4558"],
    )


def test_matches_asm() -> None:
    assert _CASES, "fixture empty"
    for c in _CASES:
        r = _run(c)
        assert r.game_state == c["out_game_state"], c
        assert r.level_timer_a == c["out_fuel"], c
        assert r.level_timer_b == c["out_oxy"], c
        assert r.frame_ctr == c["out_f4558"], c


def test_in_gameplay_only_bumps_frame_counter() -> None:
    for c in _CASES:
        if c["game_state"] != STATE_TRANSITIONAL:
            r = _run(c)
            assert r.game_state == c["game_state"]      # unchanged
            assert r.level_timer_a == c["fuel"]         # timers frozen
            assert r.level_timer_b == c["oxy"]
            assert r.frame_ctr == (c["f4558"] + 1) & 0xFFFF


def test_fixture_includes_a_real_resume_transition() -> None:
    # The demo drives at least one 0 -> 3 resume (af2c descended below the gate).
    resumes = [c for c in _CASES
               if c["game_state"] == STATE_TRANSITIONAL
               and c["out_game_state"] == STATE_GAMEPLAY]
    assert resumes, "fixture should include a real resume transition"
    for c in resumes:
        assert c["af2c"] < 0x2800  # the corrected resume condition


def test_transitional_at_exactly_the_gate_does_not_resume() -> None:
    # af2c == 0x2800 must NOT resume (the `jb` needs strictly below); with both
    # timers non-zero it stays transitional.
    r = step_level_progression(
        game_state=0, af2c=0x2800, level_timer_a=0x7000, level_timer_b=0x7000,
        timer_a_param=0xFFFF, timer_b_param=0xFFFF, ship_pos=0, frame_ctr=0,
    )
    assert r.game_state == STATE_TRANSITIONAL
