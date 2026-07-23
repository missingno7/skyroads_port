# SkyRoads rendering recovery

The original renderer and the high-resolution renderer answer two different
questions. The original path is the pixel oracle. The native path reconstructs
the stable world and geometric intent that produced those pixels, without
carrying 320x200 rounding and eight-frame projection snapping into a modern
viewport.

Neither path reads road geometry from the framebuffer or writes presentation
state back into gameplay.

## Original data-to-frame path

```text
ROADS.LZS entry (selected level + 1; entry 0 is the attract course)
  -> 1010:5614 decodes seven uint16 cells per row into DS:162C
  -> gameplay/collision reads through 1010:04C0, 1631, and 1732
  -> 1010:0C98 derives frame parameters
  -> 1010:2D1F walks 11 rows and dispatches six structural cell types
  -> 1010:34AE / 38BF select TREKDAT display-list strips
  -> 1010:3153 / 3190 rasterize mirrored RLE spans
  -> background, road, ship seam, cockpit, HUD, and page composition
  -> VGA framebuffer
```

SkyRoads is table-driven pseudo-3D. `TREKDAT.LZS` contains eight sub-row
phases of preprojected strips. The recovered original path preserves their
selection, asymmetric palettes, clipping, painter order, and the `325B` ship
insertion seam. It is exposed as `exact-projection` and remains the strict
raster reference.

## Stable source world

The native branch begins before projection:

```text
ROADS.LZS + authoritative DGROUP state
  -> immutable GameplayScene / RoadGeometry
  -> stable lane, row, and elevation vertices
  -> one recovered continuous pseudo-perspective lens
  -> ModernGL rasterization at the window resolution
```

Every source word has identity `level:<selected>:road:<row>:<lane>` and maps
to `1686:(162C + row*14 + lane*2)`. Its recovered bit layout is:

| Bits | Meaning |
|---|---|
| 0..3 | deck/bottom material; zero means no deck |
| 4..7 | optional raised-top material |
| 8 | passage flag: exposed tube alone, carved passage when combined with a block bit |
| 9 | half-height block |
| 10 | full-height block |
| 11..15 | unused by shipped levels |

The authoritative coordinate scales are:

| State | Meaning | Scale |
|---|---|---|
| `DS:9618:961A` | forward track coordinate | one row per `0x10000` |
| `DS:AF1C` | cross-road coordinate | one lane per `46 * 0x80 = 0x1700` |
| `DS:AF2C` | vertical coordinate | deck `0x2800`, half `0x3200`, full `0x3C00` |
| `DS:54AC:54AE` | forward velocity | accumulated into the track coordinate |

A road cell is always one lane by one row. Half/full blocks keep fixed
dimensions. The original dispatch establishes two tunnel families rather than
one arch decoration:

| Selector | Bits | Native structure | Original evidence roles |
|---|---|---|---|
| 1 | tunnel | exposed rounded tube | six outer shade bands, front rim, inner rim, underside |
| 3 | tunnel + half | arched passage carved through a half-height solid | front rim, top, side, inner side, underside |
| 5 | tunnel + full | the same passage in a full-height solid | front rim, lower/upper exterior, top, inner side, underside |

The normal renderer constructs the exposed form as an open-bottom shell with
separate outer, entrance-rim, interior and underside surfaces. Selectors 3 and
5 are constructive solids with a mouse-hole aperture: there is no closed box
behind the opening and no unrelated exposed tube placed over it. Consecutive
cells share one passage and emit entrance/exit rims only at structural ends.
All cross-sections use stable world coordinates and a recessed interior start,
so wall and rim thickness are geometric rather than a palette trick.

The supplied `replay_skyroads_20260722_151636` traverses level-select level 4
from road row 92 through 108. Its rows 94/95 and 98/99 contain exposed tubes;
rows 96..110 contain carved half-height passages. Cached points 0, 12, 24 and
104 retain the original TREKDAT role/palette evidence used for entrance,
interior, exterior and occlusion comparison. Full-height carved passages use
the same recovered aperture contract and occur in 17 shipped levels.

The full-height `0x0504` pair in
`snapshot_skyroads_20260723_132043` provides a nearer, unambiguous entrance
calibration. The camera is at row `41.9999847`; the structures begin at row
42 in lanes 2 and 4. Decoding the original RLE stream establishes this
composition:

