"""Native HUD gauge updater (skyroads/handrecovered_native/hud.py), verified against a
compact fixture captured from real `1010:12F8` calls (VM-traced 2026-07-13;
see run_status.md and the module docstring for the full recovery trail).

The fixture (`tests/fixtures/hud_gauge_calls.json`) holds, per call: the
DGROUP fields `update_hud` reads, the three widget banks' first 4 KB, the
three cell tables, and the REAL VM's resulting VGA byte diff (sparse). Cases
0..6 are the original increasing (fill) calls; case 0 includes the
(deliberately unported) fuel/oxygen digit-pair readout so it's checked with
that region excluded, the rest are VGA-exact. Cases 7..8 are DECREASING
(unfill) calls -- speed 29->28 and fuel 10->9 -- captured 2026-07-13 to close
the gap the user flagged (the fill path was verified but the unfill path
was not; the delta `flag=0` "off" redraw is what unfills a gauge).
"""
import json
from pathlib import Path

import pytest

from skyroads.handrecovered_native.hud import update_hud
from skyroads.handrecovered_native.image import NativeGameImage
from skyroads.handrecovered_native.state import DATA_SEG

FIXTURE = Path(__file__).parent / "fixtures" / "hud_gauge_calls.json"


def _build_image(case: dict) -> NativeGameImage:
    img = NativeGameImage()
    dg = DATA_SEG << 4
    for off_hex, val in case["fields"].items():
        img.ww(DATA_SEG, int(off_hex, 16), val)
    for seg_hex, hexbytes in case["banks"].items():
        seg = int(seg_hex, 16)
        data = bytes.fromhex(hexbytes)
        img.data[(seg << 4):(seg << 4) + len(data)] = data
    for off_hex, hexbytes in case["tables"].items():
        off = int(off_hex, 16)
        data = bytes.fromhex(hexbytes)
        img.data[dg + off:dg + off + len(data)] = data
    return img


@pytest.mark.skipif(not FIXTURE.exists(), reason="hud fixture not present")
def test_update_hud_matches_real_vga_output():
    cases = json.loads(FIXTURE.read_text())
    exact = 0
    for i, case in enumerate(cases):
        img = _build_image(case)
        ship_pos = case["fields"]["0x54ac"] | (case["fields"]["0x54ae"] << 16)
        update_hud(img, DATA_SEG, ship_pos)
        expected = {int(k): v for k, v in case["vga_diff"].items()}
        mismatches = []
        for rel, val in expected.items():
            if img.data[0xA0000 + rel] != val:
                mismatches.append(rel)
        if not mismatches:
            exact += 1
        elif i != 0:
            # every case except the known digit-pair one (0) must be exact
            pytest.fail(f"case {i}: {len(mismatches)} VGA byte(s) mismatched "
                       f"(first at rel {hex(mismatches[0])})")
    assert exact >= len(cases) - 1


@pytest.mark.skipif(not FIXTURE.exists(), reason="hud fixture not present")
def test_gauge_cache_values_match_vm():
    """The clamped speed/oxygen/fuel values update_hud derives must match
    the real VM's own post-call cache fields exactly, every case (7/7 when
    this fixture was captured)."""
    cases = json.loads(FIXTURE.read_text())
    for i, case in enumerate(cases):
        img = _build_image(case)
        ship_pos = case["fields"]["0x54ac"] | (case["fields"]["0x54ae"] << 16)
        update_hud(img, DATA_SEG, ship_pos)
        want = case["post_cache"]
        assert img.rw(DATA_SEG, 0x41BE) == want["speed"], f"case {i} speed"
        assert img.rw(DATA_SEG, 0x456C) == want["oxygen"], f"case {i} oxygen"
        assert img.rw(DATA_SEG, 0x960C) == want["fuel"], f"case {i} fuel"
