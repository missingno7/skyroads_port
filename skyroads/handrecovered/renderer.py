"""SkyRoads renderer island — recovered, VM-agnostic rendering algorithms.

This module grows bottom-up into a clean reimplementation of the road/object
renderer (see docs/skyroads/run_status.md "the renderer-island plan"). Each
function here knows nothing about the CPU or memory layout; the VM-facing hooks
in skyroads/hooks.py adapt registers/memory to these pure calls and reproduce
the exact original register/flag state for the differential verifier.

First recovered layer: the fixed-point perspective transform at 1010:04C0 —
the keystone every render path calls. It maps a 32-bit horizontal coordinate
and a depth value to a word offset into the perspective table at ds:0x162C
(the caller then reads that word). The leaf rasterizers and the 32-bit
long-arithmetic helpers this sits on are already recovered/hooked.
"""
from __future__ import annotations

from typing import NamedTuple

from skyroads.islands import oracle_link

#: ds-relative base of the perspective word table read at 1010:0521.
PERSPECTIVE_TABLE_BASE = 0x162C


class PerspectiveResult(NamedTuple):
    """Everything a caller (and the exact-state hook) needs from 04C0.

    ``in_range`` False reproduces the ASM's early ``return 0`` (row index out of
    the 0..321 window). ``offset`` is the ds word-offset to read for the result.
    ``rem128``/``rem46`` are the two divide remainders the ASM leaves in DX on
    its two exit paths; ``add_lhs``/``add_rhs`` are the operands of the final
    ``add cx,ax`` whose result becomes the offset AND sets the exit flags.
    """
    in_range: bool
    idx: int
    rem128: int
    offset: int
    rem46: int
    add_lhs: int
    add_rhs: int