1. `deck/top` remains the passage floor and extends to the cell's near edge.
2. The raised solid begins `0.10` row behind that edge. Its main aperture face
   uses selector 62; it is not the selector-65 rim color.
3. The mouse-hole opening has half-width `0.43` lane, a `0.08` straight jamb,
   and a `0.30` arched rise.
4. The longitudinal passage begins another `0.10` row deeper. Shared aperture
   vertices at those two depth planes are joined by explicit jamb and ceiling
   reveal surfaces using selector 65.
5. The passage walls and ceiling then continue to the cell's far plane.
   Consecutive cells share that plane, so they do not emit internal rims.

At the snapshot coordinate, the recovered lens projects the solid plane to
`x=94..138`, floor `y=99`; the front aperture to `x=97..135`, apex `y=82`;
and the rear reveal apex to `y=80`. Those are the original composite's exact
integer boundaries. This also explains the apparently asymmetric 1--3 pixel
`raised/front-rim` strip: it is perspective across a real `0.10`-row reveal,
not an independently fitted screen-space outline.

The earlier native mesh put the entire face at the deck edge (`x=90..137`,
`y=102`), began the passage at the correct first setback but left the interval
between them open, and colored the complete face as a rim. That disconnected
the floor, jambs, ceiling and interior and produced the visible overlap/gap.
The corrected mesh uses one shared aperture topology and applies screen
rounding only after projection.

The exact snapshot comparison uses ROI `x=80..239`, `y=32..106`, covering
both carved-full structures and their passages:

| Native mesh | Pixels differing from oracle | Normalized mean error |
|---|---:|---:|
| disconnected face/passage | 3,317 | 0.033858 |
| shared face/reveal/passage | 337 | 0.004254 |

The remaining differences are confined primarily to one-pixel polygon edges:
the oracle retains TREKDAT's phase-specific integer stair steps, whereas the
normal native view projects the recovered world vertices continuously. The
`exact-projection` mode remains the authority when those original raster
artifacts themselves must be reproduced. This snapshot covers carved-full
passages and in-volume ship occlusion. Carved-half uses the same recovered
aperture/reveal topology and is covered by the level-wide mesh tests; exposed
tubes remain a separate structural family and were intentionally unchanged.

### Carved-block shading and vertical tiers

The original renderer has no continuous face-lighting calculation for these
blocks. `1010:2FCC` selects display-list strips and patches selector bytes;
`3153`/`3190` resolve each selector through the live forward/backward tables
at `DS:0352/0353`, then write that palette index. Depth affects only the RLE
shape selected by TREKDAT. It does not scale face brightness.

The recovered selector rules are:

| Surface/role | Selector source | Orientation rule |
|---|---:|---|
| deck top | road word bits 0..3 | same selector in both passes |
| deck lateral side | deck selector + `0x1E` | forward `+X`, backward `-X` table |
| raised top | road word bits 4..7, or `0x3D` when zero | same selector in both passes |
| carved front, inner side, underside | `0x3E` | resolved through the active pass table |
| raised lateral side/lower side | `0x3F` | forward `+X`, backward `-X` table |
| entrance reveal | immediate `0x41` at `1010:2FD8` | resolved through the active pass table |

For the supplied snapshot this yields:

| Selector | Forward palette/RGB | Backward palette/RGB |
|---:|---|---|
| `0x3D` | 61 / `(16,28,255)` | 61 / `(16,28,255)` |
| `0x3E` | 62 / `(12,20,211)` | 62 / `(12,20,211)` |
| `0x3F` | 63 / `(8,20,182)` | 64 / `(8,16,150)` |
| `0x41` | 65 / `(8,16,150)` | 65 / `(8,16,150)` |

The native renderer therefore performs no renderer-local lightening or
darkening. It retains the selector identity and resolves it through the
scene's captured tables and live VGA palette. The two physical lateral faces
are resolved independently; choosing one shade from the cell's lane fails for
center-lane cells, which the original draws in both passes.

The apparent lower "brick" tier is also not an artist-adjusted proportion.
Collision routine `1010:1631` and the road-word `0x0200`/`0x0400` bits define
the absolute heights:

