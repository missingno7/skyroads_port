"""Read SkyRoads ``ROADS.LZS`` level headers, palettes, and road geometry.

The file begins with a self-terminating directory of
``(UINT16LE offset, UINT16LE length)`` pairs. Each entry contains gravity,
fuel and oxygen words, a 72-color palette, three LZS width bytes, and the
compressed road array. All 31 entries decompress to their declared lengths;
focused oracle tests also compare decoded headers and geometry with live game
state. See ``tests/test_roads_archive.py``.
"""
from __future__ import annotations

import struct
from typing import List, NamedTuple, Tuple

from skyroads.codecs.lzs import LzsWidths, decompress_block

#: bytes per directory entry: UINT16LE offset + UINT16LE length
DIRECTORY_ENTRY_SIZE = 4
#: gravity(2) + fuel(2) + oxygen(2) + a 72-entry, 3-byte VGA palette
LEVEL_HEADER_LEN = 6 + 72 * 3
#: bytes per decompressed road-array entry (UINT16LE, "seven values per line")
ROAD_VALUES_PER_LINE = 7


class LevelHeader(NamedTuple):
    gravity: int  # -> ds:[4562] jump_level_gate
    fuel: int     # -> ds:[54A2] level_timer_a divisor
    oxygen: int   # -> ds:[4566] level_timer_b divisor


def parse_directory(data: bytes) -> List[Tuple[int, int]]:
    """The self-terminating `(offset, length)` directory at the start of
    `ROADS.LZS`: entries repeat until the read position reaches the FIRST
    entry's own offset (where the first level's data actually starts)."""
    entries: List[Tuple[int, int]] = []
    pos = 0
    first_offset = None
    while True:
        offset, length = struct.unpack_from("<HH", data, pos)
        if first_offset is None:
            first_offset = offset
        entries.append((offset, length))
        pos += DIRECTORY_ENTRY_SIZE
        if pos >= first_offset:
            break
    return entries


def level_count(data: bytes) -> int:
    return len(parse_directory(data))


def _entry_span(data: bytes, entries: List[Tuple[int, int]], index: int) -> Tuple[int, int]:
    """(start, end) byte range of directory entry ``index``'s raw data --
    ``end`` is the next entry's offset, or EOF for the last entry (entries
    are packed contiguously, no padding between them)."""
    start = entries[index][0]
    end = entries[index + 1][0] if index + 1 < len(entries) else len(data)
    return start, end


def read_level_header(data: bytes, index: int) -> LevelHeader:
    entries = parse_directory(data)
    offset, _length = entries[index]
    gravity, fuel, oxygen = struct.unpack_from("<HHH", data, offset)
    return LevelHeader(gravity, fuel, oxygen)


def read_level_palette(data: bytes, index: int) -> bytes:
    """The level's 72-entry, 3-bytes-per-entry (6-bit RGB stored as 8-bit,
    per ModdingWiki) VGA palette -- plain bytes, right after the header."""
    entries = parse_directory(data)
    offset, _length = entries[index]
    start = offset + 6
    return data[start:start + 72 * 3]


def read_level_road(data: bytes, index: int) -> bytes:
    """The level's decompressed road-geometry array: an `UINT16LE[]`, seven
    values per line (per ModdingWiki's "SkyRoads level format" bit layout).
    The decode is verified byte-exact against the oracle and 31/31 by length;
    the field
    MEANINGS within each UINT16LE value, however, are sourced from
    ModdingWiki, not re-derived from ASM.

    `road[]`'s own tiny per-entry header -- three raw bytes, `(width_len,
    width_dist_long, width_dist_short)` in that order -- sits right after the
    palette; simpler than `TREKDAT.LZS`/`MUZAX.LZS`'s self-modifying-code-
    patched widths (`skyroads/codecs/lzs.py`'s docstring), but the same
    underlying LZ decode loop.
    """
    entries = parse_directory(data)
    start, end = _entry_span(data, entries, index)
    # The directory's length field is the DECOMPRESSED road size (the out_size the
    # loader `1010:5614` passes straight to the LZS decode `66E6(0x162C, size)` --
    # verified vs the VM: level 14's length=3318 decodes exactly 3318 bytes into
    # 0x162C, matching memory). The 222-byte header is UNCOMPRESSED and lives
    # before the compressed road, so it only shifts the input offset below; it
    # must not be subtracted from the decompressed output size.
    road_len = entries[index][1]
    road_offset = start + LEVEL_HEADER_LEN
    comp = data[road_offset:end]
    width_len, width_dist_long, width_dist_short = comp[0], comp[1], comp[2]
    widths = LzsWidths(width_len=width_len, width_dist_long=width_dist_long,
                        width_dist_short=width_dist_short)
    return decompress_block(comp[3:], widths, road_len)
