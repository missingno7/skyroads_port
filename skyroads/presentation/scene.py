"""Recovered, renderer-neutral SkyRoads world and camera projection.

The source of truth is the seven-word-per-row stream decoded from
``ROADS.LZS``.  The bit layout below is corroborated by the original render
dispatch (``1010:2D1F``), collision selectors (``1010:1631``), level loader
(``1010:5614``), and the shipped levels.  It is deliberately separate from
the modern renderer's vertices and camera lens: recovered facts stay stable
when presentation policy changes.

Coordinate names matter here.  Earlier experimental presentation code called
``DS:9618`` a lateral position.  The original renderer proves that it is the
*forward track coordinate*: ``([9618:961A] / 0x2000) >> 3`` selects one
14-byte road row.  ``DS:AF1C`` is the cross-road coordinate; ``1010:04C0``
divides it into seven bands of ``46 * 0x80 == 0x1700`` units.  ``DS:AF2C`` is
height.  These names are now used consistently by the semantic scene.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from functools import lru_cache
from hashlib import sha256
import struct
from pathlib import Path

from skyroads.bridge.dgroup_view import GameView
from skyroads.levels import road_archive_index, validate_playable_level
from skyroads.native.boot import load_pict, parse_lzs_container
from skyroads.native.boot import DAC_DASHBRD_BASE
from skyroads.native.hud import (
    BAR_ADD, BAR_CLAMP, BAR_DIV, FUEL_TIMER, GRAV_FONT, GRAV_GRAVITY,
    OXYGEN_TIMER, SPEED_ANCHOR, SPEED_CLAMP, SPEED_DIVISOR,
    progress_target_col,
)
from skyroads.native.level_load import decode_level_files, read_game_file
from skyroads.native.render_params import ROW_BASE_TABLE, row_band
from skyroads.native.world_load import (
    BACKGROUND_H,
    BACKGROUND_W,
    expand6,
    load_world_assets,
)


ROAD_VALUES_PER_ROW = 7
ROAD_RECORD_BASE = 0x162C
ROAD_ROW_BYTES = ROAD_VALUES_PER_ROW * 2
TRACK_ROW_UNITS = 0x10000
LANE_UNITS = 46 * 0x80             # recovered by 1010:04C0
ROAD_CENTER = 0x8000               # respawn AF1C, center of lane 3
ROAD_DECK_HEIGHT = 0x2800          # gameplay resume/deck collision seam
HALF_BLOCK_HEIGHT = 0x3200         # 1010:1631 selector 0x0200
FULL_BLOCK_HEIGHT = 0x3C00         # 1010:1631 selector 0x0400

# ``1010:2B3D`` renders the first gameplay image before publishing the live
# simulation fields.  These are the literal inputs it passes to ``2D1F``:
# row_base=100h, lateral_col=18h, screen_row=50h and ship frame 41.  The road
# iterator has already been primed to row three.  The framebuffer therefore
# contains the real initial scene throughout the palette fade even though
# DS:9618/AF1C/AF2C still read as zero.  Keep this recovered presentation seam
# explicit; treating those zeroes as camera state produced the native fade
# jump that the original never has.
INITIAL_TRACK_POSITION = 3 * TRACK_ROW_UNITS
INITIAL_LATERAL_POSITION = ROAD_CENTER
INITIAL_HEIGHT = ROAD_DECK_HEIGHT
INITIAL_SHIP_SPRITE_INDEX = 41
INITIAL_SHIP_SCREEN_X = 0x100 - 0x6E
INITIAL_SHIP_SCREEN_Y = 0x9D - 0x50


class RaisedShape(Enum):
    NONE = "none"
    HALF = "half"
    FULL = "full"


class TunnelShape(Enum):
    """Recovered structural meaning of the dispatcher selector.

    The tunnel bit is not one interchangeable arch decoration.  Selector 1
    is the exposed tube, while selectors 3 and 5 are passages carved through
    the half- and full-height block families respectively.
    """

    NONE = "none"
    EXPOSED_TUBE = "exposed-tube"
    CARVED_HALF = "carved-half"
    CARVED_FULL = "carved-full"


@dataclass(frozen=True)
class RoadCell:
    """One exact ``ROADS.LZS`` word with decoded, source-mapped meaning."""

    object_id: str
    row: int
    lane: int
    source_offset: int
    code: int
    deck_material: int
    top_material: int
    tunnel: bool
    raised: RaisedShape

    @property
    def occupied(self) -> bool:
        return bool(self.code)

    @property
    def tunnel_shape(self) -> TunnelShape:
        if not self.tunnel:
            return TunnelShape.NONE
        if self.raised is RaisedShape.HALF:
            return TunnelShape.CARVED_HALF
        if self.raised is RaisedShape.FULL:
            return TunnelShape.CARVED_FULL
        return TunnelShape.EXPOSED_TUBE

    @property
    def collision_top(self) -> int:
        if self.raised is RaisedShape.FULL:
            return FULL_BLOCK_HEIGHT
        if self.raised is RaisedShape.HALF:
            return HALF_BLOCK_HEIGHT
        return ROAD_DECK_HEIGHT


@dataclass(frozen=True)
class RoadRow:
    ordinal: int
    cells: tuple[RoadCell, ...]

    @property
    def codes(self) -> tuple[int, ...]:
        return tuple(cell.code for cell in self.cells)


@dataclass(frozen=True)
class SceneObject:
    """Stable source identity retained for Atlas/verification consumers."""

    object_id: str
    row: int
    column: int
    code: int


@dataclass(frozen=True)
class RoadGeometry:
    """One selected level's immutable road, retaining its archive identity."""

    level: int
    archive_index: int
    rows: tuple[RoadRow, ...]
    objects: tuple[SceneObject, ...]
    digest: str


