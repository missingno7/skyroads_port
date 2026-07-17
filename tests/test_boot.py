"""Native cold-boot builder (skyroads/recovered_native/boot.py)."""
from pathlib import Path

import pytest

from skyroads.recovered_native.boot import (
    DAC_CARS_BASE, DAC_DASHBRD_BASE, DASHBOARD_LEN, DASHBOARD_VGA_OFFSET,
    SEG_CARS_BANK, SEG_DASHBRD, SEG_DISPLAY_LISTS, SEG_FUL_BANK,
    SEG_OXY_BANK, SEG_SFX_BANK, SEG_SPEED_BANK, native_boot_dac,
    native_boot_dgroup, native_boot_image, paint_dashboard,
    parse_lzs_container)
from skyroads.recovered_native.level_load import read_game_file

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
def test_oxy_ful_banks_hold_the_right_file_not_swapped():
    """SEG_OXY_BANK/SEG_FUL_BANK were briefly swapped this session (a real
    bug: content matched their WRONG name). Guard against a regression by
    content, not by name: SEG_OXY_BANK must hold OXY_DISP.DAT's own stencil
    bytes, and SEG_FUL_BANK must hold FUL_DISP.DAT's."""
    img = native_boot_image(ASSETS)
    oxy = read_game_file(ASSETS, "OXY_DISP.DAT")[20:]
    ful = read_game_file(ASSETS, "FUL_DISP.DAT")[20:]
    assert bytes(img[SEG_OXY_BANK << 4:(SEG_OXY_BANK << 4) + len(oxy)]) == oxy
    assert bytes(img[SEG_FUL_BANK << 4:(SEG_FUL_BANK << 4) + len(ful)]) == ful


@needs_assets
def test_speed_and_sfx_banks_survive_the_dgroup_overlap():
    """SEG_SPEED_BANK and SEG_SFX_BANK physically overlap DGROUP_SEG's 64 KB
    window (same latent bug as the OXY/FUL swap test) -- both must still hold
    their real file content after native_boot_image runs."""
    img = native_boot_image(ASSETS)
    spd = read_game_file(ASSETS, "SPEED.DAT")[68:]
    sfx = read_game_file(ASSETS, "SFX.SND")
    assert bytes(img[SEG_SPEED_BANK << 4:(SEG_SPEED_BANK << 4) + len(spd)]) == spd
    assert bytes(img[SEG_SFX_BANK << 4:(SEG_SFX_BANK << 4) + len(sfx)]) == sfx


@needs_assets
@needs_menu
def test_dgroup_pointers_match_cold_boot():
    import struct
    dg = native_boot_dgroup(ASSETS)
    menu = MENU.read_bytes()
    DG = 0x16860
    for off in (0x0CB6, 0x4560, 0x54A6, 0x5476, 0x9610, 0x961C, 0xAF36,
                *range(0x0E76, 0x0E86, 2)):
        mine = struct.unpack_from("<H", bytes(dg), off)[0]
        vm = struct.unpack_from("<H", menu, DG + off)[0]
        assert mine == vm, hex(off)


@needs_assets
@needs_menu
def test_display_list_buffers_byte_exact_vs_cold_boot():
    """TREKDAT.LZS record framing (two raw header words A,B -> size=B,
    dest_off=A-B; the loader also stamps dest_off as a bookmark word at the
    segment's own offset 0) + the recovered 3A96 expansion must reproduce
    all 8 dl buffers byte-exact against a real cold boot."""
    img = native_boot_image(ASSETS)
    menu = MENU.read_bytes()
    for seg in SEG_DISPLAY_LISTS:
        a = bytes(img[seg << 4:(seg << 4) + 0x10000])
        b = menu[seg << 4:(seg << 4) + 0x10000]
        assert a == b, hex(seg)


@needs_assets
def test_paint_dashboard_masks_zero_pixels_and_fills_the_vga_tail():
    """paint_dashboard must only touch nonzero dashboard pixels (leaving the
    live road/sky render visible through the windshield cutout), and must
    reach exactly to the end of the VGA plane (129 + 71 == 200 rows)."""
    img = native_boot_image(ASSETS)
    vga_before = bytes(img[0xA0000:0xA0000 + 0xFA00])
    # poison the whole VGA plane so we can detect exactly what changes
    for i in range(0xA0000, 0xA0000 + 0xFA00):
        img[i] = 0x01
    paint_dashboard(img, SEG_DASHBRD)
    dashboard = img[SEG_DASHBRD << 4:(SEG_DASHBRD << 4) + DASHBOARD_LEN]
    changed = 0
    for i in range(DASHBOARD_LEN):
        vga_i = 0xA0000 + DASHBOARD_VGA_OFFSET + i
        if dashboard[i]:
            assert img[vga_i] == dashboard[i]
            changed += 1
        else:
            assert img[vga_i] == 0x01          # left untouched (transparent)
    assert changed > 0
    assert DASHBOARD_VGA_OFFSET + DASHBOARD_LEN == 0xFA00   # exactly row 200


@needs_assets
def test_native_dac_windows():
    dac = native_boot_dac(ASSETS)
    assert len(dac) == 256
    # cars 20 colours + dashbrd 50 colours populated, others untouched
    assert any(dac[DAC_CARS_BASE + i] != (0, 0, 0) for i in range(20))
    assert any(dac[DAC_DASHBRD_BASE + i] != (0, 0, 0) for i in range(50))
