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
import colorsys
from hashlib import sha256
import math
import struct
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
    alpha = bytearray(
        255 if index // width >= 9 or value else 0
        for index, value in enumerate(indices)
    )

    def paint_widgets(offsets, bank: bytes, count: int) -> None:
        for cell, record_at in enumerate(offsets):
            if record_at + 4 > len(bank):
                raise ValueError(f"HUD widget {cell} points outside its DAT bank")
            destination, w, h = struct.unpack_from("<HBB", bank, record_at)
            end = record_at + 4 + w * h
            if end > len(bank):
                raise ValueError(f"HUD widget {cell} has a truncated stencil")
            stencil = stencil_blit(
                bank[record_at + 4:end],
                0x5E if cell < count else 0x5C,
                0x5F if cell < count else 0x5D,
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
            source = int(source)
            target = int(target)
            channels.append((source, target))
            if source >= 8:
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
        expected = source * gain
        if abs(target - expected) > 5.0:
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
            *, raised: bool = False) -> None:
        self.quad(((x0, y1, z0), (x1, y1, z0), (x1, y1, z1), (x0, y1, z1)),
                  self.color(cell, "top", raised=raised))
        self.quad(((x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0)),
                  self.color(cell, "front", raised=raised))
        self.quad(((x0, y0, z1), (x0, y0, z0), (x0, y1, z0), (x0, y1, z1)),
                  self.color(cell, "left", raised=raised))
        self.quad(((x1, y0, z0), (x1, y0, z1), (x1, y1, z1), (x1, y1, z0)),
                  self.color(cell, "right", raised=raised))

    def exposed_tunnel(
        self, cell: RoadCell, x0: float, x1: float, z0: float, z1: float,
        *, entrance: bool, exit: bool,
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
        outer = self._arch_points(center, 0.0, 0.5, 0.5, segments)
        inner = self._arch_points(center, 0.0, 0.36, 0.35, segments)
        rim_depth = min(0.10, (z1 - z0) * 0.2)
        inner_z0 = z0 + rim_depth if entrance else z0
        backward = cell.lane > 3
        inner_color = self.selector_color(cell, 66, backward=backward)
        rim_color = self.selector_color(cell, 67, backward=backward)

        for step in range(segments):
            shade = 68 + min(5, step * 6 // segments)
            oa, ob = outer[step], outer[step + 1]
            ia, ib = inner[step], inner[step + 1]
            self.quad(
                ((oa[0], oa[1], z0), (ob[0], ob[1], z0),
                 (ob[0], ob[1], z1), (oa[0], oa[1], z1)),
                self.selector_color(cell, shade, backward=backward),
            )
            # Inward-facing passage surface. The renderer uses a depth buffer
            # without face culling, so winding documents topology rather than
            # deciding whether the interior exists.
            self.quad(
                ((ia[0], ia[1], inner_z0), (ia[0], ia[1], z1),
                 (ib[0], ib[1], z1), (ib[0], ib[1], inner_z0)),
                inner_color,
            )
            if entrance:
                self.quad(
                    ((oa[0], oa[1], z0), (ia[0], ia[1], inner_z0),
                     (ib[0], ib[1], inner_z0), (ob[0], ob[1], z0)),
                    rim_color,
                )
            if exit:
                self.quad(
                    ((ob[0], ob[1], z1), (ib[0], ib[1], z1),
                     (ia[0], ia[1], z1), (oa[0], oa[1], z1)),
                    inner_color,
                )

        # The two exposed base ledges are the recovered underside material.
        # They close the shell thickness without closing the passage floor.
        for outer_x, inner_x in ((x0, center - 0.36),
                                 (center + 0.36, x1)):
            self.quad(
                ((outer_x, 0.0, z0), (inner_x, 0.0, inner_z0),
                 (inner_x, 0.0, z1), (outer_x, 0.0, z1)),
                inner_color,
            )

    def _carved_face(
        self, cell: RoadCell, x0: float, x1: float, height: float, z: float,
        color: tuple[float, float, float], *, reverse: bool = False,
    ) -> None:
        """Tessellate a box end while leaving one real arched aperture."""
        center = (x0 + x1) * 0.5
        half_width = 0.38
        spring = 0.08
        arch = self._arch_points(center, spring, half_width, 0.30, 12)
        left, right = center - half_width, center + half_width
        self.quad(
            ((x0, 0.0, z), (left, 0.0, z),
             (left, height, z), (x0, height, z)), color, reverse=reverse,
        )
        self.quad(
            ((right, 0.0, z), (x1, 0.0, z),
             (x1, height, z), (right, height, z)), color, reverse=reverse,
        )
        for left_point, right_point in zip(arch, arch[1:]):
            self.quad(
                ((left_point[0], left_point[1], z),
                 (right_point[0], right_point[1], z),
                 (right_point[0], height, z),
                 (left_point[0], height, z)),
                color, reverse=reverse,
            )

    def carved_tunnel(
        self, cell: RoadCell, x0: float, x1: float, z0: float, z1: float,
        height: float, *, entrance: bool, exit: bool,
    ) -> None:
        """Build selectors 3/5 as one solid with an arched passage cut out."""
        backward = cell.lane > 3
        # The high terrain nibble remains authoritative for carved solids.
        # Level 18's 0x0520 passage is the first replay-covered example: the
        # original 2D1F trace selects palette 2 for its green exterior top,
        # while its rim/interior/side roles still use selectors 62..65.
        top_color = self.selector_color(
            cell, cell.top_material or 61, backward=backward,
        )
        inner_color = self.selector_color(cell, 62, backward=backward)
        side_color = self.selector_color(cell, 63, backward=backward)
        rim_color = self.selector_color(cell, 65, backward=backward)
        center = (x0 + x1) * 0.5
        half_width = 0.38
        spring = 0.08
        arch = self._arch_points(center, spring, half_width, 0.30, 12)
        rim_depth = min(0.10, (z1 - z0) * 0.2)
        passage_z0 = z0 + rim_depth if entrance else z0

        # Exterior surfaces belong to the same constructive solid as the
        # opening. There is deliberately no closed box hidden behind an arch.
        self.quad(
            ((x0, height, z0), (x1, height, z0),
             (x1, height, z1), (x0, height, z1)), top_color,
        )
        self.quad(
            ((x0, 0.0, z1), (x0, 0.0, z0),
             (x0, height, z0), (x0, height, z1)), side_color,
        )
        self.quad(
            ((x1, 0.0, z0), (x1, 0.0, z1),
             (x1, height, z1), (x1, height, z0)), side_color,
        )
        if entrance:
            self._carved_face(cell, x0, x1, height, z0, rim_color)
        if exit:
            self._carved_face(
                cell, x0, x1, height, z1, inner_color, reverse=True,
            )

        left, right = center - half_width, center + half_width
        # Straight lower jambs, then the curved ceiling. Both extend through
        # the solid and are therefore genuine passage surfaces for occlusion.
        self.quad(
            ((left, 0.0, passage_z0), (left, spring, passage_z0),
             (left, spring, z1), (left, 0.0, z1)), inner_color,
        )
        self.quad(
            ((right, spring, passage_z0), (right, 0.0, passage_z0),
             (right, 0.0, z1), (right, spring, z1)), inner_color,
        )
        for a, b in zip(arch, arch[1:]):
            self.quad(
                ((a[0], a[1], passage_z0), (a[0], a[1], z1),
                 (b[0], b[1], z1), (b[0], b[1], passage_z0)),
                inner_color,
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

        if cell.deck_material:
            self.box(cell, x0, x1, z0, z1, -0.07, 0.0)
        if cell.raised is not RaisedShape.NONE and not cell.tunnel:
            original_top = (FULL_BLOCK_HEIGHT if cell.raised is RaisedShape.FULL
                            else HALF_BLOCK_HEIGHT)
            y1 = (original_top - ROAD_DECK_HEIGHT) / LANE_UNITS
            self.box(cell, x0, x1, z0, z1, 0.0, y1, raised=True)
        if cell.tunnel_shape is TunnelShape.EXPOSED_TUBE:
            self.exposed_tunnel(
                cell, x0, x1, z0, z1,
                entrance=not self._same_tunnel_family(previous, cell),
                exit=not self._same_tunnel_family(following, cell),
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
                entrance=not self._same_tunnel_family(previous, cell),
                exit=not self._same_tunnel_family(following, cell),
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
    "project_world_vertex", "projection_scale", "ship_camera_depth",
]