```text
deck = 0x2800
half = 0x3200
full = 0x3C00
lane unit = 0x1700
```

Both vertical tiers are exactly `0x0A00 / 0x1700 = 0.4347826087` lane units.
The type-5 draw handler confirms the composition: it emits the lower-side and
aperture roles from the half-height list, then the raised top/side/far-edge
roles from the second tier. There is no intermediate horizontal top because
the second tier occupies it. At snapshot camera row `41.9999847` and recovered
front depth `42.1`, the common lens projects the deck, tier seam and full top
to `y=98.81`, `79.58` and `60.35`. The original RLE spans divide at integer
rows 79/60. A one-pixel thickness difference between phases is consequently
integer clipping/overdraw, not a different world height.

Evidence classification:

| Classification | Values/rules |
|---|---|
| Directly recovered | map bits; `0x2800/0x3200/0x3C00` heights; selector immediates `0x3D/0x3E/0x3F/0x41`; forward/backward fill tables; RLE painter order and row spans |
| Derived from original behavior | normalization by `0x1700`; the continuous projection of tier boundaries; `0.10` front/reveal depths and `0.43` opening width obtained by inverting the recovered lens against the exact spans |
| Remaining approximation | the normal view's rational continuous lens and twelve-facet arch interpretation; both intentionally remove low-resolution stair steps. `exact-projection` retains the literal TREKDAT raster when byte/pixel identity is required |

### Multi-capture tunnel validation

The five manual continuation snapshots captured on 2026-07-23 broaden the
evidence beyond the original level-7 type-5 entrance:

| Capture | Recovered state | Tunnel evidence |
|---|---|---|
| `140212` | level 4, row 28, phase 5 | distant, contiguous type-3 (`carved-half`) passage at rows 30--31 |
| `140305` | level 6, row 37, phase 6 | long type-1 (`exposed-tube`) runs in lanes 1/5, centre-lane tubes, the twice-painted ship row, and near clipping |
| `140337` | level 22, row 20, phase 0 | one wall alternating type-3 passages and type-2 solids across all seven lanes |
| `140428` | level 22, row 59, phase 0 | type-1 and type-3 structures in the same view, including a contiguous type-3 run |
| `140435` | level 22, row 67, phase 7 | a near type-3 entrance followed by contiguous type-5 (`carved-full`) cells |

These establish three structural families rather than one interchangeable
"tunnel":

1. Type 1 is an exposed open-bottom tube. `3059` emits six immutable shell
   strips (`68..73`), front rim `67`, and inner rim/underside `66`. The
   backward pass mirrors the strip order *and* resolves it through the
   backward palette table. Its outer section has recovered radii
   `(x=0.43, y=0.50)` in the internal square-pixel coordinate grid. The 6:5
   output pixel aspect makes that shell physically round. Its opening uses
   `(x=0.36, y=0.35)`.
2. Type 3 is a passage through the single `0x3200` half-height block family.
   `2EFD` uses top `61`, optional side `63`, front rim `65`, and inner
   side/underside `62`. It must join type-2 solid blocks without a depth or
   shade seam.
3. Type 5 is a passage through the `0x3C00` full-height family. `2FCC` first
   emits the type-3-height lower side/opening, then the equal second tier's
   top/side/far edge. It is not a scaled-up type-3 silhouette.

The road deck remains the passage floor for all three. There is no separately
guessed tunnel-floor plane. Entrance and passage surfaces share vertices with
that deck, and the depth buffer provides in-volume player occlusion.

Two previously hidden mismatches became obvious only across this set:

* The raised solid primitive started at the raw road-row plane while carved
  cells started at `row + 0.10`. Capture `140337` therefore showed vertical
  seams between alternating solid and hollow cells. Exact phase-0 spans put
  both `block/far-cap` and `block/inner-side` on the `+0.10` plane. The
  display-list footprint is fixed at `row + 0.10` through `row + 1.10` for
  every cell. The `above_type < 2` / `< 4` gates in `2EBB`/`2F58` suppress
  only internal lower/upper faces; they do not move continuation geometry
  back to an integer row.
