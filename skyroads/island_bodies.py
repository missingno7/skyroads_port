"""Island-driven bodies: hand-recovered logic wearing the generated signature.

An island (``skyroads/handrecovered/``) is pure: it computes a VALUE and knows
nothing about registers, flags, virtual time or the machine stack. A generated
body's callers consume all of that. This module is the adapter between them, and
it is deliberately the ONLY place the two meet -- the islands stay pure and the
generated corpus stays untouched.

A body here is a drop-in for its generated counterpart: same signature, same
``(outputs, _compat)`` pair, same memory effects. That shape is what lets
:mod:`dos_re.lift.shadow` prove it against the generated body on every real call
the game makes, and it means the artifact under proof is the artifact that
ships. Nothing here may be installed on evidence weaker than that.

WHY THE STACK WRITES ARE HERE, spelled out because they look like noise:

    The generated ``1010:04C0`` pushes 3 words on entry and, on the in-range
    path, 15 more as call arguments; its two callees push 3 and 1 more. All of
    that lands BELOW the final SP and stays in memory after the return. It is
    residue, not result -- but it is OBSERVABLE, and a body that skipped it
    would leave the machine in a different state than the original does. The
    shadow's memory comparison is what proves each of these 25 words, so they
    are reproduced rather than argued away.

    Reproducing the CALLEES' prologue saves (04C0's own instructions do not make
    them) is the one thing here that reaches past the address being replaced. It
    is sound only because both callees are pure leaf arithmetic on this path and
    their entire memory footprint is that prologue -- verified, not assumed: the
    shadow compares the byte-write log in order, so any missed or spurious write
    is a MISMATCH.
"""
from __future__ import annotations

from skyroads.handrecovered.blit import stencil_blit_steps
from skyroads.handrecovered.collision_response import (
    FELL_ARM_DECIDED, FELL_ARM_NO_SEGMENT, FELL_ARM_SEG_CULLED, MIRROR_NEGATIVE,
    MIRROR_NONE, MIRROR_ZERO, ship_fell_off_detail)
from skyroads.handrecovered.renderer import (
    ARM_CULLED, ARM_DEFAULT, perspective_row_offset, road_object_visible_detail,
    road_segment_clip_detail)

#: parity of the low byte, as the emitter computes it (PF is set from bits 0-7).
_PARITY = tuple((1 - bin(v).count('1') % 2) == 1 for v in range(256))

#: Flag bits, and the mask 04C0 reports on BOTH paths.
_CF, _PF, _AF, _ZF, _SF, _OF = 0x1, 0x4, 0x10, 0x40, 0x80, 0x800

#: MEASURED, not assumed (tools/measure_04c0.py, and re-proven on every shadowed
#: call): block 0 contributes 0x8D5 and the in-range tail adds 0xC5 | 0x800,
#: both subsets of it. The callees at 5D8C/5D4C contribute 0x8C5 -- also a subset
#: -- because on the paths 04C0 actually drives them neither ever sets AF and
#: neither touches DF or IF. So the mask is the same constant on both paths, and
#: the "unknown callee fmask contribution" turns out to widen nothing.
FMASK = 0x8D5
#: the callees' own contribution, kept separate so the claim above is checkable.
CALLEE_FMASK = 0x8C5

#: virtual-time cost, discriminated by ``in_range`` -- the ONLY thing that varies.
#: out-of-range: 12 (prologue+range test) + 1 + 2 (ax=0) + 4 (epilogue).
#: in-range:     12 + 34 (the tail) + 4, plus 21+21+12 for the three callee calls.
#: Do NOT derive this from one demo: two of the four recorded demos never take
#: the short path at all, so a cost model fitted to them is the constant 104 and
#: is silently wrong everywhere else.
COST_OUT_OF_RANGE = 19
COST_IN_RANGE = 104

#: ds-relative base of the perspective word table (the island owns this constant).
_TABLE = 0x162C


def _add16(a: int, b: int):
    """``add r16, r16`` -- value and the six flags it writes, emitter-identical."""
    t = a + b
    return (t & 0xFFFF,
            (_CF if t > 0xFFFF else 0)
            | (_PF if _PARITY[t & 0xFF] else 0)
            | (_AF if (a ^ b ^ t) & 0x10 else 0)
            | (_ZF if (t & 0xFFFF) == 0 else 0)
            | (_SF if t & 0x8000 else 0)
            | (_OF if (~(a ^ b) & (a ^ t) & 0x8000) else 0))


