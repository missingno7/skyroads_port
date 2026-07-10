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

from skyroads.recovered.music import Engine

_TICKS = json.loads((Path(__file__).parent / "fixtures" / "music_ticks.json").read_text())["ticks"]


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
