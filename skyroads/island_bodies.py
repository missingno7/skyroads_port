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

from skyroads.handrecovered.renderer import (
    ARM_CULLED, ARM_DEFAULT, perspective_row_offset, road_segment_clip_detail)

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


#: address -> island-driven body. This is what may be shadowed, and -- once a
#: shadow has VERIFIED it over demos exercising every path -- what may drive.
BODIES = {"1010:04C0": func_1010_04c0,
          "1010:1631": func_1010_1631}
