"""SkyRoads post-move collision RESPONSE — the middle of the gameplay tail
(`1010:26EC-2A24`), recovered incrementally.

After `resolve_move` sweeps the ship to its target and clamps it against the
track, this region resolves the leftover contact: lateral wall-bump nudges, a
vertical centering scan, landing detection, and position milestones. It is
`1732`-heavy (every probe is a `renderer.road_object_visible` cull), so each
piece here takes the same ``visible(lateral32, depth, screen_y)`` predicate
`resolve_move` uses (bind it with `skyroads.native.collision.make_visible`).

Recovered so far:

* :func:`lateral_wall_bump` — the lateral wall-bump (`1010:26EC-27A0`) that
  nudges `ds:[AF1C]` ±0x3A0 to slip past a blocked target cell;
* :func:`af1c_contact_fixup` — the vertical-contact fix-up (`1010:283C-28AE`)
  that brakes on an `af1c` collision (clears `lateral_accel`, conditionally
  zeroes `ds:[5496]`, backs `ship_pos` off by 0x97);
* :func:`resolve_landing` — the landing check (`1010:28D7-295D`) that clears
  the jump latch (`bp-8`) and the effect latch (`bp-6`), sets the
  gameplay-active flag (`bp-12`), and backs `ship_pos` off by `[AF30:AF2E]`;
* :func:`resolve_lateral_crash` — the lateral-collision handler
  (`1010:27A3-2830`) that restarts the ship (`ship_pos := 0`) and, past a
  distance gate, flags the crash (`ds:[456A]`/`ds:[456E]`);
* :func:`vertical_center_nudge` — the vertical collision-depth scan
  (`1010:2963-2A24`) that maintains `ds:[5496]`, the vertical-centering term
  `physics.compute_movement_targets` adds into `tgt_af1c`.

That covers the whole `26EC-2A24` collision-response region.
"""
from __future__ import annotations

from typing import Callable, NamedTuple

from skyroads.handrecovered.dynamics import JumpScratch
from skyroads.handrecovered.player import LEVEL_END

#: The scan probes up to this many cells each way (1010:29B5/2A01 `cmp bp,0x0E`).
SCAN_MAX_CELLS = 14
#: Per-cell depth step: `bp-22 << 7` (1010:2986 `shl ax,7`).
SCAN_CELL_STEP = 128
#: `ds:[5496]` moves by this per net clear side (1010:2A13 `imul bp-34,17`).
CENTER_NUDGE = 17
#: The wall-bump moves `ds:[AF1C]` by this to slip past a blocked cell (1010:274B/2788).
LATERAL_BUMP_STEP = 0x3A0
#: `ds:[54AC]` is braked by this on an af1c contact (1010:287E `sub [54AC],0x97`).
CONTACT_BRAKE = 0x97
#: A lateral crash flags the run only once the ship is past this forward
#: position (1010:27CA `cmp [54AC],0x0E38`).
CRASH_MILESTONE_POS = 0x0E38


def _s16(v: int) -> int:
    v &= 0xFFFF
    return v - 0x10000 if v & 0x8000 else v


def ship_fell_off(persp_word: int, af1c: int, af2c: int,
                  seg_low: int, seg_high: int) -> int:
    """The `1010:0533` pure fall predicate. ``persp_word`` is the 04C0 result;
    ``seg_low``/``seg_high`` are the per-segment clip bounds
    ``ds:[0x4C+2*seg]``/``ds:[0x98+2*seg]`` (the caller reads them once ``seg``
    is known -- see ``skyroads.native.collision.ship_fell_off``)."""
    return ship_fell_off_detail(persp_word, af1c, af2c,
                                lambda _s: seg_low, lambda _s: seg_high).result


#: The ship-fell segment index maps into these per-segment clip tables (same
#: tables road_segment_clip reads; 1010:05C2/05CB).
FELL_SEG_LOW_TABLE = 0x4C
FELL_SEG_HIGH_TABLE = 0x98

#: ``arm`` values of :class:`FellOffResult` -- WHERE 1010:0533 left the function.
FELL_ARM_NO_SEGMENT = 0    # 05EC: the 0xF00 nibble is not 0x100/0x300/0x500
FELL_ARM_SEG_CULLED = 1    # 05AA -> 05EC: the mirrored segment exceeded 0x25
FELL_ARM_DECIDED = 2       # 05AD: the row-vs-midpoint comparison actually ran

#: ``mirror`` values -- HOW 23-rem reached the `1 - seg` fix-up at 059B, which
#: is reached from two different compares and so costs two different amounts.
MIRROR_NONE = 0            # 0592 `cmp [bp-4],0` non-zero: straight to 05A4
MIRROR_NEGATIVE = 1        # 058F: `cmp [bp-4],0x7FFF` above -> seg was negative
MIRROR_ZERO = 2            # 0592: `cmp [bp-4],0` equal -> seg was exactly 0


