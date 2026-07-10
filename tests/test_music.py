"""Verify the recovered OPL music engine (skyroads.recovered.music) against real
ASM I/O captured over the cold-sound demo.

Each fixture tick records the exact DGROUP bytes the engine reads plus the
``(reg, val)`` OPL writes the ASM emitted that tick; the engine must reproduce
the writes exactly. (The full proof is the lockstep run in the commit that added
this module: the OPL write stream matched over all 12,882 cold-sound-demo ticks.)
"""
from __future__ import annotations

import json
from pathlib import Path

from skyroads.recovered.music import CURSOR, DELAY, Engine

_TICKS = json.loads((Path(__file__).parent / "fixtures" / "music_ticks.json").read_text())["ticks"]
_RESET = json.loads((Path(__file__).parent / "fixtures" / "opl_reset.json").read_text())


def _readers(mem_hex: dict[str, int]):
    mem = {int(o, 16): v for o, v in mem_hex.items()}
    rb = lambda off: mem.get(off & 0xFFFF, 0)
    rw = lambda off: mem.get(off & 0xFFFF, 0) | (mem.get((off + 1) & 0xFFFF, 0) << 8)
    return rb, rw


def test_music_engine_reproduces_opl_stream() -> None:
    assert _TICKS, "fixture empty"
    for i, tick in enumerate(_TICKS):
        rb, rw = _readers(tick["mem"])
        writes = Engine(rb, rw).run_tick()
        expected = [(r, v) for r, v in tick["writes"]]
        assert writes == expected, f"tick {i}: {writes} != {expected}"


def test_music_engine_exercises_real_note_events() -> None:
    # the fixture must include real note programming (A0/B0 freq + operator regs),
    # not just trivial single-register writes
    all_regs = {r for tick in _TICKS for r, _ in tick["writes"]}
    assert any(0xA0 <= r <= 0xA8 for r in all_regs), "no A0 (frequency) writes captured"
    assert any(0xB0 <= r <= 0xB8 for r in all_regs), "no B0 (key-on) writes captured"


def test_reset_opl_matches_asm() -> None:
    # the one-time OPL reset + percussion-patch init (1010:58A5-5913), verified
    # against the single occurrence in the cold-sound demo (63 writes, byte-exact
    # over the full 2157-frame replay).
    rb, rw = _readers(_RESET["mem"])
    writes = Engine(rb, rw).reset_opl()
    expected = [(r, v) for r, v in _RESET["writes"]]
    assert writes == expected
    # sanity: silences all 22 operator registers, then programs rhythm mode
    assert all(v == 0x3F for r, v in writes[:22] if 0x40 <= r <= 0x55)
    assert (0xBD, 0xE0) in writes


def test_engine_persists_cursor_and_delay_across_ticks() -> None:
    """Regression test for a real bug: an earlier version tracked ``cursor``/
    ``loop`` purely as Python locals and never decremented ``[0C83]`` when
    waiting, so calling ``run_tick()`` repeatedly off ITS OWN committed state
    (as a live replacement hook must, with no original ASM running alongside
    to keep memory in sync) either replayed the same song words forever or
    never advanced past a wait. Simulates exactly that: a tiny synthetic song
    in a plain dict, driven tick-by-tick through nothing but ``Engine``."""
    # word layout: op=word&7 (3 bits), al=(word&0xFF)>>4 (4 bits), ah=(word>>8)&0xFF.
    # op0 (delay:=3), op7 (flag:=0x11), op0 (delay:=0 -- immediate), op7 (flag:=0x22), loop via op5
    WORDS = [0x0300, 0x1107, 0x0000, 0x2207, 0x0005]
    LOOP_TARGET_INDEX = 0  # op5 sends the cursor back to the first word
    base = 0x4000
    mem = {}
    for i, w in enumerate(WORDS):
        off = base + i * 2
        mem[off] = w & 0xFF
        mem[off + 1] = (w >> 8) & 0xFF
    mem[CURSOR] = base & 0xFF
    mem[CURSOR + 1] = (base >> 8) & 0xFF
    mem[0x3198] = base & 0xFF          # loop point == start
    mem[0x3198 + 1] = (base >> 8) & 0xFF
    mem[DELAY] = 0
    mem[0x3194] = 0  # instr_base unused by op0/op5/op7

    def rb(off):
        return mem.get(off & 0xFFFF, 0)

    def rw(off):
        return rb(off) | (rb(off + 1) << 8)

    eng = Engine(rb, rw)

    def tick():
        writes = eng.run_tick()
        mem.update(eng.ovl)          # exactly what a live hook must do: commit back
        return writes

    def cursor():
        return mem[CURSOR] | (mem[CURSOR + 1] << 8)

    # tick 1: processes word0 op0(delay:=3), no OPL writes (op0/op5/op7 never touch the OPL)
    assert tick() == []
    assert mem[DELAY] == 3 and cursor() == base + 2
    # ticks 2-4: waiting -- delay counts down 3,2,1 and nothing else happens (a bug
    # that forgot to decrement would spin here forever without ever progressing)
    for expected in (2, 1, 0):
        assert tick() == []
        assert mem[DELAY] == expected
    # tick 5: delay hit 0 -- resumes the stream and runs to completion WITHOUT
    # stopping (nothing in words 1-4 arms a nonzero delay): op7(0x11), op0(delay:=0,
    # i.e. keep going), op7(0x22), op5(cursor:=loop==base) sends it back to word0,
    # which the SAME tick then re-processes (op0, delay:=3) -- that's what finally
    # ends the tick.  The bug this guards against: cursor stuck / drifting forever.
    assert tick() == []
    assert mem[DELAY] == 3 and cursor() == base + 2
    # the exact tick-1 outcome, one full loop cycle later -- proves the state stays
    # in a stable, self-consistent cycle across many hook-only-driven ticks, not
    # just one (a persistence bug could easily survive a single-cycle check).
    for _ in range(3):
        for expected in (2, 1, 0):
            assert tick() == [] and mem[DELAY] == expected
        assert tick() == []
        assert mem[DELAY] == 3 and cursor() == base + 2
