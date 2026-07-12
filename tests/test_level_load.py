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


def test_native_level_load_fails_loud_not_silent() -> None:
    """Until the placement is recovered, native_level_load must FAIL LOUD (never
    silently seed a wrong/empty geometry) — and only after a successful native
    file decode."""
    state = NativeGameState()
    with pytest.raises(NotImplementedError) as exc:
        native_level_load(state, 3, game_root=ASSETS)
    assert "placement" in str(exc.value).lower()
