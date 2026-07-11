"""Verify skyroads.recovered.roads_archive against the real ROADS.LZS asset.

3/3 real live-VM-captured (gravity, fuel, oxygen) triples matched exactly --
see the module docstring and docs/skyroads/run_status.md for how these three
were captured (two freshly recorded genuine cold-boot demos, including a real
keyboard DOWN-ARROW + ENTER level pick).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from skyroads.recovered.roads_archive import LevelHeader, level_count, parse_directory, read_level_header

ROOT = Path(__file__).resolve().parents[1]
ROADS_LZS = ROOT / "assets" / "ROADS.LZS"

pytestmark = pytest.mark.skipif(not ROADS_LZS.exists(), reason="needs assets/ROADS.LZS")


@pytest.fixture(scope="module")
def roads_data() -> bytes:
    return ROADS_LZS.read_bytes()


def test_directory_is_31_entries_and_self_consistent(roads_data: bytes) -> None:
    entries = parse_directory(roads_data)
    assert level_count(roads_data) == len(entries)
    assert len(entries) == 31
    # the directory's own byte size must equal the first entry's offset
    assert len(entries) * 4 == entries[0][0]
    # offsets must be strictly increasing and stay in-bounds
    offsets = [off for off, _len in entries]
    assert offsets == sorted(offsets)
    assert offsets[-1] < len(roads_data)


@pytest.mark.parametrize(
    "index,expected",
    [
        (16, LevelHeader(gravity=8, fuel=200, oxygen=180)),  # frame 282 capture
        (17, LevelHeader(gravity=7, fuel=175, oxygen=60)),   # frame 1327 capture (real DOWN-ARROW+ENTER pick)
        (1, LevelHeader(gravity=8, fuel=150, oxygen=180)),   # frame 2016 capture
    ],
)
def test_read_level_header_matches_real_vm_captures(roads_data: bytes, index: int, expected: LevelHeader) -> None:
    assert read_level_header(roads_data, index) == expected


def test_same_gravity_different_fuel_is_real_not_an_anomaly(roads_data: bytes) -> None:
    """This is exactly the 'same gate=8, different divA' puzzle from the live
    trace -- confirms it's a flat, index-addressed table, not a gravity-keyed
    lookup: multiple distinct levels legitimately share gravity=8 while
    differing on fuel."""
    gravity_8_fuels = {
        read_level_header(roads_data, i).fuel
        for i in range(level_count(roads_data))
        if read_level_header(roads_data, i).gravity == 8
    }
    assert len(gravity_8_fuels) > 1
