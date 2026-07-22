"""Recovery-evidence level-state initialization from original game data files.

This module can initialize a `NativeGameState` for any level without replay,
snapshot, or instruction interpretation. It owns the DGROUP gameplay-state
contract the authored simulation reads, reproduced from the level files, and fails loud
on anything not yet recovered (never a silent VM fallback). Render side effects
(the level-select → gameplay transition palette fade `4331`/`43A9`, the menu
glyph blits `0F62`, the tile-bitmap banks at segments `0x7176`/`0x7c3e`) are
deliberately OUT of scope here — they are the renderer's job, not the sim
contract. (There is NO separate "loading screen": the replays start ON the
level-select screen and run the level; the load is interleaved with the menu +
transition render, which is why it can't be cleanly isolated by write-tracing.)

This candidate is not independently registered as a runtime implementation or
bootstrap provider. Its module is currently retained in the selected native
dashboard/bootstrap dependency closure; no level-load identity is claimed.

What the authored simulation reads per level
(see docs/history/skyroads/run_status.md):
  * the road-cell geometry at `0x162C` (decoded from `ROADS.LZS[level]`),
  * per-level scalars — gravity (via the jump-level gate), fuel, oxygen.

The faithful loader implementation for `1010:5614` was disassembled
(churn-immune, from `gameplay_f640`) and its DGROUP writes reproduced here;
verified byte-exact against the VM (the level-select replay loads level 14 — its
`[4562]`/`[54A2]`/`[4566]`, `road[]@0x162C` and `palette@0x41C2` all match
`roads_archive`). KEY finding: the region historically called the
"perspective table" at `0x162C` is the seven-cell road grid from `ROADS.LZS`
(LZS-decoded); `1010:04C0` computes an address into that grid. There is no
separate WORLD perspective decode for the simulation; `WORLD*.LZS` supplies
render graphics. The geometry seed comes from `ROADS.LZS[level]`, which `roads_archive`
already decodes byte-exact — this module just places it.

Loader shape (`1010:5614`, verified): memset `[0x162C..+0x1B58]` (`5D07`); seek a
4-byte-per-level directory (`5CA6` lseek to `level*4`, `5F7D` read offset+size);
read 3 scalars (`6576` → gravity `[4562]`, fuel `[54A2]`, oxygen `[4566]`); read
the 216-byte palette (`6595` → `[41C2]`); LZS-decode `road[]` into `0x162C`
(`66E6`). File I/O (`5C77` open / `5F7D` read / `5C11` close) is C-lib stdio,
replaced here by the native :func:`read_game_file` shim.

[asm 1010:5614 geometry loader; 1010:66E6 LZS decode; ROADS.LZS on disk]
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import NamedTuple

from skyroads.handrecovered import roads_archive


@lru_cache(maxsize=64)
def read_game_file(game_root: str | Path, name: str) -> bytes:
    """The native file-read shim: read a game resource straight from disk, with
    NO VM/DOS layer. Replaces the game's `INT 21h` open+read (`0x6C2E` wrapper).
    Case-insensitive match so `ROADS.LZS` resolves on case-sensitive filesystems."""
    root = Path(game_root)
    target = root / name
    if target.exists():
        return target.read_bytes()
    lower = name.lower()
    for p in root.iterdir():
        if p.name.lower() == lower:
            return p.read_bytes()
    raise FileNotFoundError(f"game file {name!r} not found under {root}")


class DecodedLevel(NamedTuple):
    """The VM-free decode of a level's `ROADS.LZS` entry — the recovered,
    byte-exact input the loader's DGROUP placement consumes."""
    archive_index: int
    gravity: int
    fuel: int
    oxygen: int
    palette: bytes           # 72*3 VGA 6-bit RGB
    road: bytes              # UINT16LE[] road-geometry array (LZSS-decompressed)


