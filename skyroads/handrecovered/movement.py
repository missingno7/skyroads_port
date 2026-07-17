"""SkyRoads movement + swept-collision resolver — recovered game logic (1010:186B).

This is the clean, VM-free reimplementation of the road-segment stepper that was
first captured as a mechanical lift (``skyroads/lifted/lifted_1010_186b.py``).
It is the game's **movement integrator with swept collision**: each frame it
advances the ship's position accumulators from where they are toward a requested
target, sub-stepping so the ship cannot tunnel through a block, using the
per-segment cull ``1732`` (``renderer.road_object_visible``) as the collision
predicate and binary-refining each axis to the exact contact boundary.

State it reads/writes (the movement fields; DS-relative offsets in the captured
runtime, ds == 0x1686):

    ds:0x9618  dword  lateral    lane / horizontal position (32-bit)
    ds:0xAF1C  word   af1c       depth/vertical accumulator A
    ds:0xAF2C  word   af2c       depth/vertical accumulator B (the view Y base)

The three *target* values (``tgt_lateral``, ``tgt_af1c``, ``tgt_af2c``) are the
routine's stack arguments ([bp+4:6], [bp+8], [bp+10]) — the position the caller
(the road-walk render pass) wants the ship moved to this step.

``visible(lateral32, depth, screen_y) -> int`` is the collision predicate: the
original passes ``(lateral_lo, lateral_hi, depth, screen_y)`` to ``1732`` and
treats a non-zero return as "blocked/visible". A caller wires it to
``renderer.road_object_visible`` bound to this frame's projection + clip tables.

Verified byte-exact (output accumulators AND the exact sequence of collision
probes) against the ASM/lifted 186B over the full level demo — see the
``@oracle_link`` below.
"""
from __future__ import annotations

from typing import Callable

from skyroads.islands import oracle_link

#: Movement-field DS offsets (documented; the VM hook reads/writes these).
LATERAL = 0x9618
AF1C = 0xAF1C
AF2C = 0xAF2C

_SWEEP_STEPS = 5          # forward sub-steps (si = 1..5)
_LAT_REFINE_START = 0x1000  # lateral binary-refine initial step, /16 each round
_AXIS_REFINE_STEP = 0x7D    # 125: AF1C/AF2C refine step magnitude, /5 each round


# --- x86 integer-op helpers (exact truncation/overflow semantics) -------------

def _s16(v: int) -> int:
    v &= 0xFFFF
    return v - 0x10000 if v & 0x8000 else v


def _s32(v: int) -> int:
    v &= 0xFFFFFFFF
    return v - 0x100000000 if v & 0x80000000 else v


def _truncdiv(a: int, b: int) -> int:
    """x86 ``idiv``: signed division truncating toward zero."""
    q = abs(a) // abs(b)
    return -q if (a < 0) != (b < 0) else q


def _interp16(target: int, cur: int, si: int) -> int:
    """One 16-bit interpolation sub-step: ``(target-cur)*si/5``.

    Mirrors ``imul si; cwd; idiv 5`` — the ``cwd`` overwrites the multiply's
    high word, so the product is taken as the *low 16 bits* (sign-extended)
    before the divide.
    """
    v = _s16((_s16((target - cur) & 0xFFFF) * si) & 0xFFFF)
    return _truncdiv(v, 5)


def _ulong_mul(a: int, b: int) -> int:               # 1010:5D4C
    return (a & 0xFFFFFFFF) * (b & 0xFFFFFFFF) & 0xFFFFFFFF


def _slong_div(a: int, b: int) -> int:               # 1010:5E5A
    return _truncdiv(_s32(a), b) & 0xFFFFFFFF


