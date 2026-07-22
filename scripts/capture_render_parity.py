"""Compare a native GPU frame with a cached ReplayArtifact oracle boundary.

This is a rendering diagnostic, not another player. It restores immutable
evidence, projects the same gameplay scene used by the canonical player, and
writes the logical oracle frame plus the actual OpenGL output.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "dos_re")]

from dos_re.replay import ReplayArtifact, ReplayPoint  # noqa: E402
from skyroads.bridge.dgroup_view import GameView  # noqa: E402
from skyroads.presentation.renderer import (  # noqa: E402
    DEBUG_RENDER_MODES, RecoveredPolygonRenderer,
)
from skyroads.presentation.scene import (  # noqa: E402
    build_gameplay_scene,
    build_precomposed_level_start_scene,
    is_precomposed_level_start,
)
from skyroads.replay import capture_profile  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("replay", type=Path)
    parser.add_argument("--point", type=int, help="cached point; defaults to the endpoint")
    parser.add_argument(
        "--profile",
        help="exact cached execution profile; by default prefer the capture profile, "
             "then an oracle profile containing --point",
    )
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts" / "render_parity")
    parser.add_argument("--size", default="640x480")
    parser.add_argument(
        "--resize-from",
        help="create this initial window size, then exercise live resize to --size",
    )
    parser.add_argument("--widescreen", action="store_true")
    parser.add_argument(
        "--debug", choices=DEBUG_RENDER_MODES, default="final",
        help="final is stable world geometry; exact-projection retains RLE steps",
    )
    return parser


def _save_rgb(pygame, np, path: Path, rgb) -> None:
    image = np.ascontiguousarray(rgb, dtype=np.uint8)
    height, width = image.shape[:2]
    surface = pygame.image.frombuffer(image.tobytes(), (width, height), "RGB")
    pygame.image.save(surface, str(path))


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    import numpy as np
    import pygame

    from dos_re.display import Display
    from skyroads.presentation.moderngl_presenter import ModernGLFramePresenter

    width, height = (int(value) for value in args.size.lower().split("x", 1))
    artifact = ReplayArtifact.open(args.replay)
    if args.point is None:
        point = artifact.end_point
    else:
        point = ReplayPoint(args.point, artifact.timeline_id)
    profiles = tuple(profile for profile, _state in artifact.profiles())
    if args.profile:
        profile = next(
            (item for item in profiles if item.profile_id == args.profile), None,
        )
        if profile is None:
            raise ValueError(f"unknown replay profile {args.profile!r}")
    else:
        profile = capture_profile(artifact)
        if point not in artifact.cached_points(profile):
            candidates = tuple(
                item for item in profiles
                if point in artifact.cached_points(item)
            )
            if not candidates:
                raise ValueError(
                    f"point {point.ordinal} has no cached continuation; cache it "
                    "through the canonical replay verifier first"
                )
            profile = next(
                (item for item in candidates if item.role == "oracle"),
                candidates[0],
            )
    state = artifact.restore(profile, point)
    memory = state.regions["memory"]
    cpu = state.metadata["cpu"]
    dos = state.metadata["dos"]
    palette = tuple(tuple(color) for color in dos["vga_palette"])
    palette_array = np.asarray(palette, dtype=np.uint8)
    indices = np.frombuffer(memory, dtype=np.uint8, count=320 * 200, offset=0xA0000)
    original = np.ascontiguousarray(palette_array[indices].reshape(200, 320, 3))

    level = int.from_bytes(
        memory[(int(cpu["ds"]) << 4) + 0x9332:(int(cpu["ds"]) << 4) + 0x9334],
        "little",
    )
    view = GameView(memory, base=int(cpu["ds"]) << 4)
    scene_builder = (
        build_precomposed_level_start_scene
        if is_precomposed_level_start(view)
        else build_gameplay_scene
    )
    scene = scene_builder(
        view,
        level=level,
        game_root=ROOT / "assets",
        device_palette=palette,
    )
    packet = RecoveredPolygonRenderer(
        debug_mode=args.debug, widescreen=args.widescreen,
    ).prepare(
        scene, original,
    )

    args.output.mkdir(parents=True, exist_ok=True)
    initial_size = (width, height)
    if args.resize_from:
        initial_size = tuple(
            int(value) for value in args.resize_from.lower().split("x", 1)
        )
    pygame.init()
    display = Display(initial_size, title="SkyRoads render parity capture", opengl=True)
    display.par = 1.2
    presenter = ModernGLFramePresenter(SimpleNamespace(polygon_frame=packet))
    presenter.initialize(display)
    try:
        if initial_size != (width, height):
            display.resize(width, height)
            presenter.resize(display)
        presenter.present(original, display)
        gpu = presenter.capture_rgb(display)
    finally:
        presenter.close()
        pygame.quit()

    _save_rgb(pygame, np, args.output / "oracle_320x200.png", original)
    _save_rgb(pygame, np, args.output / "gpu_output.png", gpu)
    reference_width = min(width, round(height * 4 / 3))
    reference_left = (width - reference_width) // 2
    sample_x = reference_left + np.minimum(
        reference_width - 1,
        ((np.arange(320) + 0.5) * reference_width / 320).astype(int),
    )
    sample_y = np.minimum(
        height - 1,
        ((np.arange(200) + 0.5) * height / 200).astype(int),
    )
    logical_gpu = gpu[sample_y[:, None], sample_x[None, :]]
    differences = np.abs(original.astype(np.int16) - logical_gpu.astype(np.int16))
    _save_rgb(pygame, np, args.output / "native_320x200.png", logical_gpu)
    _save_rgb(
        pygame, np, args.output / "comparison_oracle_native.png",
        np.concatenate((original, logical_gpu), axis=1),
    )
    _save_rgb(
        pygame, np, args.output / "difference.png",
        np.clip(differences * 3, 0, 255).astype(np.uint8),
    )

    ship_pixels = np.frombuffer(packet.ship_rgba, dtype=np.uint8).reshape(
        packet.ship_height, packet.ship_width, 4,
    ) if packet.ship_rgba else np.zeros((0, 0, 4), dtype=np.uint8)
    opaque = ship_pixels[:, :, 3] > 0 if ship_pixels.size else np.zeros(
        (0, 0), dtype=bool,
    )
    x0, y0 = max(0, packet.ship_x), max(0, packet.ship_y)
    x1 = min(320, packet.ship_x + packet.ship_width)
    y1 = min(200, packet.ship_y + packet.ship_height)
    oracle_ship_visible = native_ship_visible = 0
    if x0 < x1 and y0 < y1:
        sx0, sy0 = x0 - packet.ship_x, y0 - packet.ship_y
        sx1, sy1 = sx0 + x1 - x0, sy0 + y1 - y0
        ship_rgb = ship_pixels[sy0:sy1, sx0:sx1, :3]
        ship_mask = opaque[sy0:sy1, sx0:sx1]
        oracle_ship_visible = int(np.logical_and(
            ship_mask,
            np.all(original[y0:y1, x0:x1] == ship_rgb, axis=2),
        ).sum())
        native_ship_visible = int(np.logical_and(
            ship_mask,
            np.all(logical_gpu[y0:y1, x0:x1] == ship_rgb, axis=2),
        ).sum())
    world_projection = args.debug not in ("exact-projection", "original")
    report = {
        "schema": "skyroads:render-comparison/v4",
        "replay": str(args.replay.resolve()),
        "point": point.to_json(),
        "profile": profile.profile_id,
        "profile_role": profile.role,
        "level": level,
        "ship_frame": scene.ship_sprite_index,
        "ship_screen": [scene.ship_screen_x, scene.ship_screen_y],
        "logical_pixel_aspect": "6:5",
        "physical_reference_aspect": "4:3",
        "output_size": [width, height],
        "resized_from": list(initial_size),
        "widescreen": bool(args.widescreen),
        "render_debug": args.debug,
        "reference_composition": "separate oracle frame (not overlaid)",
        "projection": {
            "model": (
                "stable world mesh + recovered continuous pseudo-perspective lens"
                if world_projection
                else "recovered TREKDAT RLE display lists"
            ),
            "sampling": (
                "floating-point world vertices; rounding only in GPU rasterization"
                if world_projection else "exact RLE scanline spans"
            ),
            "internal_raster": [320, 200],
            "road_band": [32, 137],
            "track_row": packet.projection_trace.track_row,
            "phase": packet.projection_trace.phase,
            "trace_digest": packet.projection_trace.digest,
            "draw_calls": len(packet.projection_trace.primitives),
            "visible_draw_calls": len(
                packet.projection_trace.visible_primitives
            ),
            "ship_insertion_order": packet.projection_trace.ship_draw_order,
            "roles": sorted({
                item.role for item in packet.projection_trace.primitives
            }),
            "palette_indices": sorted({
                item.palette_index
                for item in packet.projection_trace.visible_primitives
            }),
            "source_rows": (
                ["current-2", "current+10"]
                if world_projection else ["current-3", "current+7"]
            ),
            **({"tunnel_shapes": sorted({
                cell.tunnel_shape.value
                for row in scene.geometry.rows[packet.mesh.first_row:packet.mesh.last_row + 1]
                for cell in row.cells if cell.tunnel
            })} if world_projection and packet.mesh.first_row >= 0 else {}),
            **({"world_mesh_digest": packet.mesh.digest}
               if world_projection else {}),
        },
        "occlusion": (
            "single native world depth field"
            if world_projection
            else "recovered 325B ship seam / 2D1F painter order"
        ),
        "ship_opaque_pixels": int(opaque.sum()),
        "ship_exact_pixels_visible": {
            "oracle": oracle_ship_visible,
            "native": native_ship_visible,
        },
        "logical_rgb_pixels_different": int(
            np.any(differences != 0, axis=2).sum()
        ),
        "maximum_channel_difference": int(differences.max()),
    }
    (args.output / "report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8",
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