def _sub16(a: int, b: int):
    """``cmp``/``sub r16, r16`` -- the difference and the six flags."""
    t = a - b
    return (t & 0xFFFF,
            (_CF if t < 0 else 0)
            | (_PF if _PARITY[t & 0xFF] else 0)
            | (_AF if (a ^ b ^ t) & 0x10 else 0)
            | (_ZF if (t & 0xFFFF) == 0 else 0)
            | (_SF if t & 0x8000 else 0)
            | (_OF if ((a ^ b) & (a ^ t) & 0x8000) else 0))


def func_1010_04c0(mem, *, bp=0, bx=0, di=0, ds=0, dx=0, si=0, sp=0, ss=0):
    """``1010:04C0`` perspective_row_offset, driven by the island.

    Arguments arrive on the STACK: the body opens ``push bp; mov bp,sp`` and then
    reads ``[bp+4] [bp+6] [bp+8]``, which relative to the ENTRY sp are
    ``+2 +4 +6`` -- x_lo, x_hi, depth.

    BP, SI and DI are CALLEE-SAVED: pushed on entry, popped on exit, on both
    paths. So the SI this returns is the caller's, not the row index the island
    computes; ``idx`` is an internal step and never an observable. (Asserting SI
    was a previous checker's own bug, caught by shadow mode on its first real
    call: AX agreed while SI differed 0001 vs 00A1, the signature of a
    callee-saved register.)
    """
    x_lo = mem.rw(ss, (sp + 2) & 0xFFFF)
    x_hi = mem.rw(ss, (sp + 4) & 0xFFFF)
    depth = mem.rw(ss, (sp + 6) & 0xFFFF)

    r = perspective_row_offset(x_lo, x_hi, depth)

    # `push bp; mov bp,sp; push si; push di` -- the frame, and the three saves
    # whose words outlive the call.
    frame = (sp - 2) & 0xFFFF          # BP inside the body; also the saved-BP slot
    mem.ww(ss, frame, bp)
    mem.ww(ss, (sp - 4) & 0xFFFF, si)
    mem.ww(ss, (sp - 6) & 0xFFFF, di)

    if not r.in_range:
        # 04DD/0529: `mov ax,0` and out. Flags are still the ones `cmp si,0x142`
        # left, and CX still holds the 0x80 divisor from the depth divide.
        _, flags = _sub16(r.idx, 0x142)
        return ({'ax': 0, 'bp': bp & 0xFFFF, 'bx': bx & 0xFFFF, 'cx': 0x80,
                 'di': di & 0xFFFF, 'dx': r.rem128, 'si': si & 0xFFFF},
                {'flags': flags & FMASK, 'fmask': FMASK, 'cost': COST_OUT_OF_RANGE})

    # --- the in-range tail: three C-runtime calls, then the table read ---------
    # Each call pushes 4 argument words plus a return address at the SAME five
    # slots (SP is restored by `add sp,10` between them), so only the LAST set
    # survives -- but every set is written, and the shadow compares them in order.
    x = ((x_hi & 0xFFFF) << 16) | (x_lo & 0xFFFF)
    q1 = x // 0x2000                                   # 5D8C #1: ulong / 0x2000
    q2 = q1 // 8                                       # 5D8C #2: ulong / 8
    prod = (q2 & 0xFFFF) * 0xE                         # 5D4C:   ulong * 14

    def _args(w8, w10, w12, w14, retaddr, saves):
        """One call's five argument words, then the callee's own prologue saves.

        ``saves`` are the words 5D8C (bp, bx, si) or 5D4C (bp) push before doing
        any arithmetic. They are the callees' instructions, not 04C0's, and they
        are reproduced here only because they are observable residue -- see the
        module docstring.
        """
        for delta, val in ((8, w8), (10, w10), (12, w12), (14, w14), (16, retaddr)):
            mem.ww(ss, (sp - delta) & 0xFFFF, val & 0xFFFF)
        for i, val in enumerate(saves):
            mem.ww(ss, (sp - 18 - 2 * i) & 0xFFFF, val & 0xFFFF)

    # 04E0: push dx(0), ax(0x2000), bx(x_hi), cx(x_lo), ret 04F3 -> 5D8C
    _args(0x0000, 0x2000, x_hi, x_lo, 0x04F3, (frame, x_hi, r.idx))
    # 04F3: push bx(0), cx(8), dx(q1>>16), ax(q1&FFFF), ret 0500 -> 5D8C
    _args(0x0000, 0x0008, (q1 >> 16) & 0xFFFF, q1 & 0xFFFF, 0x0500, (frame, 0, r.idx))
    # 0500: push bx(0), cx(14), dx(q2>>16), ax(q2&FFFF), ret 050D -> 5D4C
    _args(0x0000, 0x000E, (q2 >> 16) & 0xFFFF, q2 & 0xFFFF, 0x050D, (frame,))

    # 050D..0521: `add cx,ax` (twice, around the /46 and `shl ax,1`), then
    # `mov bx,cx; mov ax,[bx]`. The SECOND add is what sets the exit flags, and
    # the island hands over exactly its two operands.
    offset, flags = _add16(r.add_lhs, r.add_rhs)
    assert offset == r.offset, "island offset disagrees with its own add operands"
    return ({'ax': mem.rw(ds, offset), 'bp': bp & 0xFFFF, 'bx': offset, 'cx': offset,
             'di': di & 0xFFFF, 'dx': r.rem46, 'si': si & 0xFFFF},
            {'flags': flags & FMASK, 'fmask': FMASK, 'cost': COST_IN_RANGE})