def _ulong_div(a: int, b: int) -> int:               # 1010:5D8C
    return ((a & 0xFFFFFFFF) // b) & 0xFFFFFFFF


@oracle_link(
    boundary="1010:186B",
    contract="resolve_move(lateral, af1c, af2c, tgt_lateral, tgt_af1c, tgt_af2c, "
             "visible): swept movement+collision. (1) early-out if already at "
             "target. (2) 5-step forward sweep si=1..5 interpolating all three "
             "axes (lateral via ulong_mul/signed_long_div by si then /5; the two "
             "16-bit axes via (d*si)/5 with the product truncated to 16 bits), "
             "calling visible at each; stop at the first blocked step. (3) commit "
             "the interpolation at (si-1)/5. (4) binary-refine lateral: step "
             "0x1000, advance while there's room (unsigned d>=step) and visible "
             "clears, /16 each round to 0. (5) refine af1c then af2c toward their "
             "targets: step +/-125 in the target's direction, advance while "
             "|dist|>=|step| and visible clears, /5 each round to 0. Returns the "
             "new (lateral, af1c, af2c). All compares/divides match x86 "
             "unsigned/signed + truncate-toward-zero semantics.",
    status="ASM_MATCHED",  # 1760/1760 full-demo calls: outputs AND probe sequence exact
    merge_target="skyroads.native.movement (future)",
)
def resolve_move(lateral: int, af1c: int, af2c: int,
                 tgt_lateral: int, tgt_af1c: int, tgt_af2c: int,
                 visible: Callable[[int, int, int], int]) -> tuple[int, int, int]:
    """Advance (lateral, af1c, af2c) toward the target, resolving collisions.

    ``visible(lateral32, depth, screen_y)`` returns non-zero when that probe
    position is blocked (the ASM's ``1732`` cull). Returns the new
    ``(lateral, af1c, af2c)`` (32-bit lateral, 16-bit af1c/af2c).
    """
    lat = lateral & 0xFFFFFFFF
    af1c &= 0xFFFF
    af2c &= 0xFFFF
    tgt_lateral &= 0xFFFFFFFF
    tgt_af1c &= 0xFFFF
    tgt_af2c &= 0xFFFF

    # (1) early-out — nothing to do if already at the requested target.
    if lat == tgt_lateral and af1c == tgt_af1c and af2c == tgt_af2c:
        return lat, af1c, af2c

    # (2) forward sweep: find the first sub-step that is blocked.
    si = 1
    while si <= _SWEEP_STEPS:
        af2c_i = (_interp16(tgt_af2c, af2c, si) + af2c) & 0xFFFF
        af1c_i = (_interp16(tgt_af1c, af1c, si) + af1c) & 0xFFFF
        d = (tgt_lateral - lat) & 0xFFFFFFFF
        lat_i = (_slong_div(_ulong_mul(d, si), 5) + lat) & 0xFFFFFFFF
        if visible(lat_i, af1c_i, af2c_i):
            break
        si += 1

    # (3) commit the interpolation at the last safe sub-step (si-1)/5.
    k = si - 1
    d = (tgt_lateral - lat) & 0xFFFFFFFF
    lat = (lat + _slong_div(_ulong_mul(d, k), 5)) & 0xFFFFFFFF
    af1c = (af1c + _interp16(tgt_af1c, af1c, k)) & 0xFFFF
    af2c = (af2c + _interp16(tgt_af2c, af2c, k)) & 0xFFFF

    # (4) binary-refine the lateral axis to the contact boundary.
    step = _LAT_REFINE_START
    while step != 0:
        while (tgt_lateral - lat) & 0xFFFFFFFF >= step \
                and not visible((lat + step) & 0xFFFFFFFF, af1c, af2c):
            lat = (lat + step) & 0xFFFFFFFF
        step = _ulong_div(step, 16)

    # (5) refine af1c then af2c toward their targets.
    af1c = _refine_axis(af1c, tgt_af1c,
                        lambda v: visible(lat, v & 0xFFFF, af2c))
    af2c = _refine_axis(af2c, tgt_af2c,
                        lambda v: visible(lat, af1c, v & 0xFFFF))
    return lat, af1c, af2c


def _refine_axis(cur: int, target: int, blocked: Callable[[int], int]) -> int:
    """Advance a 16-bit axis toward ``target`` (step +/-125, /5 each round),
    stopping short of the first blocked position. ``blocked(cur+step)`` is the
    collision probe.

    The initial direction uses an UNSIGNED compare (``cmp [bp+8],ax; ja`` at
    1A18/1AB0) — it differs from a signed compare only when ``cur`` and
    ``target`` straddle 0x8000, which does happen for these depth accumulators.
    """
    di = _AXIS_REFINE_STEP if (target & 0xFFFF) > (cur & 0xFFFF) else -_AXIS_REFINE_STEP
    while di != 0:
        while abs(_s16((target - cur) & 0xFFFF)) >= abs(di) \
                and not blocked((cur + di) & 0xFFFF):
            cur = (cur + di) & 0xFFFF
        di = _truncdiv(di, 5)
    return cur
