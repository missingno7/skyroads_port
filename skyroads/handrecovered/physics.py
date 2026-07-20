"""SkyRoads per-frame movement-TARGET computation — `1010:2635-26E6`.

This is the block ``movement.resolve_move`` (`1010:186B`) needs but does not
compute itself: the ``(tgt_lateral, tgt_af1c, tgt_af2c)`` triple swept toward
each frame. Focused oracle evidence covers all three targets; the
``af1c_base_offset`` selector remains an explicit evidence boundary below.

## The formulas

    tgt_af2c = (af2c + vvel) & 0xFFFF

`vvel` is `ds:[9336]` AS OF THE CALL SITE — i.e. after that frame's
`decay_bounce`/`update_vertical_velocity` (and, when it fires, the jump
impulse) have already run. Simple integration: the view-Y-base target is
just "where gravity/impulse says velocity will carry it this frame".

    tgt_lateral = (ship_pos + lateral) & 0xFFFFFFFF

Adding the 32-bit forward position (`ship_pos`, `ds:[54AC:54AE]`) to the
current lateral coordinate re-centers the target each frame as the (curving)
track advances — not a "how far should I turn" delta, a "where the road puts
me" recompute. **No offset term** — 0/682 mismatches across the whole replay,
including every steering sample.

    tgt_af1c_raw = af1c + slong_div(ulong_mul(lateral_accel_s16_as_s32,
                                              ship_pos + af1c_base_offset), 0x200)
                        + unknown_5496
    tgt_af1c = af1c if wrap-seam straddled else tgt_af1c_raw   (see below)

`lateral_accel` is `ds:[4568]` (`steer * 29`, already recovered as a *write*
target — see `player.RespawnState.lateral_accel`'s comment — but never
before consumed by anything recovered). The multiply/divide chain mirrors
`movement.resolve_move`'s own `_ulong_mul`/`_slong_div` helpers exactly (same
x86 `imul`-into-`idiv` truncation semantics), reused here rather than
reimplemented. `af1c_base_offset` is a SEPARATE quantity from anything in the
`tgt_lateral` formula (an earlier version of this module wrongly conflated
the two — a real ship_pos+lateral 32-bit accumulator is computed at
`1010:263C-2647` for `tgt_lateral`; `af1c_base_offset` comes from an
INDEPENDENT `ship_pos + (0 or 0x618)` computed at `1010:2650-2673`, used ONLY
as the multiply's `base` operand).

**`af1c_base_offset` is `0x0618` in all observed gameplay** — the default.
The ASM selects `0` vs `0x0618` on a stack-local `ss:[bp-16]` (`1010:2650`:
`bp-16 == 0 → +0x0618`, else `+0`), and `bp-16` was directly probed as `0`
in every one of 682 real E2E-replay calls (at the decision point itself), so
the base is always `ship_pos + 0x0618`. An earlier version of this module
reported the offset as "0 for non-steering, 0x0618 for steering" — that was a
measurement artifact: when `lateral_accel == 0` (not steering) the multiply is
`0 * base == 0` regardless of the offset, so the fixture-builder's "try 0
first" arbitrarily recorded 0 for those frames even though the real base still
had `+0x0618`. With `lateral_accel` held nonzero, only `0x0618` matches
(58/58). `bp-16` becomes nonzero (making the offset `0`) only via the
`af2c > 0x2800` + `ds:[0x228]`-table-match circuit at `1010:2340-23BF`, which
never triggered in the replay — a real but UNEXERCISED branch, documented in the
caveat below, not a blocking gap for any observed frame.

**Wrap-seam clamp** (`1010:26AA-26D7`): if `af1c` and the raw target straddle
the `[0x2F80, 0xD080)` band from opposite sides — `af1c < 0x2F80` and
`raw > 0xD080`, or the mirror — `tgt_af1c` is clamped to the CURRENT `af1c`
(no movement this axis), rather than the raw value. `af1c`'s role as a
circular/wrapping coordinate (hinted by `movement.py`'s own docstring) makes
this read as "don't let a single frame's interpolation take the long way
around the wrap", though the geometry behind the exact constants isn't
independently confirmed.

## The one unexercised branch (does not affect observed gameplay)

`af1c_base_offset` becomes `0` (instead of the default `0x0618`) when the ASM's
`ss:[bp-16]` selector is nonzero — reached only through the `af2c > 0x2800` +
`ds:[0x228 + 2*idx]`-table-match path at `1010:2340-23BF` (also entangled with
a side-effect call into `menu.dispatch_menu_action`). That path never fired in
the full E2E replay, so passing `af1c_base_offset=0` is CORRECT-but-untested; the
default `0x0618` is what every observed frame uses. A caller wiring this into a
native stepper can rely on the default and treat the selector as a documented
latent branch, not an undischarged gap.
"""
from __future__ import annotations

from typing import NamedTuple

from skyroads.handrecovered.movement import _slong_div, _ulong_mul

#: The wrap-seam band `1010:26AA-26D7` guards against crossing in one frame.
AF1C_WRAP_LOW = 0x2F80
AF1C_WRAP_HIGH = 0xD080

#: The af1c-multiply base offset used in all observed gameplay (ss:[bp-16]==0,
#: 1010:2662). The alternate value (0) is an unexercised branch -- see the
#: module docstring. This is the default `af1c_base_offset`.
AF1C_BASE_OFFSET = 0x0618


class MovementTargets(NamedTuple):
    tgt_lateral: int   # 32-bit, ds:[9618:961A]'s target
    tgt_af1c: int       # ds:[AF1C]'s target
    tgt_af2c: int       # ds:[AF2C]'s target


def compute_movement_targets(
    ship_pos: int, lateral: int, af1c: int, af2c: int, vvel: int,
    lateral_accel: int, unknown_5496: int, af1c_base_offset: int = AF1C_BASE_OFFSET,
) -> MovementTargets:
    tgt_af2c = (af2c + vvel) & 0xFFFF
    tgt_lateral = (ship_pos + lateral) & 0xFFFFFFFF

    la32 = lateral_accel & 0xFFFFFFFF
    if lateral_accel & 0x8000:
        la32 = (lateral_accel - 0x10000) & 0xFFFFFFFF
    base = (ship_pos + af1c_base_offset) & 0xFFFFFFFF
    q = _slong_div(_ulong_mul(la32, base), 0x200)
    raw_tgt_af1c = (af1c + q + unknown_5496) & 0xFFFF

    tgt_af1c = raw_tgt_af1c
    if (af1c < AF1C_WRAP_LOW and raw_tgt_af1c > AF1C_WRAP_HIGH) or \
       (raw_tgt_af1c < AF1C_WRAP_LOW and af1c > AF1C_WRAP_HIGH):
        tgt_af1c = af1c

    return MovementTargets(tgt_lateral, tgt_af1c, tgt_af2c)
