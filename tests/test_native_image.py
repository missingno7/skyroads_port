"""Verify skyroads.handrecovered_native.image.NativeGameImage -- the full 1 MB real-mode
address space (a separate, ADDITIVE class from skyroads.handrecovered_native.state.
NativeGameState, which stays DGROUP-only; see image.py's module docstring for
why the renderer needs real physical segment addressing)."""
from __future__ import annotations

from skyroads.handrecovered_native.image import ADDR_SPACE, NativeGameImage


def test_default_is_zeroed_full_address_space() -> None:
    img = NativeGameImage()
    assert len(img.data) == ADDR_SPACE
    assert img.rb(0x1686, 0x456E) == 0


def test_rw_ww_round_trip_at_a_real_segment_offset() -> None:
    img = NativeGameImage()
    img.ww(0x1686, 0x456E, 0x1234)
    assert img.rw(0x1686, 0x456E) == 0x1234
    assert img.rb(0x1686, 0x456E) == 0x34
    assert img.rb(0x1686, 0x456F) == 0x12


def test_different_segments_are_independent() -> None:
    img = NativeGameImage()
    img.wb(0x1686, 0, 0xAA)
    img.wb(0x2000, 0, 0xBB)
    assert img.rb(0x1686, 0) == 0xAA
    assert img.rb(0x2000, 0) == 0xBB


def test_offset_wraps_at_64k() -> None:
    img = NativeGameImage()
    img.wb(0x1000, 0x10000, 0x42)   # off wraps to 0
    assert img.rb(0x1000, 0) == 0x42


def test_undersized_seed_data_is_padded() -> None:
    img = NativeGameImage(bytearray(10))
    assert len(img.data) == ADDR_SPACE
