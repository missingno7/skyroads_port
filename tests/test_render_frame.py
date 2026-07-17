"""Verify the assembled native mode-0 render pipeline (skyroads.native.render_frame)
reproduces the VM's exact road_column_strip call sequence.

Ground truth: one real mode-0 34AE composite pass from demo_e2e_20260710_132930
made 24 road_column_strip calls. mode0_column_calls -- setup -> render_classify
-> dispatch_variant_a, assembled over a NativeGameImage -- produces the identical
24 (ax, e44, e46, e48) tuples, proving the render DECISION pipeline composes
correctly end to end. (Byte-exact PIXEL output additionally needs the not-yet-
recovered display-list builder; see the module docstring + run_status.md.)
"""
from __future__ import annotations

import json
from pathlib import Path

from skyroads.native.image import NativeGameImage
from skyroads.native.render_frame import (
    ColumnCall,
    compute_mode0_setup,
    mode0_column_calls,
)

_FX = json.loads((Path(__file__).parent / "fixtures" / "render_frame_trace.json").read_text())


def _image() -> tuple[NativeGameImage, int]:
    ds = _FX["ds"]
    img = NativeGameImage()
    for off_hex, val in _FX["dgroup_bytes"].items():
        img.wb(ds, int(off_hex, 16), val)
    return img, ds


def test_mode0_setup_matches_verified_formulas() -> None:
    img, ds = _image()
    setup = compute_mode0_setup(img, ds)
    # record_base 0x16B8 is the exact value render_classify was verified against.
    assert setup.record_base == 0x16B8
    assert setup.e64 == 0x30
    assert setup.seg_src == 0x7176
    assert setup.seg_dst == 0x8116
    assert setup.seg_records_cur == 0x311B    # [0E60]
    assert setup.seg_records_prev == 0x2B12   # [0E62]


def test_mode0_reproduces_the_vm_road_column_strip_call_sequence() -> None:
    img, ds = _image()
    got = mode0_column_calls(img, ds)
    expected = [ColumnCall(*row) for row in _FX["expected_calls"]]
    assert len(got) == len(expected) == 24
    assert got == expected
