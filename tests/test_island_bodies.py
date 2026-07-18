"""The island-driven bodies, differentialled against the generated ones they replace.

This is the OFFLINE half of the evidence for absorption; the online half is
``verify_cpuless <demo> --shadow-islands``, which re-proves the same body against
every call a real playthrough makes. Both compare the FULL contract -- all seven
outputs, flags, fmask, cost, and the ordered byte-write log -- because a
comparison that quietly covers less is indistinguishable from one that does not.

The states are randomized but SEEDED, so a failure is reproducible, and the two
paths are forced in equal measure. That last part is load-bearing: two of the
four recorded demos never take 04C0's short path at all, so evidence gathered
only from a demo can be blind to half the function while looking complete.
"""
from __future__ import annotations

import random

import pytest

from skyroads.island_bodies import BODIES, CALLEE_FMASK, FMASK
from skyroads.island_bodies import func_1010_04c0 as ISLAND_04C0
from skyroads.recovered.func_1010_04c0 import func_1010_04c0 as GEN_04C0
from skyroads.recovered.func_1010_5d4c import func_1010_5d4c
from skyroads.recovered.func_1010_5d8c import func_1010_5d8c

OUTPUTS = ("ax", "bp", "bx", "cx", "di", "dx", "si")
#: depth/128 - 95 must land in 0..321 for the in-range path.
_IN_LO, _IN_HI = 95 * 128, (95 + 322) * 128


class Mem:
    """Byte memory that LOGS every write, width-normalised, in order."""

    def __init__(self):
        self.data = bytearray(0x100000)
        self.log = []

    def _lin(self, seg, off):
        return ((((seg & 0xFFFF) << 4) + (off & 0xFFFF)) % len(self.data))

    def rb(self, seg, off):
        return self.data[self._lin(seg, off)]

    def rw(self, seg, off):
        return self.rb(seg, off) | (self.rb(seg, (off + 1) & 0xFFFF) << 8)

    def wb(self, seg, off, val):
        self.data[self._lin(seg, off)] = val & 0xFF
        self.log.append((self._lin(seg, off), val & 0xFF))

    def ww(self, seg, off, val):
        self.wb(seg, off, val & 0xFF)
        self.wb(seg, (off + 1) & 0xFFFF, (val >> 8) & 0xFF)


def _state(rng, path):
    ss, ds = rng.randrange(0x1000, 0x8000), rng.randrange(0x1000, 0x8000)
    regs = dict(bp=rng.randrange(0x10000), bx=rng.randrange(0x10000),
                di=rng.randrange(0x10000), dx=rng.randrange(0x10000),
                si=rng.randrange(0x10000), ds=ds, ss=ss,
                sp=rng.randrange(0x0100, 0xFF00) & ~1)
    depth = (rng.randrange(_IN_LO, _IN_HI) if path == "in"
             else rng.randrange(0, _IN_LO) if path == "out"
             else rng.randrange(0x10000))
    args = (rng.randrange(0x10000), rng.randrange(0x10000), depth)   # x_lo, x_hi, depth
    seed = bytes(rng.randrange(256) for _ in range(0x400))

    def build():
        m = Mem()
        m.data[ds * 16:ds * 16 + len(seed)] = seed   # non-zero table for the ds read
        for i, w in enumerate(args):
            m.ww(ss, (regs["sp"] + 2 + 2 * i) & 0xFFFF, w)
        m.log.clear()
        return m

    return regs, args, build


def _both(regs, build):
    g = build()
    got_g = GEN_04C0(g, **regs)
    i = build()
    got_i = ISLAND_04C0(i, **regs)
    return (got_g, g.log), (got_i, i.log)


@pytest.mark.parametrize("path", ["in", "out", "any"])
def test_04c0_island_body_reproduces_the_full_generated_contract(path):
    rng = random.Random(0xC0DE ^ hash(path) & 0xFFFF)
    for _ in range(400):
        regs, args, build = _state(rng, path)
        ((go, gc), glog), ((io, ic), ilog) = _both(regs, build)
        ctx = (f"x={args[1]:04X}:{args[0]:04X} depth={args[2]:04X} "
               + " ".join(f"{k}={v:04X}" for k, v in sorted(regs.items())))
        assert set(io) == set(go), f"output SET differs; {ctx}"
        for name in OUTPUTS:
            assert io[name] == go[name], (
                f"output {name}: generated={go[name]:04X} island={io[name]:04X}; {ctx}")
        for name in ("flags", "fmask", "cost"):
            assert ic[name] == gc[name], (
                f"compat {name}: generated={gc[name]:#x} island={ic[name]:#x}; {ctx}")
        assert ilog == glog, (
            f"the {len(glog)}-byte stack residue differs -- the words the body "
            f"leaves BELOW the returned SP are observable; {ctx}")


