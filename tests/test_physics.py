"""Verify the recovered movement-target formula (skyroads.handrecovered.physics)
against real 186B call arguments captured over the full E2E demo.

682/682 real calls matched (58 with real steering held, lateral_accel != 0)
with af1c_base_offset == 0x618 -- the value ss:[bp-16]==0 selects, which was
probed as 0 at the decision point in every one of the 682 calls (see
skyroads/handrecovered/physics.py's docstring; the alternate 0-offset branch is
never exercised). The fixture keeps all 58 steering samples plus a spread of
40 non-steering ones (for which the offset is irrelevant -- 0*base == 0).
"""
from __future__ import annotations

import json
from pathlib import Path

from skyroads.handrecovered.physics import AF1C_BASE_OFFSET, compute_movement_targets

_CASES = json.loads((Path(__file__).parent / "fixtures" / "movement_target_trace.json").read_text())


def test_compute_movement_targets_matches_asm() -> None:
    assert _CASES, "fixture empty"
    for case in _CASES:
        got = compute_movement_targets(
            case["ship_pos"], case["lateral"], case["af1c"], case["af2c"],
            case["vvel"], case["lateral_accel"], case["unknown_5496"],
            case["af1c_base_offset"],
        )
        assert got.tgt_lateral == case["tgt_lateral"], case
        assert got.tgt_af1c == case["tgt_af1c"], case
        assert got.tgt_af2c == case["tgt_af2c"], case


def test_fixture_exercises_real_steering() -> None:
    steering = [c for c in _CASES if c["lateral_accel"] != 0]
    assert len(steering) >= 20, "fixture should keep a healthy sample of real-steering frames"


def test_fixture_uses_the_observed_constant_offset() -> None:
    # Every real frame uses af1c_base_offset == 0x618 (ss:[bp-16]==0). The
    # native wiring relies on this constant, so lock it in.
    for case in _CASES:
        assert case["af1c_base_offset"] == AF1C_BASE_OFFSET, case


def test_default_offset_matches_the_fixture_calls() -> None:
    # The default (0x618) must reproduce every fixture call without the caller
    # passing an offset -- this is what native_gameplay_frame relies on.
    for case in _CASES:
        got = compute_movement_targets(
            case["ship_pos"], case["lateral"], case["af1c"], case["af2c"],
            case["vvel"], case["lateral_accel"], case["unknown_5496"],
        )
        assert got.tgt_af1c == case["tgt_af1c"], case


def test_compute_movement_targets_tgt_lateral_ignores_af1c_base_offset() -> None:
    a = compute_movement_targets(1000, 2000, 0, 0, 0, 0, 0, af1c_base_offset=0)
    b = compute_movement_targets(1000, 2000, 0, 0, 0, 0, 0, af1c_base_offset=AF1C_BASE_OFFSET)
    assert a.tgt_lateral == b.tgt_lateral == 3000


def test_compute_movement_targets_af2c_is_simple_integration() -> None:
    got = compute_movement_targets(0, 0, 0, 0x2800, 0xFFF0, 0, 0, 0)  # vvel = -16
    assert got.tgt_af2c == (0x2800 - 16) & 0xFFFF


def test_compute_movement_targets_wrap_seam_clamps_to_current_af1c() -> None:
    # af1c below the low seam (0x1000 < 0x2F80); lateral_accel=20000 * base=1536
    # / 0x200 = +60000, pushing the raw target (0x1000+60000=0xFA60) past the
    # high seam (0xD080) -- must clamp back to the current af1c instead.
    got = compute_movement_targets(
        ship_pos=1536, lateral=0, af1c=0x1000, af2c=0, vvel=0,
        lateral_accel=20000, unknown_5496=0, af1c_base_offset=0,
    )
    assert got.tgt_af1c == 0x1000


def test_compute_movement_targets_no_wrap_seam_when_both_sides_match() -> None:
    # af1c and the raw target both comfortably inside the band -- no clamp.
    got = compute_movement_targets(
        ship_pos=0, lateral=0, af1c=0x5000, af2c=0, vvel=0,
        lateral_accel=0, unknown_5496=100, af1c_base_offset=0,
    )
    assert got.tgt_af1c == 0x5000 + 100


def test_compute_movement_targets_zero_accel_is_a_pure_carry() -> None:
    got = compute_movement_targets(
        ship_pos=1234, lateral=5678, af1c=0x4000, af2c=0x3000, vvel=10,
        lateral_accel=0, unknown_5496=0, af1c_base_offset=0,
    )
    assert got.tgt_lateral == 1234 + 5678
    assert got.tgt_af1c == 0x4000
    assert got.tgt_af2c == 0x3000 + 10