# --- 1010:1631 road_segment_clip ---------------------------------------------
#
#: ds-relative bases of the two per-segment bound tables (76 = 0x4C low,
#: 152 = 0x98 high), indexed by ``seg*2``.
_T_LOW, _T_HIGH = 76, 152

#: Virtual-time cost per (arm, second_test). DERIVED BY SUMMING the generated
#: body's own per-block ``_cost += n`` along each path -- the generated body is
#: the authority, not a fit to observed data -- and then CONFIRMED against it:
#: the union of the two demos produced exactly {12, 30, 31, 34, 36, 37} and no
#: value outside this table, and every one of those is re-proven on every
#: shadowed call. Two arms are absent from that set and their entries are
#: therefore derivation only, flagged here rather than left to look measured:
#: the 0x500 arm (both costs) and, indistinguishably, ARM_DEFAULT -- whose 31
#: collides with the 0x100 short exit, so an observed 31 does not witness it.
CLIP_COST = {
    (ARM_CULLED, False): 12,     # 1642: `mov ax,0` straight out
    (ARM_DEFAULT, False): 31,    # 172B: fell off the selector chain  [DERIVED]
    (0x0100, False): 31,         # 16D6 -> 16E0 jb not taken
    (0x0100, True): 36,          # 16D6 -> 16E5 second bound read
    (0x0200, False): 30,         # 1660
    (0x0300, False): 32,         # 168C, coord >= 0x3200        [DERIVED]
    (0x0300, True): 37,          # 168C -> 1696
    (0x0400, False): 34,         # 1676
    (0x0500, False): 36,         # 16B1, coord >= 0x3C00        [DERIVED]
    (0x0500, True): 41,          # 16B1 -> 16BB                 [DERIVED]
}

#: Every path accumulates exactly the same six-flag mask (no block contributes
#: DF or IF, and the `shl bx,1` arms contribute 0x8C5, a strict subset).
CLIP_FMASK = 0x8D5

#: The divisor left in CX by the row divide -- live at return on every arm but
#: ARM_CULLED, which exits before `mov cx,0x80` at 164E.
CLIP_DIVISOR = 0x80


def func_1010_1631(mem, *, bp=0, bx=0, cx=0, di=0, ds=0, dx=0, si=0, sp=0, ss=0):
    """``1010:1631`` road_segment_clip, driven by the island.

    Arguments are on the stack: the body opens ``push bp; mov bp,sp; sub sp,2``
    and reads ``[bp+4] [bp+6] [bp+8]`` -- relative to the ENTRY sp, ``+2 +4 +6``
    -- dir_sel, seg, coord.

    BP, SI and DI are CALLEE-SAVED (pushed at 1631/1637/1638, popped at 172E),
    so the row this computes in DI and the seg it holds in SI are internal steps
    and never observables. CX and DX are NOT saved: the row divide leaves the
    divisor and the remainder live, and both are returned -- except on the culled
    arm, which exits before the divide runs at all.

    The one local slot (``sub sp,2`` at 1635) is never stored to on any path, so
    unlike 04C0 this body's entire memory footprint is the three pushes.
    """
    dir_sel = mem.rw(ss, (sp + 2) & 0xFFFF)
    seg = mem.rw(ss, (sp + 4) & 0xFFFF)
    coord = mem.rw(ss, (sp + 6) & 0xFFFF)

    r = road_segment_clip_detail(
        dir_sel, seg, coord,
        lambda: mem.rw(ds, (((seg << 1) & 0xFFFF) + _T_LOW) & 0xFFFF),
        lambda: mem.rw(ds, (((seg << 1) & 0xFFFF) + _T_HIGH) & 0xFFFF))

    # `push bp; mov bp,sp; sub sp,2; push si; push di` -- the local slot at
    # sp-4 is skipped because nothing ever writes it.
    mem.ww(ss, (sp - 2) & 0xFFFF, bp)
    mem.ww(ss, (sp - 6) & 0xFFFF, si)
    mem.ww(ss, (sp - 8) & 0xFFFF, di)

    _, flags = _sub16(r.cmp_lhs & 0xFFFF, r.cmp_rhs & 0xFFFF)
    culled = r.arm == ARM_CULLED
    return ({'ax': r.result & 0xFFFF, 'bp': bp & 0xFFFF,
             'bx': bx & 0xFFFF if r.bx is None else r.bx,
             'cx': cx & 0xFFFF if culled else CLIP_DIVISOR,
             'di': di & 0xFFFF, 'dx': dx & 0xFFFF if culled else r.rem128,
             'si': si & 0xFFFF},
            {'flags': flags & CLIP_FMASK, 'fmask': CLIP_FMASK,
             'cost': CLIP_COST[(r.arm, r.second_test)]})


