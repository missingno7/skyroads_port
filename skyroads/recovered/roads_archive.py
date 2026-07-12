"""SkyRoads level directory reader — `ROADS.LZS`'s per-level header, palette,
and (LZSS-decompressed) road geometry.

Where `native_menu_frame`'s level-select confirm actually gets its data from.
Traced this session (see `docs/skyroads/run_status.md`'s "RESOLVED: SkyRoads
loads levels from real .lzs compressed resource files" entry): arrow keys +
ENTER lead to a small read (`1010:568C-56A0`) of three words —
`jump_level_gate` (`ds:[4562]`), the level-timer-A divisor (`ds:[54A2]`), the
level-timer-B divisor (`ds:[4566]`) — via a buffered byte-stream reader
(`1010:6326`/`6490`/`6576`) whose ultimate source is a real, open DOS file:
`ROADS.LZS`.

**`ROADS.LZS`'s structure** (per ModdingWiki's "SkyRoads level format"): a
self-terminating directory of `(UINT16LE offset, UINT16LE length)` pairs (one
per level, repeating until the read position reaches the FIRST entry's own
offset — that's where the level data actually starts, so the directory's own
size falls out of its first entry rather than needing a separate count field),
followed by each level's data: `UINT16LE gravity; UINT16LE fuel; UINT16LE
oxygen; BYTE palette[72*3]; BYTE road[]`.

**The `road[]` bytes ARE LZSS-compressed — decoded here too**, reusing the
project's own already-VM-verified codec (`skyroads.codecs.lzs`, recovered in
an earlier session against `TREKDAT.LZS`/`MUZAX.LZS`/`INTRO.LZS`). `ROADS.LZS`
turns out to use a simpler per-entry header than those files' self-modifying-
code-patched widths: the three width bytes (`width_len`, `width_dist_long`,
`width_dist_short`, in that order) sit as plain bytes at the very start of
each entry's `road[]` data, right after the palette. **Verified 31/31 by
length** (every one of `ROADS.LZS`'s 31 levels decompresses to EXACTLY the
length the directory records) AND **byte-exact against the live VM** (2026-
07-12): drove the real game to a level-start, captured its full memory, and
found `read_level_road`'s natively decompressed output present VERBATIM in
the VM's own memory — gate-8/fuel-225/oxygen-111 (== index 14, a 3096-byte
road). So the DECODE is now proven against the original game, not just
self-consistent. See `tests/test_roads_archive.py`'s
`test_decompressed_road_matches_what_the_vm_loads_into_memory`.

**Verified 3/3 against real live-VM captures** (not a lift of one ASM routine,
so `boundary` below names the real consumer instead of a single lifted
address): `ROADS.LZS` directory index 16 → `(8, 200, 180)`, matching the demo
`demo_cold_20260711_201855`'s frame-282 capture; index 17 → `(7, 175, 60)`,
matching that same demo's frame-1327 capture (a REAL keyboard DOWN-ARROW +
ENTER level pick, traced via the pushed-argument register captures at
`1010:568C-56A0`); index 1 → `(8, 150, 180)`, matching frame 2016. All three
exact, including the "same gravity, different fuel" case (indices 0/6/8/etc.
also have `gravity=8` but different `fuel`) that had looked like an anomaly
before this file was found — it's just a flat, index-addressed table, not a
gravity-keyed lookup.
"""
from __future__ import annotations

import struct
from typing import List, NamedTuple, Tuple

from skyroads.codecs.lzs import LzsWidths, decompress_block
from skyroads.islands import oracle_link

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


@oracle_link(
    boundary="1010:568C-56A0",
    contract="read_level_header(data, index): given ROADS.LZS's raw bytes and a "
             "directory index, returns (gravity, fuel, oxygen) -- the exact "
             "three values the ASM reads via a buffered byte-stream over an "
             "open file handle into jump_level_gate/[54A2]/[4566]. Stored as "
             "three plain, UNCOMPRESSED UINT16LE words at the very start of "
             "each directory entry's data (only the road[] geometry bytes "
             "that follow are LZSS-compressed, per ModdingWiki -- not read "
             "by this function).",
    status="ASM_MATCHED",  # 3/3 real live-VM-captured (gravity,fuel,oxygen)
    # triples matched exactly: index 16 -> (8,200,180), index 17 -> (7,175,60),
    # index 1 -> (8,150,180). See run_status.md.
    merge_target="skyroads.native.menu (future)",
)
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
    The DECODE is verified byte-exact against the live VM (the decompressed
    bytes appear verbatim in the game's own memory at level-start, 2026-07-12
    — see the module docstring) as well as 31/31 by length; the FIELD
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
    _total_length = entries[index][1]
    road_offset = start + LEVEL_HEADER_LEN
    comp = data[road_offset:end]
    road_len = _total_length - LEVEL_HEADER_LEN
    width_len, width_dist_long, width_dist_short = comp[0], comp[1], comp[2]
    widths = LzsWidths(width_len=width_len, width_dist_long=width_dist_long,
                        width_dist_short=width_dist_short)
    return decompress_block(comp[3:], widths, road_len)
