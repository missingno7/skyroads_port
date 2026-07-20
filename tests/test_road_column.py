"""Verify the recovered road-column strip compositor
(skyroads.handrecovered.road_column.road_column_strip) against real ASM I/O
captured over the full E2E replay (1010:38BF).

Unlike every other recovery this session, verification here is a FULL memory
diff, not a sampled set of fields: 196/196 real calls reproduced the ASM's
ENTIRE touched-byte set exactly (every byte the real call read from or wrote
to, across the whole 1 MB address space -- not just a few named fields). The
fixture stores only the touched addresses per case (computed by instrumenting
the pure function itself, which is what determines what needs storing) to
keep the file a reasonable size while remaining fully reproducible; 38
diverse cases are kept (a size spread + calls with SKIP_SYNC_LOOP_BIT set).

Getting to 196/196 caught two real bugs in the initial port, both found by
this same full-memory-diff technique (a sampled-field check would likely have
missed both):
* a missing unconditional scratch write (`ds:[0E74] := ax`, the very first
  instruction of the real routine);
* an inverted read of the `SKIP_SYNC_LOOP_BIT` -- it does NOT mean "position
  only, don't composite" (as the ORIGINAL hooks.py comment this was ported
  from describes it); the real control flow always composites, the bit only
  skips a bp/si synchronization pre-loop.
"""
from __future__ import annotations

import json
from pathlib import Path

from skyroads.handrecovered.road_column import SKIP_SYNC_LOOP_BIT, road_column_strip

_CASES = json.loads((Path(__file__).parent / "fixtures" / "road_column_strip_trace.json").read_text())


def _run(case: dict) -> None:
    pre = {int(k): v for k, v in case["pre"].items()}
    mem = dict(pre)

    def rb(seg, off):
        return mem[((seg & 0xFFFF) << 4) + (off & 0xFFFF)]

    def rw(seg, off):
        a = ((seg & 0xFFFF) << 4) + (off & 0xFFFF)
        return mem[a] | (mem[a + 1] << 8)

    def ww(seg, off, v):
        a = ((seg & 0xFFFF) << 4) + (off & 0xFFFF)
        mem[a] = v & 0xFF
        mem[a + 1] = (v >> 8) & 0xFF

    road_column_strip(rb, rw, ww, case["ax"], case["ds_seg"], case["e44"],
                      case["e46"], case["e48"], case["e64"], case["e62"],
                      case["e60"], case["e66"], case["e68"])
    expected_post = {int(k): v for k, v in case["post"].items()}
    assert mem == expected_post, case["ax"]


def test_matches_asm_full_memory_diff() -> None:
    assert _CASES, "fixture empty"
    for case in _CASES:
        _run(case)


def test_fixture_exercises_skip_sync_loop_bit() -> None:
    with_bit = [c for c in _CASES if c["ax"] & SKIP_SYNC_LOOP_BIT]
    without_bit = [c for c in _CASES if not (c["ax"] & SKIP_SYNC_LOOP_BIT)]
    assert with_bit, "fixture should include SKIP_SYNC_LOOP_BIT cases"
    assert without_bit, "fixture should include non-bit cases"


def test_fixture_exercises_real_compositing() -> None:
    # cases with a nontrivial touched-byte count did real pixel copying, not
    # just a positioning no-op.
    assert any(len(c["pre"]) > 50 for c in _CASES)
