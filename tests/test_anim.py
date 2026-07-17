"""Native ANIM.LZS decoder (skyroads/recovered_native/anim.py)."""
from pathlib import Path

import pytest

from skyroads.recovered_native.anim import (VGA_HEIGHT, VGA_WIDTH, iter_reveal_counts,
                                  load_anim, paint_tile)

ROOT = Path(__file__).resolve().parents[1]
ANIM = ROOT / "assets" / "ANIM.LZS"

needs_assets = pytest.mark.skipif(not ANIM.exists(), reason="game assets absent")


@needs_assets
def test_parses_whole_file_into_tiles():
    cmap, tiles = load_anim(ANIM)
    assert len(cmap) // 3 == 102
    assert len(tiles) > 200
    for t in tiles:
        assert len(t.pixels) == t.h * t.w
        assert 0 <= t.dest < VGA_WIDTH * VGA_HEIGHT


@needs_assets
def test_iter_reveal_counts_covers_every_tile():
    _, tiles = load_anim(ANIM)
    assert sum(iter_reveal_counts(len(tiles))) == len(tiles)


@needs_assets
def test_paint_tile_stays_in_canvas_bounds():
    _, tiles = load_anim(ANIM)
    canvas = bytearray(VGA_WIDTH * VGA_HEIGHT)
    for t in tiles:
        paint_tile(canvas, t)     # must not raise / go out of bounds
    assert len(canvas) == VGA_WIDTH * VGA_HEIGHT
