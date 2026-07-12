"""Native, VM-free level loading (`skyroads.native.level_load`).

The file-decode half is recovered today: `decode_level_files` reads and
decompresses any of ROADS.LZS's 31 levels with no VM (reusing the VM-verified
`roads_archive`). The DGROUP-placement half is loader-lift-pending, so
`native_level_load` fails loud — this test pins both the working decode and that
honest boundary.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from skyroads.native.level_load import (DecodedLevel, decode_level_files,
                                        native_level_load, read_game_file)
from skyroads.native.state import NativeGameState
from skyroads.recovered import roads_archive

ASSETS = Path(__file__).resolve().parents[1] / "assets"

pytestmark = pytest.mark.skipif(
    not (ASSETS / "ROADS.LZS").exists(), reason="game assets not present")


def test_read_game_file_is_case_insensitive() -> None:
    a = read_game_file(ASSETS, "ROADS.LZS")
    b = read_game_file(ASSETS, "roads.lzs")
    assert a == b and len(a) > 0


def test_decode_every_level_vm_free() -> None:
    roads = read_game_file(ASSETS, "ROADS.LZS")
    n = roads_archive.level_count(roads)
    assert n == 31
    for lv in range(n):
        d = decode_level_files(lv, game_root=ASSETS)
        assert isinstance(d, DecodedLevel)
        assert d.index == lv
        assert len(d.palette) == 72 * 3          # 216-byte VGA 6-bit palette
        assert len(d.road) > 0 and len(d.road) % 2 == 0   # UINT16LE[] road array
        assert 0 <= d.gravity <= 0xFFFF


def test_decode_matches_roads_archive() -> None:
    """The module is a thin native wrapper — its decode must equal the
    VM-verified roads_archive output byte-for-byte."""
    roads = read_game_file(ASSETS, "ROADS.LZS")
    d = decode_level_files(7, game_root=ASSETS)
    assert d.road == roads_archive.read_level_road(roads, 7)
    assert d.palette == roads_archive.read_level_palette(roads, 7)


def test_decode_level_out_of_range() -> None:
    with pytest.raises(IndexError):
        decode_level_files(99, game_root=ASSETS)


def test_native_level_load_places_geometry() -> None:
    """native_level_load reproduces the loader `1010:5614`'s DGROUP writes: road[]
    at 0x162C (over a cleared 0x1B58 region), gravity/fuel/oxygen scalars, and the
    216-byte palette — all at their recovered, VM-verified offsets."""
    state = NativeGameState()
    d = native_level_load(state, 14, game_root=ASSETS)
    assert isinstance(d, DecodedLevel) and d.index == 14
    # road[] decoded into 0x162C, padded with zeros to the 0x1B58 clear region.
    assert bytes(state.data[0x162C:0x162C + len(d.road)]) == d.road
    assert all(b == 0 for b in state.data[0x162C + len(d.road):0x162C + 0x1B58])
    # per-level scalars at their fixed offsets
    assert state.rw(0x4562) == d.gravity
    assert state.rw(0x54A2) == d.fuel
    assert state.rw(0x4566) == d.oxygen
    # palette
    assert bytes(state.data[0x41C2:0x41C2 + len(d.palette)]) == d.palette


def test_native_level_load_clears_stale_geometry() -> None:
    """The 5D07 memset clears the whole 0x1B58 region first, so a load over a
    dirty state cannot leak the previous level's longer road tail."""
    state = NativeGameState()
    # dirty the region past where a short level's road ends
    for i in range(0x1B58):
        state.data[0x162C + i] = 0xEE
    d = native_level_load(state, 0, game_root=ASSETS)  # a shorter level
    assert all(b == 0 for b in state.data[0x162C + len(d.road):0x162C + 0x1B58])


def test_native_level_load_all_levels() -> None:
    """Every level places without overflowing the 0x1B58 region."""
    for lv in range(31):
        native_level_load(NativeGameState(), lv, game_root=ASSETS)
