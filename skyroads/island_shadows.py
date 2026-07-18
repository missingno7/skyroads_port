"""Islands checked against the generated bodies they claim to reproduce.

An island's ``status`` is a claim about evidence. ``ASM_MATCHED`` means "diffed on
captured cases" -- weaker than the standard the generated corpus already meets
(672 frames byte-exact, VGA plane and DAC palette, from cold start). Promoting an
island on that basis would lower the proof standard, so the ladder needs a rung
between "diffed once" and "drives the program".

Shadow mode is that rung. The generated body drives -- outputs, flags and cost are
its own, so behaviour is provably unchanged -- and the island is evaluated beside
it on every call the real game makes. Running the cold-start differential with
shadows installed turns a full playthrough into a per-call proof of the island.

Run it:  python scripts/verify_cpuless.py <demo> --shadow-islands
"""
from __future__ import annotations

import collections

from skyroads.cpuless_overrides import install_shadow

#: address -> how many real calls this shadow proved. The COUNT is the evidence:
#: "agrees on 41,000 calls across a cold playthrough" is a different claim from
#: "diffed on captured cases", which is what ASM_MATCHED means.
CALLS: "collections.Counter[str]" = collections.Counter()


def _check_04c0(mem, kw, out, compat) -> None:
    """``1010:04C0`` -- the perspective row-offset lookup (renderer).

    Arguments arrive on the STACK, not in registers: the body opens with
    ``push bp; bp = sp; ... si = [bp+8]``, so on entry the three words sit above
    the return address at ``ss:[sp+2 / +4 / +6]``.

    Only AX is a result. The body opens ``push bp; push si; push di`` and restores
    all three on exit, so the returned SI is the CALLER's, not the row index -- the
    island's ``idx`` is an internal step, not an observable. Checking SI was this
    checker's own bug, and shadow mode caught it on the first real call
    (``depth=8000``: AX agreed at 0005 while SI differed 0001 vs 00A1, which is
    exactly the signature of a callee-saved register). 04C0 only READS memory, so
    reading ``ds:[offset]`` after the call is sound.
    """
    from skyroads.handrecovered.renderer import perspective_row_offset

    ss, sp, ds = kw.get("ss", 0), kw.get("sp", 0), kw.get("ds", 0)
    x_lo = mem.rw(ss, (sp + 2) & 0xFFFF)
    x_hi = mem.rw(ss, (sp + 4) & 0xFFFF)
    depth = mem.rw(ss, (sp + 6) & 0xFFFF)

    CALLS['1010:04C0'] += 1
    r = perspective_row_offset(x_lo, x_hi, depth)
    want_ax = mem.rw(ds, r.offset) if r.in_range else 0
    got_ax = out.get("ax", 0) & 0xFFFF

    if got_ax != want_ax:
        raise AssertionError(
            f"island perspective_row_offset disagrees with generated 1010:04C0 "
            f"on x=({x_hi:04X}:{x_lo:04X}) depth={depth:04X}: "
            f"ax generated={got_ax:04X} island={want_ax:04X} "
            f"(in_range={r.in_range} idx={r.idx:04X} offset={r.offset:04X})")


#: address -> checker. Every entry is a VERIFIED island being re-proven against
#: real calls before it is allowed to drive anything.
SHADOWS = {
    "1010:04C0": _check_04c0,
}


def install_all() -> list:
    """Install every shadow. Call BEFORE the corpus is imported."""
    CALLS.clear()
    for addr, checker in SHADOWS.items():
        install_shadow(addr, checker)
    return sorted(SHADOWS)


def report() -> str:
    """One line per shadow: how many real calls it agreed on."""
    if not CALLS:
        return "no shadowed island was called (wrong demo for these addresses?)"
    return "; ".join(f"{a}: {n:,} calls agreed" for a, n in sorted(CALLS.items()))
