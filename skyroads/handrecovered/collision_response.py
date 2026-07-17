"""SkyRoads post-move collision RESPONSE — the middle of the gameplay tail
(`1010:26EC-2A24`), recovered incrementally.

After `resolve_move` sweeps the ship to its target and clamps it against the
track, this region resolves the leftover contact: lateral wall-bump nudges, a
vertical centering scan, landing detection, and position milestones. It is
`1732`-heavy (every probe is a `renderer.road_object_visible` cull), so each
piece here takes the same ``visible(lateral32, depth, screen_y)`` predicate
`resolve_move` uses (bind it with `skyroads.handrecovered_native.collision.make_visible`).

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

from skyroads.islands import oracle_link
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


@oracle_link(
    boundary="1010:0533",
    contract="ship_fell_off(persp_word, af1c, af2c, seg_low, seg_high): the "
             "fall-off-the-road test. persp_word is the 04C0 perspective word "
             "for the ship's (lateral, af1c); if its 0xF00 nibble isn't 0x100/"
             "0x300/0x500 -> 0 (no valid segment). Else seg = 23 - "
             "((af1c/128 - 49) mod 46), mirrored (1-seg) when <=0; if seg > 0x25 "
             "-> 0. row = (af2c - 0x2200)/128 (unsigned). mid = "
             "((seg_high + seg_low) & 0xFFFF)/2 where seg_high/seg_low are "
             "ds:[0x98+2*seg]/ds:[0x4C+2*seg]. Return 1 (fell) iff row < mid.",
    status="ASM_MATCHED",  # 682/682 (E2E) + 511/511 (collision demo) death-check
    # evaluations matched -- but NO real fall occurred in either demo (both are
    # clean runs / crashes, not falls), so only the negative (didn't-fall) case
    # is exercised; the positive branch is decoded from the ASM, not yet
    # confirmed on a real death. See skyroads.handrecovered_native.gaps.FallDeathTransition.
    merge_target="skyroads.handrecovered_native.collision_response (future)",
)
def ship_fell_off(persp_word: int, af1c: int, af2c: int,
                  seg_low: int, seg_high: int) -> int:
    """The `1010:0533` pure fall predicate. ``persp_word`` is the 04C0 result;
    ``seg_low``/``seg_high`` are the per-segment clip bounds
    ``ds:[0x4C+2*seg]``/``ds:[0x98+2*seg]`` (the caller reads them once ``seg``
    is known -- see ``skyroads.handrecovered_native.collision.ship_fell_off``)."""
    if (persp_word & 0xF00) not in (0x100, 0x300, 0x500):
        return 0
    rem = (((af1c & 0xFFFF) // 128) + 0xFFCF) & 0xFFFF     # af1c/128 - 49
    rem %= 46
    seg = (0x17 - rem) & 0xFFFF                             # 23 - rem
    if seg == 0 or seg > 0x7FFF:                            # <= 0 signed -> mirror
        seg = (1 - seg) & 0xFFFF
    if seg > 0x25:
        return 0
    row = ((af2c + 0xDE00) & 0xFFFF) // 128                 # (af2c - 0x2200)/128
    mid = ((seg_high + seg_low) & 0xFFFF) // 2
    return 1 if row < mid else 0


#: The ship-fell segment index maps into these per-segment clip tables (same
#: tables road_segment_clip reads; 1010:05C2/05CB).
FELL_SEG_LOW_TABLE = 0x4C
FELL_SEG_HIGH_TABLE = 0x98


def fell_off_segment(af1c: int) -> int:
    """The mirrored segment index `ship_fell_off` uses for its table lookups
    (`1010:0576-05A1`), or -1 when out of the valid ``0..0x25`` range."""
    rem = (((af1c & 0xFFFF) // 128) + 0xFFCF) & 0xFFFF
    rem %= 46
    seg = (0x17 - rem) & 0xFFFF
    if seg == 0 or seg > 0x7FFF:
        seg = (1 - seg) & 0xFFFF
    return -1 if seg > 0x25 else seg


@oracle_link(
    boundary="1010:26EC",
    contract="lateral_wall_bump(visible, cur_lateral, tgt_lateral, af1c, "
             "tgt_af1c, af2c): only when the ship's lateral was blocked short "
             "of target (cur_lateral != tgt_lateral) AND af1c reached target "
             "(af1c == tgt_af1c) AND the target cell is blocked "
             "(visible(tgt_lateral, af1c, af2c) != 0): try af1c-0x3A0, then "
             "af1c+0x3A0; move af1c to the first UNBLOCKED one and snap "
             "tgt_lateral to cur_lateral. Returns (af1c, tgt_lateral), "
             "unchanged if no bump applies.",
    status="ASM_MATCHED",  # entry/no-bump path 682/682 (E2E demo); the active
    # down-bump branch verified on a collision demo (demo_skyroads_20260710_
    # 213019: 511/511 incl. 1 real bump). The up-bump branch (2788) is decoded
    # from the ASM but was not itself triggered by any demo sampled.
    merge_target="skyroads.handrecovered_native.collision_response (future)",
)
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


@oracle_link(
    boundary="1010:283C",
    contract="af1c_contact_fixup(af1c, tgt_af1c, cur_5496, lateral_accel, "
             "ship_pos): only when af1c != tgt_af1c (a vertical collision). "
             "Clear lateral_accel := 0. Zero cur_5496 if its sign agrees with "
             "the still-needed direction: (cur_5496 > 0 and tgt_af1c > af1c) or "
             "(cur_5496 < 0 and tgt_af1c < af1c). Brake ship_pos -= 0x97, "
             "clamped >= 0. Returns (lateral_accel, cur_5496, ship_pos); "
             "unchanged if af1c == tgt_af1c.",
    status="ASM_MATCHED",  # 682/682 (E2E, mostly no-op) + 511/511 on a collision
    # demo (demo_skyroads_20260710_213019, 4 real af1c collisions exercised).
    merge_target="skyroads.handrecovered_native.collision_response (future)",
)
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


@oracle_link(
    boundary="1010:27A3",
    contract="resolve_lateral_crash(cur_lateral, tgt_lateral, ship_pos, f456a, "
             "game_state): if cur_lateral == tgt_lateral, nothing happens. "
             "Otherwise (the ship was blocked laterally = hit a wall): "
             "ship_pos := 0 (restart to the road start); and if the ship was "
             "already past forward position 0x0E38 (signed 32-bit) AND "
             "f456a == 0, flag the crash: f456a := 1 and, if game_state == 0, "
             "game_state := 1. The ASM also fires SFX (03C2), not modelled.",
    status="ASM_MATCHED",  # 511/511 real frames on a collision demo
    # (demo_skyroads_20260710_213019) on (ship_pos, f456a, game_state) -- but
    # only 2 were actual lateral crashes (both past the gate with f456a==0);
    # the pre-gate branch (ship_pos < 0x0E38) and the already-flagged branch
    # (f456a != 0) are decoded from the ASM but not exercised by any demo
    # sampled. The 2800-2828 SFX sub-branch touches only audio, so it does not
    # affect the returned game-state fields.
    merge_target="skyroads.handrecovered_native.collision_response (future)",
)
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


@oracle_link(
    boundary="1010:28D7",
    contract="resolve_landing(scratch, tgt_af2c, af2c, bounce, af2e, af30, "
             "f455a, ship_pos): gameplay_active(bp-12) := 0. A landing resolves "
             "iff af2c != tgt_af2c AND bounce < 0 (signed; descending, off the "
             "vertical target). On a landing: clear ds:[455A], jump latch "
             "(bp-8) and effect latch (bp-6) to 0, set gameplay_active := 1, "
             "subtract the 32-bit [af30:af2e] from ship_pos and clamp to "
             "[0, 0x2AAA]. bp-10 (jump_start_y) is preserved. Otherwise nothing "
             "else changes.",
    status="ASM_MATCHED",  # 224/224 real landing frames (collision demo
    # demo_skyroads_20260710_213019) byte-exact on (bp-6, bp-8, bp-12, [455A],
    # ship_pos). The non-landing branch just leaves gameplay_active=0 and is
    # trivial by construction (28E5/28EF jmp past everything). [af2e]/[af30]
    # were nonzero in only 1/224 frames -- the ship_pos back-off is a no-op in
    # practice but faithfully applied.
    merge_target="skyroads.handrecovered_native.collision_response (future)",
)
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


@oracle_link(
    boundary="1010:2963",
    contract="vertical_center_nudge(visible, lateral, af1c, af2c, cur_5496): "
             "scan for the first UNBLOCKED cell above (af1c + k*128, k=1..14) "
             "and below (af1c - k*128) the ship, probing visible(lateral, "
             "depth, af2c-1). Net = (+1 if a clear cell found above) + (-1 if "
             "found below), so net in {-1,0,+1}. If net != 0: return "
             "(cur_5496 + net*17) & 0xFFFF; else return 0. (`visible` returns "
             "non-zero when that probe is blocked, matching road_object_visible.)",
    status="ASM_MATCHED",  # 314/314 real E2E-demo scans byte-exact on ds:[5496],
    # computing every probe through renderer.road_object_visible bound to the
    # frame's DGROUP tables. See tests/test_collision_response.py + run_status.md.
    merge_target="skyroads.handrecovered_native.collision_response (future)",
)
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