# --- 1010:0533 ship_fell_off -------------------------------------------------
#
# The one body here that CALLS another address. 0533 opens by pushing its three
# arguments straight back out and calling 04C0, and everything after that is
# conditioned on 04C0's answer -- so the candidate cannot avoid the call.
#
# It calls the GENERATED 04C0 rather than resolving whatever is installed. Both
# are correct (the island 04C0 is proven byte-identical to it, which is why it
# drives at all) but only this one is INDEPENDENT of what else is installed: if
# the candidate resolved through the module the way the generated body does,
# then with 04C0 shadowed too every 0533 call would run 04C0's comparison twice
# and inflate its recorded call count. An evidence counter that depends on which
# other shadows happen to be installed is not a counter worth reading.

#: Cost of the two arms that exit before the segment is computed, keyed by
#: whether 04C0 came back in range. Both are ``c04c0 + 23``.
_FELL_NO_SEGMENT = {19: 42, 104: 127}

#: What the selector chain costs before reaching 0576, per matched nibble:
#: 0x100 takes one test, 0x300 two, 0x500 three.
_FELL_NIBBLE_COST = {0x0100: 1, 0x0300: 3, 0x0500: 4}

#: What the mirror fix-up costs, by how 059B was reached.
_FELL_MIRROR_COST = {MIRROR_NONE: 3, MIRROR_NEGATIVE: 4, MIRROR_ZERO: 5}

#: Fixed remainder of each arm past the mirror: the 0576 block, the 05A4 test,
#: and the tail. DECIDED adds 05AD's 18 and its 7-cost two-way finish; the
#: culled arm adds 05AA/05EC instead. Both are 04C0-in-range only, because the
#: out-of-range answer is AX = 0 and 0 & 0xF00 matches no selector.
_FELL_ARM_COST = {FELL_ARM_DECIDED: 104 + 12 + 13 + 2 + 18 + 7,
                  FELL_ARM_SEG_CULLED: 104 + 12 + 13 + 2 + 1 + 2 + 4}

#: Same six-flag mask as every other body here; 0533 contributes 0x8D5 directly
#: and 04C0 folds in its own 0x8D5, so the union does not widen.
FELL_FMASK = 0x8D5

#: ds-relative bases of the per-segment bound tables, indexed by ``seg*2``.
_FELL_T_LOW, _FELL_T_HIGH = 76, 152

_GEN_CACHE: dict = {}


def _gen(addr: str):
    """The GENERATED body for ``addr``, resolved on first use.

    Lazily, because importing the corpus at this module's import time would pull
    it in before ``install_overrides`` has run -- and this module is imported by
    the registry that does the installing.
    """
    fn = _GEN_CACHE.get(addr)
    if fn is None:
        from dos_re.lift.standalone import generated
        fn = _GEN_CACHE[addr] = generated("skyroads.recovered", addr)
    return fn


def _gen_04c0():
    return _gen("1010:04C0")


def _gen_1631():
    return _gen("1010:1631")

#: The two divisors 0533 leaves in CX at its earlier exits (0x2E after the
#: segment divide at 0583; 04C0's own 0x80 if the function never got that far).
_FELL_CX_AFTER_SEG = 0x2E


