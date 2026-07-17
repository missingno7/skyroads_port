"""Native SKYROADS.EXE unpacker (skyroads/recovered_native/exe_image.py)."""
from pathlib import Path

import pytest

from skyroads.recovered_native.exe_image import (
    DGROUP_REL, RELOC_SITES, build_program_image, decompress, initial_dgroup)

ROOT = Path(__file__).resolve().parents[1]
EXE = ROOT / "assets" / "SKYROADS.EXE"
TRUTH = ROOT / "artifacts" / "boot_unpacked_1mb.bin"

needs_exe = pytest.mark.skipif(not EXE.exists(), reason="game EXE absent")


@needs_exe
def test_unpack_invariants():
    img = decompress(EXE.read_bytes())
    assert len(img) == 0x75A2
    assert img[0] == 0xC8            # the first function's `enter`
    # DGROUP initialized data is inside the image
    assert (DGROUP_REL << 4) < len(img)


@needs_exe
@pytest.mark.skipif(not TRUTH.exists(), reason="cold-boot capture absent")
def test_unpack_matches_vm_post_unpack_memory():
    """build_program_image(load=0x1010) == the VM's memory at the stub's far
    jump (1010:61F3), captured from a real cold boot -- byte-exact, including
    the 3 relocation sites."""
    truth = TRUTH.read_bytes()
    img = build_program_image(EXE, 0x1010)
    prog = truth[0x10100:0x10100 + len(img)]
    assert bytes(img) == prog
    # and without relocs, EXACTLY the 3 sites differ (2 bytes each)
    raw = decompress(EXE.read_bytes())
    diff = [i for i, (a, b) in enumerate(zip(raw, prog)) if a != b]
    assert sorted(diff) == sorted(
        [a for site in RELOC_SITES for a in (site, site + 1)])


@needs_exe
def test_initial_dgroup_shape():
    dg = initial_dgroup(EXE)
    assert len(dg) == 0x10000
    # the DGROUP file-name constants are where the loaders expect them
    assert bytes(dg[0x0BFC:0x0BFC + 12]) == b"skyroads.cfg"
    assert bytes(dg[0x0E12:0x0E12 + 9]) == b"muzax.lzs"