def decode_level_files(archive_index: int, *, game_root: str | Path) -> DecodedLevel:
    """Decode `ROADS.LZS[level]` to its header + palette + road[] — 100% native
    (no VM), reusing the VM-verified `roads_archive` recovery. This is the
    file-decode half of native level loading; it is complete and testable today.

    This accepts the file identity, not ``DS:[9332]``. Playable callers map
    through :func:`skyroads.levels.road_archive_index` first.

    Raises IndexError if ``archive_index`` is out of range for the archive."""
    roads = read_game_file(game_root, "ROADS.LZS")
    archive_index = int(archive_index)
    if not (0 <= archive_index < roads_archive.level_count(roads)):
        raise IndexError(
            f"ROADS archive entry {archive_index} out of range "
            f"0..{roads_archive.level_count(roads) - 1}")
    hdr = roads_archive.read_level_header(roads, archive_index)
    return DecodedLevel(
        archive_index=archive_index,
        gravity=hdr.gravity, fuel=hdr.fuel, oxygen=hdr.oxygen,
        palette=roads_archive.read_level_palette(roads, archive_index),
        road=roads_archive.read_level_road(roads, archive_index),
    )


# DGROUP placement of the level geometry, recovered from the loader `1010:5614`
# (disassembled from gameplay_f640; churn-immune) and VERIFIED byte-exact against
# the VM (the level-select replay loads level 14: [4562]==gravity, [54A2]==fuel,
# [4566]==oxygen, road[]@0x162C and palette@0x41C2 all match roads_archive).
_PERSP_OFF = 0x162C       # legacy name: road[] geometry grid — [asm 5614: call 66E6]
_PERSP_CLEAR = 0x1B58     # region cleared before the road decode — [asm 5614: 5D07]
_GRAVITY_OFF = 0x4562     # jump-level gate / gravity — [asm 5614: 6576 -> [4562]]
_FUEL_OFF = 0x54A2        # [asm 5614: 6576 -> [54A2]]
_OXYGEN_OFF = 0x4566      # [asm 5614: 6576 -> [4566]]
_PALETTE_OFF = 0x41C2     # 216-byte level palette — [asm 5614: call 6595(0x41C2, 0xD8)]
_LENGTH_OFF = 0x41C0      # level length in road ROWS (7 UINT16 = 14 bytes each);
#                           the progress-bar denominator. `1010:5614` decodes the
#                           road and returns len(road)//14 (VM-verified: the replay
#                           level's 770-byte road -> 55, matching ds:[41C0]).
_ROAD_ROW_BYTES = 14


def native_level_load(
    state, archive_index: int, *, game_root: str | Path,
) -> DecodedLevel:
    """Populate ``state`` (a :class:`~skyroads.native.state.NativeGameState`)
    from an explicit archive entry, 100% VM-free, and return the decode.
    Playable callers must convert the selector through
    :func:`skyroads.levels.road_archive_index`.
    The caller then runs ``apply_level_init`` (player state) to reach a playable
    cold start.

    Reproduces the loader `1010:5614`'s DGROUP writes (verified byte-exact vs the
    VM): clear `[0x162C..+0x1B58]`, LZS-decode `road[]` into `0x162C`, and store
    the per-level scalars (gravity/fuel/oxygen) + the 216-byte palette at their
    fixed offsets. (The `WORLD*.LZS` tile-bitmap banks are render-only and NOT
    part of this sim contract — see the module docstring.)
    """
    decoded = decode_level_files(archive_index, game_root=game_root)  # native, verified
    d = state.data

    # [asm 5614: 5D07 memset(0x162C, 0, 0x1B58)] clear the region, then decode road[] in.
    for i in range(_PERSP_CLEAR):
        d[(_PERSP_OFF + i) & 0xFFFF] = 0
    if len(decoded.road) > _PERSP_CLEAR:
        raise ValueError(
            f"ROADS entry {archive_index} road[] ({len(decoded.road)}B) exceeds the "
            f"0x{_PERSP_CLEAR:X}-byte region at 0x{_PERSP_OFF:X}")
    d[_PERSP_OFF:_PERSP_OFF + len(decoded.road)] = decoded.road   # [asm 5614: 66E6 LZS-decode -> 0x162C]

    state.ww(_GRAVITY_OFF, decoded.gravity)                       # [asm 5614: 6576 -> [4562]]
    state.ww(_FUEL_OFF, decoded.fuel)                            # [asm 5614: 6576 -> [54A2]]
    state.ww(_OXYGEN_OFF, decoded.oxygen)                        # [asm 5614: 6576 -> [4566]]
    d[_PALETTE_OFF:_PALETTE_OFF + len(decoded.palette)] = decoded.palette  # [asm 5614: 6595 -> 0x41C2]
    state.ww(_LENGTH_OFF, len(decoded.road) // _ROAD_ROW_BYTES)   # [asm 5614 -> [41C0]]
    return decoded
