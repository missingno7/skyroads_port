"""Native WORLD graphics + MUZAX song loading (skyroads/native/world_load.py)."""
from pathlib import Path

import pytest

from skyroads.native.world_load import (
    BACKGROUND_H, BACKGROUND_W, CMAP_DAC_BASE, load_song, load_world_assets,
    parse_muzax_directory, song_for_level, world_for_level)
from skyroads.native.level_load import read_game_file

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
SNAP = ROOT / "artifacts" / "frame_2d1f" / "snap92" / "memory_1mb.bin"

needs_assets = pytest.mark.skipif(not ASSETS.exists(), reason="game assets absent")
needs_snap = pytest.mark.skipif(not SNAP.exists(), reason="baseline snapshot absent")


@needs_assets
def test_all_ten_worlds_decode():
    for w in range(10):
        assets = load_world_assets(w * 3, game_root=ASSETS)
        assert len(assets.cmap) == 342          # 114 colours x RGB6
        assert len(assets.background) == BACKGROUND_W * BACKGROUND_H
        # biased indices live in the CMAP DAC window (142..255); raw zero
        # pixels stay 0 (the nonzero-bias rule proven byte-exact on the
        # CARS/DASHBRD banks -- some worlds do contain raw zeros)
        nz = [b for b in assets.background if b]
        assert min(nz) >= CMAP_DAC_BASE


@needs_assets
def test_muzax_directory_has_ten_songs():
    entries = parse_muzax_directory(read_game_file(ASSETS, "MUZAX.LZS"))
    assert len(entries) == 10
    for off, n_instr, size in entries:
        assert 0 < n_instr < 16 and 0 < size < 0x4000


@needs_assets
@needs_snap
def test_song4_matches_vm_snapshot():
    """The level-14 baseline snapshot has song 4 loaded at 0x54B0 -- the
    native decompression must be byte-identical."""
    song = load_song(4, game_root=ASSETS)
    mem = SNAP.read_bytes()
    DG = 0x16860
    vm = mem[DG + 0x54B0:DG + 0x54B0 + len(song.data)]
    assert song.data == vm
    import struct
    assert struct.unpack_from("<H", mem, DG + 0x3194)[0] == 0x54B0


@needs_assets
@needs_snap
def test_world4_background_matches_vm_bank():
    """The snapshot's [5170] bank is world 4's background; rows 0..128 must be
    byte-identical (rows 129..137 carry a runtime road-horizon priming)."""
    assets = load_world_assets(14, game_root=ASSETS)
    mem = SNAP.read_bytes()
    import struct
    seg = struct.unpack_from("<H", mem, 0x16860 + 0x5170)[0]
    bank = mem[(seg << 4):(seg << 4) + len(assets.background)]
    n = 129 * BACKGROUND_W
    assert assets.background[:n] == bank[:n]


@needs_assets
@needs_snap
def test_composed_level14_palette_matches_real_dac():
    """ROADS' 72 colours -> DAC 0..71 + CMAP's 114 -> DAC 142..255 over the
    baseline DAC must reproduce the level-14 snapshot's palette EXACTLY."""
    import json
    from skyroads.native.level_load import decode_level_files
    from skyroads.native.world_load import expand6
    dac = [tuple(e) for e in json.loads(
        (SNAP.parent / "state.json").read_text())["dos"]["vga_palette"]]
    dec = decode_level_files(14, game_root=ASSETS)
    world = load_world_assets(14, game_root=ASSETS)
    mine = list(dac)
    for i in range(72):
        mine[i] = tuple(expand6(dec.palette[3 * i + k]) for k in range(3))
    for i in range(len(world.cmap) // 3):
        mine[CMAP_DAC_BASE + i] = tuple(
            expand6(world.cmap[3 * i + k]) for k in range(3))
    assert sum(1 for i in range(256) if mine[i] != tuple(dac[i])) == 0


def test_level30_special_mapping():
    assert world_for_level(30) == 9 and song_for_level(30) == 6
    assert world_for_level(14) == 4 and song_for_level(14) == 4
    assert world_for_level(0) == 0 and song_for_level(2) == 0
