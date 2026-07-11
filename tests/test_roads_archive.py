"""Verify skyroads.recovered.roads_archive against the real ROADS.LZS asset.

3/3 real live-VM-captured (gravity, fuel, oxygen) triples matched exactly --
see the module docstring and docs/skyroads/run_status.md for how these three
were captured (two freshly recorded genuine cold-boot demos, including a real
keyboard DOWN-ARROW + ENTER level pick).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from skyroads.recovered.roads_archive import (
    LEVEL_HEADER_LEN,
    LevelHeader,
    level_count,
    parse_directory,
    read_level_header,
    read_level_palette,
    read_level_road,
)

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


def test_read_level_palette_is_72_entries(roads_data: bytes) -> None:
    for index in (0, 1, 16, 17):
        palette = read_level_palette(roads_data, index)
        assert len(palette) == 72 * 3
        assert all(b <= 63 for b in palette), "VGA palette entries are 6-bit RGB"


def test_read_level_road_decompresses_to_the_exact_directory_length(roads_data: bytes) -> None:
    """31/31 real levels decompress to exactly their directory-recorded
    length, using the project's own already-VM-verified skyroads.codecs.lzs
    codec -- the strongest available check without a live VM oracle capture
    of the in-memory road array (not pursued this session)."""
    entries = parse_directory(roads_data)
    for index, (_offset, total_length) in enumerate(entries):
        road = read_level_road(roads_data, index)
        assert len(road) == total_length - LEVEL_HEADER_LEN, f"index {index}"
        assert len(road) % 2 == 0, "road[] is a UINT16LE array"
