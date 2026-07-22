"""The original SkyRoads pseudo-3D projection as inspectable geometry.

SkyRoads does not transform road vertices with a conventional perspective
matrix.  TREKDAT contains eight sub-row phases of preprojected RLE silhouettes;
``1010:2D1F`` selects, recolors, clips and composites those silhouettes in a
fixed painter order.  This module runs that recovered dispatcher without
touching a framebuffer and exposes its exact spans to diagnostics and the GPU
presentation layer.

The model intentionally keeps two coordinate spaces distinct:

* source identities and bounds are stable road-row/lane facts;
* projected coordinates are the game's internal square 320x200 raster grid.

The DOS 6:5 pixel aspect correction belongs to final presentation, never to
the recovered projection itself.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import struct

from skyroads.handrecovered.rle_sprite import decode_rle_strip
from skyroads.native.boot import SEG_DISPLAY_LISTS
from skyroads.native.tile_dispatch import TileDrawCall, render_tile_passes
from skyroads.presentation.scene import GameplayScene


INTERNAL_WIDTH = 320
INTERNAL_HEIGHT = 200
GAMEPLAY_HEIGHT = 138
# The canonical gameplay compositor renders into [5478] through the recovered
# +0x280-paragraph destination alias.  Relative to the buffer later presented,
# that is exactly 0x2800 / 320 == 32 scanlines.  TREKDAT offsets themselves
# therefore occupy rows 0..105 and become visible rows 32..137.
ROAD_DESTINATION_Y = 32
DGROUP_SEG = 0x1000
LIST_SEG = SEG_DISPLAY_LISTS[0]
DEST_SEG = 0x7000


@dataclass(frozen=True)
class ProjectedSpan:
    """One scanline of an original preprojected face silhouette."""

    x0: int
    x1: int
    y: int
    clipped: bool


@dataclass(frozen=True)
class ProjectedPrimitive:
    """One exact original draw call, retaining evidence and painter identity."""

    object_id: str
    road_row: int
    lane: int
    terrain_code: int
    world_bounds: tuple[float, float, float, float, float, float]
    role: str
    palette_selector: int
    palette_index: int
    rgb: tuple[int, int, int]
    phase: int
    pass_index: int
    stream_offset: int
    draw_order: int
    after_ship: bool
    spans: tuple[ProjectedSpan, ...]

    @property
    def visible(self) -> bool:
        return any(not span.clipped for span in self.spans)


@dataclass(frozen=True)
class ProjectionTrace:
    """Complete terrain painter stream for one semantic presentation state."""

    level: int
    track_row: int
    phase: int
    primitives: tuple[ProjectedPrimitive, ...]
    ship_draw_order: int
    digest: str

    @property
    def visible_primitives(self) -> tuple[ProjectedPrimitive, ...]:
        return tuple(item for item in self.primitives if item.visible)


class _ProjectionMemory:
    """Small segment-addressed carrier for trace-only tile dispatch."""

    __slots__ = ("segments",)

    def __init__(self, phase: bytes):
        self.segments = {
            DGROUP_SEG: bytearray(0x10000),
            LIST_SEG: bytearray(phase),
        }

    def rb(self, seg: int, off: int) -> int:
        return self.segments[seg][off & 0xFFFF]

    def wb(self, seg: int, off: int, value: int) -> None:
        # Trace mode writes only style selectors into the active display list.
        self.segments[seg][off & 0xFFFF] = value & 0xFF

    def rw(self, seg: int, off: int) -> int:
        data = self.segments[seg]
        at = off & 0xFFFF
        return data[at] | (data[(at + 1) & 0xFFFF] << 8)

    def ww(self, seg: int, off: int, value: int) -> None:
        data = self.segments[seg]
        at = off & 0xFFFF
        data[at] = value & 0xFF
        data[(at + 1) & 0xFFFF] = (value >> 8) & 0xFF


def _world_bounds(scene: GameplayScene, call: TileDrawCall):
    if not (0 <= call.road_row < len(scene.geometry.rows)
            and 0 <= call.lane < 7):
        return (float(call.lane), 0.0, float(call.road_row),
                float(call.lane + 1), 0.0, float(call.road_row + 1))
    cell = scene.geometry.rows[call.road_row].cells[call.lane]
    height = cell.collision_top / 0x1700 if cell.occupied else 0.0
    return (
        float(call.lane) - 3.5, 0.0, float(call.road_row),
        float(call.lane) - 2.5, height, float(call.road_row + 1),
    )


def trace_original_projection(scene: GameplayScene) -> ProjectionTrace:
    """Run the exact 2D1F selection/order logic and decode its RLE faces."""
    e2a = (int(scene.track_position) // 0x2000) & 0xFFFF
    phase = e2a & 7
    memory = _ProjectionMemory(scene.assets.projection_lists[phase])
    dg = memory.segments[DGROUP_SEG]

    def ww(off: int, value: int) -> None:
        struct.pack_into("<H", dg, off, value & 0xFFFF)

    # Only the selected active segment is present in this compact carrier.
    ww(0x0E76 + phase * 2, LIST_SEG)
    ww(0x0E2A, e2a)
    ww(0x0E36, DEST_SEG)
    ww(0x003C, 1)
    for index, value in enumerate(scene.face_palette_forward):
        dg[0x0352 + index * 4] = value & 0xFF
    for index, value in enumerate(scene.face_palette_backward):
        dg[0x0353 + index * 4] = value & 0xFF
    for row in scene.geometry.rows:
        for cell in row.cells:
            if cell.source_offset + 1 < len(dg):
                ww(cell.source_offset, cell.code)

    primitives: list[ProjectedPrimitive] = []
    ship_order = -1

    def observe(call: TileDrawCall) -> None:
        decoded = decode_rle_strip(
            memory.rb, DGROUP_SEG, call.stream_segment, call.stream_offset,
            backward=bool(call.pass_index),
        )
        spans: list[ProjectedSpan] = []
        for item in decoded.spans:
            encoded_y, x0 = divmod(item.offset, INTERNAL_WIDTH)
            y = encoded_y + ROAD_DESTINATION_Y
            x1 = x0 + item.length
            clipped = y >= GAMEPLAY_HEIGHT or x0 >= INTERNAL_WIDTH or x1 <= 0
            spans.append(ProjectedSpan(
                max(0, x0), min(INTERNAL_WIDTH, x1), y, clipped,
            ))
        if (0 <= call.road_row < len(scene.geometry.rows)
                and 0 <= call.lane < 7):
            cell = scene.geometry.rows[call.road_row].cells[call.lane]
            object_id = cell.object_id
            terrain_code = cell.code
        else:
            object_id = f"level:{scene.level}:padding:{call.road_row}:{call.lane}"
            terrain_code = 0
        palette_index = decoded.palette_index
        rgb = (scene.palette[palette_index]
               if palette_index < len(scene.palette) else (0, 0, 0))
        primitives.append(ProjectedPrimitive(
            object_id=object_id,
            road_row=call.road_row,
            lane=call.lane,
            terrain_code=terrain_code,
            world_bounds=_world_bounds(scene, call),
            role=call.role,
            palette_selector=decoded.palette_selector,
            palette_index=palette_index,
            rgb=tuple(int(channel) for channel in rgb),
            phase=call.track_phase,
            pass_index=call.pass_index,
            stream_offset=call.stream_offset,
            draw_order=call.order,
            after_ship=call.after_ship,
            spans=tuple(spans),
        ))

    def mark_ship(_ctx) -> None:
        nonlocal ship_order
        ship_order = len(primitives)

    render_tile_passes(
        memory, DGROUP_SEG, on_ship_row=mark_ship,
        observer=observe, rasterize=False,
    )
    payload = bytearray(struct.pack(
        "<4I", scene.level, scene.track_row, phase, ship_order,
    ))
    for primitive in primitives:
        payload.extend(struct.pack(
            "<IhhHBB", primitive.draw_order, primitive.road_row,
            primitive.lane, primitive.stream_offset,
            primitive.palette_index, int(primitive.after_ship),
        ))
        payload.extend(primitive.role.encode("ascii") + b"\0")
        for span in primitive.spans:
            payload.extend(struct.pack("<4h", span.x0, span.x1, span.y,
                                       int(span.clipped)))
    return ProjectionTrace(
        scene.level, scene.track_row, phase, tuple(primitives), ship_order,
        sha256(payload).hexdigest(),
    )


def projection_triangles(trace: ProjectionTrace, *, after_ship: bool) -> tuple[float, ...]:
    """Return exact raster-equivalent triangles (x, y, r, g, b).

    This deliberately retains one rectangle per original scanline and is the
    strict 320x200 reference, not the normal high-resolution presentation.
    """
    vertices: list[float] = []
    for primitive in trace.primitives:
        if primitive.after_ship != after_ship:
            continue
        r, g, b = (channel / 255.0 for channel in primitive.rgb)
        for span in primitive.spans:
            if span.clipped or span.x1 <= span.x0:
                continue
            x0, x1 = float(span.x0), float(span.x1)
            y0, y1 = float(span.y), float(span.y + 1)
            for x, y in (
                (x0, y0), (x1, y0), (x1, y1),
                (x0, y0), (x1, y1), (x0, y1),
            ):
                vertices.extend((x, y, r, g, b))
    return tuple(vertices)


__all__ = [
    "GAMEPLAY_HEIGHT", "INTERNAL_HEIGHT", "INTERNAL_WIDTH",
    "ROAD_DESTINATION_Y", "ProjectedPrimitive", "ProjectedSpan", "ProjectionTrace",
    "projection_triangles", "trace_original_projection",
]