class FellOffResult(NamedTuple):
    """1010:0533's answer plus the structure its ABI adapter cannot re-derive.

    ``result`` is the whole semantic answer -- 1 iff the ship fell. Everything
    else exists because the generated body leaves live, observable state that
    depends on WHICH way it got there, and re-deriving that in the adapter would
    duplicate the decision this function just made.

    ``arm`` and ``mirror`` together pick the virtual-time cost and the set of
    stack slots written. ``rem46``, ``row`` and ``parity`` are the three divide
    residues left in registers at the various exits; ``cmp_lhs``/``cmp_rhs`` are
    the operands of the LAST compare, whose flags the function returns.

    ``seg`` is the post-mirror segment index; it is None on the no-segment arm,
    which exits before the segment is ever computed.
    """

    result: int
    arm: int
    mirror: int
    nibble: int          # persp_word & 0xF00
    seg: "int | None"    # post-mirror index, and BX/2 on the decided arm
    rem46: int           # DX after 0576's `div 46`
    row: "int | None"    # CX on the decided arm: (af2c - 0x2200) / 128
    mid: "int | None"    # AX on the decided arm: (high + low) / 2
    parity: "int | None"  # DX on the decided arm: (high + low) & 1
    cmp_lhs: int
    cmp_rhs: int


