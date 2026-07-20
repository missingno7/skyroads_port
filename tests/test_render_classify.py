"""Verify skyroads.handrecovered.render_classify against a real 34AE render pass.

Ground truth: one full 34AE render invocation (variant A, record_base 0x16B8)
captured from replay_e2e_20260710_132930 -- 80 dispatch calls (= 10 outer x 4
middle x 2 inner). render_classify reproduces every classification field of
every call byte-exact (80/80). See the module docstring + run_status.md.
"""
from __future__ import annotations

import json
from pathlib import Path

from skyroads.handrecovered.render_classify import ColumnClass, render_classify
from skyroads.handrecovered.render_dispatch import dispatch_variant_a

_FX = json.loads((Path(__file__).parent / "fixtures" / "render_classify_trace.json").read_text())


def _reader():
    """A DGROUP byte reader backed by the fixture's record window + BA7 bytes."""
    lo = _FX["window"]["lo"]
    win = bytes.fromhex(_FX["window"]["bytes"])
    ba7 = bytes.fromhex(_FX["ba7"])

    def rb(off: int) -> int:
        off &= 0xFFFF
        if 0x0BA7 <= off < 0x0BA7 + len(ba7):
            return ba7[off - 0x0BA7]
        if lo <= off < lo + len(win):
            return win[off - lo]
        raise AssertionError(f"read outside captured window: {off:#06x}")

    return rb


def test_render_classify_matches_real_34ae_invocation() -> None:
    rb = _reader()
    got = render_classify(rb, _FX["record_base"])
    keys = _FX["keys"]
    expected = [ColumnClass(**dict(zip(keys, row))) for row in _FX["calls"]]
    assert len(got) == len(expected) == 80
    assert got == expected


def test_render_classify_shape_and_bounds() -> None:
    """10 outer x 4 middle x 2 inner = 80, e44 counts 11 down to 2, e46 cycles
    1..4, e48 toggles 0/1 -- the loop structure, independent of the fixture."""
    rb = _reader()
    got = render_classify(rb, _FX["record_base"])
    assert [c.e44 for c in got[:8]] == [11] * 8      # first outer pass
    assert [c.e44 for c in got[-8:]] == [2] * 8      # last outer pass
    assert [c.e46 for c in got[:8]] == [1, 1, 2, 2, 3, 3, 4, 4]
    assert [c.e48 for c in got[:8]] == [0, 1, 0, 1, 0, 1, 0, 1]


def test_render_classify_feeds_dispatch_variant_a() -> None:
    """End-to-end: the classification this produces is exactly what
    dispatch_variant_a consumes -- every ColumnClass drives a real dispatch
    call without error, closing the classify -> dispatch pipeline."""
    rb = _reader()
    for c in render_classify(rb, _FX["record_base"]):
        calls = dispatch_variant_a(c.e44, c.e46, c.e4e, c.e50, c.e52, c.e54,
                                   c.e56, c.e58, c.e5a)
        assert isinstance(calls, list)
        assert all(isinstance(ax, int) for ax in calls)
