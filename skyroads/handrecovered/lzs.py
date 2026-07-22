"""CPU-independent semantic surface for SkyRoads' recovered LZS decoder."""
from __future__ import annotations

from skyroads.codecs.lzs import LzsWidths, decompress_block


def decode_lzs_block(payload: bytes, widths: LzsWidths, out_size: int) -> bytes:
    """Decode one recovered 66E6 block without exposing carrier state."""
    return decompress_block(payload, widths, out_size)


__all__ = ["decode_lzs_block"]
