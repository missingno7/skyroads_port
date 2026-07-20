"""Verify the pure tile-dispatch loop (`skyroads.native.tile_dispatch`,
= `1010:2D1F`'s road loop) against a captured VM frame.

The capture (`artifacts/frame_2d1f`, produced by driving the level-select replay
to a real gameplay `2D1F` call — regenerate with the scratch capture script if
absent, see run_status.md) holds the pre-call 1MB image, the 8 stack params and
the VM's ordered write log. The pure loop must reproduce the VM's road-tile
rasterizer writes (`3153`/`3190`) EXACTLY: same count, same order, same
offsets, same values — verified 3166/3166 on 2026-07-12. The frame's remaining
writers (`3a22` ship sprite, `325b` ship-row tile) belong to the `34AE(1)` /
ship-row chain the loop intentionally leaves to the frame assembler.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from skyroads.native.image import NativeGameImage
from skyroads.native.tile_dispatch import render_tile_passes

CAPTURE = Path(__file__).resolve().parents[1] / "artifacts" / "frame_2d1f"

pytestmark = pytest.mark.skipif(
    not (CAPTURE / "write_log.json").exists(),
    reason="frame_2d1f capture not present (gitignored; regenerate from the replay)")


class _LogImage(NativeGameImage):
    """Records every write landing physically inside the dest 64KB window."""
    __slots__ = ("log", "lo")

    def wb(self, seg: int, off: int, v: int) -> None:
        phys = ((seg & 0xFFFF) << 4) + (off & 0xFFFF)
        if self.lo <= phys < self.lo + 0x10000:
            self.log.append((phys - self.lo, v & 0xFF))
        NativeGameImage.wb(self, seg, off, v)


def test_tile_passes_reproduce_vm_rle_writes_exactly() -> None:
    meta = json.loads((CAPTURE / "meta.json").read_text())
    vmlog = json.loads((CAPTURE / "write_log.json").read_text())
    pre = (CAPTURE / "pre_1mb.bin").read_bytes()
    params = meta["params"]

    img = _LogImage(bytearray(pre))
    img.log = []
    img.lo = (params[7] & 0xFFFF) << 4
    for k, v in enumerate(params):
        img.ww(0x1686, 0x0E28 + 2 * k, v)

    render_tile_passes(img, 0x1686)

    vm_rle = [(o, v) for o, v, ip in vmlog if ip in (0x3153, 0x3190)]
    assert len(vm_rle) == len(img.log), (
        f"write count: VM {len(vm_rle)} vs native {len(img.log)}")
    for i, (vm, mine) in enumerate(zip(vm_rle, img.log)):
        assert tuple(vm) == mine, f"write #{i}: VM {vm} vs native {mine}"


def test_full_native_frame_is_byte_exact() -> None:
    """THE FRAME PROOF: tile passes + the ship-row tile chain reproduce the
    VM's ENTIRE post-2D1F destination window byte-for-byte (65536/65536), and
    the road+ship-tile write sequence matches exactly (3553/3553 on the
    2026-07-12 capture). The `3a22` ship-sprite calls (34AE(1)/39D4 chain) are
    delta-stable rewrites on this frame, hence not needed for byte-equality
    here; they get exercised by a full-redraw capture later."""
    from skyroads.handrecovered.tile_raster import tile_rasterize

    meta = json.loads((CAPTURE / "meta.json").read_text())
    vmlog = json.loads((CAPTURE / "write_log.json").read_text())
    pre = (CAPTURE / "pre_1mb.bin").read_bytes()
    post = (CAPTURE / "post_1mb.bin").read_bytes()
    params = meta["params"]
    dest = params[7] & 0xFFFF

    img = _LogImage(bytearray(pre))
    img.log = []
    img.lo = dest << 4
    for k, v in enumerate(params):
        img.ww(0x1686, 0x0E28 + 2 * k, v)

    def ship_row(_ctx) -> None:
        tile_rasterize(img.rb, img.wb,
                       lambda o: img.rw(0x1686, o),
                       lambda o, v: img.ww(0x1686, o, v), 0x1686)

    render_tile_passes(img, 0x1686, on_ship_row=ship_row)

    vm = [(o, v) for o, v, ip in vmlog if ip in (0x3153, 0x3190, 0x325B)]
    assert len(vm) == len(img.log)
    for i, (a, b) in enumerate(zip(vm, img.log)):
        assert tuple(a) == b, f"write #{i}: VM {a} vs native {b}"

    base = dest << 4
    residual = sum(1 for i in range(0x10000)
                   if img.data[base + i] != post[base + i])
    assert residual == 0, f"{residual} bytes differ from the VM's post-frame"
