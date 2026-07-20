"""Verify the recovered jump-latch + steering + gravity block
(skyroads.handrecovered.dynamics.step_jump_steer_gravity) against real ASM I/O
captured over the full E2E replay (1010:252B-2635).

415/416 real frames match byte-exact on (bounce, lateral_accel, bp-8, bp-10);
the fixture keeps a representative spread plus every jump-fire, 1DFA-effect,
and steering frame. The single non-match is a 1DFA-effect frame whose call
separately rewrote lateral_accel -- flagged by the function (hit_effect_path),
asserted here to be excluded from the lateral_accel check but still exact on
bounce and the jump scratch.
"""
from __future__ import annotations

import json
from pathlib import Path

from skyroads.handrecovered.dynamics import JumpScratch, step_jump_steer_gravity

_CASES = json.loads((Path(__file__).parent / "fixtures" / "dynamics_trace.json").read_text())


def _run(c) -> "tuple":
    return step_jump_steer_gravity(
        JumpScratch(c["bp8"], c["bp10"], c["bp6"]),
        class_skip=c["bp14"], class_zero=c["bp18"],
        bounce=c["bounce"], lateral_accel=c["accel"], af2c=c["af2c"],
        steer=c["steer"], jump_req=c["jump_req"], jump_gate=c["jump_gate"],
        grounded=c["grounded"], gravity=c["gravity"], effect_gate=c["f4570"],
    )


def test_bounce_and_jump_scratch_match_asm() -> None:
    assert _CASES, "fixture empty"
    for c in _CASES:
        r = _run(c)
        assert r.bounce == c["out_bounce"], c
        assert r.scratch.jumping == c["out_bp8"], c
        assert r.scratch.jump_start_y == c["out_bp10"], c


def test_lateral_accel_matches_asm_except_on_effect_path() -> None:
    for c in _CASES:
        r = _run(c)
        if r.hit_effect_path:
            # 1DFA rewrote lateral_accel in a way this block does not model.
            assert c["hit_1dfa"], c
            continue
        assert r.lateral_accel == c["out_accel"], c


def test_effect_path_flag_agrees_with_the_real_1dfa_call() -> None:
    for c in _CASES:
        r = _run(c)
        assert r.hit_effect_path == bool(c["hit_1dfa"]), c


def test_effect_latch_is_set_when_the_effect_path_fires() -> None:
    for c in _CASES:
        r = _run(c)
        if r.hit_effect_path:
            assert r.scratch.effect_latch == 1, c


def test_jump_fire_sets_impulse_latch_and_start_height() -> None:
    fired = [c for c in _CASES
             if c["bp8"] == 0 and c["bp18"] == 0 and c["jump_req"] != 0
             and c["jump_gate"] < 0x14]
    assert fired, "fixture should include real jump-fire frames"
    for c in fired:
        r = _run(c)
        assert r.scratch.jumping == 1, c
        assert r.scratch.jump_start_y == (c["af2c"] & 0xFFFF), c
        # the impulse is written before gravity; on a jump-fire frame the ship
        # is airborne with af2c>=gate, so gravity is then added on top.
        assert r.bounce == ((0x0480 + c["gravity"]) & 0xFFFF), c


def test_fixture_exercises_real_steering() -> None:
    assert sum(1 for c in _CASES if c["steer"] != 0) >= 10
