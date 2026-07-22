"""Authored recovery evidence for WORLD graphics and MUZAX song loading.

The recovered format shows that the three level-load decompressions come from
three different files:

* **MUZAX.LZS** (loader `1010:57C4`): the per-world SONG -> `DG:0x54B0`.
  File = 6-byte directory entries ``{u16 offset, u16 n_instruments,
  u16 decompressed_size}`` (count = first offset / 6 = 10, one per world),
  then per-song LZS records (3 width bytes + bitstream, `66E6`-style).
  After the decode, `1010:5A7D` points the music engine at it:
  ``[3194] = 0x54B0`` (instrument base, 16-byte patch records),
  ``[3196] = [3198] = 0x54B0 + 16*n_instruments`` (cursor + loop),
  ``[31A6] = 0``. `[0BF2]` caches the loaded song index.
* **ROADS.LZS** (loader `1010:5614`): road[] -> `0x162C` + scalars + the
  72-colour level palette -> `[41C2]` (already native, `level_load.py`).
* **WORLD<n>.LZS** (generic graphic loader `1010:4084`): the 320x138
  BACKGROUND -> the bank at segment `[5170]`. File = ``"CMAP" + u8 count +
  count x RGB6`` — the u8 at offset 4 is a color count (0x72 = 114
  colors = 342 bytes). Then graphic records: ``[u32 scalar][u16 h][u16 w]
  [3 width bytes][LZS bitstream]`` (`4084`: reads a scalar via `6576`, an
  8-byte descriptor whose ``[+4]*[+6]`` is the alloc size, then `66E6`).
  Background pixels are stored palette-relative and biased by **+0x8E =
  142**, the DAC base of the CMAP block.

During gameplay, ROADS' 72 colors occupy DAC 0..71 and CMAP's 114 colors
occupy DAC 142..255. DAC 72..141 is reserved for cockpit, ship, and HUD
colors.
"""
from __future__ import annotations

from functools import lru_cache
import struct
from typing import List, NamedTuple

from skyroads.codecs.lzs import LzsWidths, decompress_block
from skyroads.native.level_load import read_game_file

#: GRAPHICS are per-world: the background/palette a selected level shows is
#: ``world_for_level(level) = level // 3`` (verified: WORLD<n>.LZS byte-exact).
#: Music is selected independently; see :func:`pick_gameplay_song`.
LEVELS_PER_WORLD = 3

#: MUZAX song 0 is the INTRO track and song 2 the MENU track (cold-boot trace).
#: Gameplay never uses song 0; it draws from songs 1..9 -- nine tracks.
GAMEPLAY_SONG_COUNT = 9


def world_for_level(level: int) -> int:
    from skyroads.levels import validate_playable_level

    return validate_playable_level(level) // LEVELS_PER_WORLD


def pick_gameplay_song(rand_value: int, prev: "int | None" = None) -> "tuple[int, int]":
    """Reproduce the per-level random song pick at ``1010:0296-02C8``:

        di = rand_value % 9            # 02A4  div cx (cx=9)
        if di == prev: di = (di+1) % 9 # 02A8-02BE: no immediate repeat
        song_index = di + 1            # 02C5  add ax,1  (songs 1..9)

    The MUZAX index is ``di + 1``: songs 1..9 form the gameplay pool; song 0
    (the intro track) is NEVER chosen for gameplay. ``rand_value`` is the
    game's PRNG output (`call` at 02C, then ``div 9``); a detached-state caller
    supplies any random 16-bit value. ``prev`` is the PREVIOUS pick's ``di``
    (0..8, i.e. ``last_song_index - 1``), used only to avoid repeating the
    immediately-previous track. Returns ``(song_index, di)`` so the caller
    threads ``di`` back in as ``prev`` for the next level.
    """
    di = rand_value % GAMEPLAY_SONG_COUNT
    if prev is not None and di == prev:
        di = (di + 1) % GAMEPLAY_SONG_COUNT
    return di + 1, di
#: the DAC index where the CMAP colours load — also the bias added to
#: background pixels ([asm: the +0x8E offset observed on every pixel]).
CMAP_DAC_BASE = 0x8E
#: DGROUP offsets the song load touches ([asm 57C4/5A7D]).
SONG_BASE = 0x54B0
MUSIC_INSTR_BASE = 0x3194
MUSIC_CURSOR = 0x3196
MUSIC_LOOP = 0x3198
MUSIC_FLAG = 0x31A6
MUSIC_DELAY = 0x0C83
SONG_CACHE = 0x0BF2

BACKGROUND_W = 320
BACKGROUND_H = 138


