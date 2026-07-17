"""Native, VM-free level loading (`skyroads.recovered_native.level_load`).

`decode_level_files` reads and decompresses any of ROADS.LZS's 31 levels with no
VM (reusing the VM-verified `roads_archive`); `native_level_load` places the
geometry seed at its recovered, VM-verified DGROUP offsets. The final test plays
a natively-loaded level and checks it advances on the golden trajectory that was
verified byte-for-byte against the VM (2026-07-12).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from skyroads.recovered_native.level_load import (DecodedLevel, decode_level_files,
                                        native_level_load, read_game_file)
from skyroads.recovered_native.state import NativeGameState, DATA_SEG
from skyroads.recovered import roads_archive

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
#: a level-independent constants baseline (a captured gameplay DGROUP); the sim's
#: level-independent constants (clip/shape tables) are computed at startup, so a
#: fresh state lacks them. Gitignored; the play test skips without it.
BASELINE = ROOT / "artifacts" / "snapshots" / "gameplay_f640" / "memory_1mb.bin"

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


@pytest.mark.skipif(not BASELINE.exists(), reason="constants baseline snapshot not present")
def test_native_loaded_level_plays_the_vm_golden_trajectory() -> None:
    """MILESTONE 1: a level loaded purely from ROADS.LZS by index, over a
    level-independent constants baseline, plays IDENTICALLY to the VM. Holding
    accelerate, the ship advances +75/tick on the exact trajectory captured from
    the original game for level 14 (0x4B,0x96,0xE1,…) and crashes into the same
    obstacle at frame_ctr 108 (game_state=3) — verified byte-for-byte against a
    VM-captured level-14 seed (see run_status.md 2026-07-12)."""
    from skyroads.recovered_native.loop import NativeGameplayDriver, apply_level_init
    from skyroads.bridge.dgroup_view import GameView

    dg = BASELINE.read_bytes()[(DATA_SEG << 4):(DATA_SEG << 4) + 0x10000]
    state = NativeGameState(bytearray(dg))
    native_level_load(state, 14, game_root=ASSETS)      # VM-free load by index
    gate = state.rw(0x4562)
    view = GameView(state)
    scratch = apply_level_init(view, gate)
    driver = NativeGameplayDriver(view, gate, scratch)

    trajectory = []
    outcome = None
    for _ in range(400):
        view.speed = 1                                  # hold accelerate
        outcome = driver.tick()
        trajectory.append(view.ship_pos)
        if outcome.transitioned:
            break

    assert trajectory[:5] == [0x4B, 0x96, 0xE1, 0x12C, 0x177]   # +75/tick, VM-exact
    assert outcome is not None and outcome.transitioned
    assert "game_state=3" in outcome.reason and "frame_ctr=108" in outcome.reason
