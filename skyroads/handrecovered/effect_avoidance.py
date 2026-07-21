"""Semantic recovery of SkyRoads' airborne obstacle avoidance (1010:1DFA).

The gameplay loop invokes this once per jump after the ship rises past a
height gate.  It predicts the remaining arc and, when that arc intersects a
special road cell, tries small steering or forward-position adjustments.  The
first clear candidate becomes authoritative.  This module expresses that
search in game terms; it does not dispatch generated CPU instructions.
"""
from __future__ import annotations

from typing import Callable, NamedTuple

from skyroads.handrecovered.movement import (
    _s16,
    _slong_div,
    _truncdiv,
    _ulong_mul,
)
from skyroads.handrecovered.renderer import perspective_row_offset


AF1C_VALID_LOW = 0x2F80
AF1C_VALID_HIGH = 0xD080
ARC_END_HEIGHT = 0x2800
PREDICTION_BASE_OFFSET = 0x0618
FORWARD_STEP_MUL = 0x4B
ADJUSTMENT_DIVISOR = 10
MAX_ADJUSTMENT_STEPS = 6
MAX_SHIP_POSITION = 0x2AAA


class AvoidanceAdjustment(NamedTuple):
    lateral_accel: int
    ship_pos: int
    position_delta: int
    mark_effect: bool


def select_avoidance_adjustment(
    rw: Callable[[int], int],
    *,
    lateral: int,
    af1c: int,
    af2c: int,
    ship_pos: int,
    lateral_accel: int,
    bounce: int,
    gravity: int,
    speed: int,
    center_nudge: int,
) -> AvoidanceAdjustment | None:
    """Return the adjustment selected by ``1DFA``, or ``None`` if clear.

    ``None`` is significant: on an already-clear projected arc the original
    routine leaves AF2E/AF30 and the effect marker untouched.  A non-``None``
    result always writes the position delta, even when all candidates fail and
    that delta is zero.
    """
    state = dict(
        lateral=lateral,
        af1c=af1c,
        af2c=af2c,
        bounce=bounce,
        gravity=gravity,
        speed=speed,
        center_nudge=center_nudge,
    )
    original_pos = ship_pos & 0xFFFFFFFF
    original_accel = lateral_accel & 0xFFFF
    if _projected_arc_is_clear(
        rw, ship_pos=original_pos, lateral_accel=original_accel, **state,
    ):
        return None

    chosen_pos = original_pos
    chosen_accel = original_accel
    selected_step = MAX_ADJUSTMENT_STEPS + 1
    for step in range(1, MAX_ADJUSTMENT_STEPS + 1):
        accel_delta = _truncdiv(
            _s16((_s16(original_accel) * step) & 0xFFFF),
            ADJUSTMENT_DIVISOR,
        )
        for candidate in (
            (original_accel + accel_delta) & 0xFFFF,
            (original_accel - accel_delta) & 0xFFFF,
        ):
            if _projected_arc_is_clear(
                rw,
                ship_pos=original_pos,
                lateral_accel=candidate,
                **state,
            ):
                chosen_accel = candidate
                selected_step = step
                break
        if selected_step == step:
            break

        position_delta = _slong_div(
            _ulong_mul(original_pos, step), ADJUSTMENT_DIVISOR,
        )
        for candidate in (
            (original_pos + position_delta) & 0xFFFFFFFF,
            (original_pos - position_delta) & 0xFFFFFFFF,
        ):
            if _projected_arc_is_clear(
                rw,
                ship_pos=candidate,
                lateral_accel=original_accel,
                **state,
            ):
                chosen_pos = candidate
                chosen_accel = original_accel
                selected_step = step
                break
        if selected_step == step:
            break

    delta = (chosen_pos - original_pos) & 0xFFFFFFFF
    return AvoidanceAdjustment(
        chosen_accel,
        chosen_pos,
        delta,
        selected_step <= MAX_ADJUSTMENT_STEPS,
    )


def _projected_arc_is_clear(
    rw: Callable[[int], int],
    *,
    lateral: int,
    af1c: int,
    af2c: int,
    ship_pos: int,
    lateral_accel: int,
    bounce: int,
    gravity: int,
    speed: int,
    center_nudge: int,
) -> bool:
    """Project the jump until landing and test its endpoint road cells."""
    projected = _project_arc(
        lateral=lateral, af1c=af1c, af2c=af2c, ship_pos=ship_pos,
        lateral_accel=lateral_accel, bounce=bounce, gravity=gravity,
        speed=speed, center_nudge=center_nudge,
    )
    if projected is None:
        return False
    initial_lateral, initial_af1c, lateral, af1c = projected
    return not (
        _effect_cell_blocked(rw, initial_lateral, initial_af1c)
        or _effect_cell_blocked(rw, lateral, af1c)
    )


def _project_arc(
    *, lateral: int, af1c: int, af2c: int, ship_pos: int,
    lateral_accel: int, bounce: int, gravity: int, speed: int,
    center_nudge: int,
) -> tuple[int, int, int, int] | None:
    """Return initial/final road coordinates, or ``None`` past AF1C bounds."""
    lateral &= 0xFFFFFFFF
    af1c &= 0xFFFF
    probe_lateral = lateral
    probe_af1c = af1c
    af2c &= 0xFFFF
    ship_pos &= 0xFFFFFFFF
    lateral_accel &= 0xFFFF
    bounce &= 0xFFFF

    while True:
        # 1CCD refreshes this probe at the top of every prediction step.  Its
        # final road test therefore spans the last step of the arc, not the
        # entire arc's original coordinate.
        probe_lateral = lateral
        probe_af1c = af1c
        bounce = (bounce + gravity) & 0xFFFF
        lateral = (lateral + ship_pos) & 0xFFFFFFFF
        accel32 = _s16(lateral_accel) & 0xFFFFFFFF
        base = (ship_pos + PREDICTION_BASE_OFFSET) & 0xFFFFFFFF
        af1c = (
            af1c
            + _slong_div(_ulong_mul(accel32, base), 0x200)
            + (center_nudge & 0xFFFF)
        ) & 0xFFFF
        if not AF1C_VALID_LOW <= af1c <= AF1C_VALID_HIGH:
            return None

        af2c = (af2c + bounce) & 0xFFFF
        speed32 = _s16(speed) & 0xFFFFFFFF
        ship_pos = (
            ship_pos + _ulong_mul(speed32, FORWARD_STEP_MUL)
        ) & 0xFFFFFFFF
        ship_pos = _clamp_projected_position(ship_pos)
        if af2c <= ARC_END_HEIGHT:
            break

    return probe_lateral, probe_af1c, lateral, af1c


def _clamp_projected_position(value: int) -> int:
    high = (value >> 16) & 0xFFFF
    low = value & 0xFFFF
    signed_high = _s16(high)
    if signed_high < 0 or (signed_high == 0 and _s16(low) < 0):
        return 0
    if signed_high > 0 or low > MAX_SHIP_POSITION:
        return MAX_SHIP_POSITION
    return low


def _effect_cell_blocked(
    rw: Callable[[int], int], lateral: int, af1c: int,
) -> bool:
    """Natural form of the small 1010:1C62 road-cell classifier."""
    result = perspective_row_offset(
        lateral & 0xFFFF, (lateral >> 16) & 0xFFFF, af1c,
    )
    word = rw(result.offset) if result.in_range else 0
    selector = word & 0x0F00
    if selector == 0:
        return (word & 0x000F) in (0, 0x0C)
    if selector == 0x0100:
        return False
    return ((word >> 4) & 0x000F) == 0x0C
