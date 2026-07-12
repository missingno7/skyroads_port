"""Native, VM-free level loading — the milestone-1 spine (`play_native --level N`).

Goal (user-set 2026-07-12): boot a `NativeGameState` for ANY level from the game
files alone — no demo, no snapshot, no VM. This module is the SkyRoads analog of
`pre2_port`'s `pre2/native/level_load.py`: it owns the **DGROUP gameplay-state
contract** the native sim reads, reproduced from the level files, and FAILS LOUD
on anything not yet recovered (never a silent VM fallback). Render side effects
(the level-select → gameplay transition palette fade `4331`/`43A9`, the menu
glyph blits `0F62`, the tile-bitmap banks at segments `0x7176`/`0x7c3e`) are
deliberately OUT of scope here — they are the renderer's job, not the sim
contract. (There is NO separate "loading screen": the demos start ON the
level-select screen and run the level; the load is interleaved with the menu +
transition render, which is why it can't be cleanly isolated by write-tracing.)

What the native SIM actually reads per level (see docs/skyroads/run_status.md):
  * the `0x162C` perspective LUT (LZS-decompressed from `WORLD<n>.LZS` block B),
  * the road-cell geometry (derived from `ROADS.LZS[level]` `road[]`),
  * per-level scalars — gravity (via the jump-level gate), fuel, oxygen.

STATUS (honest): the FILE-DECODE half is recovered and VM-free today —
`ROADS.LZS[level]` decompresses byte-exact (`roads_archive`, verified vs the VM),
and the recovered `codecs/lzs` handles the `WORLD*.LZS` LZS payloads. The
DGROUP-PLACEMENT half (road[]→cells, the `WORLD` perspective sub-block →
`0x162C`, the exact scalar offsets + level→world map) is produced by the game's
loader (`1010:4B8E` + the `0x6C2E` file wrapper + `6712` decode + `5D18`/`5F95`
copies), which is entangled at RUNTIME with the loading-screen render — so it
cannot be cleanly isolated by write-tracing. It is being recovered by LIFTING
that loader family (liftgen census: 10/10 liftable, ~215 insts) + a native
file-read shim, exactly as pre2 recovered its `3ed6`. Until that lands,
:func:`native_level_load` fails loud at the placement boundary.

[asm 1010:4B8E level-load orchestrator; 1010:6712 lzs_decode_loop; ROADS.LZS +
 WORLD<n>.LZS on disk]
"""
from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

from skyroads.recovered import roads_archive


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
    index: int
    gravity: int
    fuel: int
    oxygen: int
    palette: bytes           # 72*3 VGA 6-bit RGB
    road: bytes              # UINT16LE[] road-geometry array (LZSS-decompressed)


def decode_level_files(level: int, *, game_root: str | Path) -> DecodedLevel:
    """Decode `ROADS.LZS[level]` to its header + palette + road[] — 100% native
    (no VM), reusing the VM-verified `roads_archive` recovery. This is the
    file-decode half of native level loading; it is complete and testable today.

    Raises IndexError if `level` is out of range for the archive."""
    roads = read_game_file(game_root, "ROADS.LZS")
    if not (0 <= level < roads_archive.level_count(roads)):
        raise IndexError(
            f"level {level} out of range 0..{roads_archive.level_count(roads) - 1}")
    hdr = roads_archive.read_level_header(roads, level)
    return DecodedLevel(
        index=level,
        gravity=hdr.gravity, fuel=hdr.fuel, oxygen=hdr.oxygen,
        palette=roads_archive.read_level_palette(roads, level),
        road=roads_archive.read_level_road(roads, level),
    )


def native_level_load(state, level: int, *, game_root: str | Path):
    """Populate ``state`` (a :class:`~skyroads.native.state.NativeGameState`)
    with level ``level``'s geometry seed, VM-free, then hand off to the driver's
    ``apply_level_init`` (player state) at the call site.

    Recovered so far: the file decode (:func:`decode_level_files`). NOT yet
    recovered: the loader's DGROUP PLACEMENT (road[]→cells, WORLD perspective →
    `0x162C`, scalar offsets, level→world map) — being lifted from the `4B8E`
    loader family. Fails loud here rather than seed a wrong/empty geometry that
    would make the sim silently diverge.
    """
    decoded = decode_level_files(level, game_root=game_root)  # native, verified
    raise NotImplementedError(
        "native DGROUP placement for the level geometry is not recovered yet "
        "(0x162C perspective LUT, road-cell array, per-level scalars). The file "
        "decode is done (see the returned DecodedLevel); the placement is being "
        "recovered by lifting the 4B8E loader family (see "
        "docs/skyroads/run_status.md, task #21). "
        f"decoded level {decoded.index}: gravity={decoded.gravity:#06x} "
        f"road={len(decoded.road)}B palette={len(decoded.palette)}B"
    )