@oracle_link(
    boundary="1010:04C0",
    contract="perspective_row_offset(x_lo, x_hi, depth): row idx = "
             "((depth>>7) - 95) mod 2^16; if idx>=322 (unsigned) the ASM returns 0; "
             "else offset = 0x162C + low16(((x_hi:x_lo)/0x2000)/8*14) + 2*(idx/46), "
             "and the caller's AX becomes ds:[offset]. Two unsigned truncating "
             "divides (ulong_div) then an unsigned multiply (ulong_mul, by 14), "
             "in sequence; then a 16-bit /46. Only low words feed the offset.",
    status="VERIFIED",
    merge_target="skyroads.native.renderer (future)",
)
def perspective_row_offset(x_lo: int, x_hi: int, depth: int) -> PerspectiveResult:
    idx = (((depth & 0xFFFF) // 128) + 0xFFA1) & 0xFFFF
    rem128 = (depth & 0xFFFF) % 128
    if idx >= 0x142:  # unsigned compare `cmp si,0x142; jb` at 04D7 -> out of range
        return PerspectiveResult(False, idx, rem128, 0, 0, 0, 0)

    x = ((x_hi & 0xFFFF) << 16) | (x_lo & 0xFFFF)
    # Two truncating unsigned divides (04F0 /0x2000, 04FD /8) then an unsigned
    # MULTIPLY by 14 (050A calls ulong_mul at 5D4C, NOT ulong_div). Only the
    # low word of the product is consumed by the `add cx,ax` at 0510.
    q = (((x // 0x2000) // 8) * 14) & 0xFFFF
    add_lhs = (PERSPECTIVE_TABLE_BASE + q) & 0xFFFF
    add_rhs = (2 * (idx // 46)) & 0xFFFF  # `div bx(46)` then `shl ax,1` at 0519/051B
    offset = (add_lhs + add_rhs) & 0xFFFF
    return PerspectiveResult(True, idx, rem128, offset, idx % 46, add_lhs, add_rhs)


#: ds-relative bases of the two per-segment screen-bound tables road_segment_clip
#: reads (word entries, indexed by segment*2). t4C is a per-segment near/low
#: bound; t98 is a per-segment far/high bound (a flat 0x20 in the captured level).
SEG_BOUND_LOW_TABLE = 0x4C
SEG_BOUND_HIGH_TABLE = 0x98


#: ``arm`` values of :class:`SegmentClipResult` -- WHICH tail 1010:1631 ran.
#: The five selector arms are the selector value itself, so an arm doubles as
#: the ``dir_sel & 0xF00`` that chose it; the two structural arms are negative
#: so they can never collide with one.
ARM_CULLED = -1        # 1642: seg > 0x25, `mov ax,0` and straight out
ARM_DEFAULT = -2       # 172B: no selector matched, AX left holding the selector


class SegmentClipResult(NamedTuple):
    """Everything 1010:1631's caller -- and its ABI adapter -- needs.

    ``result`` is the returned AX and is the whole SEMANTIC answer; the rest is
    the structure an exact-state adapter needs and cannot re-derive without
    duplicating the decision the island just made. Nothing here is virtual time
    or a flag: the island owns the CONTROL FLOW, the adapter owns the ABI.

    ``second_test`` says whether the arm ran its SECOND comparison (the bound
    read at 1696/16BB/16E5). It is what discriminates the two costs an arm can
    have, and it also names which compare set the exit flags -- so it must come
    from here rather than being guessed from ``result``: on the 0x100 arm
    ``result == 0`` is produced by BOTH the short exit (row >= high) and the
    long one (row < low), at different costs.

    ``cmp_lhs``/``cmp_rhs`` are the operands of the LAST compare executed, whose
    flags are the ones the function returns. ``bx`` is ``seg*2`` when a bound
    was actually read (the `shl bx,1` at 1696/16BB/16D6/16E5 leaves it live) and
    None when no read happened, because BX is otherwise untouched.
    """

    result: int
    arm: int
    second_test: bool
    row: int           # DI inside the body: (coord - 0x2200) / 128, unsigned
    rem128: int        # DX at return: the same divide's remainder
    bx: "int | None"   # seg*2 if a bound table was read, else BX is unmodified
    cmp_lhs: int
    cmp_rhs: int


@oracle_link(
    boundary="1010:1631",
    contract="road_segment_clip(dir_sel, seg, coord, low_bound, high_bound): "
             "segment visibility/clip test. seg>37 -> 0. row di=((coord-0x2200)&0xFFFF)>>7 "
             "(unsigned). Switch on dir_sel&0xF00: 0x100 -> low<=di<high; "
             "0x200 -> coord<0x3200; 0x300 -> coord<0x3200 and di>=low; "
             "0x400 -> coord<0x3C00; 0x500 -> coord<0x3C00 and di>=low; "
             "else -> the selector value itself. All compares unsigned. "
             "low_bound/high_bound are ds:[0x4C+2*seg]/ds:[0x98+2*seg].",
    # Byte-exact against the generated 1010:1631 -- which is itself byte-exact
    # against the interpreted ASM oracle from cold start -- on the WHOLE
    # contract: all seven output registers, exit flags, fmask, virtual-time cost
    # and the ordered byte-write log, with NO exemptions. Two populations, and
    # the claim is exactly their union and no wider:
    #
    #  * dos_re.lift.shadow over 1,851 REAL calls -- demo_cold_20260718_003412
    #    (2) + demo_colde2e_full_20260713_144604 (1,849). MEASURED arm coverage,
    #    7 of the 10 (arm, second_test) combinations: CULLED 774, 0x300+second
    #    399, 0x100+second 372, DEFAULT 134, 0x100 short 103, 0x200 68,
    #    0x400 1. The two demos are BOTH needed: 0x400 occurs only in the cold
    #    demo, everything else only in the E2E one.
    #  * tests/test_island_bodies.py forced states for all 10 arms, 50 randomized
    #    register sets each -- which is the only evidence covering the three no
    #    demo reaches: (0x300, no-second), (0x500, no-second), (0x500, second).
    #
    # So the 0x500 arm has never run in a real playthrough; it is proven against
    # the generated body, not observed in the game.
    status="VERIFIED",
    merge_target="skyroads.native.renderer (future)",
)
def road_segment_clip(dir_sel: int, seg: int, coord: int,
                      low_bound: int, high_bound: int) -> int:
    """The pure predicate. Bounds are supplied eagerly; see
    :func:`road_segment_clip_detail` for the lazy-read, ABI-shaped variant."""
    return road_segment_clip_detail(dir_sel, seg, coord,
                                    lambda: low_bound, lambda: high_bound).result


def road_segment_clip_detail(dir_sel: int, seg: int, coord: int,
                             read_low, read_high) -> SegmentClipResult:
    """1010:1631 with its decision structure exposed, and the bounds read LAZILY.

    ``read_low``/``read_high`` are zero-argument accessors for
    ``ds:[0x4C + 2*seg]`` / ``ds:[0x98 + 2*seg]``. They are called only on the
    arms that really touch those tables, and in the ASM's own order, so a caller
    that counts memory traffic sees what the original does.
    """
    seg &= 0xFFFF
    coord &= 0xFFFF
    if seg > 0x25:  # `cmp si,0x25; ja` at 163A — >37 culled
        return SegmentClipResult(0, ARM_CULLED, False, 0, 0, None, seg, 0x25)

    # 1648: ax = coord + 0xDE00 (i.e. coord - 0x2200); xor dx,dx; div cx(0x80).
    biased = (coord + 0xDE00) & 0xFFFF
    row, rem128 = biased >> 7, biased & 0x7F
    bx = (seg << 1) & 0xFFFF
    sel = dir_sel & 0x0F00

    if sel == 0x0100:                              # 16D6: two-sided band
        high = read_high()
        if row >= high:                            # 16E0 jb not taken -> ax = 0
            return SegmentClipResult(0, sel, False, row, rem128, bx, row, high)
        low = read_low()                           # 16E5: re-`shl`, read t4C
        return SegmentClipResult(1 if row >= low else 0, sel, True,
                                 row, rem128, bx, row, low)
    if sel == 0x0200:                              # 1660
        return SegmentClipResult(1 if coord < 0x3200 else 0, sel, False,
                                 row, rem128, None, coord, 0x3200)
    if sel == 0x0300:                              # 168C
        if coord >= 0x3200:
            return SegmentClipResult(0, sel, False, row, rem128, None,
                                     coord, 0x3200)
        low = read_low()                           # 1696
        return SegmentClipResult(1 if row >= low else 0, sel, True,
                                 row, rem128, bx, row, low)
    if sel == 0x0400:                              # 1676
        return SegmentClipResult(1 if coord < 0x3C00 else 0, sel, False,
                                 row, rem128, None, coord, 0x3C00)
    if sel == 0x0500:                              # 16B1
        if coord >= 0x3C00:
            return SegmentClipResult(0, sel, False, row, rem128, None,
                                     coord, 0x3C00)
        low = read_low()                           # 16BB
        return SegmentClipResult(1 if row >= low else 0, sel, True,
                                 row, rem128, bx, row, low)
    # 172B: the ASM falls off the selector chain returning AX = the selector,
    # with the flags of the last compare in that chain (`cmp ax,0x500`).
    return SegmentClipResult(sel, ARM_DEFAULT, False, row, rem128, None,
                             sel, 0x500)


@oracle_link(
    boundary="1010:1732",
    contract="road_object_visible(persp_word, clip, x_lo, x_hi, depth, screen_y): "
             "the layer-2 per-segment cull. Projects the segment's near/far edges "
             "(depth +/- 0x700) via persp_word; a segment with a nonzero low nibble "
             "on either edge that also straddles the near screen band "
             "(screen_y<0x2800 and screen_y+0x600>0x2480) is visible (1). Otherwise "
             "cull if screen_y+0x680<=0x2800, or if both edges' 0xF00 nibble is 0. "
             "Surviving segments run a mirrored two-sided clip (1631): compute "
             "seg=23-((depth>>7 - 49) mod 46), mirror it (and the x-delta) when <=0, "
             "clip the center edge, else clip the far edge at depth+delta. All "
             "compares unsigned. persp_word(depth) = the 04C0 table word (0 if out "
             "of range); clip = road_segment_clip bound to this frame's tables.",
    # Byte-exact against the generated 1010:1732 -- itself byte-exact against
    # the interpreted ASM oracle from cold start -- on the WHOLE contract: all
    # seven output registers, exit flags, fmask, virtual-time cost and the
    # ordered byte-write log, with NO exemptions. Two populations, and the claim
    # is exactly their union and no wider:
    #
    #  * dos_re.lift.shadow over 66,144 REAL calls -- demo_cold_20260718_003412
    #    (6,878) + demo_colde2e_full_20260713_144604 (59,266). MEASURED block
    #    coverage: 15 distinct basic-block paths reaching 26 of the function's
    #    27 blocks. The two demos are BOTH needed, and on a knife edge: they
    #    share six paths, the E2E demo contributes eight of its own, and the
    #    cold demo contributes exactly ONE -- on exactly ONE call in 672 frames
    #    -- which is the only real-playthrough evidence that the SECOND 1631
    #    call can succeed (block 22 -> 24). The E2E demo takes 22 -> 23, the
    #    failing side of that same test, 907 times and never once the other.
    #  * tests/test_island_bodies.py forced states: 42 distinct block paths,
    #    27 of 27 blocks, 20 randomized register sets each.
    #
    # So block 7 (the 1797 jump) has never run in a real playthrough; it is
    # proven against the generated body, not observed in the game. A companion
    # test proves by exhaustion over all 65,536 screen_y values that every path
    # through it falls straight into the 17A5 cull, so it contributes a fixed 1
    # to the cost and can change no answer.
    status="VERIFIED",
    merge_target="skyroads.native.renderer (future)",
)
def road_object_visible(persp_word, clip, x_lo: int, x_hi: int,
                        depth: int, screen_y: int) -> int:
    """The pure 0/1 decision; see :func:`road_object_visible_detail` for the
    ABI-shaped variant that also reports the block path and the exit compare."""
    return road_object_visible_detail(persp_word, clip, x_lo, x_hi,
                                      depth, screen_y).result


class RoadObjectResult(NamedTuple):
    """1010:1732's answer plus the structure its ABI adapter cannot re-derive.

    ``path`` is the sequence of the ASM's own basic blocks, in execution order,
    named by the generated body's block indices (``bb == n`` in
    ``skyroads/recovered/func_1010_1732.py``). It exists because 1732's
    virtual-time cost is NOT a small table keyed on the answer: seven exits, a
    branch prefix that costs 1 or 4 depending on WHICH edge's low nibble was
    nonzero, and a mirror prefix worth 3, 7 or 8 multiply out to dozens of
    static totals on top of four variable callee costs. Summing the blocks the
    island actually walked is the same derivation the generated body performs,
    per call, instead of a table that has to be enumerated correctly.

    ``cmp_lhs``/``cmp_rhs`` are the operands of the LAST compare executed --
    the flags 1732 returns. Like 1631's, they must come from here: the same
    ``result`` is produced by exits whose flags differ (0 from 17A5, from 17BB
    and from 1858; 1 from 179A, from 182F and from 185B).
    """

    result: int
    path: tuple        # ASM basic blocks visited, in order
    cmp_lhs: int
    cmp_rhs: int


def road_object_visible_detail(persp_word, clip, x_lo: int, x_hi: int,
                               depth: int, screen_y: int,
                               on_segment=None) -> RoadObjectResult:
    """1010:1732 with its block-level control flow exposed.

    ``persp_word`` and ``clip`` are called exactly where -- and as often as --
    the ASM calls 04C0 and 1631, so an adapter that counts memory traffic and
    callee cost through them sees what the original does.

    ``on_segment(seg, delta, rem)`` is the same idea for a point that is not a
    call: the ASM stores the segment index and the x-delta into its own frame
    at 17EC/17F4 and, when it mirrors, stores both AGAIN at 180C/1815. Those
    four writes sit BETWEEN the center-edge projection and the first clip, so
    an adapter reproducing the ordered write log cannot emit them after the
    fact. ``rem`` is the mod-46 divide remainder the ASM leaves live in DX
    there, and it is unchanged by the mirror. Optional: the pure predicate has
    no use for it.
    """
    depth &= 0xFFFF
    screen_y &= 0xFFFF
    path = [0]                                   # 1732: prologue + both edges
    r1 = persp_word((depth + 0x700) & 0xFFFF)    # near edge (1740 add ax,0x700)
    r2 = persp_word((depth + 0xF900) & 0xFFFF)   # far edge  (1755 add ax,0xF900)

    if r1 & 0xF:                                 # 176E test [bp-6],0xF
        path.append(1)                           # 1773
        near_nibble = True
    else:
        path.append(2)                           # 1776 test [bp-4],0xF
        near_nibble = bool(r2 & 0xF)
        if not near_nibble:
            path.append(3)                       # 1781

    if near_nibble:
        path.append(4)                           # 1784 cmp di,0x2800
        if screen_y < 0x2800:
            path.append(6)                       # 178D
            edge = (screen_y + 0x600) & 0xFFFF
            if edge > 0x2480:
                path += [8, 26]                  # 179A: mov ax,1
                return RoadObjectResult(1, tuple(path), edge, 0x2480)
            path.append(7)                       # 1797
        else:
            path.append(5)                       # 178A

    path.append(9)                               # 17A0
    band = (screen_y + 0x680) & 0xFFFF
    if band <= 0x2800:                           # 17A5 ja not taken
        path += [10, 25, 26]                     # 1861: mov ax,0
        return RoadObjectResult(0, tuple(path), band, 0x2800)

    path.append(11)                              # 17AD test [bp-4],0xF00
    if r2 & 0xF00:
        path.append(12)                          # 17B8
    else:
        path.append(13)                          # 17BB test [bp-6],0xF00
        hi1 = r1 & 0xF00
        if not hi1:
            path += [14, 25, 26]                 # 17C6 -> 1861
            return RoadObjectResult(0, tuple(path), hi1, 0)

    path.append(15)                              # 17C9
    r3 = persp_word(depth)                       # 17D0 center edge
    rem = ((depth >> 7) + 0xFFCF) & 0xFFFF
    rem %= 46                                    # 17DC-17EA: (depth/128 - 49) mod 46
    seg = (0x17 - rem) & 0xFFFF                  # 17EC 23 - rem
    delta = 0xE900                               # 17F4 [bp-10]
    if on_segment is not None:
        on_segment(seg, delta, rem)              # 17EC/17F4 store both slots
    if seg > 0x7FFF:                             # 17F9 cmp [bp-8],0x7FFF ja
        path.append(16)                          # 1800
        mirror = True
    else:
        path.append(17)                          # 1803 cmp [bp-8],0
        mirror = seg == 0
        if not mirror:
            path.append(18)                      # 1809
    if mirror:
        path.append(19)                          # 180C 1-seg, neg delta
        seg = (1 - seg) & 0xFFFF
        delta = 0x1700
        if on_segment is not None:
            on_segment(seg, delta, rem)          # 180C/1815 store both again

    path.append(20)                              # 181D near/center clip
    a1 = clip(r3, seg, screen_y) & 0xFFFF
    if a1:
        path += [21, 24, 26]                     # 182F -> 185B: mov ax,1
        return RoadObjectResult(1, tuple(path), a1, 0)

    path.append(22)                              # 1832 far edge at depth+delta
    r4 = persp_word((depth + delta) & 0xFFFF)    # 1846
    a2 = clip(r4, (0x2F - seg) & 0xFFFF, screen_y) & 0xFFFF
    if a2:
        path += [24, 26]                         # 185B: mov ax,1
        return RoadObjectResult(1, tuple(path), a2, 0)
    path += [23, 25, 26]                         # 1858 -> 1861: mov ax,0
    return RoadObjectResult(0, tuple(path), a2, 0)
