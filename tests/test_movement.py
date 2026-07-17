"""Verify the recovered 186B movement/collision solver (skyroads.handrecovered.movement).

Uses captured (input, collision-probe-answers, output) traces of the real
routine: each case replays the exact ``1732`` results the ASM saw, so the pure
``resolve_move`` is checked against the ASM both on its output accumulators AND
on the exact sequence of collision probes it makes (an unrecorded probe means
the reconstruction's interpolation diverged). Fixture captured from the level
demo; the full-demo run verifies 1760/1760 calls (see run_status 2026-07-10).
"""
from __future__ import annotations

import json
from pathlib import Path

from skyroads.handrecovered.movement import resolve_move

_FIXTURE = Path(__file__).parent / "fixtures" / "movement_186b_trace.json"
_CASES = json.loads(_FIXTURE.read_text())


def _oracle_predicate(queries):
    table = {(q[0], q[1], q[2]): q[3] for q in queries}
    seen_miss = []

    def visible(lateral32, depth, screen_y):
        key = (lateral32 & 0xFFFFFFFF, depth & 0xFFFF, screen_y & 0xFFFF)
        if key not in table:            # a probe the real routine never made
            seen_miss.append(key)
            return -999
        return table[key]

    return visible, seen_miss


def test_resolve_move_matches_asm_over_captured_trace() -> None:
    assert _CASES, "fixture is empty"
    changed = 0
    for i, case in enumerate(_CASES):
        visible, miss = _oracle_predicate(case["q"])
        out = resolve_move(*case["in"], *case["tgt"], visible)
        assert not miss, f"case {i}: solver probed positions the ASM never did: {miss[:3]}"
        assert list(out) == case["out"], f"case {i}: {out} != {case['out']}"
        if list(out) != case["in"]:
            changed += 1
    # the fixture deliberately includes state-changing calls (sweep/refine paths)
    assert changed >= 8, f"fixture should exercise the movement paths (only {changed} changed)"
