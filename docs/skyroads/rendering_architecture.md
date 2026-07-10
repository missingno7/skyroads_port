# SkyRoads rendering architecture — is it a 3D engine? (2026-07-10)

**Verdict: no. SkyRoads is a table-driven pseudo-3D ("fake 3D road") renderer** in
the *Pole Position → Outrun* lineage — not a 3D engine. There is **no matrix
multiply, no rotation, no z-buffer, no polygon rasterization, and no per-vertex
perspective divide** anywhere in the recovered code. The 3D illusion comes from
**precomputed perspective tables + per-slice offset accumulators + pre-shaded
tile/sprite art**, all driven by integer shifts/adds, table lookups, and the
32-bit long-mul/div helpers used for linear interpolation.

This document is grounded in the verified island (see `symbol_ledger.md`); every
routine named below is either byte-exact VERIFIED or fully disassembled.

## The pipeline, top to bottom

```
main loop (22xx) --per frame--> 0C98 render frame
  road-walk (26xx): steps the road grid, one depth-rank at a time
    186B  road-space CONVERGENCE stepper   [decoded; see below]
      1732  road_object_visible (per-segment cull)     VERIFIED
        04C0  perspective_transform (table LOOKUP)      VERIFIED
        1631  road_segment_clip (screen-bound tables)   ASM_MATCHED
  rasterizers (bitmaps, not polygons):
    38BF  road_column_strip (scanline strips)           VERIFIED
    325B  tile_rasterizer  = 32C1 mask + blit + 33FD shade   VERIFIED
    3153/3190 rle_sprite (ship / objects)               VERIFIED
  math primitives: 5D4C mul / 5D8C udiv / 5E5A sdiv     VERIFIED
```

## Why it is NOT 3D (the decisive evidence)

1. **The projection is precomputed, never live.** The perspective table at
   `ds:0x162C` does **not change across 200 frames of gameplay** — built once at
   load. `04C0` (verified over 34,786 calls) computes a screen value by:
   - `row_index = (depth >> 7) − 95` — depth → screen row by a **shift and
     subtract**, then a table index (`/46`);
   - a horizontal term `X × 14 / 65536` — X scaled by a **constant**, *not*
     divided by depth;
   - returning `ds:[0x162C + …]` — a **table read**.
   Real perspective is `screen = worldXY / z`. Here that division is baked into
   the lookup tables; the transform itself never divides by depth.

2. **The world is 2D "road-space", walked and linearly interpolated.** `186B`
   (the segment stepper, decoded in full) converges four position accumulators —
   a 32-bit lateral X (`9618:961A`), a depth (`AF1C`), and a screen-Y (`AF2C`) —
   from the current position toward a target, using `1732` (visibility
   projection) as an oracle. Its inner math is pure **linear interpolation**:
   `interp = current + (target − current) · si / 5` — the 16-bit coordinates via
   `imul; idiv 5`, the 32-bit X via `ulong_mul` then `signed_long_div`. It then
   fine-tunes with fixed-step searches (X step `0x1000` halved by `÷0x10`; depth
   and screen-Y step `±0x7D` halved by `÷5`) until converged. No trig, no
   matrices — a 2D DDA/bisection in road-space, projected per candidate by table
   lookup.

3. **Curves and hills are per-slice offsets, not geometry.** The road-walk
   computes each slice's screen placement as **base projection + an additive
   accumulator**: `screen_y = [9336] + [AF2C]`, where `[9336]` is a bounded,
   wrapping offset updated as the walk proceeds. Each horizontal slice of road is
   *shifted*, rather than the road bending in 3D — the textbook Outrun road-curve
   trick. (Observed static/flat in the available demo, which holds `[9336]≈1`;
   the mechanism is in the code structure and the additive `screen_y`.)

4. **Rasterization is blitting, not triangles.** `38BF` copies vertical pixel
   runs from a source bitmap to the screen (scanline strips). `325B` draws road
   **tiles** as bitmap blits through a coverage mask (`32C1`) then applies a
   brightness ramp (`33FD`: colour `+0x2D`) — the "3D" block faces are
   **pre-drawn, pre-shaded tile art** composited per position. Objects/ship are
   **RLE sprites** (`3153`/`3190`). Painter's order comes from walking segments
   front-to-back; there is no z-buffer.

## What the "3D" actually is

A **heightfield of blocks addressed in a 2D grid** (lateral × distance). Each
frame the engine walks the grid by distance; for each cell it looks up the
screen row/scale from the static projection tables, adds the per-slice curve/
height offset, and blits the appropriate pre-shaded tile (or scanline strip) at
that scale. Foreshortening, the vanishing point, block "height" and road
curvature all fall out of **tables + artist art + integer interpolation**. That
is why it ran smoothly on a 286 with no FPU.

## Confidence and the remaining sliver

- Verdict confidence: **high**, grounded at the transform (`04C0`), cull
  (`1732`/`1631`), stepper (`186B`, decoded), and rasterizer (`38BF`/`325B`)
  levels — all VERIFIED or fully disassembled — plus the static-table proof.
- Not yet byte-exact-hooked: `186B` (the stepper) and the road-walk (`26xx`) /
  frame root (`0C98`). `186B` is fully decoded (a ~150-instruction, 5-phase
  iterative solver that mutates the position accumulators); a byte-exact hook is
  the largest single remaining recovery and is the natural next collapse of the
  road-segment path (`186B` would subsume `1732`+`04C0`+the interpolation math).
- The one behaviour not *demonstrated* dynamically: the curve/height offset
  sweeping over a hilly/curvy stretch — the available demo is a flat, near-static
  section. A few seconds recorded over hills/curves would show `[9336]`/`[AF2C]`
  driving the vertical directly.
