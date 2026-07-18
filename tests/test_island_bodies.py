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


# --- 1010:0533 ship_fell_off --------------------------------------------------
#
# 0533's first act is to call 04C0 with its own first three arguments, so the
# perspective word it switches on comes back through ds -- which means a forced
# state has to steer 04C0 too. af1c is [bp+8], the SAME word 04C0 projects as
# depth, so the segment index and the range test are not independently
# choosable; the depths below are picked to land both.

from skyroads.island_bodies import func_1010_0533 as ISLAND_0533   # noqa: E402
from skyroads.island_bodies import (                               # noqa: E402
    _FELL_ARM_COST, _FELL_MIRROR_COST, _FELL_NIBBLE_COST, _FELL_NO_SEGMENT)
from skyroads.recovered.func_1010_0533 import func_1010_0533 as GEN_0533  # noqa: E402
from skyroads.handrecovered.collision_response import (             # noqa: E402
    FELL_ARM_DECIDED, FELL_ARM_NO_SEGMENT, FELL_ARM_SEG_CULLED, MIRROR_NEGATIVE,
    MIRROR_NONE, MIRROR_ZERO, ship_fell_off_detail)

#: depth values whose 04C0 row index is in range AND whose (depth/128 - 49) mod
#: 46 lands on the residue that produces each mirror case. All three project to
#: table offset 0x162C (idx // 46 == 0), so one poked word steers every case.
_FELL_DEPTH = {MIRROR_NONE: 95 * 128,       # rem 0  -> seg 23, no fix-up
               MIRROR_ZERO: 118 * 128,      # rem 23 -> seg 0   -> mirrored to 1
               MIRROR_NEGATIVE: 119 * 128}  # rem 24 -> seg -1  -> mirrored to 2
_FELL_SEG = {MIRROR_NONE: 23, MIRROR_ZERO: 1, MIRROR_NEGATIVE: 2}
#: a depth whose row index is BELOW the window, so 04C0 returns AX = 0
_FELL_DEPTH_OUT = 32 * 128
_FELL_TABLE = 0x162C


def _fell_state(rng, *, depth, persp, af2c, low, high, seg):
    ss, ds = rng.randrange(0x1000, 0x8000), rng.randrange(0x1000, 0x8000)
    regs = dict(bp=rng.randrange(0x10000), bx=rng.randrange(0x10000),
                di=rng.randrange(0x10000), dx=rng.randrange(0x10000),
                si=rng.randrange(0x10000), ds=ds, ss=ss,
                sp=rng.randrange(0x0100, 0xFF00) & ~1)

    def build():
        m = Mem()
        for i, w in enumerate((0, 0, depth, af2c)):   # x_lo, x_hi, af1c, af2c
            m.ww(ss, (regs["sp"] + 2 + 2 * i) & 0xFFFF, w)
        m.ww(ds, _FELL_TABLE, persp)                  # what 04C0 will read back
        m.ww(ds, ((seg << 1) + 76) & 0xFFFF, low)
        m.ww(ds, ((seg << 1) + 152) & 0xFFFF, high)
        m.log.clear()
        return m

    return regs, build


def _fell_cases():
    """(id, kwargs) for every REACHABLE (arm, mirror, nibble, result) shape.

    SEG_CULLED is absent on purpose -- see the unreachability test below.
    """
    out = [("no_segment_04c0_out",
            dict(depth=_FELL_DEPTH_OUT, persp=0x0100, af2c=0x2300,
                 low=0x40, high=0x40, seg=23)),
           ("no_segment_04c0_in",
            dict(depth=_FELL_DEPTH[MIRROR_NONE], persp=0x0200, af2c=0x2300,
                 low=0x40, high=0x40, seg=23)),
           ("no_segment_nibble_F00",
            dict(depth=_FELL_DEPTH[MIRROR_NONE], persp=0x0F00, af2c=0x2300,
                 low=0x40, high=0x40, seg=23))]
    for mirror in (MIRROR_NONE, MIRROR_ZERO, MIRROR_NEGATIVE):
        for nibble in (0x0100, 0x0300, 0x0500):
            # mid is (0x40 + 0x40) / 2 = 64, so row must clear 64 to NOT fall:
            # row = (af2c - 0x2200) / 128, i.e. af2c >= 0x4200.
            for fell, af2c in ((1, 0x2300), (0, 0x5000)):
                out.append((f"decided_{mirror}_{nibble:#05x}_fell{fell}",
                            dict(depth=_FELL_DEPTH[mirror], persp=nibble,
                                 af2c=af2c, low=0x40, high=0x40,
                                 seg=_FELL_SEG[mirror])))
    return out


