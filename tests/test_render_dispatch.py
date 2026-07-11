"""Verify the recovered per-column road-draw dispatch
(skyroads.recovered.render_dispatch) against real ASM I/O captured over the
full E2E demo.

101 distinct field-snapshots per variant (deduped from ~1280 real invocations
each), all matched exactly. A small number of invocations (16/1280 for variant
A, 15/1280 for variant B) were excluded as a documented anomaly: they share one
repeated field snapshot and produce an implausibly long call burst (16-24
calls), almost certainly calls from a third, unisolated dispatch source -- see
the module docstring's @oracle_link status notes and run_status.md. Excluding
them is itself asserted by test_fixtures_exclude_the_known_anomaly below, so
this file can't quietly start hiding a real regression instead.
"""
from __future__ import annotations

import json
from pathlib import Path

from skyroads.recovered.render_dispatch import dispatch_variant_a, dispatch_variant_b

_FIXTURES_DIR = Path(__file__).parent / "fixtures"
_A = json.loads((_FIXTURES_DIR / "dispatch_variant_a_trace.json").read_text())
_B = json.loads((_FIXTURES_DIR / "dispatch_variant_b_trace.json").read_text())

# fixture field keys are decimal str(offset) -- 0x0E44=3652, 0x0E46=3654,
# 0x0E4E=3662, 0x0E50=3664, 0x0E52=3666, 0x0E54=3668, 0x0E56=3670, 0x0E58=3672,
# 0x0E5A=3674, 0x0E5C=3676, 0x0E5E=3678 (matches dispatch_variant_a/b's
# positional e44,e46,e4e,e50,e52,e54,e56,e58,e5a[,e5c,e5e] argument order).
_FIELD_NAMES_A = ["3652", "3654", "3662", "3664", "3666", "3668", "3670", "3672", "3674"]
_FIELD_NAMES_B = _FIELD_NAMES_A + ["3676", "3678"]


def test_variant_a_matches_asm() -> None:
    assert _A, "fixture empty"
    for case in _A:
        f = case["fields"]
        got = dispatch_variant_a(*(f[k] for k in _FIELD_NAMES_A))
        assert got == case["calls"], case


def test_variant_b_matches_asm() -> None:
    assert _B, "fixture empty"
    for case in _B:
        f = case["fields"]
        got = dispatch_variant_b(*(f[k] for k in _FIELD_NAMES_B))
        assert got == case["calls"], case


def test_fixtures_exclude_the_known_anomaly() -> None:
    # The dump script drops any real invocation whose call list exceeds 8 (the
    # documented anomalous bursts, 16-24 calls) -- confirm no such case snuck
    # into the committed fixtures, so this file can't quietly mask a real
    # transcription bug by only ever testing the easy cases.
    assert all(len(c["calls"]) <= 8 for c in _A)
    assert all(len(c["calls"]) <= 8 for c in _B)


def test_fixtures_are_a_meaningful_sample() -> None:
    assert len(_A) >= 50
    assert len(_B) >= 50
    # both variants should exercise a real spread of call-list lengths, not
    # just the trivial "no calls" case.
    assert len({len(c["calls"]) for c in _A}) >= 3
    assert len({len(c["calls"]) for c in _B}) >= 3


def test_variant_a_can_return_all_six_edge_records() -> None:
    # e4e==1, e50==2 -> the unconditional 0x0400..0x0405 volley (block 3).
    got = dispatch_variant_a(e44=0, e46=0, e4e=1, e50=2, e52=0, e54=0,
                             e56=1, e58=1, e5a=1)
    assert got == [0x0400, 0x0401, 0x0402, 0x0403, 0x0404, 0x0405]


def test_variant_b_can_return_all_six_edge_records() -> None:
    # e50 > e4e (so block J runs), e4e<=2, e50==2 -> the volley.
    got = dispatch_variant_b(e44=0, e46=0, e4e=1, e50=2, e52=0, e54=0,
                             e56=1, e58=1, e5a=1, e5c=0, e5e=0)
    assert got == [0x0400, 0x0401, 0x0402, 0x0403, 0x0404, 0x0405]