def func_1010_0533(mem, *, bp=0, bx=0, di=0, ds=0, dx=0, si=0, sp=0, ss=0):
    """``1010:0533`` ship_fell_off, driven by the island.

    Four stack arguments at ``[bp+4..bp+10]`` -- relative to the ENTRY sp,
    ``+2 +4 +6 +8`` -- x_lo, x_hi, af1c, af2c. The first three are pushed
    straight back out as 04C0's arguments (that is what 0533 opens with), and
    af1c doubles as the depth 04C0 projects.

    BP, SI and DI are callee-saved. CX and DX are not: each divide leaves its
    divisor and remainder live, and WHICH divide ran last is what the island's
    ``arm`` and ``mirror`` fields decide.
    """
    x_lo = mem.rw(ss, (sp + 2) & 0xFFFF)
    x_hi = mem.rw(ss, (sp + 4) & 0xFFFF)
    af1c = mem.rw(ss, (sp + 6) & 0xFFFF)
    af2c = mem.rw(ss, (sp + 8) & 0xFFFF)

    # `push bp; mov bp,sp; sub sp,6; push si; push di`, then 04C0's three
    # argument words and the return address -- seven words, in this order.
    frame = (sp - 2) & 0xFFFF
    mem.ww(ss, frame, bp)
    mem.ww(ss, (sp - 10) & 0xFFFF, si)
    mem.ww(ss, (sp - 12) & 0xFFFF, di)
    for delta, val in ((14, af1c), (16, x_hi), (18, x_lo), (20, 0x0545)):
        mem.ww(ss, (sp - delta) & 0xFFFF, val)

    inner_sp = (sp - 20) & 0xFFFF
    o4, c4 = _gen_04c0()(mem, bp=frame, bx=bx, di=di, ds=ds, dx=dx, si=si,
                         sp=inner_sp, ss=ss)
    persp = o4['ax']

    # 054B `and ax,0xF00` then `mov [bp-2],ax` -- the nibble is STORED, and that
    # store is observable residue like any other.
    nibble = persp & 0x0F00
    mem.ww(ss, (sp - 4) & 0xFFFF, nibble)

    r = ship_fell_off_detail(
        persp, af1c, af2c,
        lambda s: mem.rw(ds, (((s << 1) & 0xFFFF) + _FELL_T_LOW) & 0xFFFF),
        lambda s: mem.rw(ds, (((s << 1) & 0xFFFF) + _FELL_T_HIGH) & 0xFFFF))

    if r.arm == FELL_ARM_NO_SEGMENT:
        _, flags = _sub16(nibble, 0x0500)
        return ({'ax': 0, 'bp': bp & 0xFFFF, 'bx': o4['bx'], 'cx': o4['cx'],
                 'di': di & 0xFFFF, 'dx': o4['dx'], 'si': si & 0xFFFF},
                {'flags': flags & FELL_FMASK, 'fmask': FELL_FMASK,
                 'cost': _FELL_NO_SEGMENT[c4['cost']]})

    # 0576 stores 23-rem; 059B stores the mirrored value OVER it, so on a
    # mirrored path the slot is written twice and both writes are in the log.
    mem.ww(ss, (sp - 6) & 0xFFFF, (0x17 - r.rem46) & 0xFFFF)
    if r.mirror != MIRROR_NONE:
        mem.ww(ss, (sp - 6) & 0xFFFF, r.seg)

    cost = (_FELL_ARM_COST[r.arm] + _FELL_NIBBLE_COST[r.nibble]
            + _FELL_MIRROR_COST[r.mirror])
    if r.arm == FELL_ARM_SEG_CULLED:
        _, flags = _sub16(r.seg, 0x25)
        return ({'ax': 0, 'bp': bp & 0xFFFF, 'bx': o4['bx'],
                 'cx': _FELL_CX_AFTER_SEG, 'di': di & 0xFFFF, 'dx': r.rem46,
                 'si': si & 0xFFFF},
                {'flags': flags & FELL_FMASK, 'fmask': FELL_FMASK, 'cost': cost})

    # 05AD: the row divide stores its quotient at [bp-6]; AX ends holding the
    # midpoint, CX the row, DX the sum's low bit, BX the doubled segment index.
    mem.ww(ss, (sp - 8) & 0xFFFF, r.row)
    _, flags = _sub16(r.row, r.mid)
    return ({'ax': r.result, 'bp': bp & 0xFFFF, 'bx': (r.seg << 1) & 0xFFFF,
             'cx': r.row, 'di': di & 0xFFFF, 'dx': r.parity,
             'si': si & 0xFFFF},
            {'flags': flags & FELL_FMASK, 'fmask': FELL_FMASK, 'cost': cost})


# --- 1010:1732 road_object_visible -------------------------------------------
#
# The compound one: up to FOUR 04C0 calls and TWO 1631 calls, and 27 basic
# blocks. Both callees are reached through :func:`_gen`, for the reason spelled
# out above 0533 -- an evidence counter that moves depending on which OTHER
# shadows happen to be installed is not a counter worth reading.