def test_both_paths_are_actually_reached_by_the_random_states():
    """Without this the test above could pass while exercising one path only --
    the exact blindness that makes a spine-demo-derived cost model look sound."""
    rng = random.Random(1234)
    costs = set()
    for _ in range(400):
        regs, _args, build = _state(rng, "any")
        costs.add(GEN_04C0(build(), **regs)[1]["cost"])
    assert costs == {19, 104}, f"expected both paths, saw costs {costs}"


def test_cost_is_two_valued_and_discriminated_by_in_range():
    """The island computes ``in_range``; that IS the cost discriminant, and it is
    the whole reason 04C0 can declare a virtual-time cost at all."""
    from skyroads.handrecovered.renderer import perspective_row_offset

    rng = random.Random(99)
    for _ in range(300):
        regs, args, build = _state(rng, "any")
        cost = GEN_04C0(build(), **regs)[1]["cost"]
        r = perspective_row_offset(*args)
        assert cost == (104 if r.in_range else 19), (
            f"cost {cost} does not follow in_range={r.in_range}")


def test_the_callee_fmask_contribution_is_measured_not_assumed():
    """The handoff called this the one genuinely unknown quantity in 04C0's
    contract. On the frames 04C0 actually builds, 5D8C's divisor high word is
    always 0 and 5D4C's multiplicand high word is always 0, so both take their
    short path, neither ever writes AF, and neither touches DF or IF. The union
    is 0x8C5 -- a strict subset of the 0x8D5 the caller reports -- so the callees
    widen the mask by nothing."""
    rng = random.Random(7)
    ss, sp = 0x2000, 0x8000
    union, costs = 0, {}
    for _ in range(300):
        lo, hi = rng.randrange(0x10000), rng.randrange(0x10000)
        for divisor in (0x2000, 0x8):
            m = Mem()
            m.ww(ss, sp - 14, lo)          # [bp+4]  low word
            m.ww(ss, sp - 12, hi)          # [bp+6]  high word
            m.ww(ss, sp - 10, divisor)     # [bp+8]  divisor low
            m.ww(ss, sp - 8, 0)            # [bp+10] divisor high -- ALWAYS 0 here
            _o, c = func_1010_5d8c(m, bp=sp - 2, bx=hi, dx=0, si=0x100,
                                   sp=(sp - 16) & 0xFFFF, ss=ss)
            union |= c["fmask"]
            costs.setdefault("5D8C", set()).add(c["cost"])
        m = Mem()
        m.ww(ss, sp - 14, lo)
        m.ww(ss, sp - 12, 0)               # high word of x//0x10000 is always 0
        m.ww(ss, sp - 10, 0xE)
        m.ww(ss, sp - 8, 0)
        _o, c = func_1010_5d4c(m, bp=sp - 2, cx=0xE, sp=(sp - 16) & 0xFFFF, ss=ss)
        union |= c["fmask"]
        costs.setdefault("5D4C", set()).add(c["cost"])

    assert union == CALLEE_FMASK == 0x8C5
    assert union & FMASK == union, "a callee widened 04C0's reported fmask"
    assert costs == {"5D8C": {21}, "5D4C": {12}}
    assert 12 + 34 + 4 + 21 + 21 + 12 == 104, "the in-range cost must add up"


def test_every_declared_body_matches_its_generated_signature():
    """A drop-in that is not actually a drop-in fails at the first real call."""
    import importlib
    import inspect

    for addr, body in BODIES.items():
        seg, off = addr.split(":")
        name = f"func_{seg.lower()}_{off.lower()}"
        gen = getattr(importlib.import_module(f"skyroads.recovered.{name}"), name)
        assert inspect.signature(body) == inspect.signature(gen), (
            f"{addr}: island body signature differs from the generated one")


# --- 1010:1631 road_segment_clip ---------------------------------------------
#
# The demos leave three of the ten (arm, second_test) combinations UNTOUCHED --
# (0x300, False), (0x500, False) and (0x500, True) -- so no amount of replay
# proves them. These forced states do, against the same authority and with the
# same total comparison, which is what lets the body drive without its
# unexercised arms being taken on trust.

from skyroads.island_bodies import CLIP_COST                # noqa: E402
from skyroads.island_bodies import func_1010_1631 as ISLAND_1631     # noqa: E402
from skyroads.recovered.func_1010_1631 import func_1010_1631 as GEN_1631  # noqa: E402
from skyroads.handrecovered.renderer import (                        # noqa: E402
    ARM_CULLED, ARM_DEFAULT, road_segment_clip_detail)

