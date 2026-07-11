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
* :func:`vertical_center_nudge` — the vertical collision-depth scan
  (`1010:2963-2A24`) that maintains `ds:[5496]`, the vertical-centering term
  `physics.compute_movement_targets` adds into `tgt_af1c`.

Still to recover in this region (see docs/skyroads/vmless_roadmap.md): the
position milestones (`27A3-2800`) and the landing check that clears the jump
latch (`28DC-2901`, already mapped — see `skyroads.native.gaps.JumpGateGap`).
"""
from __future__ import annotations

from typing import Callable

from skyroads.islands import oracle_link

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


def _s16(v: int) -> int:
    v &= 0xFFFF
    return v - 0x10000 if v & 0x8000 else v


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
    merge_target="skyroads.native.collision_response (future)",
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
    merge_target="skyroads.native.collision_response (future)",
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
    merge_target="skyroads.native.collision_response (future)",
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