def ship_fell_off_detail(persp_word: int, af1c: int, af2c: int,
                         read_low, read_high) -> FellOffResult:
    """:func:`ship_fell_off` with its decision structure exposed and the two
    bound-table words read LAZILY, only on the arm that reaches 05AD.

    ``read_low(seg)``/``read_high(seg)`` are accessors for
    ``ds:[0x4C + 2*seg]``/``ds:[0x98 + 2*seg]``. They take the segment index as
    an argument rather than closing over it because -- unlike 1631's -- it is
    computed HERE, three compares into the function.
    """
    nibble = persp_word & 0x0F00
    if nibble not in (0x0100, 0x0300, 0x0500):
        # 0562 `cmp [bp-2],0x500` is the last compare on the way out, whichever
        # of the three tests failed -- the chain falls through it.
        return FellOffResult(0, FELL_ARM_NO_SEGMENT, MIRROR_NONE, nibble, None,
                             0, None, None, None, nibble, 0x0500)

    rem46 = ((((af1c & 0xFFFF) // 128) + 0xFFCF) & 0xFFFF) % 46
    seg = (0x17 - rem46) & 0xFFFF
    if seg > 0x7FFF:                      # 058F: above 0x7FFF -> negative
        mirror, seg = MIRROR_NEGATIVE, (1 - seg) & 0xFFFF
    elif seg == 0:                        # 0592: exactly zero
        mirror, seg = MIRROR_ZERO, 1
    else:
        mirror = MIRROR_NONE

    if seg > 0x25:                        # 05A4 `cmp [bp-4],0x25`, above -> out
        return FellOffResult(0, FELL_ARM_SEG_CULLED, mirror, nibble, seg,
                             rem46, None, None, None, seg, 0x25)

    row = ((af2c + 0xDE00) & 0xFFFF) // 128
    total = (read_high(seg) + read_low(seg)) & 0xFFFF  # 05C2 then 05CB, in order
    mid, parity = total // 2, total & 1
    return FellOffResult(1 if row < mid else 0, FELL_ARM_DECIDED, mirror, nibble,
                         seg, rem46, row, mid, parity, row, mid)


def fell_off_segment(af1c: int) -> int:
    """The mirrored segment index `ship_fell_off` uses for its table lookups
    (`1010:0576-05A1`), or -1 when out of the valid ``0..0x25`` range."""
    rem = (((af1c & 0xFFFF) // 128) + 0xFFCF) & 0xFFFF
    rem %= 46
    seg = (0x17 - rem) & 0xFFFF
    if seg == 0 or seg > 0x7FFF:
        seg = (1 - seg) & 0xFFFF
    return -1 if seg > 0x25 else seg


def lateral_wall_bump(
    visible: Callable[[int, int, int], int],
    cur_lateral: int, tgt_lateral: int, af1c: int, tgt_af1c: int, af2c: int,
) -> tuple[int, int]:
    """The `1010:26EC-27A0` lateral wall-bump. Returns ``(af1c, tgt_lateral)``.

    ``visible`` returns non-zero when a probe is blocked (road_object_visible).
    On a bump the ASM also plays an SFX (`03C2(2)`), not modelled here.
    """
    af1c &= 0xFFFF
    cur_lateral &= 0xFFFFFFFF
    tgt_lateral &= 0xFFFFFFFF
    if cur_lateral != tgt_lateral and af1c == (tgt_af1c & 0xFFFF):
        if visible(tgt_lateral, af1c, af2c) != 0:                 # target blocked
            down = (af1c - LATERAL_BUMP_STEP) & 0xFFFF
            if visible(tgt_lateral, down, af2c) == 0:             # 274B: slip down
                return down, cur_lateral
            up = (af1c + LATERAL_BUMP_STEP) & 0xFFFF
            if visible(tgt_lateral, up, af2c) == 0:               # 2788: slip up
                return up, cur_lateral
    return af1c, tgt_lateral


def af1c_contact_fixup(
    af1c: int, tgt_af1c: int, cur_5496: int, lateral_accel: int, ship_pos: int,
) -> tuple[int, int, int]:
    """The `1010:283C-28AE` af1c-collision brake. Returns
    ``(lateral_accel, cur_5496, ship_pos)``."""
    af1c &= 0xFFFF
    tgt_af1c &= 0xFFFF
    if af1c == tgt_af1c:                                          # 2845: no collision
        return lateral_accel & 0xFFFF, cur_5496 & 0xFFFF, ship_pos & 0xFFFFFFFF
    lateral_accel = 0                                             # 2848
    s = _s16(cur_5496)
    if (s > 0 and tgt_af1c > af1c) or (s < 0 and tgt_af1c < af1c):
        cur_5496 = 0                                              # 2878
    pos = (ship_pos - CONTACT_BRAKE) & 0xFFFFFFFF                 # 287E
    if pos & 0x80000000:                                         # clamp >= 0
        pos = 0
    return lateral_accel, cur_5496 & 0xFFFF, pos


class LateralCrashResult(NamedTuple):
    ship_pos: int      # ds:[54AC:54AE] -- reset to 0 (restart) on any lateral crash
    f456a: int         # ds:[456A]
    game_state: int    # ds:[456E]
    crashed: bool


def resolve_lateral_crash(
    cur_lateral: int, tgt_lateral: int, ship_pos: int, f456a: int, game_state: int,
) -> LateralCrashResult:
    """The `1010:27A3-2830` lateral-collision (wall-crash) handler."""
    if (cur_lateral & 0xFFFFFFFF) == (tgt_lateral & 0xFFFFFFFF):
        return LateralCrashResult(ship_pos & 0xFFFFFFFF, f456a & 0xFFFF,
                                  game_state & 0xFFFF, False)
    hi = _s16((ship_pos >> 16) & 0xFFFF)
    lo = ship_pos & 0xFFFF
    past_gate = hi > 0 or (hi == 0 and lo >= CRASH_MILESTONE_POS)
    if past_gate and (f456a & 0xFFFF) == 0:
        f456a = 1
        if (game_state & 0xFFFF) == 0:
            game_state = 1
    return LateralCrashResult(0, f456a & 0xFFFF, game_state & 0xFFFF, True)


class LandingResult(NamedTuple):
    scratch: JumpScratch    # bp-6 (effect) and bp-8 (jump latch) cleared on landing
    gameplay_active: int    # bp-12: 0 normally, 1 the frame a landing resolves
    f455a: int              # ds:[455A] (cleared to 0 on landing)
    ship_pos: int           # ds:[54AC:54AE]
    landed: bool


def resolve_landing(
    scratch: JumpScratch, tgt_af2c: int, af2c: int, bounce: int,
    af2e: int, af30: int, f455a: int, ship_pos: int,
) -> LandingResult:
    """The `1010:28D7-295D` landing check. Returns a :class:`LandingResult`."""
    if (af2c & 0xFFFF) != (tgt_af2c & 0xFFFF) and _s16(bounce) < 0:
        pos = (ship_pos - ((af2e & 0xFFFF) | ((af30 & 0xFFFF) << 16))) & 0xFFFFFFFF
        if pos & 0x80000000:
            pos = 0
        elif pos > LEVEL_END:
            pos = LEVEL_END
        return LandingResult(
            JumpScratch(0, scratch.jump_start_y, 0), 1, 0, pos, True)
    return LandingResult(scratch, 0, f455a & 0xFFFF, ship_pos & 0xFFFFFFFF, False)


def vertical_center_nudge(
    visible: Callable[[int, int, int], int],
    lateral: int, af1c: int, af2c: int, cur_5496: int,
) -> int:
    """Return the new ``ds:[5496]`` after the vertical centering scan.

    NOTE the enclosing ASM block also zeroes ``ds:[AF2E]`` and ``ds:[AF30]``
    (1010:2963-2969) before scanning; a native stepper reproducing the whole
    block must do that too -- it is not part of this pure nudge computation.
    """
    af1c &= 0xFFFF
    screen_y = (af2c - 1) & 0xFFFF
    net = 0
    for k in range(1, SCAN_MAX_CELLS + 1):                 # upward (2983-29BB)
        if visible(lateral, (af1c + k * SCAN_CELL_STEP) & 0xFFFF, screen_y) == 0:
            net += 1
            break
    for k in range(1, SCAN_MAX_CELLS + 1):                 # downward (29CD-2A07)
        if visible(lateral, (af1c - k * SCAN_CELL_STEP) & 0xFFFF, screen_y) == 0:
            net -= 1
            break
    if net != 0:
        return (cur_5496 + net * CENTER_NUDGE) & 0xFFFF    # 2A13 imul + add
    return 0                                                # 2A1E [5496] := 0