* The exposed shell was modelled lane-wide and its backward palette was
  applied without mirroring shell roles. Across phase-6 scanlines its color
  bands had nearly correct widths but lay 4--6 pixels too far outboard.
  Inverting the spans gives the `0.43` horizontal radius. The pass geometry
  anchors an off-centre tube to the lane boundary facing road centre, shifting
  its centre inward by `0.07`. At capture `140428`, projecting the outer base
  on `row + 0.10` gives `(x=89.59, y=75.97)`, matching the exact rim boundary
  `(89,76)`; the raw row gives `(86.47,77.89)`. The opening begins on the next
  `+0.10` plane. A centre-lane tube is the overlap between both original
  passes: only shell roles 1/2 survive on each half, so the native section
  mirrors that exact pair instead of exposing all six off-centre bands.

The final normal-view comparisons use the untouched VGA framebuffer restored
from each snapshot as oracle:

| Capture | Road differing pixels, before → after | Tunnel-span differing pixels, before → after |
|---|---:|---:|
| `140212` | 377 → 224 | 43 → 19 |
| `140305` | 5,908 → 2,787 | 5,425 → 2,689 |
| `140337` | 1,886 → 1,749 | 568 → 476 |
| `140428` | 1,136 → 1,053 | 588 → 537 |
| `140435` | 1,240 → 1,240 | 666 → 666 |

`140435` is deliberately unchanged: it exercises the previously recovered
full-height type-5 geometry and proves that correcting type-1 tubes and
type-2/type-3 adjacency did not perturb that family.

Evidence classification for the multi-capture corrections:

| Classification | Values/rules |
|---|---|
| Directly recovered | handler/type association; `above_type` and `side_type` gates; shell/rim/inner display-list order; selectors `61..73`; forward/backward raster direction and palette tables; exact VGA spans and painter order |
| Derived by inverting exact original output through the shared lens | raised shell plane `+0.10`; passage plane `+0.20`; exposed outer `(0.43,0.50)` and inner `(0.36,0.35)` cross-sections; `0.07` centre-facing lane anchor |
| Still inferred | treating the six exposed shade strips as twelve smooth stable facets in the enhanced view; the continuous lens between the eight literal TREKDAT phases; depth-buffer equivalence where the original uses ship-row painter duplication |

### Raised-tier junction validation

Snapshot `snapshot_skyroads_20260723_144526` isolates a non-tunnel case that
the earlier tunnel captures did not exercise. The camera is immediately before
row 9 of level 12. Exact live road words and the `2D1F` draw trace show:

| Row | Lanes | Words | Recovered role |
|---:|---:|---:|---|
| 8 | 2, 4 | `0x0200` | continuing lower type-2 tier |
| 8 | 3 | `0x0009` | deck-only opening between the lower tiers |
| 9 | 2, 3, 4 | `0x0400` | one contiguous full-height type-4 wall |

`2F58` does not describe each type-4 word as an independent floor-to-top box.
It reuses the type-2 lower tier and adds an equal upper tier. For each original
pass, `side_type < 2` exposes a lower side, `side_type < 4` exposes an upper
side, `above_type < 2` exposes the lower near face, and `above_type < 4`
exposes the upper near face. Consequently:

* lanes 2 and 4 suppress their lower near faces because row 8 already reaches
  half height;
* lane 3 emits both near-face tiers because its predecessor is deck-only;
* all three lanes emit their upper tier on the same `row + 0.10` plane;
* the internal lateral faces between lanes 2/3/4 are suppressed.

The former native model selected one entrance plane for an entire box. Lanes
2/4 therefore started at raw row 9 while lane 3 started at row 9.10, opening
two background holes and making the centre cell resemble a pillar. The native
mesh now represents the two recovered tiers independently, uses their original
neighbor thresholds, and keeps the display-list depth interval fixed.

The first tier correction exposed a narrower high-resolution gap directly
behind the ship. Its cause was the same coordinate contract on the other side
of the junction: ordinary deck quads still ended at raw row 9 while the lower
block face began at row 9.10. The exact row-8 `deck/top` spans cover y=98..102
and the row-9 `raised/far-cap` begins at y=98. Inverting those shared pixels
proves that the deck slot also spans `row + 0.10` through `row + 1.10`.
All deck cells now use that common footprint; there is no overlap or
renderer-only filler.

