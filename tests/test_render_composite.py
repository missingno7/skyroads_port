"""Byte-exact verification of the native mode-0 road COMPOSITE.

skyroads.native.render_frame.composite_mode0 runs the full mode-0 pass --
setup -> render_classify -> dispatch_variant_a -> road_column_strip per column
-- writing composited road pixels into the destination buffer. Against a real
VM capture (demo_e2e_20260710_132930), captured at the 39D4-finalize entry
(i.e. AFTER all 24 road columns, BEFORE the finalize sprites), the native
composite reproduces every written byte: 686/686 exact.

This is the renderer's lockstep proof for the off-screen road pass: not just
the call SEQUENCE (see test_render_frame.py) but the actual composited PIXELS
match the original game byte-for-byte. See run_status.md's 2026-07-12 entries
(and the correction retracting an earlier, wrong "display-list builder gap").
"""
from __future__ import annotations

import json
from pathlib import Path

from skyroads.native.image import NativeGameImage
from skyroads.native.render_frame import composite_mode0

_FX = json.loads((Path(__file__).parent / "fixtures" / "render_composite_trace.json").read_text())


class _Recorder:
    """A NativeGameImage proxy recording every ww write (physical addr -> byte)."""

    def __init__(self, img: NativeGameImage) -> None:
        self._img = img
        self.data = img.data
        self.writes: dict[int, int] = {}

    def rb(self, seg: int, off: int) -> int:
        return self._img.rb(seg, off)

    def rw(self, seg: int, off: int) -> int:
        return self._img.rw(seg, off)

    def wb(self, seg: int, off: int, v: int) -> None:
        self._img.wb(seg, off, v)

    def ww(self, seg: int, off: int, v: int) -> None:
        a = ((seg & 0xFFFF) << 4) + (off & 0xFFFF)
        self.writes[a] = v & 0xFF
        self.writes[a + 1] = (v >> 8) & 0xFF
        self._img.ww(seg, off, v)


def test_composite_mode0_matches_the_vm_pixel_for_pixel() -> None:
    img = NativeGameImage()
    for addr_hex, val in _FX["seed"].items():
        img.data[int(addr_hex, 16)] = val
    rec = _Recorder(img)

    _setup, n = composite_mode0(rec, _FX["ds"])

    assert n == 24  # the mode-0 pass's road_column_strip calls
    expected = {int(a, 16): v for a, v in _FX["expected_writes"].items()}
    assert len(expected) == 686
    assert rec.writes == expected