#: Per-block virtual-time cost, DERIVED by reading the generated body's own
#: ``_cost += n`` at the tail of each ``bb == n`` arm, and summed over the
#: blocks the island reports it walked. This is a per-call derivation rather
#: than a table keyed on the answer because 1732 has SEVEN exits, a prefix
#: worth 1 or 4 depending on which edge's low nibble was nonzero, and a mirror
#: prefix worth 3, 7 or 8 -- dozens of static totals, on top of four variable
#: callee costs. Callee cost is deliberately absent here and added from what
#: each call actually reports.
OBJ_BLOCK_COST = {0: 25, 1: 1, 2: 4, 3: 1, 4: 2, 5: 1, 6: 4, 7: 1, 8: 2,
                  9: 4, 10: 1, 11: 4, 12: 1, 13: 4, 14: 1, 15: 20, 16: 1,
                  17: 2, 18: 1, 19: 6, 20: 7, 21: 1, 22: 16, 23: 1, 24: 2,
                  25: 2, 26: 4}

#: Every block that touches flags contributes exactly 0x8D5, block 0 is on
#: every path, and both callees contribute 0x8D5 -- so the mask is this
#: constant on all seven exits and the callees widen nothing.
OBJ_FMASK = 0x8D5

#: What block 15 leaves in CX and DX: the mod-46 divisor and its remainder.
#: They are the registers the FIRST 1631 call receives, and nothing between
#: 17EA and 181D touches either.
_OBJ_SEG_DIVISOR = 0x2E

#: Return addresses pushed for the first three 04C0 calls (1740, 1755, 17C9).
_OBJ_PERSP_RET = (0x174D, 0x1762, 0x17D3)


def func_1010_1732(mem, *, bp=0, bx=0, di=0, ds=0, dx=0, si=0, sp=0, ss=0):
    """``1010:1732`` road_object_visible, driven by the island.

    Four stack arguments at ``[bp+4..bp+10]`` -- relative to the ENTRY sp,
    ``+2 +4 +6 +8`` -- x_lo, x_hi, depth (kept in SI), screen_y (kept in DI).

    THE FRAME, because every write below is an offset into it. ``push bp`` then
    ``sub sp,10`` then ``push si; push di`` gives, from the entry SP: -2 saved
    BP, -4 the center-edge word [bp-2], -6 the far edge [bp-4], -8 the near
    edge [bp-6], -10 the segment index [bp-8], -12 the x-delta [bp-10], -14
    saved SI, -16 saved DI, and the call area from -18 down.

    BP, SI and DI are CALLEE-SAVED (pushed at 1732/1736/1737, popped at 1867),
    so the depth and screen_y this works in are internal and never observables.
    BX, CX and DX are not saved and 1732 never writes BX at all: all three are
    simply whatever the LAST callee left live, except in block 15, whose two
    divides set CX and DX -- and those are consumed by the 1631 call that
    always follows, so they never reach an exit.
    """
    x_lo = mem.rw(ss, (sp + 2) & 0xFFFF)
    x_hi = mem.rw(ss, (sp + 4) & 0xFFFF)
    depth = mem.rw(ss, (sp + 6) & 0xFFFF)
    screen_y = mem.rw(ss, (sp + 8) & 0xFFFF)

    frame = (sp - 2) & 0xFFFF
    mem.ww(ss, frame, bp)
    mem.ww(ss, (sp - 14) & 0xFFFF, si)
    mem.ww(ss, (sp - 16) & 0xFFFF, di)

    # CX has no entry value: the generated body's local is unbound until the
    # first 04C0 call assigns it, and that call precedes every read.
    st = {"bx": bx & 0xFFFF, "cx": 0, "dx": dx & 0xFFFF, "cost": 0,
          "seg": 0, "persp": 0, "clips": 0}

    def _push(*words):
        """``push`` each word at its entry-SP-relative slot, in ASM order."""
        for delta, val in words:
            mem.ww(ss, (sp - delta) & 0xFFFF, val & 0xFFFF)

    def _persp(arg_depth):
        """One ``call 04C0``: its argument words, the call, then its result slot."""
        st["persp"] += 1
        n = st["persp"]
        if n <= 3:
            # 1740/1755/17C9: push depth-arg, [bp+6], [bp+4], return address.
            _push((18, arg_depth), (20, x_hi), (22, x_lo),
                  (24, _OBJ_PERSP_RET[n - 1]))
            inner = (sp - 24) & 0xFFFF
        else:
            # 1832: block 22 pushes DI and 0x2F-seg FIRST -- they are the second
            # 1631 call's arguments and outlive this one -- so 04C0's own frame
            # sits four words deeper.
            _push((18, screen_y), (20, (0x2F - st["seg"]) & 0xFFFF),
                  (22, arg_depth), (24, x_hi), (26, x_lo), (28, 0x1849))
            inner = (sp - 28) & 0xFFFF
        o, c = _gen_04c0()(mem, bp=frame, bx=st["bx"], di=screen_y, ds=ds,
                           dx=st["dx"], si=depth, sp=inner, ss=ss)
        st["bx"], st["cx"], st["dx"] = o["bx"], o["cx"], o["dx"]
        st["cost"] += c["cost"]
        if n <= 3:
            # 174D/1762/17D3 store the word at [bp-6]/[bp-4]/[bp-2]; the fourth
            # call's AX is pushed straight back out and never lands in a slot.
            mem.ww(ss, (sp - (8, 6, 4)[n - 1]) & 0xFFFF, o["ax"])
        return o["ax"]

    def _on_segment(seg, delta, rem):
        """17EC/17F4 (and again 180C/1815): the two frame slots, then DX/CX."""
        st["seg"] = seg
        mem.ww(ss, (sp - 10) & 0xFFFF, seg & 0xFFFF)
        mem.ww(ss, (sp - 12) & 0xFFFF, delta & 0xFFFF)
        st["cx"], st["dx"] = _OBJ_SEG_DIVISOR, rem & 0xFFFF

    def _clip(dir_sel, seg, coord):
        """One ``call 1631``: its argument words, then the call."""
        st["clips"] += 1
        if st["clips"] == 1:
            _push((18, coord), (20, seg), (22, dir_sel), (24, 0x1827))  # 181D
        else:
            # 1849: DI and 0x2F-seg were pushed back in block 22, so only the
            # projected word and the return address go down here.
            _push((22, dir_sel), (24, 0x1850))
        o, c = _gen_1631()(mem, bp=frame, bx=st["bx"], cx=st["cx"], di=screen_y,
                           ds=ds, dx=st["dx"], si=depth,
                           sp=(sp - 24) & 0xFFFF, ss=ss)
        st["bx"], st["cx"], st["dx"] = o["bx"], o["cx"], o["dx"]
        st["cost"] += c["cost"]
        return o["ax"]

    r = road_object_visible_detail(_persp, _clip, x_lo, x_hi, depth, screen_y,
                                   on_segment=_on_segment)

    _, flags = _sub16(r.cmp_lhs & 0xFFFF, r.cmp_rhs & 0xFFFF)
    cost = st["cost"] + sum(OBJ_BLOCK_COST[b] for b in r.path)
    return ({'ax': r.result & 0xFFFF, 'bp': bp & 0xFFFF, 'bx': st["bx"],
             'cx': st["cx"], 'di': di & 0xFFFF, 'dx': st["dx"],
             'si': si & 0xFFFF},
            {'flags': flags & OBJ_FMASK, 'fmask': OBJ_FMASK, 'cost': cost})


