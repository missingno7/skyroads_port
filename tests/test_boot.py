"""Native cold-boot builder (skyroads/native/boot.py)."""
from pathlib import Path

import pytest

from skyroads.native.boot import (
    DAC_CARS_BASE, DAC_DASHBRD_BASE, SEG_CARS_BANK, SEG_DASHBRD,
    native_boot_dac, native_boot_dgroup, native_boot_image,
    parse_lzs_container)
from skyroads.native.level_load import read_game_file

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
MENU = ROOT / "artifacts" / "boot_menu_1mb.bin"

needs_assets = pytest.mark.skipif(not ASSETS.exists(), reason="assets absent")
needs_menu = pytest.mark.skipif(not MENU.exists(), reason="menu capture absent")


@needs_assets
def test_container_parses():
    for name, (h, w) in [("CARS.LZS", (2310, 24)), ("DASHBRD.LZS", (71, 320)),
                         ("WORLD4.LZS", (138, 320)), ("GOMENU.LZS", (200, 320))]:
        cmap, aux, at, dest, hh, ww = parse_lzs_container(
            read_game_file(ASSETS, name))
        assert (hh, ww) == (h, w), name
        assert len(cmap) % 3 == 0 and len(cmap) > 0


@needs_assets
@needs_menu
def test_banks_byte_exact_vs_cold_boot():
    """The cars + dashbrd banks built natively (PICT decode + nonzero DAC
    bias) match the real cold boot's memory byte-exact."""
    img = native_boot_image(ASSETS)
    menu = MENU.read_bytes()
    for seg, size in ((SEG_CARS_BANK, 55440), (SEG_DASHBRD, 22720)):
        a = bytes(img[seg << 4:(seg << 4) + size])
        b = menu[seg << 4:(seg << 4) + size]
        assert a == b, hex(seg)


@needs_assets
@needs_menu
def test_dgroup_pointers_match_cold_boot():
    import struct
    dg = native_boot_dgroup(ASSETS)
    menu = MENU.read_bytes()
    DG = 0x16860
    for off in (0x0CB6, 0x4560, 0x54A6, 0x961C, 0xAF36,
                *range(0x0E76, 0x0E86, 2)):
        mine = struct.unpack_from("<H", bytes(dg), off)[0]
        vm = struct.unpack_from("<H", menu, DG + off)[0]
        assert mine == vm, hex(off)


@needs_assets
def test_native_dac_windows():
    dac = native_boot_dac(ASSETS)
    assert len(dac) == 256
    # cars 20 colours + dashbrd 50 colours populated, others untouched
    assert any(dac[DAC_CARS_BASE + i] != (0, 0, 0) for i in range(20))
    assert any(dac[DAC_DASHBRD_BASE + i] != (0, 0, 0) for i in range(50))