#: (arm, second_test) -> a (dir_sel, seg, coord, low, high) that FORCES it.
#: row = ((coord + 0xDE00) & 0xFFFF) >> 7, so coord 0x2200 + 128*r gives row r.
_CLIP_CASES = {
    (ARM_CULLED, False):  (0x0100, 0x0026, 0x3000, 0x0000, 0x0040),
    (ARM_DEFAULT, False): (0x0700, 0x0010, 0x3000, 0x0000, 0x0040),
    (0x0100, False):      (0x0100, 0x0010, 0x2200 + 128 * 20, 0x0005, 0x0010),
    (0x0100, True):       (0x0100, 0x0010, 0x2200 + 128 * 20, 0x0005, 0x0040),
    (0x0200, False):      (0x0200, 0x0010, 0x3000, 0x0000, 0x0040),
    (0x0300, False):      (0x0300, 0x0010, 0x3300, 0x0000, 0x0040),
    (0x0300, True):       (0x0300, 0x0010, 0x3100, 0x0005, 0x0040),
    (0x0400, False):      (0x0400, 0x0010, 0x3000, 0x0000, 0x0040),
    (0x0500, False):      (0x0500, 0x0010, 0x3D00, 0x0000, 0x0040),
    (0x0500, True):       (0x0500, 0x0010, 0x3B00, 0x0005, 0x0040),
}
_CLIP_T_LOW, _CLIP_T_HIGH = 76, 152


def _clip_state(rng, case):
    """A full pre-state that forces ``case``; registers are randomized so a body
    that leaked an input into the wrong output cannot hide behind a zero."""
    dir_sel, seg, coord, low, high = case
    ss, ds = rng.randrange(0x1000, 0x8000), rng.randrange(0x1000, 0x8000)
    regs = dict(bp=rng.randrange(0x10000), bx=rng.randrange(0x10000),
                cx=rng.randrange(0x10000), di=rng.randrange(0x10000),
                dx=rng.randrange(0x10000), si=rng.randrange(0x10000),
                ds=ds, ss=ss, sp=rng.randrange(0x0100, 0xFF00) & ~1)

    def build():
        m = Mem()
        for i, w in enumerate((dir_sel, seg, coord)):
            m.ww(ss, (regs["sp"] + 2 + 2 * i) & 0xFFFF, w)
        bx = (seg << 1) & 0xFFFF
        m.ww(ds, (bx + _CLIP_T_LOW) & 0xFFFF, low)
        m.ww(ds, (bx + _CLIP_T_HIGH) & 0xFFFF, high)
        m.log.clear()
        return m

    return regs, build


@pytest.mark.parametrize("case", sorted(_CLIP_CASES, key=str))
def test_1631_island_body_reproduces_the_full_contract_on_every_arm(case):
    rng = random.Random(0x1631 ^ (hash(case) & 0xFFFF))
    for _ in range(50):
        regs, build = _clip_state(rng, _CLIP_CASES[case])
        g = build()
        go, gc = GEN_1631(g, **regs)
        i = build()
        io, ic = ISLAND_1631(i, **regs)
        ctx = (f"case={case} " + " ".join(f"{k}={v:04X}"
                                          for k, v in sorted(regs.items())))
        assert set(io) == set(go), f"output SET differs; {ctx}"
        for name in OUTPUTS:
            assert io[name] == go[name], (
                f"output {name}: generated={go[name]:04X} island={io[name]:04X}; {ctx}")
        for name in ("flags", "fmask", "cost"):
            assert ic[name] == gc[name], (
                f"compat {name}: generated={gc[name]:#x} island={ic[name]:#x}; {ctx}")
        assert i.log == g.log, f"the stack residue differs; {ctx}"


def test_every_1631_arm_is_actually_forced_by_its_case():
    """The table above is only evidence if each entry reaches the arm it names --
    otherwise ten parametrized cases can all exercise the same path and read as
    complete coverage. This asserts the mapping is onto."""
    rng = random.Random(4242)
    seen = set()
    for case, args in _CLIP_CASES.items():
        dir_sel, seg, coord, low, high = args
        r = road_segment_clip_detail(dir_sel, seg, coord,
                                     lambda: low, lambda: high)
        assert (r.arm, r.second_test) == case, (
            f"case {case} actually reaches {(r.arm, r.second_test)}")
        regs, build = _clip_state(rng, args)
        assert GEN_1631(build(), **regs)[1]["cost"] == CLIP_COST[case], (
            f"case {case}: the generated body disagrees with the cost table")
        seen.add(case)
    assert seen == set(CLIP_COST), "an arm in the cost table has no forcing case"
