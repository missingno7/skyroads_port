"""Inspect or export the recovered SkyRoads polygon scene without launching.

Examples::

    python scripts/inspect_render_scene.py --level 14 --track-row 40
    python scripts/inspect_render_scene.py --level 8 --debug collision --json
    python scripts/inspect_render_scene.py --level 29 --dump-obj artifacts/level29.obj

The report distinguishes source records from derived triangles and includes
stable hashes, making it useful in CI and replay investigations without visual
eyeballing.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "dos_re")]

from skyroads.bridge.dgroup_view import GameView  # noqa: E402
from skyroads.levels import PLAYABLE_LEVEL_COUNT, road_archive_index  # noqa: E402
from skyroads.native.level_load import native_level_load  # noqa: E402
from skyroads.native.state import NativeGameState  # noqa: E402
from skyroads.presentation.renderer import (  # noqa: E402
    CALIBRATION,
    DEBUG_RENDER_MODES,
    RecoveredPolygonRenderer,
    build_polygon_mesh,
)
from skyroads.presentation.original_projection import (  # noqa: E402
    trace_original_projection,
)
from skyroads.presentation.scene import (  # noqa: E402
    ROAD_CENTER,
    TRACK_ROW_UNITS,
    build_gameplay_scene,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--level", type=int, required=True, choices=range(PLAYABLE_LEVEL_COUNT),
        help="zero-based level-select identity (0..29; ROADS entry zero is attract mode)",
    )
    parser.add_argument("--track-row", type=int, default=3)
    parser.add_argument("--debug", choices=DEBUG_RENDER_MODES, default="final")
    parser.add_argument("--game-root", type=Path, default=ROOT / "assets")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--dump-obj", type=Path)
    parser.add_argument(
        "--dump-projection", type=Path,
        help="write every original TREKDAT draw call/span as JSON evidence",
    )
    parser.add_argument(
        "--gpu-smoke", action="store_true",
        help="open a bounded 60-frame ModernGL diagnostic using this exact scene",
    )
    parser.add_argument("--widescreen", action="store_true")
    return parser


def _report(scene, mesh, projection) -> dict:
    cells = [cell for row in scene.geometry.rows for cell in row.cells]
    occupied = [cell for cell in cells if cell.occupied]
    return {
        "schema": "skyroads:recovered-render-scene-report/v3",
        "level": scene.level,
        "road_archive_index": scene.geometry.archive_index,
        "source": {
            "road_rows": len(scene.geometry.rows),
            "road_cells": len(cells),
            "occupied_cells": len(occupied),
            "geometry_sha256": scene.geometry.digest,
            "runtime_record_base": "1686:162C",
            "row_bytes": 14,
        },
        "cell_features": {
            "deck_materials": dict(sorted(Counter(
                cell.deck_material for cell in occupied).items())),
            "top_materials": dict(sorted(Counter(
                cell.top_material for cell in occupied).items())),
            "raised_shapes": dict(sorted(Counter(
                cell.raised.value for cell in occupied).items())),
            "tunnels": sum(cell.tunnel for cell in occupied),
            "tunnel_shapes": dict(sorted(Counter(
                cell.tunnel_shape.value for cell in occupied
                if cell.tunnel
            ).items())),
        },
        "timeline": {
            "track_position": scene.track_position,
            "track_row": scene.track_row,
            "track_phase": scene.track_phase,
            "projection_phase": projection.phase,
        },
        "ship": {
            "lateral_position": scene.lateral_position,
            "lateral_lanes": scene.lateral_lanes,
            "height": scene.height,
            "height_lanes": scene.height_lanes,
            "screen_x": scene.ship_screen_x,
            "screen_y": scene.ship_screen_y,
        },
        "mesh": {
            "model": (
                "stable lane/row/elevation world primitives with distinct "
                "thick exposed tubes and carved-solid passages"
            ),
            "first_row": mesh.first_row,
            "last_row": mesh.last_row,
            "vertices": mesh.vertex_count,
            "triangles": mesh.triangle_count,
            "source_objects": len(mesh.source_ids),
            "sha256": mesh.digest,
            "lens": {
                "camera_height": CALIBRATION.camera_height,
                "horizon_y": CALIBRATION.horizon_y,
                "gain": CALIBRATION.lens_gain,
                "near_bias": CALIBRATION.near_bias,
                "vanishing_depth": CALIBRATION.vanishing_depth,
            },
        },
        "original_projection": {
            "model": "TREKDAT RLE display lists + 2D1F painter dispatch",
            "internal_raster": [320, 200],
            "road_band": [32, 137],
            "draw_calls": len(projection.primitives),
            "visible_draw_calls": len(projection.visible_primitives),
            "spans": sum(len(item.spans) for item in projection.primitives),
            "ship_insertion_order": projection.ship_draw_order,
            "roles": dict(sorted(Counter(
                item.role for item in projection.primitives
            ).items())),
            "palette_indices": sorted({
                item.palette_index for item in projection.visible_primitives
            }),
            "source_objects": len({
                item.object_id for item in projection.primitives
            }),
            "sha256": projection.digest,
        },
    }


def _write_projection(path: Path, projection) -> None:
    payload = {
        "schema": "skyroads:original-projection-trace/v1",
        "level": projection.level,
        "track_row": projection.track_row,
        "phase": projection.phase,
        "ship_draw_order": projection.ship_draw_order,
        "sha256": projection.digest,
        "primitives": [
            {
                "object_id": item.object_id,
                "road_row": item.road_row,
                "lane": item.lane,
                "terrain_code": item.terrain_code,
                "world_bounds": item.world_bounds,
                "role": item.role,
                "palette_selector": item.palette_selector,
                "palette_index": item.palette_index,
                "rgb": item.rgb,
                "phase": item.phase,
                "pass": item.pass_index,
                "stream_offset": item.stream_offset,
                "draw_order": item.draw_order,
                "after_ship": item.after_ship,
                "spans": [
                    {"x0": span.x0, "x1": span.x1, "y": span.y,
                     "clipped": span.clipped}
                    for span in item.spans
                ],
            }
            for item in projection.primitives
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_obj(path: Path, mesh) -> None:
    lines = ["# SkyRoads recovered scene mesh", f"# sha256 {mesh.digest}"]
    values = mesh.vertices
    for at in range(0, len(values), 6):
        lines.append(f"v {values[at]:.7g} {values[at + 1]:.7g} {values[at + 2]:.7g}")
    for at in range(0, len(mesh.indices), 3):
        a, b, c = (mesh.indices[at + index] + 1 for index in range(3))
        lines.append(f"f {a} {b} {c}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _gpu_smoke(scene, *, debug_mode: str, widescreen: bool) -> None:
    """Exercise the product presenter with a nonempty deterministic scene.

    This is a renderer diagnostic, not an alternate game player: it has no
    simulation, input, execution plan, or replay authority.
    """
    import numpy as np
    import pygame
    from types import SimpleNamespace

    from dos_re.display import Display
    from skyroads.presentation.moderngl_presenter import ModernGLFramePresenter

    original = np.zeros((200, 320, 3), dtype=np.uint8)
    renderer = RecoveredPolygonRenderer(
        debug_mode=debug_mode, widescreen=widescreen,
    )
    packet = renderer.prepare(scene, original)
    pygame.init()
    display = Display((960, 540 if widescreen else 600),
                      title="SkyRoads recovered scene diagnostic", opengl=True)
    presenter = ModernGLFramePresenter(SimpleNamespace(polygon_frame=packet))
    presenter.initialize(display)
    try:
        clock = pygame.time.Clock()
        for _ in range(60):
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    return
            presenter.present(original, display)
            display.flip()
            clock.tick(60)
    finally:
        presenter.close()
        pygame.quit()


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    state = NativeGameState()
    native_level_load(
        state, road_archive_index(args.level), game_root=args.game_root,
    )
    view = GameView(state)
    view.lateral = args.track_row * TRACK_ROW_UNITS
    view.af1c = ROAD_CENTER
    view.af2c = 0x2800
    scene = build_gameplay_scene(view, level=args.level, game_root=args.game_root)
    mesh = build_polygon_mesh(scene, debug_mode=args.debug)
    projection = trace_original_projection(scene)
    report = _report(scene, mesh, projection)
    if args.dump_obj:
        _write_obj(args.dump_obj, mesh)
        report["mesh"]["obj"] = str(args.dump_obj.resolve())
    if args.dump_projection:
        _write_projection(args.dump_projection, projection)
        report["original_projection"]["trace"] = str(
            args.dump_projection.resolve()
        )
    if args.gpu_smoke:
        _gpu_smoke(scene, debug_mode=args.debug, widescreen=args.widescreen)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"level {args.level}: {report['source']['road_rows']} rows, "
              f"{report['source']['occupied_cells']} occupied cells")
        print(f"source geometry: {scene.geometry.digest}")
        print(f"timeline: row={scene.track_row} phase={scene.track_phase:.3f}; "
              f"TREKDAT phase={projection.phase}")
        print(f"ship: lateral={scene.lateral_lanes:.3f} lanes "
              f"height={scene.height_lanes:.3f} lanes "
              f"screen=({scene.ship_screen_x},{scene.ship_screen_y})")
        print(f"mesh: rows {mesh.first_row}..{mesh.last_row}, "
              f"{mesh.vertex_count} vertices, {mesh.triangle_count} triangles")
        print(f"mesh digest: {mesh.digest}")
        print("original projection: "
              f"{len(projection.primitives)} draw calls, "
              f"{sum(len(item.spans) for item in projection.primitives)} spans, "
              f"digest {projection.digest}")
        if args.dump_obj:
            print(f"OBJ: {args.dump_obj.resolve()}")
        if args.dump_projection:
            print(f"projection trace: {args.dump_projection.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
