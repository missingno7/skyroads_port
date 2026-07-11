"""SkyRoads level directory reader — `ROADS.LZS`'s per-level header.

Where `native_menu_frame`'s level-select confirm actually gets its data from.
Traced this session (see `docs/skyroads/run_status.md`'s "RESOLVED: SkyRoads
loads levels from real .lzs compressed resource files" entry): arrow keys +
ENTER lead to a small read (`1010:568C-56A0`) of three words —
`jump_level_gate` (`ds:[4562]`), the level-timer-A divisor (`ds:[54A2]`), the
level-timer-B divisor (`ds:[4566]`) — via a buffered byte-stream reader
(`1010:6326`/`6490`/`6576`) whose ultimate source is a real, open DOS file:
`ROADS.LZS`.

**`ROADS.LZS`'s structure** (per ModdingWiki's "SkyRoads level format", which
documents an LZSS scheme for the ROAD GEOMETRY bytes — not yet ported here):
a self-terminating directory of `(UINT16LE offset, UINT16LE length)` pairs (one
per level, repeating until the read position reaches the FIRST entry's own
offset — that's where the level data actually starts, so the directory's own
size falls out of its first entry rather than needing a separate count field),
followed by each level's data: `UINT16LE gravity; UINT16LE fuel; UINT16LE
oxygen; BYTE palette[72*3]; BYTE road[]` (`road[]` LZSS-compressed, per
ModdingWiki; not implemented here — this module only needs the plain, leading
three words).

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

from skyroads.islands import oracle_link

#: bytes per directory entry: UINT16LE offset + UINT16LE length
DIRECTORY_ENTRY_SIZE = 4


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
