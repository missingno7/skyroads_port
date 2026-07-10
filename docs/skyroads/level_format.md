# SkyRoads level file format — WORLD*.LZS (2026-07-10)

Decoded from `WORLD7.LZS` (cross-checked against `WORLD0..9.LZS` and the game's
own decompression during a level-load demo). The design is exactly as expected
for this engine: **beautifully data-driven** — each world file bundles its
palette, level grid, *render tables* (including the "perspective" projection
LUT), and tile graphics, all LZS-compressed.

## Container layout

A `WORLD*.LZS` file is a small chunked container followed by one LZS payload:

```
offset 0x00  "CMAP"                       4-byte tag
offset 0x04  <u32 size = 0x72 = 114>      little-endian
offset 0x08  114 bytes                    the level PALETTE: 38 VGA colours
                                          (6-bit RGB triples, 38*3 = 114)
offset 0x7A  ... LZS-compressed payload ... (to EOF)
```

Every `WORLD*.LZS` observed begins `43 4D 41 50 72 00 00 00` (`"CMAP"`, size 114);
the 114 CMAP bytes differ per world (each level has its own colour scheme).

## The LZS payload → three blocks

The game decompresses the payload (via the recovered LZS decoder, see
`skyroads/codecs/lzs.py`) into **three** blocks — captured live during the
level-load demo:

| Block | Lands at (seg:off) | Size | Role |
|---|---|---|---|
| A | `1686:54B0` | 7,926 B | **the level GRID** (block layout) — see below |
| B | `1686:162C` | 3,136 B | **render tables**, including the projection LUT `04C0` reads at `ds:0x162C` |
| C | `7176:0000` | 44,160 B | **tile / sprite bitmap graphics** (the `0x7176` source segment the rasterizers blit from) |

**Key finding — the "3D" is precomputed data, not code.** Block B lands exactly
at `ds:0x162C`, which is where the `04C0` perspective transform does its table
lookup. So the projection table is **shipped in the level file and loaded**, not
computed at runtime. That is the most direct possible confirmation of the
table-driven pseudo-3D design (see `rendering_architecture.md`): the entire
"perspective" is a LUT baked into the data.

## Block A — the level grid

After a small header/pointer table (`54B0`..~`5537`, far-pointer records), block
A is a dense structure of per-road-row block descriptors. Cells are nibble-coded
`(high nibble = lane 1..7, low nibble = type)` paired with a value byte; empty
cells are `0x00` (1,487 of the 7,926 bytes are zero). The road is a **2D grid of
lanes × distance**; each non-empty cell places a block of a given type/height.
(Exact per-cell byte semantics — height vs colour vs bitmap-index split — is the
next decode step; it needs correlating with the road-walk's grid iterator.)

## How a cell becomes pixels

The road-walk reads the grid and, per visible cell, calls the generic tile
drawer:

```
2D1F draw_tile(screenX, Y, ?, bitmap_off, bitmap_seg, ?, height, dst_seg)
     -> stores args to globals 0E28..0E36
     -> dispatches to a rasterizer via `call [0E38]` (tile path)
        or the per-tile handler `[0E40]` (RLE sprites 3153/3190)
```

So a block's on-screen appearance is a **pre-drawn bitmap** blitted at a screen
position derived from the grid cell + the projection LUT (block B). A block type
ships as a **set of pre-scaled frames** (in the `5E61` graphics bank); as a block
approaches, the renderer swaps among those frames by distance rather than scaling
at runtime (see `rendering_architecture.md` §3b). Empirically, a grid cell is
read **once when it scrolls into the visible range** and its instance cached;
per-frame draws then use the cached position + a distance-selected frame (the
`draw_tile` `height` arg is the block's *current projected screen height*, e.g.
`0x08→0x2F→…` as it nears and passes, not a raw grid value).

**Not yet pinned:** the exact per-cell field split of block A (which nibbles/
bytes are lane vs block-type vs colour vs the grid coordinate) — that needs
tracing the grid-scan that spawns block instances (distinct from the per-frame
draw path observed here). The container format, block roles, and the
projection-LUT-is-data finding above are solid.

## Summary

A SkyRoads world is a self-contained data package:
**palette (CMAP) + level grid (block A) + render/projection tables (block B) +
tile bitmaps (block C)**, LZS-compressed. The renderer is a thin, data-driven
walker over this package. This is why the game is small, fast, and — as the
author noted — simple and well-structured.