@pytest.mark.parametrize("name,kw", _fell_cases(), ids=[c[0] for c in _fell_cases()])
def test_0533_island_body_reproduces_the_full_contract(name, kw):
    rng = random.Random(0x0533 ^ (hash(name) & 0xFFFF))
    for _ in range(30):
        regs, build = _fell_state(rng, **kw)
        g = build()
        go, gc = GEN_0533(g, **regs)
        i = build()
        io, ic = ISLAND_0533(i, **regs)
        ctx = f"{name} " + " ".join(f"{k}={v:04X}" for k, v in sorted(regs.items()))
        assert set(io) == set(go), f"output SET differs; {ctx}"
        for out in OUTPUTS:
            assert io[out] == go[out], (
                f"output {out}: generated={go[out]:04X} island={io[out]:04X}; {ctx}")
        for c in ("flags", "fmask", "cost"):
            assert ic[c] == gc[c], (
                f"compat {c}: generated={gc[c]:#x} island={ic[c]:#x}; {ctx}")
        assert i.log == g.log, f"the stack residue differs; {ctx}"


def test_0533_forced_cases_cover_every_reachable_shape():
    """The cases are evidence only if they reach what they name, and only if
    together they leave nothing reachable uncovered."""
    seen = set()
    for name, kw in _fell_cases():
        # What 0533 switches on is 04C0's RETURN, not the word poked into the
        # table: out of range, 04C0 answers 0 and the poked word is never read.
        in_range = ((kw["depth"] // 128) + 0xFFA1) & 0xFFFF < 0x142
        persp = kw["persp"] if in_range else 0
        r = ship_fell_off_detail(persp, kw["depth"], kw["af2c"],
                                 lambda _s: kw["low"], lambda _s: kw["high"])
        seen.add((r.arm, r.mirror, r.nibble, r.result))
        if name.startswith("decided"):
            assert r.arm == FELL_ARM_DECIDED, f"{name} reached arm {r.arm}"
            assert r.seg == _FELL_SEG[r.mirror], f"{name}: seg {r.seg}"
            assert str(r.result) == name[-1], f"{name}: result {r.result}"
        else:
            assert r.arm == FELL_ARM_NO_SEGMENT, f"{name} reached arm {r.arm}"
    # every mirror case, every accepted nibble, and BOTH outcomes
    assert {m for a, m, _n, _r in seen if a == FELL_ARM_DECIDED} == {
        MIRROR_NONE, MIRROR_ZERO, MIRROR_NEGATIVE}
    assert {n for a, _m, n, _r in seen if a == FELL_ARM_DECIDED} == {
        0x0100, 0x0300, 0x0500}
    assert {r for a, _m, _n, r in seen if a == FELL_ARM_DECIDED} == {0, 1}


def test_the_0533_segment_cull_is_UNREACHABLE_in_the_original():
    """`cmp [bp-4],0x25 ; ja` at 05A4 is dead code, proven by exhaustion.

    The residue of the /46 spans 0..45, so 23-rem spans -22..23; every value at
    or below 0 is mirrored to 1-x, which maps -22..0 onto 1..23. The post-mirror
    index is therefore always 1..23 and can never exceed the 0x25 threshold.

    This is asserted over ALL 65,536 af1c values rather than argued, because the
    consequence is load-bearing: FELL_ARM_SEG_CULLED has a cost entry that no
    input can ever select, and a forced case for it would have to fabricate a
    state the program cannot reach. Recording the arm and proving it dead is
    honest; quietly dropping it, or claiming it covered, is not.
    """
    segs = set()
    for af1c in range(0x10000):
        r = ship_fell_off_detail(0x0100, af1c, 0x2300,
                                 lambda _s: 0x40, lambda _s: 0x40)
        assert r.arm != FELL_ARM_SEG_CULLED
        segs.add(r.seg)
    assert segs == set(range(1, 24)), f"segment range moved: {min(segs)}..{max(segs)}"
    assert FELL_ARM_SEG_CULLED in _FELL_ARM_COST, (
        "the dead arm stays in the cost table -- it is in the original, and a "
        "table that silently omits it no longer mirrors the code it models")


def test_0533_cost_pieces_add_up_to_the_observed_values():
    """The cost table is built from the generated body's own per-block sums; these
    are the four totals two full playthroughs actually produced."""
    decided = _FELL_ARM_COST[FELL_ARM_DECIDED]
    assert _FELL_NO_SEGMENT == {19: 42, 104: 127}
    assert decided + _FELL_NIBBLE_COST[0x100] + _FELL_MIRROR_COST[MIRROR_NONE] == 160
    assert decided + _FELL_NIBBLE_COST[0x100] + _FELL_MIRROR_COST[MIRROR_NEGATIVE] == 161
    assert decided + _FELL_NIBBLE_COST[0x300] + _FELL_MIRROR_COST[MIRROR_NONE] == 162


# --- 1010:1732 road_object_visible --------------------------------------------
#
# The compound one: 27 basic blocks, up to four 04C0 calls and two 1631 calls.
# Its arguments do not reach the decisions directly -- every value it branches on
# comes back through 04C0 from the ds perspective table -- so a forced state has
# to steer the CALLEES. With x_lo = x_hi = 0 the table offset collapses to
# 0x162C + 2*(idx // 46), so poking two words there controls all four
# projections, and the segment-bound tables steer both 1631 calls.

from skyroads.island_bodies import OBJ_BLOCK_COST                    # noqa: E402
from skyroads.island_bodies import func_1010_1732 as ISLAND_1732     # noqa: E402
from skyroads.recovered.func_1010_1732 import func_1010_1732 as GEN_1732  # noqa: E402
from skyroads.handrecovered import renderer as _renderer            # noqa: E402

#: The grid the forced cases are drawn from. Depths are 128-steps from the
#: bottom of 04C0's window (so idx = depth/128 - 95 walks 0..59, crossing the
#: /46 bucket boundary and running the ±0x700 edges in and out of range);
#: screen_y sits on both sides of every band threshold (0x1E80, 0x2180, 0x2800);
#: the table words carry every low/0xF00 nibble combination the branches test.
_OBJ_DEPTHS = tuple(95 * 128 + 128 * k for k in range(60))
_OBJ_YS = (0x0000, 0x1000, 0x1E80, 0x1F00, 0x2180, 0x2200, 0x2500, 0x2900, 0x3000)
_OBJ_WORDS = (0x0000, 0x0001, 0x0100, 0x0101, 0x0301, 0x0500)
_OBJ_BOUNDS = ((0x0000, 0x0020), (0x0064, 0x0020), (0x0000, 0x0002))
_OBJ_TABLE = 0x162C


def _obj_build(rng, depth, screen_y, w0, w1, low, high):
    ss, ds = rng.randrange(0x1000, 0x8000), rng.randrange(0x1000, 0x8000)
    regs = dict(bp=rng.randrange(0x10000), bx=rng.randrange(0x10000),
                di=rng.randrange(0x10000), dx=rng.randrange(0x10000),
                si=rng.randrange(0x10000), ds=ds, ss=ss,
                sp=rng.randrange(0x0100, 0xFF00) & ~1)

    def build():
        m = Mem()
        for i, w in enumerate((0, 0, depth, screen_y)):  # x_lo, x_hi, depth, screen_y
            m.ww(ss, (regs["sp"] + 2 + 2 * i) & 0xFFFF, w)
        m.ww(ds, _OBJ_TABLE, w0)                    # idx // 46 == 0
        m.ww(ds, _OBJ_TABLE + 2, w1)                # idx // 46 == 1
        for s in range(0x30):                       # both 1631 calls' bounds
            m.ww(ds, (_CLIP_T_LOW + 2 * s) & 0xFFFF, low)
            m.ww(ds, (_CLIP_T_HIGH + 2 * s) & 0xFFFF, high)
        m.log.clear()
        return m

    return regs, build


def _obj_path(rng, args):
    """The block path the island walks for ``args`` -- measured, not predicted."""
    seen = []
    real = _renderer.road_object_visible_detail

    def spy(*a, **kw):
        r = real(*a, **kw)
        seen.append(r.path)
        return r

    import skyroads.island_bodies as _ib
    _ib.road_object_visible_detail = spy
    try:
        regs, build = _obj_build(rng, *args)
        ISLAND_1732(build(), **regs)
    finally:
        _ib.road_object_visible_detail = real
    return seen[0]


def _obj_cases():
    """One state per DISTINCT block path the grid can reach, keyed by that path.

    Deduplicating by path rather than taking the whole grid keeps the case list
    honest: 9,720 states that all walk the same six blocks are one piece of
    evidence, not 9,720, and the key names exactly what each case establishes.
    """
    rng = random.Random(0x1732)
    out = {}
    for depth in _OBJ_DEPTHS:
        for screen_y in _OBJ_YS:
            for w0 in _OBJ_WORDS:
                for w1 in _OBJ_WORDS:
                    for low, high in _OBJ_BOUNDS:
                        args = (depth, screen_y, w0, w1, low, high)
                        out.setdefault(_obj_path(rng, args), args)
    return out


_OBJ_CASES = _obj_cases()


@pytest.mark.parametrize("path", sorted(_OBJ_CASES, key=str),
                         ids=lambda p: "-".join(str(b) for b in p))
def test_1732_island_body_reproduces_the_full_contract_on_every_block_path(path):
    """FORCED-STATE evidence, distinct from the shadow's real-call evidence.

    It is what covers block 7 (the 1797 jump), which no recorded demo reaches:
    it needs a screen_y at or below 0x1E80 while an edge's low nibble is set,
    and every such state falls straight through 17A5 to the cull -- so 1797 is
    structurally always followed by 17AA and can never change an answer.
    """
    rng = random.Random(0x1732 ^ (hash(path) & 0xFFFF))
    for _ in range(20):
        regs, build = _obj_build(rng, *_OBJ_CASES[path])
        g = build()
        go, gc = GEN_1732(g, **regs)
        i = build()
        io, ic = ISLAND_1732(i, **regs)
        ctx = ("path=" + ",".join(str(b) for b in path) + " "
               + " ".join(f"{k}={v:04X}" for k, v in sorted(regs.items())))
        assert set(io) == set(go), f"output SET differs; {ctx}"
        for name in OUTPUTS:
            assert io[name] == go[name], (
                f"output {name}: generated={go[name]:04X} island={io[name]:04X}; {ctx}")
        for name in ("flags", "fmask", "cost"):
            assert ic[name] == gc[name], (
                f"compat {name}: generated={gc[name]:#x} island={ic[name]:#x}; {ctx}")
        assert i.log == g.log, (
            f"the stack residue differs -- the words the body leaves BELOW the "
            f"returned SP are observable; {ctx}")


def test_the_1732_forced_cases_reach_every_basic_block():
    """Cases are coverage only if they collectively walk the whole function.

    27 of 27, against the generated body's own ``bb ==`` dispatch -- so this
    also fails if a regeneration adds a block, rather than silently proving a
    smaller function than the one that ships.
    """
    reached = set()
    for path in _OBJ_CASES:
        reached |= set(path)
    assert reached == set(OBJ_BLOCK_COST), (
        f"forced cases miss block(s) {sorted(set(OBJ_BLOCK_COST) - reached)}")
    assert set(OBJ_BLOCK_COST) == set(range(27)), (
        "the cost table no longer mirrors the generated body's block set")


def test_1732_block_7_is_always_followed_by_the_cull():
    """WHY the one block no demo reaches cannot matter, proven by exhaustion.

    1797 is reached when screen_y < 0x2800 and (screen_y + 0x600) & 0xFFFF is at
    or below 0x2480 -- i.e. screen_y <= 0x1E80 -- and 17A5 then tests
    screen_y + 0x680 against 0x2800, which for the same screen_y is at most
    0x2500. So every path through block 7 exits at 1861 with AX = 0, and the
    block contributes a fixed 1 to the cost and nothing else. Asserted over all
    65,536 screen_y values rather than argued.
    """
    reachable = [y for y in range(0x10000)
                 if y < 0x2800 and ((y + 0x600) & 0xFFFF) <= 0x2480]
    assert reachable, "block 7 is unreachable for every screen_y -- re-derive"
    for y in reachable:
        assert ((y + 0x680) & 0xFFFF) <= 0x2800, (
            f"screen_y={y:#06x} reaches block 7 without falling into the cull")
    assert OBJ_BLOCK_COST[7] == 1


# --- 1010:0F62 stencil_blit ---------------------------------------------------
#
# The first LOOP absorbed, and the only body whose fmask is not constant. Three
# things a recorded demo cannot establish and these cases must: the DF-set
# direction (both demos enter with DF clear on all 347 calls), an all-zero
# source (so `cmp al,1` never runs and the mask is 0x8C5 instead of 0x8D5), and
# a count of 0 -- which `loop` turns into 65,536 iterations, not none.

from skyroads.island_bodies import (                               # noqa: E402
    STENCIL_COST_FIXED, STENCIL_COST_ONE, STENCIL_COST_OTHER, STENCIL_COST_ZERO,
    STENCIL_FMASK, STENCIL_FMASK_COMPARED, _STENCIL_ES_PTR)
from skyroads.island_bodies import func_1010_0f62 as ISLAND_0F62   # noqa: E402
from skyroads.recovered.func_1010_0f62 import func_1010_0f62 as GEN_0F62  # noqa: E402

_ST_SRC_SEG, _ST_ES = 0x6000, 0x8000
_ST_OUTPUTS = ("ax", "bp", "cx", "di", "ds", "es", "si")


def _stencil_state(rng, source, *, df, count=None, src_off=0x0400):
    """A pre-state whose source run is ``source``, laid out in DF order.

    The segments are fixed and mutually disjoint (source 0x60000, destination
    0x80000, ds/ss below 0x50000) so that a difference between the two bodies is
    a difference in what they compute, not an artefact of an overlap the test
    happened to construct.
    """
    ss, ds = rng.randrange(0x1000, 0x4000), rng.randrange(0x1000, 0x4000)
    regs = dict(ax=rng.randrange(0x10000), bp=rng.randrange(0x10000),
                di=rng.randrange(0x10000), ds=ds, si=rng.randrange(0x10000),
                ss=ss, sp=rng.randrange(0x0100, 0xF000) & ~1,
                _df=1 if df else 0)
    step = -1 if df else 1
    n = len(source) if count is None else count
    tc, oc = rng.randrange(0x10000), rng.randrange(0x10000)

    def build():
        m = Mem()
        for i, w in enumerate((src_off, _ST_SRC_SEG, n, tc, oc)):
            m.ww(ss, (regs["sp"] + 2 + 2 * i) & 0xFFFF, w)
        m.ww(ds, _STENCIL_ES_PTR, _ST_ES)
        for i, b in enumerate(source):
            m.wb(_ST_SRC_SEG, (src_off + i * step) & 0xFFFF, b)
        m.log.clear()
        return m

    return regs, build


def _stencil_cases():
    """(id, source, df, count) covering every per-byte class, both tail shapes
    that decide AX and AF, both fmasks, and both directions."""
    mixed = bytes([0, 1, 2, 0, 7, 1, 0, 0, 255, 1, 3])
    trailing = bytes([1, 2, 0, 5, 0, 0, 0, 0])           # ends in zeros AFTER a hit
    return [
        ("mixed_forward", mixed, False, None),
        ("mixed_backward", mixed, True, None),
        ("trailing_zeros_forward", trailing, False, None),
        ("trailing_zeros_backward", trailing, True, None),
        ("all_zero", bytes(24), False, None),
        ("all_zero_backward", bytes(24), True, None),
        ("all_one", bytes([1] * 24), False, None),
        ("all_other", bytes([9] * 24), False, None),
        ("no_ones", bytes([0, 4, 0, 200, 0]), False, None),
        ("single_zero", bytes([0]), False, None),
        ("single_one", bytes([1]), False, None),
        ("single_other", bytes([0xFE]), False, None),
        ("leading_zero_run", bytes([0] * 20 + [1]), False, None),
    ]


@pytest.mark.parametrize("name,source,df,count", _stencil_cases(),
                         ids=[c[0] for c in _stencil_cases()])
def test_0f62_island_body_reproduces_the_full_contract(name, source, df, count):
    rng = random.Random(0x0F62 ^ (hash(name) & 0xFFFF))
    for _ in range(20):
        regs, build = _stencil_state(rng, source, df=df, count=count)
        g = build()
        go, gc = GEN_0F62(g, **regs)
        i = build()
        io, ic = ISLAND_0F62(i, **regs)
        ctx = f"{name} " + " ".join(f"{k}={v:04X}" for k, v in sorted(regs.items()))
        assert set(io) == set(go), f"output SET differs; {ctx}"
        for out in _ST_OUTPUTS:
            assert io[out] == go[out], (
                f"output {out}: generated={go[out]:04X} island={io[out]:04X}; {ctx}")
        for c in ("flags", "fmask", "cost"):
            assert ic[c] == gc[c], (
                f"compat {c}: generated={gc[c]:#x} island={ic[c]:#x}; {ctx}")
        assert i.log == g.log, f"the byte-write log differs; {ctx}"


def test_0f62_count_zero_means_65536_iterations_not_none():
    """``loop`` decrements BEFORE testing, so CX = 0 is the longest run there is.

    Separated from the parametrized cases because it is the one state whose
    whole point is its size, and because reading it as "no iterations" is the
    natural mistake: it would make the body return with AX untouched and write
    nothing at all, and no recorded demo would ever contradict it (both demos'
    counts run 18..150).
    """
    rng = random.Random(0x0F62)
    src = bytes(range(256)) * 4          # every byte class, in bulk
    regs, build = _stencil_state(rng, src, df=False, count=0)
    g = build()
    go, gc = GEN_0F62(g, **regs)
    i = build()
    io, ic = ISLAND_0F62(i, **regs)
    assert len(g.log) == 0x10000 + 8, (
        f"expected 65,536 stosb bytes plus four pushed words, got {len(g.log)}")
    assert i.log == g.log
    for out in _ST_OUTPUTS:
        assert io[out] == go[out], f"output {out} differs on the CX=0 run"
    for c in ("flags", "fmask", "cost"):
        assert ic[c] == gc[c], f"compat {c} differs on the CX=0 run"


def test_0f62_fmask_is_NOT_constant_and_the_cases_prove_both_values():
    """Unique to this body: an all-zero source never reaches ``cmp al,1``, so it
    reports 0x8C5 and not the 0x8D5 every other absorbed address returns. A
    body that hard-coded one mask would pass on all 347 recorded calls."""
    rng = random.Random(7)
    seen = set()
    for name, source, df, count in _stencil_cases():
        regs, build = _stencil_state(rng, source, df=df, count=count)
        seen.add(GEN_0F62(build(), **regs)[1]["fmask"])
    assert seen == {STENCIL_FMASK, STENCIL_FMASK_COMPARED}, (
        f"the cases no longer force both masks: {sorted(hex(m) for m in seen)}")


def test_0f62_cost_is_linear_in_the_source_byte_census():
    """The cost model, checked against the generated body on constructed runs
    rather than fitted to observed totals."""
    rng = random.Random(0xC057)
    for source in (bytes(30), bytes([1] * 30), bytes([5] * 30),
                   bytes([0, 1, 5] * 10)):
        regs, build = _stencil_state(rng, source, df=False)
        want = STENCIL_COST_FIXED + sum(
            STENCIL_COST_ZERO if b == 0 else
            STENCIL_COST_ONE if b == 1 else STENCIL_COST_OTHER for b in source)
        assert GEN_0F62(build(), **regs)[1]["cost"] == want, (
            f"cost model disagrees with the generated body on {source[:6]!r}...")


# --- 1010:3A22 sprite_blit ----------------------------------------------------
#
# No stack frame at all: 3A22 pushes nothing and takes every argument in a
# register, so SI, DI and BX are live outputs. Three things the two demos never
# produce and these cases must: a mask with NO transparent column (all 8,634
# real calls are either mixed or fully transparent), a final `add si,0x123`
# that CARRIES (every real call exits with CF clear), and DX = 0.

from skyroads.island_bodies import (                               # noqa: E402
    SPRITE_COST_COL, SPRITE_COST_OPAQUE, SPRITE_COST_RET, SPRITE_COST_ROW,
    SPRITE_FMASK)
from skyroads.island_bodies import func_1010_3a22 as ISLAND_3A22   # noqa: E402
from skyroads.recovered.func_1010_3a22 import func_1010_3a22 as GEN_3A22  # noqa: E402
from skyroads.handrecovered.present import SPRITE_BLIT_WIDTH       # noqa: E402

_SP_SRC, _SP_MASK, _SP_DEST = 0x5000, 0x6000, 0x8000
_SP_OUTPUTS = ("ax", "bx", "cx", "di", "dx", "si")


def _sprite_state(rng, *, rows, mask_of, src_off=0x0100, mask_off=0x0080):
    """A pre-state for ``rows`` rows whose mask byte at column i is ``mask_of(i)``.

    The three segments are fixed and disjoint (source 0x50000, mask 0x60000,
    destination 0x80000) so a disagreement is a disagreement in what the bodies
    compute, not an artefact of an overlap the test built by accident.
    """
    regs = dict(ax=rng.randrange(0x10000), bx=mask_off, ds=_SP_SRC,
                dx=rows, es=_SP_DEST, si=src_off, ss=_SP_MASK)

    def build():
        m = Mem()
        # Only 64 KB of mask exists; a longer run re-reads it from the wrap.
        n = min((rows or 0x10000) * SPRITE_BLIT_WIDTH, 0x10000)
        for i in range(n):
            m.wb(_SP_MASK, (mask_off + i) & 0xFFFF, mask_of(i))
        for i in range(0x10000):
            m.wb(_SP_SRC, i, (i * 7 + 3) & 0xFF)
        m.log.clear()
        return m

    return regs, build


def _sprite_cases():
    return [
        ("mixed_24_rows", 24, lambda i: (2, 0, 5, 2)[i % 4], 0x0100),
        ("mixed_9_rows", 9, lambda i: (2, 0, 5, 2)[i % 4], 0x0100),
        ("fully_opaque", 6, lambda i: 2, 0x0100),
        ("fully_transparent", 6, lambda i: 0, 0x0100),
        ("single_row", 1, lambda i: (2, 0)[i % 2], 0x0100),
        ("trailing_transparent_columns", 3,
         lambda i: 2 if i % SPRITE_BLIT_WIDTH < 4 else 0, 0x0100),
        # the LAST row's `add si,0x123` carries -- unobserved in both demos
        ("carry_out_of_the_row_add", 1, lambda i: (2, 0)[i % 2], 0xFFE0),
        ("carry_multi_row", 3, lambda i: (2, 0)[i % 2], 0xFD00),
        # mask bytes that are neither 0 nor 2: only ==2 is opaque
        ("mask_values_other_than_0_and_2", 4, lambda i: (1, 2, 3, 0xFF)[i % 4],
         0x0100),
    ]


@pytest.mark.parametrize("name,rows,mask_of,src_off", _sprite_cases(),
                         ids=[c[0] for c in _sprite_cases()])
def test_3a22_island_body_reproduces_the_full_contract(name, rows, mask_of, src_off):
    rng = random.Random(0x3A22 ^ (hash(name) & 0xFFFF))
    for _ in range(10):
        regs, build = _sprite_state(rng, rows=rows, mask_of=mask_of,
                                    src_off=src_off)
        g = build()
        go, gc = GEN_3A22(g, **regs)
        i = build()
        io, ic = ISLAND_3A22(i, **regs)
        ctx = f"{name} " + " ".join(f"{k}={v:04X}" for k, v in sorted(regs.items()))
        assert set(io) == set(go), f"output SET differs; {ctx}"
        for out in _SP_OUTPUTS:
            assert io[out] == go[out], (
                f"output {out}: generated={go[out]:04X} island={io[out]:04X}; {ctx}")
        for c in ("flags", "fmask", "cost"):
            assert ic[c] == gc[c], (
                f"compat {c}: generated={gc[c]:#x} island={ic[c]:#x}; {ctx}")
        assert i.log == g.log, f"the byte-write log differs; {ctx}"


def test_3a22_forced_cases_force_the_two_exits_no_demo_produces():
    """The cases are evidence only if they reach what they name.

    Both demos run ONLY mixed and fully transparent masks, and every one of
    their 8,634 calls exits with CF clear -- so a body that hard-coded either
    would pass on the whole recorded population.
    """
    rng = random.Random(11)
    carries, masks = set(), set()
    for name, rows, mask_of, src_off in _sprite_cases():
        regs, build = _sprite_state(rng, rows=rows, mask_of=mask_of,
                                    src_off=src_off)
        _o, c = GEN_3A22(build(), **regs)
        carries.add(bool(c["flags"] & 0x1))
        total = rows * SPRITE_BLIT_WIDTH
        hits = sum(1 for i in range(total) if mask_of(i) == 2)
        masks.add("none" if hits == 0 else "all" if hits == total else "some")
        assert c["fmask"] == SPRITE_FMASK
    assert carries == {False, True}, "no case makes the final `add si,0x123` carry"
    assert masks == {"none", "all", "some"}, f"mask opacity coverage: {masks}"


def test_3a22_dx_zero_means_65536_rows_not_none():
    """The outer loop is a do-while, so DX = 0 is the longest run there is.

    Both demos pass only 24 or 9, so nothing recorded contradicts reading DX = 0
    as "draw nothing" -- which would return with AX, SI, BX untouched and write
    no pixels. One state, not twenty: the point of this one is its size.
    """
    rng = random.Random(0x3A22)
    regs, build = _sprite_state(rng, rows=0, mask_of=lambda i: 2 if i % 29 else 0)
    g = build()
    go, gc = GEN_3A22(g, **regs)
    i = build()
    io, ic = ISLAND_3A22(i, **regs)
    # The COST is what pins the row count: SI and BX both advance by an exact
    # multiple of 0x10000 over 65,536 rows and come back to where they started,
    # so neither can tell this run from one that drew nothing. The cost cannot
    # be faked -- it is 65,536 rows of fixed work plus two per opaque column.
    assert gc["cost"] == (SPRITE_COST_RET + 0x10000 * SPRITE_COST_ROW
                          + 0x10000 * SPRITE_BLIT_WIDTH * SPRITE_COST_COL
                          + len(g.log) * SPRITE_COST_OPAQUE), (
        "the DX=0 run is not 65,536 rows of the model's own arithmetic")
    assert len(g.log) > 1_800_000, (
        f"DX=0 drew only {len(g.log)} pixels -- it is not being read as 65,536 rows")
    assert i.log == g.log
    for out in _SP_OUTPUTS:
        assert io[out] == go[out], f"output {out} differs on the DX=0 run"
    for c in ("flags", "fmask", "cost"):
        assert ic[c] == gc[c], f"compat {c} differs on the DX=0 run"


def test_3a22_cost_is_linear_in_rows_and_opaque_columns():
    """The cost model, checked against the generated body on constructed runs
    rather than fitted to observed totals."""
    rng = random.Random(0xC057)
    for rows, mask_of in ((3, lambda i: 2), (3, lambda i: 0),
                          (7, lambda i: (2, 0)[i % 2])):
        regs, build = _sprite_state(rng, rows=rows, mask_of=mask_of)
        hits = sum(1 for i in range(rows * SPRITE_BLIT_WIDTH) if mask_of(i) == 2)
        want = (SPRITE_COST_RET + rows * SPRITE_COST_ROW
                + rows * SPRITE_BLIT_WIDTH * SPRITE_COST_COL
                + hits * SPRITE_COST_OPAQUE)
        assert GEN_3A22(build(), **regs)[1]["cost"] == want, (
            f"cost model disagrees with the generated body at rows={rows}")
