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
#: address -> observed virtual-time costs. An island computes a VALUE and has no
#: cycle count, so it can only DRIVE a body whose cost it can declare. If the set
#: collapses to one value the cost is static and declarable; if it spreads, the
#: island needs a cost model before it may take over.
COSTS: "dict[str, collections.Counter]" = collections.defaultdict(collections.Counter)


def _check_04c0(mem, kw, out, compat, pre=None) -> None:
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
    COSTS['1010:04C0'][compat.get('cost')] += 1
    r = perspective_row_offset(x_lo, x_hi, depth)
    want_ax = mem.rw(ds, r.offset) if r.in_range else 0
    got_ax = out.get("ax", 0) & 0xFFFF

    if got_ax != want_ax:
        raise AssertionError(
            f"island perspective_row_offset disagrees with generated 1010:04C0 "
            f"on x=({x_hi:04X}:{x_lo:04X}) depth={depth:04X}: "
            f"ax generated={got_ax:04X} island={want_ax:04X} "
            f"(in_range={r.in_range} idx={r.idx:04X} offset={r.offset:04X})")


SEGMENT_BYTES = 0x10000


def _snap_3a96(mem, kw):
    """The whole image before the unpack.

    The destination segment is NOT predictable from the entry registers -- the
    body transforms ``bx`` before loading ``es`` from its table, so an entry-time
    guess was wrong (predicted D437, actual 57FE). Snapshotting the image instead
    of the addressing mode sidesteps that entirely: ``es`` is read from the
    OUTPUTS afterwards and both segments are taken from this copy. 3A96 runs a
    handful of times per intro, so the copy is irrelevant next to the unpack.
    """
    return bytes(mem.data)


def _check_3a96(mem, kw, out, compat, pre) -> None:
    """``1010:3A96`` -- unpack one animation segment (the intro decompressor).

    Unlike a pure lookup this island WRITES, so it is re-run against a COPY of the
    pre-state and its buffer compared byte-for-byte with what the generated body
    actually produced, plus both returned cursors.

    The island's CONTRACT text claims it unpacks "in place", but the real call is
    cross-segment: ds=1686 (the game's data segment) -> es=57FE (an allocated
    buffer). Its INTERFACE is fine -- ``rb``/``wb`` are separate callbacks, which
    is exactly ds:si -> es:di -- so only the prose was wrong, and shadow mode
    caught it on the first real call. Bound here to the two actual segments.
    """
    from skyroads.handrecovered.intro_anim import unpack_animation_segment

    # The body REPLACES ds: `bx ^= bx` then `ds = es = ss:[3702]`. So the working
    # segment is neither the caller's ds nor a second buffer -- both sides are the
    # SAME segment, exactly as the island's contract says. Bind both to it.
    seg = out.get("es", 0) & 0xFFFF
    src = pre[seg * 16:seg * 16 + SEGMENT_BYTES]
    buf = bytearray(src)
    r = unpack_animation_segment(lambda o: src[o & 0xFFFF],
                                 lambda o, v: buf.__setitem__(o & 0xFFFF, v & 0xFF))
    CALLS["1010:3A96"] += 1

    got = mem.data[seg * 16:seg * 16 + SEGMENT_BYTES]
    if bytes(buf) != bytes(got):
        first = next(i for i in range(SEGMENT_BYTES) if buf[i] != got[i])
        n = sum(1 for i in range(SEGMENT_BYTES) if buf[i] != got[i])
        raise AssertionError(
            f"island unpack_animation_segment disagrees with generated 1010:3A96 "
            f"in segment {seg:04X}: {n} bytes differ, first at +{first:04X} "
            f"(island={buf[first]:02X} generated={got[first]:02X})")
    want_si, want_di = out.get("si", 0) & 0xFFFF, out.get("di", 0) & 0xFFFF
    if (r.cursor_si, r.cursor_di) != (want_si, want_di):
        raise AssertionError(
            f"island unpack_animation_segment cursors disagree with generated "
            f"1010:3A96: island=(si={r.cursor_si:04X},di={r.cursor_di:04X}) "
            f"generated=(si={want_si:04X},di={want_di:04X})")


#: address -> checker. Every entry is a VERIFIED island being re-proven against
#: real calls before it is allowed to drive anything.
#: address -> (checker, snapshot-or-None)
SHADOWS = {
    "1010:04C0": (_check_04c0, None),
    "1010:3A96": (_check_3a96, _snap_3a96),
}



def install_all() -> list:
    """Install every shadow. Call BEFORE the corpus is imported."""
    CALLS.clear()
    for addr, (checker, snap) in SHADOWS.items():
        install_shadow(addr, checker, snap)
    return sorted(SHADOWS)


def report() -> str:
    """One line per shadow: how many real calls it agreed on."""
    if not CALLS:
        return "no shadowed island was called (wrong demo for these addresses?)"
    out = []
    for a, n in sorted(CALLS.items()):
        c = COSTS.get(a)
        cost = ""
        if c:
            top = c.most_common(4)
            cost = ("  cost=" + ("STATIC " + str(top[0][0]) if len(c) == 1
                    else f"{len(c)} distinct {dict(top)}"))
        out.append(f"{a}: {n:,} calls agreed{cost}")
    return "; ".join(out)