@dataclass(frozen=True)
class PresentationAssets:
    """Original visual assets decoded once, without becoming game state."""

    background_width: int
    background_height: int
    background_indices: bytes
    ship_sheet_width: int
    ship_sheet_height: int
    ship_sheet_indices: bytes
    ship_palette: tuple[tuple[int, int, int], ...]
    world_palette: tuple[tuple[int, int, int], ...]
    projection_lists: tuple[bytes, ...]
    dashboard_indices: bytes
    oxygen_cells: tuple[int, ...]
    oxygen_widgets: bytes
    fuel_cells: tuple[int, ...]
    fuel_widgets: bytes
    speed_cells: tuple[int, ...]
    speed_widgets: bytes
    digest: str


@dataclass(frozen=True)
class DashboardState:
    """Authoritative values consumed by the recovered native dashboard."""

    speed_cells: int
    oxygen_cells: int
    fuel_cells: int
    progress_columns: int
    gravity: int
    digit_font: bytes


@dataclass(frozen=True)
class ShadowState:
    """Recovered ``33FD`` road-shadow stencil and its screen-space seam."""

    visible: bool
    band: int
    screen_x: int
    screen_y: int
    mask: bytes
    coverage: bytes


@dataclass(frozen=True)
class GameplayScene:
    """Immutable authoritative input to all gameplay presentation backends."""

    tick: int
    level: int
    track_position: int
    lateral_position: int
    height: int
    forward_velocity: int
    vertical_velocity: int
    game_state: int
    ship_sprite_index: int
    ship_screen_x: int
    ship_screen_y: int
    palette: tuple[tuple[int, int, int], ...]
    face_palette_forward: tuple[int, ...]
    face_palette_backward: tuple[int, ...]
    dashboard: DashboardState
    shadow: ShadowState
    geometry: RoadGeometry
    assets: PresentationAssets

    @property
    def track_row(self) -> int:
        return self.track_position // TRACK_ROW_UNITS

    @property
    def track_phase(self) -> float:
        return (self.track_position % TRACK_ROW_UNITS) / TRACK_ROW_UNITS

    @property
    def lateral_lanes(self) -> float:
        return _signed16_delta(self.lateral_position, ROAD_CENTER) / LANE_UNITS

    @property
    def height_lanes(self) -> float:
        return (self.height - ROAD_DECK_HEIGHT) / LANE_UNITS

    @property
    def content_digest(self) -> str:
        payload = (
            self.tick, self.level, self.track_position, self.lateral_position,
            self.height, self.forward_velocity, self.vertical_velocity,
            self.game_state, self.ship_sprite_index,
            self.ship_screen_x, self.ship_screen_y, self.palette,
            self.face_palette_forward, self.face_palette_backward,
            self.dashboard, self.shadow,
            self.geometry.digest, self.assets.digest,
        )
        return sha256(repr(payload).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SceneComparison:
    equivalent: bool
    differences: tuple[str, ...] = ()


def _signed16_delta(value: int, origin: int) -> int:
    delta = ((int(value) - int(origin) + 0x8000) & 0xFFFF) - 0x8000
    return delta


def decode_road_cell(row: int, lane: int, code: int, *, level: int = 0) -> RoadCell:
    """Decode the original 16-bit map word without renderer assumptions.

    Bits 0..3 select the thin/deck material, bits 4..7 the optional raised
    top material, bit 8 the tunnel/pipe structure, bit 9 a half-height block,
    and bit 10 a full-height block.  The original dispatcher reaches only
    selector values 0..5; simultaneous half/full bits are rejected loudly.
    """
    code &= 0xFFFF
    half = bool(code & 0x0200)
    full = bool(code & 0x0400)
    if code & 0xF800:
        raise ValueError(f"road cell {row}:{lane} uses unrecovered bits {code:#06x}")
    if half and full:
        raise ValueError(f"road cell {row}:{lane} sets both half and full height")
    raised = RaisedShape.FULL if full else RaisedShape.HALF if half else RaisedShape.NONE
    return RoadCell(
        object_id=f"level:{level}:road:{row}:{lane}",
        row=row,
        lane=lane,
        source_offset=ROAD_RECORD_BASE + row * ROAD_ROW_BYTES + lane * 2,
        code=code,
        deck_material=code & 0x000F,
        top_material=(code >> 4) & 0x000F,
        tunnel=bool(code & 0x0100),
        raised=raised,
    )


def _geometry_digest(rows: tuple[RoadRow, ...]) -> str:
    encoded = bytearray()
    for row in rows:
        encoded.extend(struct.pack("<H", row.ordinal))
        for cell in row.cells:
            encoded.extend(struct.pack("<H", cell.code))
    return sha256(encoded).hexdigest()


def decode_road_geometry(
    road: bytes,
    *,
    level: int,
    archive_index: int,
) -> RoadGeometry:
    """Decode exact road bytes while preserving both stable number spaces."""
    if len(road) % ROAD_ROW_BYTES:
        raise ValueError(
            f"level {level} road geometry has an incomplete seven-word row"
        )
    rows = []
    for ordinal in range(len(road) // ROAD_ROW_BYTES):
        words = struct.unpack_from("<7H", road, ordinal * ROAD_ROW_BYTES)
        rows.append(RoadRow(
            ordinal,
            tuple(decode_road_cell(ordinal, lane, word, level=level)
                  for lane, word in enumerate(words)),
        ))
    result = tuple(rows)
    objects = tuple(
        SceneObject(cell.object_id, cell.row, cell.lane, cell.code)
        for row in result for cell in row.cells if cell.occupied
    )
    return RoadGeometry(
        int(level), int(archive_index), result, objects, _geometry_digest(result),
    )


@lru_cache(maxsize=30)
def load_road_geometry(game_root: str, level: int) -> RoadGeometry:
    """Decode one playable level using the original selector-to-file mapping."""
    level = validate_playable_level(level)
    archive = road_archive_index(level)
    decoded = decode_level_files(archive, game_root=Path(game_root))
    return decode_road_geometry(
        decoded.road, level=level, archive_index=archive,
    )


def project_live_road_geometry(view: GameView, *, level: int) -> RoadGeometry:
    """Read the road actually installed in DGROUP for the active gameplay run.

    This is intentionally the live authoritative array, not a parallel level
    interpretation loaded by the presentation layer.  The generated 5614
    loader owns placement and ``DS:[41C0]`` owns its row count.
    """
    level = validate_playable_level(level)
    row_count = int(view.rw(0x41C0))
    maximum = 0x1B58 // ROAD_ROW_BYTES
    if not 0 < row_count <= maximum:
        raise ValueError(
            f"active level {level} has invalid live road row count {row_count}"
        )
    road = bytearray(row_count * ROAD_ROW_BYTES)
    for at in range(0, len(road), 2):
        struct.pack_into("<H", road, at, view.rw(ROAD_RECORD_BASE + at))
    return decode_road_geometry(
        bytes(road), level=level, archive_index=road_archive_index(level),
    )


def _rgb6(cmap: bytes) -> tuple[tuple[int, int, int], ...]:
    return tuple(
        tuple(expand6(cmap[index * 3 + component]) for component in range(3))
        for index in range(len(cmap) // 3)
    )


@lru_cache(maxsize=31)
def load_presentation_assets(game_root: str, level: int) -> PresentationAssets:
    from skyroads.native.boot import load_trek_display_lists

    root = Path(game_root)
    world = load_world_assets(level, game_root=root)
    world_palette = _rgb6(world.cmap)
    cars = read_game_file(root, "CARS.LZS")
    cars_cmap, _, pict_at, _, cars_h, cars_w = parse_lzs_container(cars)
    _, cars_pixels = load_pict(cars, pict_at)
    car_palette = _rgb6(cars_cmap)
    projection_lists = load_trek_display_lists(str(root.resolve()))
    dashboard_file = read_game_file(root, "DASHBRD.LZS")
    _, _, dashboard_at, _, dashboard_h, dashboard_w = parse_lzs_container(
        dashboard_file
    )
    _, dashboard_pixels = load_pict(dashboard_file, dashboard_at)
    dashboard_indices = bytes(
        (pixel + DAC_DASHBRD_BASE) & 0xFF if pixel else 0
        for pixel in dashboard_pixels
    )

    def widgets(name: str, table_bytes: int):
        data = read_game_file(root, name)
        cells = struct.unpack(
            "<" + "H" * (table_bytes // 2), data[:table_bytes],
        )
        return tuple(cells), data[table_bytes:]

    oxygen_cells, oxygen_widgets = widgets("OXY_DISP.DAT", 20)
    fuel_cells, fuel_widgets = widgets("FUL_DISP.DAT", 20)
    speed_cells, speed_widgets = widgets("SPEED.DAT", 68)
    digest = sha256(
        world.background + world.cmap + cars_pixels + cars_cmap
        + struct.pack("<HH", cars_w, cars_h)
        + b"".join(projection_lists) + dashboard_indices
        + oxygen_widgets + fuel_widgets + speed_widgets
    ).hexdigest()
    return PresentationAssets(
        BACKGROUND_W, BACKGROUND_H, world.background,
        cars_w, cars_h, cars_pixels, car_palette, world_palette,
        projection_lists, dashboard_indices,
        oxygen_cells, oxygen_widgets, fuel_cells, fuel_widgets,
        speed_cells, speed_widgets, digest,
    )


def _palette(
    view: GameView,
    assets: PresentationAssets,
    device_palette,
) -> tuple[tuple[int, int, int], ...]:
    """Return the complete live VGA DAC, with a deterministic tool fallback."""
    if device_palette is not None:
        palette = tuple(
            tuple(int(component) & 0xFF for component in color[:3])
            for color in device_palette
        )
        if len(palette) < 256:
            palette += ((0, 0, 0),) * (256 - len(palette))
        return palette[:256]

    road = tuple(
        tuple(expand6(int(view._backend.rb(0x41C2 + index * 3 + component)))
              for component in range(3))
        for index in range(72)
    )
    palette = [(0, 0, 0)] * 256
    palette[:72] = road
    palette[72:72 + len(assets.ship_palette)] = assets.ship_palette
    palette[142:142 + len(assets.world_palette)] = assets.world_palette
    return tuple(palette)


def build_gameplay_scene(
    view: GameView,
    *,
    level: int,
    game_root: str | Path,
    geometry: RoadGeometry | None = None,
    device_palette=None,
) -> GameplayScene:
    """Project authoritative state without writes or simulated CPU state."""
    root = str(Path(game_root).resolve())
    level = validate_playable_level(level)
    af1c = int(view.af1c)
    af2c = int(view.af2c)
    ship_x = project_ship_screen_x(view, af1c)
    ship_y = 0x9D - af2c // 0x80
    assets = load_presentation_assets(root, level)
    live_fill_forward = tuple(
        int(view._backend.rb(0x0352 + index * 4))
        for index in range(256)
    )
    live_fill_backward = tuple(
        int(view._backend.rb(0x0353 + index * 4))
        for index in range(256)
    )
    if not any(live_fill_forward[1:74]):
        from skyroads.handrecovered.rle_sprite import RECOVERED_FILL_FORWARD
        live_fill_forward = RECOVERED_FILL_FORWARD
    if not any(live_fill_backward[1:74]):
        from skyroads.handrecovered.rle_sprite import RECOVERED_FILL_BACKWARD
        live_fill_backward = RECOVERED_FILL_BACKWARD

    def rd(off: int) -> int:
        return int(view.rw(off))

    anchor = rd(SPEED_ANCHOR) | (rd(SPEED_ANCHOR + 2) << 16)
    ship_position = int(view.ship_pos) & 0xFFFFFFFF
    speed = min(((ship_position - anchor) & 0xFFFFFFFF) // SPEED_DIVISOR,
                SPEED_CLAMP)
    dashboard = DashboardState(
        speed_cells=speed,
        oxygen_cells=min((rd(OXYGEN_TIMER) + BAR_ADD) // BAR_DIV, BAR_CLAMP),
        fuel_cells=min((rd(FUEL_TIMER) + BAR_ADD) // BAR_DIV, BAR_CLAMP),
        progress_columns=progress_target_col(
            int(view.lateral) & 0xFFFFFFFF, rd(0x41C0),
        ),
        gravity=rd(GRAV_GRAVITY),
        digit_font=bytes(
            int(view._backend.rb(GRAV_FONT + index)) for index in range(200)
        ),
    )
    height_clip = rd(0x0E34)
    shadow_band = height_clip // 5
    shadow_offset = rd(0x0E70)
    shadow = ShadowState(
        visible=shadow_band < 5 and shadow_offset < 320 * 200,
        band=shadow_band,
        screen_x=shadow_offset % 320,
        screen_y=shadow_offset // 320,
        mask=(
            bytes(int(view._backend.rb(0x068E + shadow_band * 0x105 + index))
                  for index in range(29 * 9))
            if shadow_band < 5 else b""
        ),
        # 33FD does not stamp its pattern blindly.  It shades only texels
        # admitted by 325B/32C1's exact road/tunnel coverage mask.  Retaining
        # that second input is what preserves original occlusion in a native
        # renderer; the pattern alone paints through tunnel rims and gaps.
        coverage=(
            bytes(int(view._backend.rb(0x113E + index))
                  for index in range(29 * 9))
            if shadow_band < 5 else b""
        ),
    )
    return GameplayScene(
        tick=int(view.elapsed_ticks),
        level=level,
        track_position=int(view.lateral),
        lateral_position=af1c,
        height=af2c,
        forward_velocity=int(view.ship_pos),
        vertical_velocity=int(view.bounce),
        game_state=int(view.game_state),
        ship_sprite_index=int(view.rw(0x0E24)),
        ship_screen_x=ship_x,
        ship_screen_y=ship_y,
        palette=_palette(view, assets, device_palette),
        face_palette_forward=live_fill_forward,
        face_palette_backward=live_fill_backward,
        dashboard=dashboard,
        shadow=shadow,
        geometry=(geometry if geometry is not None
                  else project_live_road_geometry(view, level=level)),
        assets=assets,
    )


def project_ship_screen_x(view: GameView, af1c: int | None = None) -> int:
    """Recover ``0C98``/``325B``'s authoritative horizontal ship position.

    This is the original seven-band row-base lookup plus the sub-band AF1C
    term, before final sprite placement.  Presentation enhancements such as
    stereo may consume this semantic position; they must not invent a second
    lateral model.
    """
    lateral = int(view.af1c if af1c is None else af1c) & 0xFFFF
    projected_row_base = (
        int(view.rw(ROW_BASE_TABLE + row_band(lateral) * 2))
        + lateral // 0x80
    ) & 0xFFFF
    return projected_row_base - 0x6E


def ship_stereo_pan(view: GameView) -> float:
    """Map the recovered ship sprite centre to an optional stereo aperture.

    SkyRoads' original OPL2/SB output is mono.  This value is therefore an
    explicit presentation enhancement, but its source coordinate is the exact
    original renderer coordinate: the 29-pixel ship centre in a 320-pixel
    physical viewport.
    """
    centre = project_ship_screen_x(view) + 14.5
    return max(-1.0, min(1.0, (centre - 160.0) / 160.0))


def is_precomposed_level_start(view: GameView) -> bool:
    """Whether ``2B3D``'s precomposed restart image owns presentation.

    ``0E32 == 1`` is part of the literal eight-word ``2D1F`` call made only
    for this frame; the ordinary ``0C98`` frame orchestrator supplies zero.
    The complete recovered call signature is the ownership token.  In the
    crash/restart path the old live simulation fields intentionally survive
    while this image fades in, and are not reset until the fade completes.
    Requiring those fields to be zero therefore rendered the stale crash
    camera throughout the fade and produced a visible jump at the seam.
    """
    return (
        int(view.rw(0x0E28)) == 0x100
        and int(view.rw(0x0E2A)) == 0x18
        and int(view.rw(0x0E2C)) == 0x50
        and int(view.rw(0x0E32)) == 1
        and int(view.rw(0x0E34)) == 0
    )


def build_precomposed_level_start_scene(
    view: GameView,
    *,
    level: int,
    game_root: str | Path,
    geometry: RoadGeometry | None = None,
    device_palette=None,
) -> GameplayScene:
    """Recover the immutable scene already present beneath the start fade.

    Only presentation coordinates are substituted.  Palette, dashboard and
    shadow remain the values produced by the original setup path at this
    instant, so the fade remains a palette transition rather than a synthetic
    gameplay tick.
    """
    scene = build_gameplay_scene(
        view,
        level=level,
        game_root=game_root,
        geometry=geometry,
        device_palette=device_palette,
    )
    return replace(
        scene,
        track_position=INITIAL_TRACK_POSITION,
        lateral_position=INITIAL_LATERAL_POSITION,
        height=INITIAL_HEIGHT,
        ship_sprite_index=INITIAL_SHIP_SPRITE_INDEX,
        ship_screen_x=INITIAL_SHIP_SCREEN_X,
        ship_screen_y=INITIAL_SHIP_SCREEN_Y,
    )


def interpolate_scene(previous: GameplayScene, current: GameplayScene,
                      alpha: float) -> GameplayScene:
    """Interpolate camera/ship presentation only; geometry remains immutable."""
    if previous.level != current.level or previous.geometry != current.geometry:
        return current
    alpha = max(0.0, min(1.0, float(alpha)))

    def blend(left: int, right: int) -> int:
        return round(left + (right - left) * alpha)

    return GameplayScene(
        tick=current.tick,
        level=current.level,
        track_position=blend(previous.track_position, current.track_position),
        lateral_position=blend(previous.lateral_position, current.lateral_position),
        height=blend(previous.height, current.height),
        forward_velocity=current.forward_velocity,
        vertical_velocity=current.vertical_velocity,
        game_state=current.game_state,
        ship_sprite_index=current.ship_sprite_index,
        ship_screen_x=blend(previous.ship_screen_x, current.ship_screen_x),
        ship_screen_y=blend(previous.ship_screen_y, current.ship_screen_y),
        palette=current.palette,
        face_palette_forward=current.face_palette_forward,
        face_palette_backward=current.face_palette_backward,
        dashboard=current.dashboard,
        shadow=current.shadow,
        geometry=current.geometry,
        assets=current.assets,
    )


def compare_scene_contents(expected: GameplayScene,
                           actual: GameplayScene) -> SceneComparison:
    """Compare authoritative scene inputs and name omitted source records."""
    differences: list[str] = []
    for name in (
        "level", "track_position", "lateral_position", "height",
        "forward_velocity", "vertical_velocity", "game_state",
        "ship_sprite_index", "ship_screen_x", "ship_screen_y", "palette",
        "face_palette_forward", "face_palette_backward",
        "dashboard", "shadow",
    ):
        if getattr(expected, name) != getattr(actual, name):
            differences.append(f"scene.{name}")
    expected_objects = {item.object_id for item in expected.geometry.objects}
    actual_objects = {item.object_id for item in actual.geometry.objects}
    for object_id in sorted(expected_objects - actual_objects)[:8]:
        differences.append(f"scene.missing-object:{object_id}")
    for object_id in sorted(actual_objects - expected_objects)[:8]:
        differences.append(f"scene.unexpected-object:{object_id}")
    if expected.geometry.digest != actual.geometry.digest:
        differences.append("scene.road-geometry")
    if expected.assets.digest != actual.assets.digest:
        differences.append("scene.presentation-assets")
    return SceneComparison(not differences, tuple(differences))


__all__ = [
    "DashboardState", "FULL_BLOCK_HEIGHT", "GameplayScene", "HALF_BLOCK_HEIGHT",
    "INITIAL_HEIGHT", "INITIAL_LATERAL_POSITION", "INITIAL_SHIP_SCREEN_X",
    "INITIAL_SHIP_SCREEN_Y", "INITIAL_SHIP_SPRITE_INDEX",
    "INITIAL_TRACK_POSITION", "LANE_UNITS", "project_ship_screen_x",
    "ship_stereo_pan",
    "PresentationAssets", "ROAD_CENTER", "ROAD_DECK_HEIGHT", "ROAD_RECORD_BASE",
    "ROAD_ROW_BYTES", "ROAD_VALUES_PER_ROW", "RaisedShape", "RoadCell",
    "ShadowState", "TunnelShape",
    "RoadGeometry", "RoadRow", "SceneComparison", "SceneObject",
    "TRACK_ROW_UNITS", "build_gameplay_scene",
    "build_precomposed_level_start_scene", "compare_scene_contents",
    "decode_road_cell", "decode_road_geometry", "interpolate_scene",
    "load_presentation_assets", "load_road_geometry",
    "is_precomposed_level_start", "project_live_road_geometry",
]