# --- 1010:0F62 stencil_blit ---------------------------------------------------
#
# The first LOOP absorbed here, and the first body with a hidden compat input:
# ``_df`` is the caller's direction flag, and it steers BOTH pointers. So the
# island is fed a generator that walks the source in DF order and the adapter
# writes the destination the same way -- and it does so in lockstep, one byte
# at a time, because the source and destination are unrelated caller-chosen far
# pointers and nothing here can prove them disjoint.

def _or8(b: int) -> int:
    """``or al,al`` -- five flags. AF is NOT among them: ``or`` leaves it
    alone, which is why the exit AF can belong to an earlier byte."""
    return ((_PF if _PARITY[b & 0xFF] else 0)
            | (_ZF if (b & 0xFF) == 0 else 0)
            | (_SF if b & 0x80 else 0))


def _cmp8(b: int) -> int:
    """``cmp al,1`` -- all six, at BYTE width."""
    t = b - 1
    return ((_CF if t < 0 else 0)
            | (_PF if _PARITY[t & 0xFF] else 0)
            | (_AF if (b ^ 1 ^ t) & 0x10 else 0)
            | (_ZF if (t & 0xFF) == 0 else 0)
            | (_SF if t & 0x80 else 0)
            | (_OF if ((b ^ 1) & (b ^ t) & 0x80) else 0))


#: ds-relative pointer to the destination SEGMENT, read once at 0F68.
_STENCIL_ES_PTR = 0xAF2A

#: Virtual-time cost, DERIVED by summing the generated body's own per-block
#: ``_cost +=``: 8 for the prologue, 5 for the epilogue, and per source byte
#: 0F75(3) + 0F84(2), plus 0F7A(3) for a nonzero byte and 0F81(1) for a byte
#: that is neither 0 nor 1. So the cost is a linear function of the source's
#: byte census -- not a table -- which is why this island has to report the
#: census rather than just the mapped bytes.
STENCIL_COST_FIXED = 13
STENCIL_COST_ZERO, STENCIL_COST_ONE, STENCIL_COST_OTHER = 5, 8, 9

