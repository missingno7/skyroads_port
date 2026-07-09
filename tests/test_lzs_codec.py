"""Regression test for skyroads.codecs.lzs against real oracle memory dumps.

CI has no game files: this module skips entirely when assets/TREKDAT.LZS is
missing, same pattern as test_skyroads_boot.py. The fixtures under
tests/fixtures/lzs/ are NOT re-derivable without the game asset, but they are
tiny (our own decoded-byte dumps, not a redistribution of the compressed
asset) and pin down the exact bug found in this module's history: the
short-distance formula was assumed get_bits(WIDTH_DIST_SHORT)+3 by analogy
with the long-distance branch; a full-record oracle-memory diff (anchored to
the real INT21h AH=3Dh open of TREKDAT.LZS, not a step-count guess) found the
real ASM does get_bits(WIDTH_DIST_SHORT)+0x400+2 (confirmed by disassembling
1010:6750 directly: "05 00 04" = ADD AX,0x0400). See skyroads/codecs/lzs.py's
module docstring for the full evidence trail.
"""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_LZS_FILE = ROOT / "assets" / "TREKDAT.LZS"
if not _LZS_FILE.is_file():
    pytest.skip("assets/TREKDAT.LZS not present — game files are never committed",
                allow_module_level=True)

from skyroads.codecs.lzs import LzsWidths, decompress_block  # noqa: E402

_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "lzs"
_WIDTHS = LzsWidths(width_len=4, width_dist_long=10, width_dist_short=13)


def test_trekdat_record0_matches_oracle_memory_exactly():
    ground_truth = (_FIXTURES / "trekdat_record0.bin").read_bytes()
    data = _LZS_FILE.read_bytes()
    payload = data[7:]  # 3-byte header (len, long-dist, short-dist widths) precedes the bitstream
    decoded = decompress_block(payload, _WIDTHS, len(ground_truth))
    assert decoded == ground_truth


def test_trekdat_record1_matches_oracle_memory():
    ground_truth = (_FIXTURES / "trekdat_record1_first3000.bin").read_bytes()
    data = _LZS_FILE.read_bytes()
    # Record 1's own 3-byte header immediately follows record 0's bitstream,
    # byte-aligned; its absolute file offset was found by reconstructing the
    # oracle's staging-buffer state (ds:41B2/41B4/41B6) across the refill
    # that happens partway through record 0's own decode — see
    # skyroads/hooks.py's lzs_decode_loop_hook for the same reconstruction
    # applied generally.
    payload = data[11375:]
    decoded = decompress_block(payload, _WIDTHS, len(ground_truth))
    assert decoded == ground_truth