class WorldAssets(NamedTuple):
    """The per-world render assets a detached-state implementation needs."""
    cmap: bytes         # 342 B: 114 VGA 6-bit RGB triples -> DAC 142..255
    background: bytes   # 44,160 B: 320x138, ALREADY +0x8E-biased DAC indices


class Song(NamedTuple):
    """One decompressed MUZAX song, ready to place at `0x54B0`."""
    index: int
    n_instruments: int  # 16-byte patch records at the start of ``data``
    data: bytes         # instrument records + event stream
    cursor: int         # initial [3196]/[3198]: SONG_BASE + 16*n_instruments


def expand6(v: int) -> int:
    """VGA 6-bit -> 8-bit DAC expansion, ``(v<<2)|(v>>4)``."""
    return ((v << 2) | (v >> 4)) & 0xFF


@lru_cache(maxsize=8)
def load_world_assets(level: int, *, game_root) -> WorldAssets:
    """Parse level ``level``'s world file: CMAP palette + the 320x138
    background (decompressed and +0x8E-biased, exactly as the game banks it)."""
    data = read_game_file(game_root, f"WORLD{world_for_level(level)}.LZS")
    if data[:4] != b"CMAP":
        raise ValueError("WORLD file does not start with CMAP")
    n_colours = data[4]                              # u8 COLOUR count (= 114)
    cmap = data[5:5 + 3 * n_colours]
    # Find the background record: 8-byte descriptor ending in h=138, w=320
    # ([asm 4084: alloc size = [si+4]*[si+6]]), then 3 LZS width bytes.
    needle = struct.pack("<HH", BACKGROUND_H, BACKGROUND_W)
    at = data.find(needle, 5 + 3 * n_colours)
    if at < 0:
        raise ValueError("background descriptor (138x320) not found")
    widths_at = at + 4
    widths = LzsWidths(data[widths_at], data[widths_at + 1], data[widths_at + 2])
    raw = decompress_block(data[widths_at + 3:], widths,
                           BACKGROUND_W * BACKGROUND_H)
    # bias into the WORLD DAC window; nonzero-only, matching the byte-exact
    # rule proven on the CARS/DASHBRD banks (background has no zero pixels,
    # so this changes nothing for it -- kept consistent regardless)
    biased = bytes((b + CMAP_DAC_BASE) & 0xFF if b else 0 for b in raw)
    return WorldAssets(cmap=cmap, background=biased)


def parse_muzax_directory(data: bytes) -> List[tuple]:
    """The MUZAX.LZS 6-byte directory: ``(offset, n_instruments, size)``."""
    first = struct.unpack_from("<H", data, 0)[0]
    return [struct.unpack_from("<3H", data, 6 * i) for i in range(first // 6)]


def load_song(index: int, *, game_root) -> Song:
    """Decompress MUZAX song ``index`` (= the world number for gameplay).
    Verified byte-exact vs the VM for song 4 (see module docstring)."""
    data = read_game_file(game_root, "MUZAX.LZS")
    entries = parse_muzax_directory(data)
    off, n_instr, size = entries[index]
    widths = LzsWidths(data[off], data[off + 1], data[off + 2])
    song = decompress_block(data[off + 3:], widths, size)
    return Song(index=index, n_instruments=n_instr, data=song,
                cursor=(SONG_BASE + 16 * n_instr) & 0xFFFF)


def native_song_load(state, song_index: int, *, game_root) -> Song:
    """Place MUZAX song ``song_index`` into ``state`` (a DGROUP-backed
    NativeGameState) and point the music engine at it, reproducing
    `57C4` + `5A7D`'s DGROUP writes: song bytes -> `0x54B0`, instrument
    base `[3194]`, cursor/loop `[3196]/[3198]`, flag `[31A6]=0`, delay
    `[0C83]=0`, song cache `[0BF2]`.

    ``song_index`` is an explicit MUZAX directory index (the real game picks
    it randomly per level -- see :func:`pick_gameplay_song`); this loader is
    deterministic given the index so callers/tests control the choice."""
    song = load_song(song_index, game_root=game_root)
    d = state.data
    d[SONG_BASE:SONG_BASE + len(song.data)] = song.data
    state.ww(MUSIC_INSTR_BASE, SONG_BASE)            # [asm 5A8D]
    state.ww(MUSIC_CURSOR, song.cursor)              # [asm 5A84]
    state.ww(MUSIC_LOOP, song.cursor)                # [asm 5A87]
    d[MUSIC_FLAG] = 0                                # [asm 5A90]
    d[MUSIC_DELAY] = 0
    state.ww(SONG_CACHE, song.index)
    return song
