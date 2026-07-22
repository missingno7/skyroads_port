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
captures and applies the same mask, draw order, and live road-palette
darkening. It renders this already-occluded result in screen space rather than
depth-testing it as an invented world-space billboard.

Presentation ownership is deliberately wider than execution-region ownership.
Road identity allows native output to begin at recovered fade head `434A`
before entry into the gameplay region and remain active through `22F8`, the
generated departure head `0EF8`, wait head `4468`, and the final fade. At the
black handoff, a changed selected-level identity releases presentation to the
generated level selector. This prevents both an original-frame flash and the
opposite error of drawing stale gameplay geometry over the selector.

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
