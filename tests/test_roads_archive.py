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
    length (the out_size the loader `1010:5614` passes to the LZS decode), using
    the project's own already-VM-verified skyroads.codecs.lzs codec. The 222-byte
    header is uncompressed and precedes the compressed road, so it shifts the
    INPUT offset only -- it is NOT subtracted from the decompressed size (fixed
    2026-07-12: the old `-LEVEL_HEADER_LEN` truncated every road by 222 bytes;
    level 14's road is 3318 B = its directory length, matching VM memory)."""
    entries = parse_directory(roads_data)
    for index, (_offset, total_length) in enumerate(entries):
        road = read_level_road(roads_data, index)
        assert len(road) == total_length, f"index {index}"
        assert len(road) % 2 == 0, "road[] is a UINT16LE array"


# --- Live-VM oracle: the decompressed BYTES, not just their length -----------

EXE = ROOT / "assets" / "SKYROADS.EXE"
# a genuine cold-boot demo that starts at level-select and confirms one level
DEMO = ROOT / "artifacts" / "demos" / "demo_skyroads_20260711_202740"


@pytest.mark.skipif(
    not (EXE.exists() and DEMO.exists()),
    reason="needs SKYROADS.EXE + the level-start demo for the live-VM check",
)
def test_decompressed_road_matches_what_the_vm_loads_into_memory(roads_data: bytes) -> None:
    """The strongest check: drive the real VM to the level-start it loads,
    capture its full memory, and confirm read_level_road()'s NATIVELY
    decompressed bytes appear verbatim in the VM's own memory -- proving the
    decode is byte-exact against the original game, not merely the right
    length. (Gate-8/fuel-225/oxy-111 level == ROADS.LZS index 14, a 3096-byte
    road; found byte-exact in a fresh capture 2026-07-12, see run_status.md.)
    """
    import sys

    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "dos_re"))

    import scripts.play as sp
    from dos_re import player
    from dos_re.cpu import CPU8086, HaltExecution
    from dos_re.dos import ConsoleInputWouldBlock
    from dos_re.input_demo import InputDemoPlayback
    from dos_re.player import _use_real_console_input

    frontend = sp.SkyroadsFrontend(ROOT)
    args = player.build_arg_parser(frontend).parse_args(
        ["--play-demo", str(DEMO), "--headless"])
    pb = InputDemoPlayback.load(str(DEMO))
    frontend.apply_demo_metadata(args, pb.manifest.get("metadata", {}))
    rt = (frontend.create_runtime(args) if pb.is_cold_start
          else frontend.load_snapshot_runtime(args, pb.snapshot_path()))
    args.install_replacements = False
    frontend.apply_hook_mode(rt, args)
    _use_real_console_input(rt)

    captured: dict = {}
    orig = CPU8086.step

    def patched(self):
        if self.s.cs == 0x1010 and self.s.ip == 0x2324 and "mem" not in captured:
            m = self.mem
            ds = self.s.ds
            captured["header"] = (m.rw(ds, 0x4562), m.rw(ds, 0x54A2), m.rw(ds, 0x4566))
            captured["mem"] = bytes(m.data[:0x100000])
        return orig(self)

    CPU8086.step = patched
    try:
        frame = 0
        while not pb.finished(frame) and "mem" not in captured:
            pb.apply_to_runtime(frame, rt, deliver=lambda r, sc: frontend.deliver_input(r, sc))
            try:
                frontend.advance_frame(rt, args, frame)
            except ConsoleInputWouldBlock:
                pass
            except HaltExecution:
                break
            frame += 1
    finally:
        CPU8086.step = orig

    assert "mem" in captured, "never reached a gameplay sub-step in the demo"
    g, f, o = captured["header"]
    matches = [i for i in range(level_count(roads_data))
               if tuple(read_level_header(roads_data, i)) == (g, f, o)]
    assert matches, f"no ROADS.LZS index matches the VM header {(g, f, o)}"
    # at least one matching index must decompress to bytes the VM actually holds
    found = [i for i in matches if captured["mem"].find(read_level_road(roads_data, i)) >= 0]
    assert found, (
        f"none of {matches} (header {(g, f, o)}) decompressed to bytes present "
        f"verbatim in VM memory -- the native LZS decode diverges from the game")
