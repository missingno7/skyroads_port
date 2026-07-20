"""Verify the recovered ship physics (skyroads.handrecovered.player) against real
ASM I/O captured over the level replay.

``advance_ship`` (1010:24C4) and ``decay_bounce`` (1010:24A1) are inline in the
gameplay handler; the fixture is (input -> output) triples/pairs sampled by
watching those IPs during the replay (full run: 1610/1610 advance_ship and 63/63
decay_bounce byte-exact). The fixture deliberately includes negative-speed
cases: the ASM sign-extends speed (cwd) before multiplying by 75, so a negative
speed moves the ship *backward* — an earlier unsigned reconstruction diverged on
exactly these (33 of 1610 real calls).
"""
from __future__ import annotations

import json
from pathlib import Path

from skyroads.handrecovered.player import (
    JUMP_IMPULSE, RESUME_HEIGHT_GATE, TERMINAL_VVEL, RespawnState, advance_ship,
    decay_bounce, is_landed_for_resume, level_gravity, respawn,
    update_vertical_velocity,
)


def test_level_gravity_matches_asm() -> None:
    # -(jump_level_gate * 0x1680 / 0x190); verified vs the ASM at 1010:201C for
    # the E2E replay's init values (jump_gate 8 -> 0xFF8D, 9 -> 0xFF7F).
    assert level_gravity(8) == 0xFF8D
    assert level_gravity(9) == 0xFF7F


def test_level_gravity_stronger_with_higher_gate() -> None:
    # more negative (as signed 16-bit) for a higher gate
    def s16(v):
        return v - 0x10000 if v & 0x8000 else v
    assert s16(level_gravity(9)) < s16(level_gravity(8)) < 0

_FIXTURE = Path(__file__).parent / "fixtures" / "physics_trace.json"
_CASES = json.loads(_FIXTURE.read_text())
_VPHYS = json.loads((Path(__file__).parent / "fixtures" / "vphysics_trace.json").read_text())
_RESPAWNS = json.loads((Path(__file__).parent / "fixtures" / "respawn_trace.json").read_text())


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


def test_update_vertical_velocity_matches_asm() -> None:
    # gravity + jump-impulse path: byte-exact vs the deaths replay (all airborne,
    # af2c>=0x2800; the fixture includes a jump-impulse frame)
    cases = _VPHYS["update_vertical_velocity"]
    assert cases, "fixture empty"
    for c in cases:
        got = update_vertical_velocity(c["pre"], bool(c["jumped"]), c["af2c"],
                                       c["gravity"], bool(c["grounded"]))
        assert got == (c["post"] & 0xFFFF), c
    assert any(c["jumped"] for c in cases), "fixture should include a jump frame"


def test_update_vertical_velocity_branches() -> None:
    # jump impulse overrides the incoming velocity (on the airborne gravity path,
    # af2c>=0x2800, where jumps actually occur; gravity=0 leaves the impulse intact)
    assert update_vertical_velocity(0, True, 0x3000, 0, grounded=False) == JUMP_IMPULSE
    # airborne gravity accumulates (signed)
    assert update_vertical_velocity(10, False, 0x3000, -115, grounded=False) == (10 - 115) & 0xFFFF
    # airborne below the gate clamps down to terminal (ASM-derived branch)
    assert update_vertical_velocity(0, False, 0x0000, -115, grounded=False) == (TERMINAL_VVEL & 0xFFFF)


def test_respawn_matches_asm() -> None:
    # 3/3 real deaths-replay respawns byte-exact, all 19 fields (1010:201F-20A7)
    assert _RESPAWNS, "fixture empty"
    expected = respawn()._asdict()
    for event in _RESPAWNS:
        if event["ctrl"] != 0:
            continue  # only the keyboard control-mode path is modeled
        assert event["post"] == expected, event


def test_respawn_is_landed_for_resume() -> None:
    # respawn() sets AF2C to exactly the resume gate, which does NOT resume yet
    # (1010:2AB1 `jb` needs af2c strictly below the gate) -- the ship stays
    # transitional until it descends below 0x2800. Verified 682/682 via the full
    # progression state machine; corrects an earlier inverted (>=) reading.
    assert respawn().vert_af2c == RESUME_HEIGHT_GATE
    assert is_landed_for_resume(respawn().vert_af2c) is False
    assert is_landed_for_resume(RESUME_HEIGHT_GATE - 1) is True
    assert is_landed_for_resume(RESUME_HEIGHT_GATE) is False
