"""GPU presentation packets for the recovered SkyRoads scene.

The normal view is a stable world model: immutable road-row/lane/elevation
vertices transformed by one recovered pseudo-perspective lens.  TREKDAT's
integer RLE silhouettes remain an independent ``exact-projection`` reference;
they are calibration evidence, not the high-resolution geometry.  Neither
path reads game state from pixels or feeds presentation values back into the
simulation.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from functools import lru_cache
import colorsys
from hashlib import sha256
import math
import struct
from types import SimpleNamespace
from typing import Iterable

from skyroads.presentation.scene import (
    FULL_BLOCK_HEIGHT,
    GameplayScene,
    HALF_BLOCK_HEIGHT,
    LANE_UNITS,
    ROAD_DECK_HEIGHT,
    RaisedShape,
    RoadCell,
    TunnelShape,
)
from skyroads.presentation.original_projection import (
    ProjectionTrace,
    projection_triangles,
    trace_original_projection,
)
from skyroads.handrecovered.blit import stencil_blit
from skyroads.native.hud import (
    GRAV_COL, GRAV_COLOR_BASE, GRAV_GLYPH_H, GRAV_GLYPH_W, GRAV_PITCH,
    GRAV_ROW, GRAV_WIDTH, PROGRESS_COL0, PROGRESS_FILL, PROGRESS_ROW,
    grav_value,
)


DEBUG_RENDER_MODES = (
    "final", "exact-projection", "terrain", "wireframe", "source-ids",
    "collision", "original",
)

# DASHBRD.LZS declares destination 0xA140, exactly scanline 129 in mode 13h.
# Rows 129..137 overlap the road compositor band and must be painted by the
# dashboard last; cropping at 138 exposed terrain through the cockpit rim.
DASHBOARD_TOP = 129


@dataclass(frozen=True)
class ProjectionCalibration:
    """Recovered continuous form of the original table-driven lens.

    Offline calibration projects a synthetic stable seven-lane grid through
    every TREKDAT phase and associates its exact spans with known row/lane and
    half/full-height vertices.  One scale explains horizontal width and
    vertical height, while the ground plane is ``horizon + camera_y*scale``.
    The rational scale reaches the original finite vanishing row smoothly.

    This is intentionally a coherent pseudo-perspective model, not a claim
    that the DOS program contained these floating-point constants.  See
    ``scripts/recover_projection_calibration.py`` for their evidence path.
    """

    lanes: int = 7
    lane_units: int = LANE_UNITS
    row_units: int = 0x10000
    visible_rows_original: int = 11
    road_band_top: int = 32
    road_band_bottom: int = 138
    camera_height: float = 1.4971649422
    horizon_y: float = 32.5900849601
    lens_gain: float = 15.3423662022
    near_bias: float = 2.545
    vanishing_depth: float = 7.725
    near_clip: float = -2.45
    ship_depth: float = 0.0


CALIBRATION = ProjectionCalibration()

# Recovered carved-passage dimensions.  At snapshot
# ``snapshot_skyroads_20260723_132043`` the nearest 0x0504 cell begins at road
# row 42 while the camera is at 41.9999847.  TREKDAT's composite establishes:
#
# * deck near edge: x=90..137, y=102;
# * solid/front aperture plane: x=94..138, floor y=99;
# * aperture floor: x=97..135, apex y=82;
# * rear of the entrance reveal: apex y=80.
#
# Inverting the recovered continuous lens gives two successive 0.10-row
# offsets, a 0.43-lane aperture half-width, and the already recovered
# 0.08+0.30 mouse-hole height.  These are world dimensions shared by every
# carved-half/full cell, not snapshot-specific screen coordinates.
# Every TREKDAT road-cell slot has the same recovered display-list footprint:
# row+0.10 through row+1.10. Snapshot 144526 makes the shared boundary
# explicit: row-8 ``deck/top`` ends at y=98 exactly where row-9
# ``raised/far-cap`` begins. Keeping decks on raw integer rows while moving
# blocks to +0.10 left a real uncovered strip behind the ship.
ROAD_CELL_DEPTH_OFFSET = 0.10
RAISED_FRONT_SETBACK = ROAD_CELL_DEPTH_OFFSET
CARVED_FRONT_SETBACK = ROAD_CELL_DEPTH_OFFSET
CARVED_REVEAL_DEPTH = 0.10
CARVED_OPENING_HALF_WIDTH = 0.43
CARVED_OPENING_SPRING = 0.08
CARVED_OPENING_ARCH_HEIGHT = 0.30

# Selector-1 occupies the complete lane cross-section. In snapshot 185622,
# the near row-31 entrance projects to x=61..126; inversion at row+0.10 gives
# exactly -1.50..-0.50 for lane 2. Its near/far apex spans invert to a
# 0.43-lane rise. Earlier distant captures could not separate that full-lane
# base from the asymmetric rim strips and incorrectly implied a 0.43-wide
# shell shifted 0.07 lane inward.
EXPOSED_OUTER_HALF_WIDTH = 0.50
EXPOSED_OUTER_HEIGHT = 0.43
# The selector-66/67 masks at rows 31, 34 and 35 constrain the recessed
# opening independently: a 0.07 lateral and 0.10 vertical shell thickness,
# with the inner surface beginning 0.08 row behind the entrance.
EXPOSED_INNER_HALF_WIDTH = 0.43
EXPOSED_INNER_HEIGHT = 0.33
EXPOSED_RIM_DEPTH = 0.08
# The type-1 entrance is not one selector-67 annulus. 3059 paints selector 67
# first, then the selector-66 inner-rim and underside streams overwrite it
# from opposite halves. At snapshot 185622's unobscured row-31 entrance, the
# surviving boundary is x=65 at the floor and x=87 near the spring. Inverting
# those spans through the shared lens puts the boundary 45% of the way from
# the outer cross-section to the recessed opening. Selector 67 survives only
# on the remaining inner 55% of the road-outward half.
EXPOSED_FRONT_OUTER_SHARE = 0.45

# Direct selector constants from the original 1010:2FCC type-5 handler:
# 3023 supplies 0x3D when the road word has no explicit raised-top material;
# the display-list streams at DI+6/DI+0A carry the other fixed selectors.
# Selector-to-palette resolution remains data-driven through the live
# forward/backward tables at DS:0352/0353.
RAISED_TOP_DEFAULT_SELECTOR = 0x3D
CARVED_FACE_SELECTOR = 0x3E
CARVED_SIDE_SELECTOR = 0x3F
CARVED_RIM_SELECTOR = 0x41

# Direct gameplay heights from 1010:1631, normalized by the 0x1700 lane unit.
# A full block is exactly two equal 0x0A00 tiers above the 0x2800 deck:
# 0x3200 (half) then 0x3C00 (full). The RLE role boundary in the supplied
# snapshot projects to y=79.58, matching its integer row-79 split.
CARVED_LOWER_LAYER_HEIGHT = (
    HALF_BLOCK_HEIGHT - ROAD_DECK_HEIGHT
) / LANE_UNITS


def projection_scale(
    relative_depth: float,
    calibration: ProjectionCalibration = CALIBRATION,
) -> float:
    """Continuous recovered scale for one world-space depth.

    Only the final rasterizer rounds this result.  The clamp represents the
    finite far convergence and near singularity already present in TREKDAT's
    visible row window.
    """
    denominator = max(float(relative_depth) + calibration.near_bias, 0.05)
    return (
        calibration.lens_gain
        * max(calibration.vanishing_depth - float(relative_depth), 0.0)
        / denominator
    )


def project_world_vertex(
    x: float,
    y: float,
    z: float,
    camera_track: float,
    calibration: ProjectionCalibration = CALIBRATION,
) -> tuple[float, float, float]:
    """Project one stable world vertex into logical screen coordinates."""
    depth = float(z) - float(camera_track)
    scale = projection_scale(depth, calibration)
    return (
        160.0 + float(x) * scale,
        calibration.horizon_y + (calibration.camera_height - float(y)) * scale,
        depth,
    )


def ship_camera_depth(
    scene: GameplayScene, calibration: ProjectionCalibration = CALIBRATION,
) -> float:
    """Return the ship's stable depth in the native world projection.

    The billboard remains at the original fixed chase-view position.  World
    faces participate in one depth field, so tunnels no longer move the ship
    between guessed compositor planes from frame to frame.
    """
    del scene
    return calibration.ship_depth


def shadow_camera_depth(
    scene: GameplayScene, calibration: ProjectionCalibration = CALIBRATION,
) -> float:
    """Place 33FD's stencil on the road in the shared native depth field.

    The original emits the shadow from the ship-row 325B pass, before nearer
    tunnel/terrain painter spans.  Its recovered 29x9 mask remains the exact
    alpha authority.  The billboard depth is just behind the ship seam: the
    original painter draws the rocket after its road-shadow stencil, so their
    overlapping opaque texels must resolve to the rocket independently of the
    depth-write optimization used for the translucent decal.  The bias remains
    tiny enough that genuinely nearer tunnel faces occlude both.
    """
    return ship_camera_depth(scene, calibration) + 0.01


@dataclass(frozen=True)
class PolygonMesh:
    """Compact interleaved vertices and triangle indices.

    Vertex layout is ``x,y,z,r,g,b``. Stable string identities remain in the
    parallel ``source_ids`` tuple; debug colors are resolved once while the
    mesh is built rather than sent as a redundant per-vertex attribute.
    """

    vertices: tuple[float, ...]
    indices: tuple[int, ...]
    source_ids: tuple[str, ...]
    first_row: int
    last_row: int
    digest: str

    @property
    def vertex_count(self) -> int:
        return len(self.vertices) // 6

    @property
    def triangle_count(self) -> int:
        return len(self.indices) // 3


@dataclass(frozen=True)
class PolygonFrame:
    scene: GameplayScene
    mesh: PolygonMesh
    projection_trace: ProjectionTrace | None
    projection_before_ship: tuple[float, ...]
    projection_after_ship: tuple[float, ...]
    background_rgb: bytes
    dashboard_rgba: object
    ship_rgba: bytes
    ship_width: int
    ship_height: int
    ship_x: int
    ship_y: int
    shadow_rgba: bytes
    shadow_width: int
    shadow_height: int
    shadow_x: int
    shadow_y: int
    palette_gain: float
    debug_mode: str
    widescreen: bool


def _palette(scene: GameplayScene, index: int,
             shade: float = 1.0) -> tuple[float, float, float]:
    if not scene.palette:
        return (0.5, 0.5, 0.5)
    rgb = scene.palette[max(0, min(index, len(scene.palette) - 1))]
    return tuple(max(0.0, min(1.0, channel / 255.0 * shade)) for channel in rgb)


def _id_color(source_id: int) -> tuple[float, float, float]:
    hue = ((source_id * 0.6180339887498949) % 1.0)
    return colorsys.hsv_to_rgb(hue, 0.72, 0.95)


@lru_cache(maxsize=8)
def _dashboard_base_alpha(indices: bytes) -> bytes:
    """Cache the immutable DASHBRD colour-key mask."""
    width = 320
    return bytes(
        255 if index // width >= 9 or value else 0
        for index, value in enumerate(indices)
    )


@lru_cache(maxsize=512)
def _dashboard_widget(
    bank: bytes, record_at: int, enabled: bool,
) -> tuple[int, int, int, bytes]:
    """Decode each immutable DAT stencil once for its on/off state."""
    if record_at + 4 > len(bank):
        raise ValueError("HUD widget points outside its DAT bank")
    destination, width, height = struct.unpack_from("<HBB", bank, record_at)
    end = record_at + 4 + width * height
    if end > len(bank):
        raise ValueError("HUD widget has a truncated stencil")
    stencil = stencil_blit(
        bank[record_at + 4:end],
        0x5E if enabled else 0x5C,
        0x5F if enabled else 0x5D,
    )
    return destination, width, height, stencil


def _compose_dashboard_rgba(scene: GameplayScene):
    """Rebuild ``DASHBRD`` and HUD widgets without reading VGA pixels.

    The apparent translucency at rows 129..137 is the original loader's
    colour-key contract: zero asset pixels are transparent and every nonzero
    pixel is opaque.  The gauges are recovered DAT stencils layered over that
    mask, not a crop of the already-composited oracle framebuffer.
    """
    import numpy as np

    width, height = 320, 71
    indices = bytearray(scene.assets.dashboard_indices)
    # Only rows 129..137 overlap the road compositor in the original frame.
    # Zero-key pixels there reveal the road. Below that seam the destination
    # plane is the cockpit's black backing, so make index zero opaque instead
    # of allowing the native world mesh to leak behind the instruments.
    alpha = bytearray(_dashboard_base_alpha(scene.assets.dashboard_indices))

    def paint_widgets(offsets, bank: bytes, count: int) -> None:
        for cell, record_at in enumerate(offsets):
            destination, w, h, stencil = _dashboard_widget(
                bank, record_at, cell < count,
            )
            for row in range(h):
                y = destination // 320 + row - DASHBOARD_TOP
                x = destination % 320
                if not 0 <= y < height:
                    continue
                for col in range(w):
                    value = stencil[row * w + col]
                    if value and x + col < width:
                        at = y * width + x + col
                        indices[at] = value
                        alpha[at] = 255

    paint_widgets(
        scene.assets.speed_cells, scene.assets.speed_widgets,
        scene.dashboard.speed_cells,
    )
    paint_widgets(
        scene.assets.oxygen_cells, scene.assets.oxygen_widgets,
        scene.dashboard.oxygen_cells,
    )
    paint_widgets(
        scene.assets.fuel_cells, scene.assets.fuel_widgets,
        scene.dashboard.fuel_cells,
    )

    for col in range(scene.dashboard.progress_columns):
        x = PROGRESS_COL0 + col
        y = PROGRESS_ROW - DASHBOARD_TOP
        if not 0 <= y < height:
            continue
        reference = indices[y * width + x]
        while y > 0 and indices[y * width + x] == reference:
            y -= 1
        y += 1
        while y < height and indices[y * width + x] == reference:
            at = y * width + x
            indices[at] = PROGRESS_FILL
            alpha[at] = 255
            y += 1

    value = grav_value(scene.dashboard.gravity)
    remaining = value
    for digit_index in range(GRAV_WIDTH):
        if remaining == 0 and digit_index != 0:
            break
        power = 10 ** digit_index
        digit = (remaining // power) % 10
        x0 = GRAV_COL + (GRAV_WIDTH - digit_index - 1) * GRAV_PITCH
        y0 = GRAV_ROW - DASHBOARD_TOP
        source = digit * GRAV_GLYPH_W * GRAV_GLYPH_H
        for row in range(GRAV_GLYPH_H):
            for col in range(GRAV_GLYPH_W):
                glyph = scene.dashboard.digit_font[
                    source + row * GRAV_GLYPH_W + col
                ]
                at = (y0 + row) * width + x0 + col
                indices[at] = 0 if glyph == 0 else GRAV_COLOR_BASE + glyph
                alpha[at] = 255
        remaining -= digit * power

    palette = np.asarray(scene.palette, dtype=np.uint8)
    rgba = np.empty((height, width, 4), dtype=np.uint8)
    rgba[:, :, :3] = palette[np.frombuffer(bytes(indices), dtype=np.uint8)].reshape(
        height, width, 3,
    )
    rgba[:, :, 3] = np.frombuffer(bytes(alpha), dtype=np.uint8).reshape(height, width)
    return np.ascontiguousarray(rgba)


def _shadow_rgba(scene: GameplayScene) -> bytes:
    """Turn recovered ``33FD`` stencil art into a native darkening decal.

    Opacity is derived from the actual live palette remap (1..15 -> +0x2D,
    plus 0x3D -> 0x40), rather than an artist-chosen constant.
    """
    if (not scene.shadow.visible
            or len(scene.shadow.mask) != 29 * 9
            or len(scene.shadow.coverage) != 29 * 9):
        return b""
    ratios = []
    for source, target in [*((i, i + 0x2D) for i in range(1, 0x10)), (0x3D, 0x40)]:
        before = scene.palette[source]
        after = scene.palette[target]
        old = sum(before)
        if old:
            ratios.append(min(1.0, sum(after) / old))
    opacity = round(255 * (1.0 - sum(ratios) / len(ratios))) if ratios else 0
    return bytes(
        channel
        for pattern, coverage in zip(
            scene.shadow.mask, scene.shadow.coverage, strict=True,
        )
        for channel in (
            0, 0, 0, opacity if pattern and coverage else 0,
        )
    )


def _uniform_palette_gain(
    basis: tuple[tuple[int, int, int], ...],
    current: tuple[tuple[int, int, int], ...],
) -> float | None:
    """Recognize the original full-DAC fade-to-black transform.

    ``4331`` changes only a scalar fade percentage during the gameplay
    transition.  Re-decoding every indexed asset and rebuilding the complete
    world mesh for each of its ~30 palette steps made a presentation-only
    effect one of the largest frame stalls.  This recognizer is deliberately
    conservative: non-uniform palette changes return ``None`` and retain the
    exact rebuild path.
    """
    if len(basis) != len(current) or not basis:
        return None
    numerator = denominator = 0
    channels = []
    for before, after in zip(basis, current, strict=True):
        for source, target in zip(before, after, strict=True):
            # Both values passed through the real VGA 6-bit DAC.  Fit in the
            # domain in which 4331 actually performs its integer
            # interpolation, rather than treating the nonlinear replicated
            # high bits of the 8-bit display expansion as source data.
            source = (int(source) & 0xFF) >> 2
            target = (int(target) & 0xFF) >> 2
            channels.append((source, target))
            if source >= 2:
                numerator += source * target
                denominator += source * source
    if not denominator:
        return None
    gain = numerator / denominator
    # The basis is the immutable four-bank asset palette, never a transient
    # live DAC sample.  The same test therefore covers fade-in and fade-out.
    if not -0.01 <= gain <= 1.01:
        return None
    for source, target in channels:
        # Zero and near-black entries in the assembled source palette include
        # deliberately unused DAC slots.  The original fade interpolates the
        # complete *live* DAC, so those slots can retain unrelated menu/audio
        # colours even though no native gameplay asset references them.
        # Treating an unreferenced zero slot as geometry authority caused a
        # full world-mesh rebuild at several fade steps.  Low-valued channels
        # are also dominated by the original integer division's quantization.
        # They contribute neither useful gain evidence nor a visible error
        # large enough to justify invalidating immutable geometry.
        if source < 2:
            continue
        expected = source * gain
        # IDIV truncation plus the final VGA DAC quantization can each move a
        # component by one 6-bit unit.  Anything beyond that is a real
        # non-uniform palette mutation and retains the exact rebuild path.
        if abs(target - expected) > 2.0:
            return None
    return max(0.0, min(1.0, gain))


class _MeshBuilder:
    def __init__(self, scene: GameplayScene, debug_mode: str) -> None:
        self.scene = scene
        self.debug_mode = debug_mode
        self.vertices: list[float] = []
        self.indices: list[int] = []
        self.source_ids: list[str] = []
        self._vertices: dict[tuple[float, ...], int] = {}

    def color(self, cell: RoadCell, face: str, *, raised: bool = False) -> tuple[float, float, float]:
        sid = cell.row * 7 + cell.lane + 1
        if self.debug_mode == "source-ids":
            return _id_color(sid)
        if self.debug_mode == "collision":
            if cell.tunnel:
                return (0.95, 0.25, 0.75)
            if cell.raised is not RaisedShape.NONE:
                return (0.95, 0.25, 0.18)
            return (0.15, 0.85, 0.30)
        if self.debug_mode == "terrain":
            base = {
                "top": (0.33, 0.48, 0.72),
                "front": (0.20, 0.30, 0.48),
                "left": (0.24, 0.37, 0.58),
                "right": (0.16, 0.25, 0.40),
                "pipe": (0.50, 0.58, 0.70),
            }
            return base.get(face, base["top"])

        material = cell.deck_material or 1
        if raised and face == "top":
            index = cell.top_material or 61
        elif raised and face == "front":
            index = 62
        elif raised and face == "right":
            index = 63
        elif raised and face == "left":
            index = 64
        elif face == "top":
            index = material
        elif face == "front":
            index = material + 15
        elif face == "right":
            index = material + 30
        elif face == "left":
            index = material + 45
        else:
            index = 66 + (cell.lane % 6)
        return _palette(self.scene, index)

    def selector_color(self, cell: RoadCell, selector: int, *, backward: bool) -> tuple[float, float, float]:
        """Resolve one original face selector through the live asymmetric map."""
        if self.debug_mode != "final":
            # Diagnostic policy changes color only, never the cross-section.
            return self.color(cell, "pipe")
        table = (self.scene.face_palette_backward if backward
                 else self.scene.face_palette_forward)
        palette_index = table[selector] if selector < len(table) else 0
        return _palette(self.scene, palette_index)

    def vertex(self, position: tuple[float, float, float],
               color: tuple[float, float, float]) -> int:
        # Positions come from one integer lane/row/elevation lattice.  A hard
        # palette seam may require two render vertices, but both retain the
        # exact same position tuple and therefore project identically.
        values = tuple(round(float(value), 6) for value in (*position, *color))
        existing = self._vertices.get(values)
        if existing is not None:
            return existing
        at = len(self.vertices) // 6
        self.vertices.extend(values)
        self._vertices[values] = at
        return at

    def quad(self, points: Iterable[tuple[float, float, float]],
             color: tuple[float, float, float],
             *, reverse: bool = False) -> None:
        ids = [self.vertex(point, color) for point in points]
        if reverse:
            self.indices.extend((ids[0], ids[2], ids[1], ids[0], ids[3], ids[2]))
        else:
            self.indices.extend((ids[0], ids[1], ids[2], ids[0], ids[2], ids[3]))

    @staticmethod
    def _arch_points(
        center: float, base: float, radius_x: float, radius_y: float,
        segments: int,
    ) -> tuple[tuple[float, float], ...]:
        """Return one immutable left-to-right arched cross-section."""
        return tuple(
            (
                center + math.cos(math.pi - math.pi * step / segments) * radius_x,
                base + math.sin(math.pi - math.pi * step / segments) * radius_y,
            )
            for step in range(segments + 1)
        )

    def box(self, cell: RoadCell, x0: float, x1: float,
            z0: float, z1: float, y0: float, y1: float,
            *, raised: bool = False,
            entrance: bool = True,
            depth_splits: tuple[float, ...] = ()) -> None:
        front_z = (
            z0 + min(RAISED_FRONT_SETBACK, (z1 - z0) * 0.2)
            if raised and entrance else z0
        )
        depths = (
            front_z,
            *(value for value in sorted(set(depth_splits))
              if front_z < value < z1),
            z1,
        )
        top_color = self.color(cell, "top", raised=raised)
        left_color = self.color(cell, "left", raised=raised)
        right_color = self.color(cell, "right", raised=raised)
        for near, far in zip(depths, depths[1:]):
            self.quad(
                ((x0, y1, near), (x1, y1, near),
                 (x1, y1, far), (x0, y1, far)),
                top_color,
            )
            self.quad(
                ((x0, y0, far), (x0, y0, near),
                 (x0, y1, near), (x0, y1, far)),
                left_color,
            )
            self.quad(
                ((x1, y0, near), (x1, y0, far),
                 (x1, y1, far), (x1, y1, near)),
                right_color,
            )
        if not raised or entrance:
            self.quad((
                (x0, y0, front_z), (x1, y0, front_z),
                (x1, y1, front_z), (x0, y1, front_z),
            ), self.color(cell, "front", raised=raised))

    @staticmethod
    def _raised_tiers(cell: RoadCell | None) -> int:
        """Return the original dispatcher's ordered height threshold.

        Types 2/3 own the lower 0x0a00 tier and types 4/5 own both equal
        tiers.  This is exactly the comparison domain used by 2EBB/2F58:
        ``neighbor_type < 2`` exposes tier one and ``neighbor_type < 4``
        exposes tier two.  Tunnel and solid cells therefore participate in
        the same occlusion topology even though their front faces differ.
        """
        if cell is None or cell.raised is RaisedShape.NONE:
            return 0
        return 2 if cell.raised is RaisedShape.FULL else 1

    def raised_block(
        self,
        cell: RoadCell,
        x0: float,
        x1: float,
        z0: float,
        z1: float,
        *,
        previous: RoadCell | None,
        left: RoadCell | None,
        right: RoadCell | None,
    ) -> None:
        """Build an ordinary raised cell using 2EBB/2F58's tier topology.

        A type-4 cell is not one independent floor-to-top box.  The recovered
        handler first conditionally emits the type-2 lower tier, then emits a
        second equal tier with independent previous-row and side-neighbor
        gates.  Preserving those two gates makes adjacent cells share one
        continuous wall and prevents false holes where a full block follows a
        half block.
        """
        tiers = self._raised_tiers(cell)
        previous_tiers = self._raised_tiers(previous)
        side_tiers = (
            self._raised_tiers(left),
            self._raised_tiers(right),
        )
        tier_height = CARVED_LOWER_LAYER_HEIGHT
        top_color = self.color(cell, "top", raised=True)
        front_color = self.color(cell, "front", raised=True)
        side_colors = (
            self.color(cell, "left", raised=True),
            self.color(cell, "right", raised=True),
        )
        # The display-list slot has one fixed row-relative footprint. Traces
        # invert its near/far planes to row+0.10 and row+1.10 for isolated and
        # continued blocks alike; neighbor checks suppress end/side faces,
        # they do not move the strip. This also makes consecutive source rows
        # share the exact same world-space boundary.
        plane_offset = min(RAISED_FRONT_SETBACK, (z1 - z0) * 0.2)
        near_z = z0 + plane_offset
        far_z = z1 + plane_offset

        # The top belongs only to the highest occupied tier.  Its front depth
        # is fixed by the slot; the tier's ``above_type`` threshold controls
        # only whether its near face is visible.
        self.quad(
            ((x0, tiers * tier_height, near_z),
             (x1, tiers * tier_height, near_z),
             (x1, tiers * tier_height, far_z),
             (x0, tiers * tier_height, far_z)),
            top_color,
        )

        for tier in range(1, tiers + 1):
            y0 = (tier - 1) * tier_height
            y1 = tier * tier_height
            entrance = previous_tiers < tier
            if entrance:
                self.quad(
                    ((x0, y0, near_z), (x1, y0, near_z),
                     (x1, y1, near_z), (x0, y1, near_z)),
                    front_color,
                )
            if side_tiers[0] < tier:
                self.quad(
                    ((x0, y0, far_z), (x0, y0, near_z),
                     (x0, y1, near_z), (x0, y1, far_z)),
                    side_colors[0],
                )
            if side_tiers[1] < tier:
                self.quad(
                    ((x1, y0, near_z), (x1, y0, far_z),
                     (x1, y1, far_z), (x1, y1, near_z)),
                    side_colors[1],
                )

    def exposed_tunnel(
        self, cell: RoadCell, x0: float, x1: float, z0: float, z1: float,
        *, entrance: bool,
    ) -> None:
        """Build the selector-1 tube as a thick, open-bottom shell.

        TREKDAT exposes six longitudinal shading roles plus distinct front,
        inner and underside roles.  They describe material thickness, not six
        unrelated flat roof planes.  The high-resolution model keeps the six
        recovered shade bands but samples a stable twelve-facet cross-section
        so the intended rounded tube does not inherit 320x200 stair steps.
        """
        center = (x0 + x1) * 0.5
        segments = 12
        outer = self._arch_points(
            center, 0.0,
            EXPOSED_OUTER_HALF_WIDTH, EXPOSED_OUTER_HEIGHT,
            segments,
        )
        inner = self._arch_points(
            center, 0.0,
            EXPOSED_INNER_HALF_WIDTH, EXPOSED_INNER_HEIGHT,
            segments,
        )
        rim_depth = min(EXPOSED_RIM_DEPTH, (z1 - z0) * 0.2)
        # Every shell occupies the same fixed road-cell display-list
        # footprint as its deck: row+0.10 through row+1.10. ``above_type < 1``
        # controls only whether 3059 emits entrance roles.
        plane_offset = min(ROAD_CELL_DEPTH_OFFSET, (z1 - z0) * 0.2)
        front_z = z0 + plane_offset
        far_z = z1 + plane_offset
        inner_z0 = (
            front_z + min(rim_depth, (z1 - front_z) * 0.2)
            if entrance else front_z
        )
        backward = cell.lane > 3

        for step in range(segments):
            # The backward rasterizer mirrors the display-list geometry, not
            # merely its palette lookup. Preserve the original shell-0..5
            # ordering on both physical sides of the road.
            facet_backward = backward
            if cell.lane == 3:
                # The centre cell is the one overlap between 2D1F's passes.
                # Its phase traces retain shell-1/2 on each half: 69 then 70
                # toward the apex in the forward half, mirrored through the
                # backward table on the other half.
                facet_backward = step >= segments // 2
                half_step = (
                    step if not facet_backward
                    else segments - 1 - step
                )
                shade = 69 + int(half_step >= segments // 4)
            else:
                shade_step = segments - 1 - step if backward else step
                shade = 68 + min(5, shade_step * 6 // segments)
            oa, ob = outer[step], outer[step + 1]
            ia, ib = inner[step], inner[step + 1]
            inner_color = self.selector_color(
                cell, 66, backward=facet_backward,
            )
            self.quad(
                ((oa[0], oa[1], front_z), (ob[0], ob[1], front_z),
                 (ob[0], ob[1], far_z), (oa[0], oa[1], far_z)),
                self.selector_color(
                    cell, shade, backward=facet_backward,
                ),
            )
            # Inward-facing passage surface. The renderer uses a depth buffer
            # without face culling, so winding documents topology rather than
            # deciding whether the interior exists.
            self.quad(
                ((ia[0], ia[1], inner_z0), (ia[0], ia[1], far_z),
                 (ib[0], ib[1], far_z), (ib[0], ib[1], inner_z0)),
                inner_color,
            )
            if entrance:
                # 3059's final paint ownership is asymmetric. ``inner-rim``
                # owns the outward half's outer band, ``underside`` owns the
                # complete inward half, and only the inner part of the
                # outward half retains the earlier ``front-rim`` selector.
                # The backward road pass mirrors these roles physically.
                outward_half = (
                    cell.lane == 3
                    or (cell.lane < 3 and step < segments // 2)
                    or (cell.lane > 3 and step >= segments // 2)
                )
                if outward_half:
                    share = EXPOSED_FRONT_OUTER_SHARE
                    split_a = (
                        oa[0] + (ia[0] - oa[0]) * share,
                        oa[1] + (ia[1] - oa[1]) * share,
                        front_z + (inner_z0 - front_z) * share,
                    )
                    split_b = (
                        ob[0] + (ib[0] - ob[0]) * share,
                        ob[1] + (ib[1] - ob[1]) * share,
                        front_z + (inner_z0 - front_z) * share,
                    )
                    self.quad(
                        ((oa[0], oa[1], front_z), split_a, split_b,
                         (ob[0], ob[1], front_z)),
                        inner_color,
                    )
                    self.quad(
                        (split_a, (ia[0], ia[1], inner_z0),
                         (ib[0], ib[1], inner_z0), split_b),
                        self.selector_color(
                            cell, 67, backward=facet_backward,
                        ),
                    )
                else:
                    self.quad(
                        ((oa[0], oa[1], front_z),
                         (ia[0], ia[1], inner_z0),
                         (ib[0], ib[1], inner_z0),
                         (ob[0], ob[1], front_z)),
                        inner_color,
                    )
        # The two exposed base ledges are the recovered underside material.
        # They close the shell thickness without closing the passage floor.
        for outer_x, inner_x, ledge_backward in (
            (
                center - EXPOSED_OUTER_HALF_WIDTH,
                center - EXPOSED_INNER_HALF_WIDTH,
                False if cell.lane == 3 else backward,
            ),
            (
                center + EXPOSED_INNER_HALF_WIDTH,
                center + EXPOSED_OUTER_HALF_WIDTH,
                True if cell.lane == 3 else backward,
            ),
        ):
            self.quad(
                ((outer_x, 0.0, front_z), (inner_x, 0.0, inner_z0),
                 (inner_x, 0.0, far_z), (outer_x, 0.0, far_z)),
                self.selector_color(
                    cell, 66, backward=ledge_backward,
                ),
            )

    def _carved_face(
        self, cell: RoadCell, x0: float, x1: float, height: float, z: float,
        color: tuple[float, float, float], *,
        upper_color: tuple[float, float, float] | None = None,
        layer_height: float | None = None,
        half_width: float = CARVED_OPENING_HALF_WIDTH,
        spring: float = CARVED_OPENING_SPRING,
        reverse: bool = False,
    ) -> None:
        """Tessellate a layered solid end around one real arched aperture."""
        center = (x0 + x1) * 0.5
        arch = self._arch_points(
            center, spring, half_width, CARVED_OPENING_ARCH_HEIGHT, 12,
        )
        left, right = center - half_width, center + half_width
        lower_top = (
            height if layer_height is None
            else max(spring + CARVED_OPENING_ARCH_HEIGHT,
                     min(height, layer_height))
        )
        self.quad(
            ((x0, 0.0, z), (left, 0.0, z),
             (left, lower_top, z), (x0, lower_top, z)),
            color, reverse=reverse,
        )
        self.quad(
            ((right, 0.0, z), (x1, 0.0, z),
             (x1, lower_top, z), (right, lower_top, z)),
            color, reverse=reverse,
        )
        for left_point, right_point in zip(arch, arch[1:]):
            self.quad(
                ((left_point[0], left_point[1], z),
                 (right_point[0], right_point[1], z),
                 (right_point[0], lower_top, z),
                 (left_point[0], lower_top, z)),
                color, reverse=reverse,
            )
        if height > lower_top:
            self.quad(
                ((x0, lower_top, z), (x1, lower_top, z),
                 (x1, height, z), (x0, height, z)),
                color if upper_color is None else upper_color,
                reverse=reverse,
            )

    def _carved_side(
        self, x: float, front_z: float, z1: float, height: float,
        color: tuple[float, float, float], *, left: bool,
    ) -> None:
        """Emit the original equal lower/upper tiers on one lateral face."""
        split = min(height, CARVED_LOWER_LAYER_HEIGHT)
        levels = (0.0, split, height) if height > split else (0.0, height)
        for y0, y1 in zip(levels, levels[1:]):
            points = (
                ((x, y0, z1), (x, y0, front_z),
                 (x, y1, front_z), (x, y1, z1))
                if left else
                ((x, y0, front_z), (x, y0, z1),
                 (x, y1, z1), (x, y1, front_z))
            )
            self.quad(points, color)

    def _carved_reveal(
        self, center: float, front_z: float, passage_z: float,
        color: tuple[float, float, float],
    ) -> None:
        """Join the front aperture to the passage with one watertight reveal.

        The original ``raised/front-rim`` RLE role is the narrow visible band
        between the front opening and the deeper ``inner-side``/``underside``
        shapes.  The two cross-sections share the same world coordinates;
        perspective alone produces the asymmetric 1--3 pixel band.
        """
        half_width = CARVED_OPENING_HALF_WIDTH
        spring = CARVED_OPENING_SPRING
        arch = self._arch_points(
            center, spring, half_width, CARVED_OPENING_ARCH_HEIGHT, 12,
        )
        left, right = center - half_width, center + half_width
        self.quad(
            ((left, 0.0, front_z), (left, 0.0, passage_z),
             (left, spring, passage_z), (left, spring, front_z)),
            color,
        )
        self.quad(
            ((right, spring, front_z), (right, spring, passage_z),
             (right, 0.0, passage_z), (right, 0.0, front_z)),
            color,
        )
        for a, b in zip(arch, arch[1:]):
            self.quad(
                ((a[0], a[1], front_z), (a[0], a[1], passage_z),
                 (b[0], b[1], passage_z), (b[0], b[1], front_z)),
                color,
            )

    def carved_tunnel(
        self, cell: RoadCell, x0: float, x1: float, z0: float, z1: float,
        height: float, *, entrance: bool, exit: bool,
    ) -> None:
        """Build selectors 3/5 as one solid with an arched passage cut out."""
        # The high terrain nibble remains authoritative for carved solids.
        # Level 18's 0x0520 passage is the first replay-covered example: the
        # original 2D1F trace selects palette 2 for its green exterior top,
        # while its rim/interior/side roles still use selectors 62..65.
        top_color = self.selector_color(
            cell, cell.top_material or RAISED_TOP_DEFAULT_SELECTOR,
            backward=False,
        )
        face_color = self.selector_color(
            cell, CARVED_FACE_SELECTOR, backward=False,
        )
        # 2D1F's forward pass draws the +X face; the backward pass draws the
        # mirrored -X face. Resolve each physical face independently rather
        # than inferring one shade for the whole cell from its lane.
        left_side_color = self.selector_color(
            cell, CARVED_SIDE_SELECTOR, backward=True,
        )
        right_side_color = self.selector_color(
            cell, CARVED_SIDE_SELECTOR, backward=False,
        )
        rim_color = self.selector_color(
            cell, CARVED_RIM_SELECTOR, backward=False,
        )
        center = (x0 + x1) * 0.5
        half_width = CARVED_OPENING_HALF_WIDTH
        spring = CARVED_OPENING_SPRING
        arch = self._arch_points(
            center, spring, half_width, CARVED_OPENING_ARCH_HEIGHT, 12,
        )
        front_z = (
            z0 + min(CARVED_FRONT_SETBACK, (z1 - z0) * 0.2)
            if entrance else z0
        )
        passage_z0 = (
            front_z + min(CARVED_REVEAL_DEPTH, (z1 - front_z) * 0.2)
            if entrance else z0
        )

        # Exterior surfaces belong to the same constructive solid as the
        # opening. At a structural entrance the solid is set behind the deck's
        # near edge exactly as TREKDAT shows; the uncovered deck is the
        # passage's floor/threshold, not an overlapping second tunnel object.
        self.quad(
            ((x0, height, front_z), (x1, height, front_z),
             (x1, height, z1), (x0, height, z1)), top_color,
        )
        self._carved_side(
            x0, front_z, z1, height, left_side_color, left=True,
        )
        self._carved_side(
            x1, front_z, z1, height, right_side_color, left=False,
        )
        layer_height = min(height, CARVED_LOWER_LAYER_HEIGHT)
        if entrance:
            # Selector 62 owns the main solid face. Selector 65 is only the
            # narrow aperture reveal (the original raised/front-rim role).
            self._carved_face(
                cell, x0, x1, height, front_z, face_color,
                upper_color=face_color, layer_height=layer_height,
            )
            self._carved_reveal(center, front_z, passage_z0, rim_color)
        if exit:
            self._carved_face(
                cell, x0, x1, height, z1, face_color,
                upper_color=face_color, layer_height=layer_height,
                reverse=True,
            )

        left, right = center - half_width, center + half_width
        # Straight lower jambs, then the curved ceiling. Both extend through
        # the solid and are therefore genuine passage surfaces for occlusion.
        self.quad(
            ((left, 0.0, passage_z0), (left, spring, passage_z0),
             (left, spring, z1), (left, 0.0, z1)), face_color,
        )
        self.quad(
            ((right, spring, passage_z0), (right, 0.0, passage_z0),
             (right, 0.0, z1), (right, spring, z1)), face_color,
        )
        for a, b in zip(arch, arch[1:]):
            self.quad(
                ((a[0], a[1], passage_z0), (a[0], a[1], z1),
                 (b[0], b[1], z1), (b[0], b[1], passage_z0)),
                face_color,
            )

    @staticmethod
    def _same_tunnel_family(left: RoadCell | None, right: RoadCell) -> bool:
        if left is None:
            return False
        if right.tunnel_shape is TunnelShape.EXPOSED_TUBE:
            return left.tunnel_shape is TunnelShape.EXPOSED_TUBE
        if right.tunnel_shape in (TunnelShape.CARVED_HALF, TunnelShape.CARVED_FULL):
            return left.tunnel_shape in (
                TunnelShape.CARVED_HALF, TunnelShape.CARVED_FULL,
            )
        return False

    def cell(
        self, cell: RoadCell, *, previous: RoadCell | None = None,
        following: RoadCell | None = None,
    ) -> None:
        if not cell.occupied:
            return
        x0 = float(cell.lane - 3.5)
        x1 = x0 + 1.0
        # Absolute row coordinates make the object immutable.  Camera motion
        # is a uniform in the projector, never baked into or rounded into the
        # mesh.  Every source cell remains exactly one lane by one row.
        z0 = float(cell.row)
        z1 = z0 + 1.0
        entrance = not self._same_tunnel_family(previous, cell)
        exit = not self._same_tunnel_family(following, cell)

        if cell.deck_material:
            if entrance and cell.tunnel_shape is TunnelShape.EXPOSED_TUBE:
                deck_splits = (z0 + 0.1,)
            elif entrance and cell.tunnel_shape in (
                TunnelShape.CARVED_HALF, TunnelShape.CARVED_FULL,
            ):
                deck_splits = (
                    z0 + CARVED_FRONT_SETBACK,
                    z0 + CARVED_FRONT_SETBACK + CARVED_REVEAL_DEPTH,
                )
            else:
                deck_splits = ()
            deck_z0 = z0 + ROAD_CELL_DEPTH_OFFSET
            deck_z1 = z1 + ROAD_CELL_DEPTH_OFFSET
            # Splitting the otherwise planar deck at the tunnel depth planes
            # makes its floor vertices identical to the face/reveal/passage
            # vertices. This avoids a geometric T-junction while preserving
            # the original continuous deck material.
            self.box(
                cell, x0, x1, deck_z0, deck_z1, -0.07, 0.0,
                depth_splits=deck_splits,
            )
        if cell.raised is not RaisedShape.NONE and not cell.tunnel:
            row = self.scene.geometry.rows[cell.row]
            left = row.cells[cell.lane - 1] if cell.lane > 0 else None
            right = row.cells[cell.lane + 1] if cell.lane + 1 < len(row.cells) else None
            self.raised_block(
                cell, x0, x1, z0, z1,
                previous=previous, left=left, right=right,
            )
        if cell.tunnel_shape is TunnelShape.EXPOSED_TUBE:
            self.exposed_tunnel(
                cell, x0, x1, z0, z1,
                entrance=entrance,
            )
        elif cell.tunnel_shape in (
            TunnelShape.CARVED_HALF, TunnelShape.CARVED_FULL,
        ):
            original_top = (FULL_BLOCK_HEIGHT
                            if cell.tunnel_shape is TunnelShape.CARVED_FULL
                            else HALF_BLOCK_HEIGHT)
            self.carved_tunnel(
                cell, x0, x1, z0, z1,
                (original_top - ROAD_DECK_HEIGHT) / LANE_UNITS,
                entrance=entrance,
                exit=exit,
            )
        self.source_ids.append(cell.object_id)


def build_polygon_mesh(scene: GameplayScene, *, debug_mode: str = "final",
                       rows_behind: int = 2, rows_ahead: int = 10,
                       full_level: bool = False) -> PolygonMesh:
    """Build the stable native source window around the camera row.

    ``2D1F`` starts at ``current + 7`` and walks backward through
    ``current - 3``. The continuous native lens keeps three additional source
    rows: they enter at zero projected area beyond the recovered vanishing
    depth, then grow smoothly instead of popping in at the DOS admission row.
    The native lens omits only ``current - 3``, whose near edge lies beyond
    its singular near plane and outside the gameplay view.
    """
    if debug_mode not in DEBUG_RENDER_MODES:
        raise ValueError(f"unknown render debug mode {debug_mode!r}")
    if full_level:
        # The world geometry is immutable for a loaded level.  Keeping one
        # resident mesh lets the GPU near/far clip it as the camera advances;
        # rebuilding a moving 13-row window caused a large Python allocation
        # and GPU-upload spike at every row crossing.
        first = 0
        last = len(scene.geometry.rows)
    else:
        first = max(0, scene.track_row - rows_behind)
        last = min(len(scene.geometry.rows), scene.track_row + rows_ahead + 1)
    builder = _MeshBuilder(scene, debug_mode)
    visible_rows = scene.geometry.rows[first:last] if first < last else ()
    for row in visible_rows:
        for cell in row.cells:
            previous = (scene.geometry.rows[cell.row - 1].cells[cell.lane]
                        if cell.row > 0 else None)
            following = (scene.geometry.rows[cell.row + 1].cells[cell.lane]
                         if cell.row + 1 < len(scene.geometry.rows) else None)
            builder.cell(cell, previous=previous, following=following)
    # Hash the exact binary buffers consumed by ModernGL. Textual float repr
    # differs subtly between CPython and PyPy and made an otherwise identical
    # mesh acquire a different cache/evidence identity.
    payload = bytearray(struct.pack("<ii", first, last))
    if builder.vertices:
        payload.extend(struct.pack(f"<{len(builder.vertices)}f", *builder.vertices))
    if builder.indices:
        payload.extend(struct.pack(f"<{len(builder.indices)}I", *builder.indices))
    return PolygonMesh(
        tuple(builder.vertices), tuple(builder.indices), tuple(builder.source_ids),
        (-1 if not visible_rows else first),
        (-1 if not visible_rows else last - 1),
        sha256(payload).hexdigest(),
    )


def _ship_rgba(scene: GameplayScene) -> tuple[bytes, int, int]:
    assets = scene.assets
    # 325B's outer DX loop emits 29 *screen columns* and its inner CX loop
    # emits 24 screen rows. LODSB advances down one column, so CARS stores a
    # frame column-major as source[x * 24 + y]. Reading it as a conventional
    # 24x29 row-major image rotates the ship by 90 degrees.
    frame_width = 29
    frame_height = 24
    frame_stride = 0x2D0
    count = len(assets.ship_sheet_indices) // frame_stride
    sprite = scene.ship_sprite_index
    if sprite == 0xFFFF or sprite < 0 or sprite >= count:
        return b"", 0, 0
    rgba = bytearray(frame_width * frame_height * 4)
    frame_start = sprite * frame_stride
    for y in range(frame_height):
        for x in range(frame_width):
            index = assets.ship_sheet_indices[
                frame_start + x * frame_height + y
            ]
            out = (y * frame_width + x) * 4
            if index == 0:
                rgba[out:out + 4] = b"\0\0\0\0"
            else:
                # CARS pixels are palette-local on disk and biased by +72 in
                # video memory. Resolve through the live DAC so entry/exit
                # fades affect the enhanced ship exactly when they affect the
                # original framebuffer.
                live_index = 72 + index
                rgb = (scene.palette[live_index]
                       if live_index < len(scene.palette) else (0, 0, 0))
                rgba[out:out + 3] = bytes(rgb)
                rgba[out + 3] = 255
    return bytes(rgba), frame_width, frame_height


class RecoveredPolygonRenderer:
    """Prepare a stable world mesh plus the independent exact reference."""

    def __init__(self, *, debug_mode: str = "final", widescreen: bool = False) -> None:
        if debug_mode not in DEBUG_RENDER_MODES:
            raise ValueError(f"unknown render debug mode {debug_mode!r}")
        self.debug_mode = debug_mode
        self.widescreen = bool(widescreen)
        self._projection_key = None
        self._projection = None
        self._projection_before_ship = ()
        self._projection_after_ship = ()
        self._mesh_key = None
        self._mesh = None
        self._mesh_cache = {}
        # Presentation runs independently of the 30 Hz simulation.  These
        # payloads are immutable for a given semantic input and must not be
        # decoded/recoloured again on every interpolated host frame.
        self._background_key = None
        self._background_rgb = b""
        self._dashboard_key = None
        self._dashboard_rgba = None
        self._ship_key = None
        self._ship = (b"", 0, 0)
        self._shadow_key = None
        self._shadow = b""
        self._palette_basis_key = None
        self._palette_basis = None

    def prewarm_level(self, geometry, assets, face_palette_forward,
                      face_palette_backward) -> None:
        """Build immutable native geometry before the gameplay clock starts."""
        if self.debug_mode == "exact-projection":
            return
        # prepare() imports numpy lazily for headless users. Native-3D has
        # already been selected here, so pay that module-import cost during
        # startup/selector idle rather than on the first audible game frame.
        import numpy  # noqa: F401
        scene = SimpleNamespace(
            geometry=geometry,
            palette=assets.source_palette,
            face_palette_forward=tuple(face_palette_forward),
            face_palette_backward=tuple(face_palette_backward),
        )
        key = self._mesh_identity(scene)
        mesh = self._mesh_cache.get(key)
        if mesh is None:
            mesh = build_polygon_mesh(
                scene, debug_mode=self.debug_mode, full_level=True,
            )
            self._mesh_cache[key] = mesh
            while len(self._mesh_cache) > 4:
                self._mesh_cache.pop(next(iter(self._mesh_cache)))
        self._mesh_key = key
        self._mesh = mesh

    def _mesh_identity(self, scene: GameplayScene):
        return (
            scene.geometry.digest,
            scene.palette,
            scene.face_palette_forward,
            scene.face_palette_backward,
            self.debug_mode,
        )

    def _render_scene(self, scene: GameplayScene) -> tuple[GameplayScene, float]:
        """Return an immutable RGB basis plus a GPU-only fade multiplier."""
        key = (
            scene.assets.digest,
            scene.source_palette,
            scene.face_palette_forward,
            scene.face_palette_backward,
        )
        if key != self._palette_basis_key or self._palette_basis is None:
            self._palette_basis_key = key
            self._palette_basis = scene.source_palette
        gain = _uniform_palette_gain(self._palette_basis, scene.palette)
        if gain is None:
            # Non-scalar DAC changes are authoritative colour changes, not a
            # fade. Preserve the exact fallback without allowing transient
            # display state to replace the declared source palette.
            return scene, 1.0
        return replace(scene, palette=self._palette_basis), gain

    def _prepare_projection(self, scene: GameplayScene):
        # TREKDAT changes only at 1/8-row boundaries. The trace is reference
        # evidence and diagnostics in final mode, never its geometry. Cache it
        # so a high host presentation rate does not repeatedly decode an
        # unchanged display list. Palette changes remain in the key for the
        # exact reference.
        key = (
            scene.geometry.digest,
            scene.assets.digest,
            int(scene.track_position) // 0x2000,
            scene.palette[:72],
            scene.face_palette_forward,
            scene.face_palette_backward,
        )
        if key != self._projection_key:
            projection = trace_original_projection(scene)
            self._projection_key = key
            self._projection = projection
            if self.debug_mode == "exact-projection":
                self._projection_before_ship = projection_triangles(
                    projection, after_ship=False,
                )
                self._projection_after_ship = projection_triangles(
                    projection, after_ship=True,
                )
            else:
                self._projection_before_ship = ()
                self._projection_after_ship = ()
        return (
            self._projection,
            self._projection_before_ship,
            self._projection_after_ship,
        )

    def prepare(self, scene: GameplayScene, original_frame=None) -> PolygonFrame:
        import numpy as np

        # ``original_frame`` remains an optional argument for offline parity
        # tools only. It is deliberately not read: native gameplay has one
        # presentation authority and no composited VGA layer can leak into it.
        render_scene, palette_gain = self._render_scene(scene)
        background_key = (render_scene.assets.digest, render_scene.palette)
        if background_key != self._background_key:
            palette = np.asarray(render_scene.palette, dtype=np.uint8)
            background_indices = np.frombuffer(
                render_scene.assets.background_indices, dtype=np.uint8,
            )
            background = np.ascontiguousarray(
                palette[background_indices].reshape(
                    render_scene.assets.background_height,
                    render_scene.assets.background_width,
                    3,
                )
            )
            self._background_rgb = background.tobytes()
            self._background_key = background_key

        dashboard_key = (
            render_scene.assets.digest, render_scene.palette,
            render_scene.dashboard,
        )
        if dashboard_key != self._dashboard_key:
            self._dashboard_rgba = _compose_dashboard_rgba(render_scene)
            self._dashboard_key = dashboard_key

        ship_key = (
            render_scene.assets.digest, render_scene.palette,
            render_scene.ship_sprite_index,
        )
        if ship_key != self._ship_key:
            self._ship = _ship_rgba(render_scene)
            self._ship_key = ship_key
        ship, width, height = self._ship

        shadow_key = (render_scene.palette, render_scene.shadow)
        if shadow_key != self._shadow_key:
            self._shadow = _shadow_rgba(render_scene)
            self._shadow_key = shadow_key
        if self.debug_mode == "exact-projection":
            projection, before_ship, after_ship = self._prepare_projection(
                render_scene,
            )
            first_row = max(0, scene.track_row - 3)
            last_row = min(len(scene.geometry.rows) - 1, scene.track_row + 7)
            mesh = PolygonMesh(
                (), (), (), first_row, last_row, projection.digest,
            )
        else:
            # The stable high-resolution renderer does not consume TREKDAT's
            # raster-span trace.  Running the full original dispatcher here
            # merely for diagnostics duplicated 3153/3190 decoding every 1/8
            # row and caused recurring presentation spikes.  The exact trace
            # remains available explicitly through ``exact-projection`` mode
            # and offline verification tools.
            projection = None
            before_ship = after_ship = ()
            mesh_key = self._mesh_identity(render_scene)
            if mesh_key != self._mesh_key:
                self._mesh = self._mesh_cache.get(mesh_key)
                if self._mesh is None:
                    self._mesh = build_polygon_mesh(
                        render_scene, debug_mode=self.debug_mode,
                        full_level=True,
                    )
                    self._mesh_cache[mesh_key] = self._mesh
                    while len(self._mesh_cache) > 4:
                        self._mesh_cache.pop(next(iter(self._mesh_cache)))
                self._mesh_key = mesh_key
            mesh = self._mesh
        return PolygonFrame(
            scene=scene,
            mesh=mesh,
            projection_trace=projection,
            projection_before_ship=before_ship,
            projection_after_ship=after_ship,
            background_rgb=self._background_rgb,
            dashboard_rgba=self._dashboard_rgba,
            ship_rgba=ship,
            ship_width=width,
            ship_height=height,
            ship_x=scene.ship_screen_x,
            ship_y=scene.ship_screen_y,
            shadow_rgba=self._shadow,
            shadow_width=29,
            shadow_height=9,
            shadow_x=scene.shadow.screen_x,
            shadow_y=scene.shadow.screen_y,
            palette_gain=palette_gain,
            debug_mode=self.debug_mode,
            widescreen=self.widescreen,
        )

    def render(self, scene: GameplayScene, original_frame):
        """Compatibility/reference path: return a read-only original-frame copy.

        The canonical live viewer consumes :meth:`prepare` through the ModernGL
        presenter.  Returning the oracle frame here keeps headless capture and
        strict framebuffer diagnostics explicit rather than pretending a CPU
        rasterizer is the GPU renderer.
        """
        import numpy as np

        if not scene.geometry.rows:
            raise ValueError("polygon presentation requires road geometry")
        return np.array(original_frame, dtype=np.uint8, copy=True, order="C")


__all__ = [
    "CALIBRATION", "DEBUG_RENDER_MODES", "PolygonFrame", "PolygonMesh",
    "DASHBOARD_TOP", "ProjectionCalibration", "RecoveredPolygonRenderer", "build_polygon_mesh",
    "project_world_vertex", "projection_scale", "shadow_camera_depth",
    "ship_camera_depth",
]