#: 0F62/0F75 contribute this; ``cmp al,1`` at 0F7A adds AF on top. So unlike
#: every other body here the mask is NOT constant: a source of nothing but
#: zeros never runs the compare and reports 0x8C5.
STENCIL_FMASK = 0x8C5
STENCIL_FMASK_COMPARED = 0x8D5


def func_1010_0f62(mem, *, _df=0, ax=0, bp=0, di=0, ds=0, si=0, sp=0, ss=0):
    """``1010:0F62`` stencil_blit, driven by the island.

    Five stack arguments at ``[bp+4..bp+12]`` -- relative to the ENTRY sp,
    ``+2 +4 +6 +8 +10`` -- the source far pointer (offset, segment), the byte
    count, and the two colour words. The destination is ``ds:[0xAF2A]:0``,
    read once at entry through the CALLER's ds, before ``lds si,[bp+4]``
    replaces it.

    BP, SI, DI and DS are all CALLEE-SAVED (pushed at 0F62/0F66/0F67/0F6E,
    popped at 0F87), so the source cursor and the destination cursor are
    internal and never observables -- SI is NOT the final read position, which
    is the first thing that looks true here and is not. CX is 0 on every exit:
    that is ``loop``'s own postcondition. Only AX, CX and ES change at all.

    A count of 0 means 65,536 iterations, not none: ``loop`` decrements FIRST.
    """
    src_off = mem.rw(ss, (sp + 2) & 0xFFFF)
    src_seg = mem.rw(ss, (sp + 4) & 0xFFFF)
    count = mem.rw(ss, (sp + 6) & 0xFFFF)
    template = mem.rw(ss, (sp + 8) & 0xFFFF)
    other = mem.rw(ss, (sp + 10) & 0xFFFF)
    es = mem.rw(ds, _STENCIL_ES_PTR)             # 0F68, through the CALLER's ds

    # `push bp; mov bp,sp; push si; push di` ... `push ds`. The ds push is
    # AFTER the es load and the `xor di,di`, and it saves the caller's ds --
    # `lds si,[bp+4]` has not run yet.
    mem.ww(ss, (sp - 2) & 0xFFFF, bp)
    mem.ww(ss, (sp - 4) & 0xFFFF, si)
    mem.ww(ss, (sp - 6) & 0xFFFF, di)
    mem.ww(ss, (sp - 8) & 0xFFFF, ds)

    step = -1 if _df else 1
    n = count or 0x10000

    def _source():
        """``lodsb`` -- pulled one byte at a time, so each read follows the
        previous byte's ``stosb`` exactly as the original interleaves them."""
        off = src_off
        for _ in range(n):
            yield mem.rb(src_seg, off & 0xFFFF)
            off = (off + step) & 0xFFFF

    dest = 0
    cost = STENCIL_COST_FIXED
    last = 0
    last_compared = None
    for s in stencil_blit_steps(_source(), template, other, ax):
        mem.wb(es, dest & 0xFFFF, s.value)       # 0F84 stosb
        dest = (dest + step) & 0xFFFF
        ax = s.ax
        last = s.byte
        if s.compared:
            last_compared = s.byte
            cost += STENCIL_COST_ONE if s.byte == 1 else STENCIL_COST_OTHER
        else:
            cost += STENCIL_COST_ZERO

    # The exit flags are the LAST byte's own comparison -- `or al,al` for a
    # zero byte, `cmp al,1` otherwise -- except AF, which the `or` does not
    # write and which therefore still belongs to the last NONZERO byte.
    flags = _cmp8(last) if last else _or8(last)
    fmask = STENCIL_FMASK
    if last_compared is not None:
        fmask = STENCIL_FMASK_COMPARED
        if not last:
            flags |= _cmp8(last_compared) & _AF
    return ({'ax': ax & 0xFFFF, 'bp': bp & 0xFFFF, 'cx': 0, 'di': di & 0xFFFF,
             'ds': ds & 0xFFFF, 'es': es & 0xFFFF, 'si': si & 0xFFFF},
            {'flags': flags & fmask, 'fmask': fmask, 'cost': cost})


#: address -> island-driven body. This is what may be shadowed, and -- once a
#: shadow has VERIFIED it over demos exercising every path -- what may drive.
BODIES = {"1010:04C0": func_1010_04c0,
          "1010:1631": func_1010_1631,
          "1010:0533": func_1010_0533,
          "1010:1732": func_1010_1732,
          "1010:0F62": func_1010_0f62}
