"""SkyRoads level-progression state machine — `1010:2A35-2AE2`.

The tail of the gameplay sub-step: it counts down the two level timers and
then drives the `ds:[456E]` game-state transitions that end (or resume) a
level. Runs once per gameplay sub-step (the `2317` loop).

## Level timers (only while game_state == 0, i.e. transitional/just-respawned)

* `level_timer_b` (`ds:[B13C]`, TIME-based / "oxygen") -=
  `0x7530 / (0x24 * ds:[4566])` each sub-step (`2A45-2A64`);
* `level_timer_a` (`ds:[5494]`, DISTANCE-based / "fuel") -=
  `slong_div(ulong_mul(0x7530 / ds:[54A2], ship_pos), 0x10000)` -- i.e. a
  ship_pos-proportional amount (`2A6A-2AA1`).

Both are unsigned-clamped at 0 (the ASM's `cmp > 0x7530` after the subtract
catches the wrap and zeroes it).

## State transitions (only while game_state == 0)

Evaluated in this order, later ones overriding earlier (`2AB1-2ADC`):

1. if `af2c < 0x2800` (ship descended past the resume gate,
   :func:`player.is_landed_for_resume`) -> game_state = 3 (resume gameplay);
2. if `level_timer_a == 0` -> game_state = 4 (distance/"fuel" timer expired);
3. if `level_timer_b == 0` -> game_state = 5 (time/"oxygen" timer expired).

While game_state != 0 (in gameplay, or already in a post-level state 3/4/5),
none of the above runs -- instead the frame counter `ds:[4558]` increments
(`2AE5`).

Verified 682/682 against the real ASM over the full E2E replay (including the
real 0->3 resume transitions). This is the level-complete / out-of-time death
logic the vmless_roadmap lists under item 1.
"""
from __future__ import annotations

from typing import NamedTuple

from skyroads.handrecovered.movement import _slong_div, _ulong_mul
from skyroads.handrecovered.player import RESUME_HEIGHT_GATE, is_landed_for_resume

#: Both level timers start here and are clamped to [0, 0x7530] (1010:2A3F etc).
LEVEL_TIMER_MAX = 0x7530
#: `level_timer_b` (oxygen) per-sub-step rate divisor multiplier (1010:2A45).
OXY_RATE_MUL = 0x24
#: `level_timer_a` (fuel) per-sub-step scale shift -- the ulong product is
#: divided by this (1010:2A88 `bx=1, cx=0` -> a 0x10000 divisor).
FUEL_SCALE_DIV = 0x10000

#: game_state values this state machine writes.
STATE_TRANSITIONAL = 0   # respawned / not yet resumed
STATE_GAMEPLAY = 3       # resumed (af2c descended past the gate)
STATE_TIMER_A_EXPIRED = 4  # ds:[5494] (distance/"fuel") hit 0
STATE_TIMER_B_EXPIRED = 5  # ds:[B13C] (time/"oxygen") hit 0


class ProgressionResult(NamedTuple):
    game_state: int    # ds:[456E]
    level_timer_a: int  # ds:[5494]
    level_timer_b: int  # ds:[B13C]
    frame_ctr: int     # ds:[4558]


def _tick_down(timer: int, dec: int) -> int:
    """Subtract ``dec`` from a level timer, clamping the unsigned wrap to 0
    (the ASM's `cmp timer, 0x7530; ja -> 0` underflow guard)."""
    t = (timer - dec) & 0xFFFF
    return 0 if t > LEVEL_TIMER_MAX else t


def step_level_progression(
    game_state: int, af2c: int, level_timer_a: int, level_timer_b: int,
    timer_a_param: int, timer_b_param: int, ship_pos: int, frame_ctr: int,
) -> ProgressionResult:
    game_state &= 0xFFFF
    level_timer_a &= 0xFFFF
    level_timer_b &= 0xFFFF

    if game_state != STATE_TRANSITIONAL:
        return ProgressionResult(game_state, level_timer_a, level_timer_b,
                                 (frame_ctr + 1) & 0xFFFF)

    # --- level timers (2A45-2AA1) ---
    oxy_divisor = (OXY_RATE_MUL * (timer_b_param & 0xFFFF)) & 0xFFFF
    if oxy_divisor != 0:
        level_timer_b = _tick_down(level_timer_b, LEVEL_TIMER_MAX // oxy_divisor)
    if (timer_a_param & 0xFFFF) != 0:
        rate = LEVEL_TIMER_MAX // (timer_a_param & 0xFFFF)
        dec = _slong_div(_ulong_mul(rate, ship_pos & 0xFFFFFFFF), FUEL_SCALE_DIV) & 0xFFFF
        level_timer_a = _tick_down(level_timer_a, dec)

    # --- state transitions (2AB1-2ADC): later overrides earlier ---
    if is_landed_for_resume(af2c):
        game_state = STATE_GAMEPLAY
    if level_timer_a == 0:
        game_state = STATE_TIMER_A_EXPIRED
    if level_timer_b == 0:
        game_state = STATE_TIMER_B_EXPIRED

    return ProgressionResult(game_state, level_timer_a, level_timer_b, frame_ctr & 0xFFFF)
