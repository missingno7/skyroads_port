"""Verify the recovered OPL music engine (skyroads.handrecovered.music) against real
ASM I/O captured over the cold-sound demo.

Each fixture tick records the exact DGROUP bytes the engine reads plus the
``(reg, val)`` OPL writes the ASM emitted that tick; the engine must reproduce
the writes exactly. (The full proof is the lockstep run in the commit that added
this module: the OPL write stream matched over all 12,882 cold-sound-demo ticks.)
"""
from __future__ import annotations

import json
from pathlib import Path

from skyroads.handrecovered.music import CURSOR, DELAY, Engine

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
    """Regression test for two real bugs found while checking whether the
    engine could be wired as a live replacement hook (which must drive itself
    off ITS OWN committed state, with no original ASM running alongside to
    keep memory in sync -- unlike the ASM-comparison fixtures above, which
    can't catch either of these):

    1. An earlier version tracked ``cursor``/``loop`` purely as Python locals
       and never wrote them back to ``ds:[3196]``/``[3198]``, so a second call
       would just replay the same words forever.
    2. The ASM's loop-back target (1010:5A7B `jmp 5A58`) is the delay check
       ITSELF, not the word-fetch -- so when an ``op0`` arms the delay counter,
       the very same tick immediately loops back, sees it nonzero, and
       decrements it once more before returning. The stored delay after a
       tick that arms it is one less than the song data says. Both the "just
       return" and the "decrement once" shapes emit identical (empty) OPL
       output for that tick, so pure output verification against the ASM
       cannot distinguish them -- only replaying many ticks off nothing but
       the engine's own state exposes it.

    Simulates exactly that: a tiny synthetic song in a plain dict, driven
    tick-by-tick through nothing but ``Engine``, with the expected sequence
    below independently computed (not hand-derived) and cross-checked to be a
    stable, self-repeating cycle -- confirming the state doesn't drift.
    """
    # word layout: op=word&7 (3 bits), al=(word&0xFF)>>4 (4 bits), ah=(word>>8)&0xFF.
    # op0 (delay:=3), op7 (flag:=0x11), op0 (delay:=0 -- immediate), op7 (flag:=0x22), loop via op5
    WORDS = [0x0300, 0x1107, 0x0000, 0x2207, 0x0005]
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

    # Arming to 3 nets an effective 2-tick wait (the arming tick itself performs
    # the first decrement -- see bug 2 above), then the "delay==0" tick both
    # finishes waiting AND re-arms in one call: a stable period-3 cycle,
    # [2, 1, 0], repeating forever; the cursor always ends back at base+2 (word0
    # consumed every time the cycle re-arms). No tick ever produces an OPL write
    # (this song only exercises op0/op5/op7, none of which touch the OPL).
    for _ in range(4):                 # several full cycles -- proves no drift
        for expected in (2, 1, 0):
            assert tick() == []
            assert mem[DELAY] == expected
            assert cursor() == base + 2
