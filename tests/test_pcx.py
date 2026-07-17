"""Native PCX decoder (skyroads/recovered_native/pcx.py) against LOGO.PCX."""
from pathlib import Path

import pytest

from skyroads.recovered_native.pcx import load_pcx

ROOT = Path(__file__).resolve().parents[1]
LOGO = ROOT / "assets" / "LOGO.PCX"


@pytest.mark.skipif(not LOGO.exists(), reason="game assets absent")
def test_logo_decodes_to_expected_dimensions():
    img = load_pcx(LOGO)
    assert (img.width, img.height) == (279, 156)
    assert len(img.pixels) == 279 * 156
    assert len(img.palette) == 256