On the preserved 320x200 nearest-sampled framebuffer comparison, differing
road-band pixels fall from `2,470` to `160` and normalized road-band mean
absolute error from `0.01380977` to `0.00092567`. The remaining differences
are continuous-lens versus integer-phase edge quantization, not missing wall
topology.

This correction uses directly recovered handler comparisons, road words,
draw roles and palettes. The `+0.10` / `+1.10` planes are derived by inverting
the exact block-top spans through the already shared continuous lens. No
snapshot-specific screen coordinates or visual fill polygons are present in
the implementation.

The remaining normal-view differences concentrate on one-pixel phase
quantization, near-plane clipping, and the original ship-row overdraw. They
are not unresolved material selectors, tier dimensions, or independent face
coordinates. `exact-projection` remains the authority when those literal
integer raster artifacts are the desired comparison.

## Recovering the higher-level projection

An earlier experiment joined consecutive RLE span endpoints into trapezoids.
Although pixel-exact at original sample centres, it remained a vectorization
of eight integer images. Each phase had separately rounded boundaries, faces
did not share a world vertex pool, and changing phase made objects subtly
morph. That mechanism has been removed from `final`.

`scripts/recover_projection_calibration.py` instead renders a synthetic stable
seven-lane grid through every exact TREKDAT phase. It associates the observed
spans with known row/lane vertices and repeats the measurement with recovered
half- and full-height blocks. The measurements establish one common depth
scale for both horizontal width and vertical height:

```text
scale(depth) = gain * max(vanishing_depth - depth, 0)
                        / (depth + near_bias)
screen_x     = 160 + world_x * scale(depth)
screen_y     = horizon_y + (camera_height - world_y) * scale(depth)
```

Recovered calibration:

| Parameter | Value |
|---|---:|
| gain | `15.3423662022` |
| near bias | `2.545` rows |
| vanishing depth | `7.725` rows |
| logical horizon | `32.5900849601` |
| camera height | `1.4971649422` lane units |

Across the usable calibration window, the rational lens fits recovered lane
scale to about 0.2 original pixels RMS. The ground samples fit
`horizon + camera_height*measured_scale` within 0.05 pixels RMS; the combined
rational lens predicts ground rows within 0.30 pixels RMS. Half- and
full-height samples independently recover the same vertical scale.

This is a coherent continuous interpretation of the original pseudo-3D lens,
not a claim that the DOS executable contained floating-point camera constants.
TREKDAT is calibration evidence; it is not runtime geometry in `final`.

## Native projection invariants

- The world mesh is identical across all fractional positions inside a road
  row. Three look-ahead rows beyond the DOS `current+7` compositor window are
  retained behind the recovered vanishing depth. New blocks therefore enter
  the projection at zero area and grow continuously instead of popping at a
  visible source-window boundary. Geometry behind the near plane is removed.
- Fractional track position is supplied once as a GPU camera uniform.
- Shared faces derive their endpoints from the same exact lane/row/elevation
  tuples. Hard palette seams may duplicate render vertices, but their position
  values remain identical and use the same projector.
- Floating-point coordinates are retained through projection. Pixel rounding
  occurs only in final GPU rasterization.
- `final`, `terrain`, `wireframe`, `source-ids`, and `collision` share the same
  vertex placement and camera. Debug policy cannot change geometry.
- `exact-projection` alone consumes phase-specific RLE spans.

The fixed chase camera does not follow `AF1C` or `AF2C`; those fields move the
ship in the view. The original ship position and CARS frame selection remain:

```text
x = row_base_table[row_band(AF1C)] + AF1C / 0x80 - 0x6E
y = 0x9D - AF2C / 0x80
frame = ((row_band(AF1C) * 3 + pitch) * 3) + 0x0E + wobble
```

CARS frames are 29 by 24, stored column-major. `exact-projection` retains the
original `325B` mask seam. `final` places the ship and all native terrain in
one depth field; tunnel state cannot move the ship or camera between guessed
planes.

The 320x200 logical grid has 6:5 pixels and was physically 4:3. The presenter
applies that correction once. Widescreen expands outside the 4:3 reference
aperture without changing simulation or the recovered lens.

## Modes and diagnostics

```text
pypy scripts/play.py --composition faithful-product --renderer native-3d --render-debug final
pypy scripts/play.py --composition faithful-product --renderer native-3d --render-debug exact-projection
pypy scripts/play.py --composition faithful-product --renderer native-3d --render-debug terrain
pypy scripts/play.py --composition faithful-product --renderer native-3d --render-debug wireframe
pypy scripts/play.py --composition faithful-product --renderer native-3d --render-debug source-ids
pypy scripts/play.py --composition faithful-product --renderer native-3d --render-debug collision
pypy scripts/play.py --composition faithful-product --renderer native-3d --render-debug original
```

`final` is the high-resolution stable world. `exact-projection` draws literal
RLE spans and is the strict terrain raster reference. `original` presents the
complete DOS frame. The remaining modes inspect topology, edges, identities,
and collision classes without changing placement.

Recovery and comparison tools:

```text
python scripts/recover_projection_calibration.py --summary
python scripts/inspect_render_scene.py --level 14 --track-row 40 --json
python scripts/inspect_render_scene.py --level 29 --dump-obj artifacts/level29.obj
python scripts/inspect_render_scene.py --level 14 --track-row 40 --dump-projection artifacts/projection.json
pypy scripts/capture_render_parity.py artifacts/replays/replay_skyroads_20260722_110639 --size 1280x960
pypy scripts/capture_render_parity.py artifacts/replays/replay_skyroads_20260722_110639 --debug exact-projection --size 320x200
```

Replay capture writes the oracle frame, native frame, logical sample,
side-by-side image, difference image, source mesh identity, and exact
projection identity. In `exact-projection`, points 60, 70, and 123 of
`replay_skyroads_20260722_110639` remain 0/64,000 pixels different. That is the
raster claim. `final` is compared for source contents, proportions, palette
roles, stable motion, and semantic scene identity while intentionally excluding
integer stair steps and phase snapping.

## Complete gameplay-frame ownership

The native renderer does not crop or composite the live VGA framebuffer.
`DASHBRD.LZS` is decoded as its original nonzero-pixel stencil, and the speed,
oxygen, fuel, progress, and gravity displays are rebuilt from their recovered
DAT widget records and authoritative values. The transparent holes in rows
129..137 expose native road geometry; they are not an estimated alpha effect.

The rocket shadow is likewise not a guessed ellipse or another ship sprite.
It is the five-band 29x9 stencil selected by original routine `1010:33FD`; its
position follows `[0E70]`. Routine `325B` first builds a 29x33 per-pixel
coverage mask at `[113E]`; `33FD` darkens a shadow pixel only when both its
29x9 band stencil and that coverage byte are nonzero. The native renderer
captures and applies the same mask and live road-palette darkening. The
original emits it at the ship-row painter seam, before nearer terrain and
tunnel faces; the continuous renderer expresses that ordering in the shared
depth field. Thus the recovered mask is not reshaped into a guessed world
blob, but near tunnel shells correctly cover it instead of the shadow being
painted as a final always-visible overlay.

Presentation ownership is deliberately wider than execution-region ownership.
The shared fade head `434A` cannot identify a screen. Its recovered caller
chain does: `2C5B`/`2CBE` are gameplay fades and `5295`/`5377` are selector
fades. Native output remains active through `22F8`, generated departure head
`0EF8`, wait head `4468`, and the gameplay exit fade, then releases on the
first selector fade even when the selected-level value has not changed. This
prevents both an original-frame flash and stale gameplay drawn with the
selector palette, and is stable after replay boundary restoration.

The gameplay DAC is recovered as four immutable asset banks: 72 level-road
colours, 20 CARS colours, 50 DASHBRD colours, and 114 WORLD colours.  The live
VGA DAC remains authoritative for fades, but it is display state rather than
mesh identity.  During `4331`'s uniform fade the renderer therefore retains
one source-colour mesh and one set of decoded indexed assets, applying only a
GPU colour gain.  A bounded four-level mesh cache also preserves recently
visited levels.  Non-uniform DAC changes deliberately fall back to exact
recolouring rather than being misclassified as a fade.

Remaining visual work is limited to fidelity refinement: validate native
depth-field ship/tunnel occlusion across more replay points and extend
widescreen road contents only from stable source geometry. Exact `325B`
behavior remains available as the reference.
