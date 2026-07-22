"""Recover a continuous world-to-screen calibration from TREKDAT evidence.

This is an offline analysis tool.  It renders synthetic, source-valid road
grids through the exact recovered compositor, associates the resulting spans
with their known world-space lane/row boundaries, and reports the projection
samples used by the native world renderer.  Runtime geometry never consumes
the synthetic grid or the raster spans.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
import json
import math
from pathlib import Path
from statistics import median
import struct
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "dos_re"))

from skyroads.bridge.dgroup_view import GameView  # noqa: E402
from skyroads.levels import road_archive_index  # noqa: E402
from skyroads.native.level_load import native_level_load  # noqa: E402
from skyroads.native.state import NativeGameState  # noqa: E402
from skyroads.presentation.original_projection import trace_original_projection  # noqa: E402
from skyroads.presentation.renderer import (  # noqa: E402
    CALIBRATION,
    build_polygon_mesh,
    project_world_vertex,
    projection_scale,
)
from skyroads.presentation.scene import (  # noqa: E402
    TRACK_ROW_UNITS,
    build_gameplay_scene,
    decode_road_geometry,
)


BASE_ROW = 20
ROW_COUNT = 40


def _synthetic_scene(code: int):
    state = NativeGameState()
    native_level_load(state, road_archive_index(0), game_root=ROOT / "assets")
    view = GameView(state)
    view.lateral = BASE_ROW * TRACK_ROW_UNITS
    scene = build_gameplay_scene(view, level=0, game_root=ROOT / "assets")
    row = struct.pack("<7H", *([code] * 7))
    geometry = decode_road_geometry(
        row * ROW_COUNT, level=0, archive_index=1,
    )
    return replace(scene, geometry=geometry)


def _linear_edge(spans, field: str, y: float) -> float:
    """Least-squares edge through pixel-centre samples, evaluated at *y*."""
    samples = [(span.y + 0.5, float(getattr(span, field))) for span in spans]
    if len(samples) == 1:
        return samples[0][1]
    mean_y = sum(item[0] for item in samples) / len(samples)
    mean_x = sum(item[1] for item in samples) / len(samples)
    variance = sum((item[0] - mean_y) ** 2 for item in samples)
    slope = sum(
        (sy - mean_y) * (sx - mean_x) for sy, sx in samples
    ) / variance
    return mean_x + slope * (y - mean_y)


def recover_deck_samples() -> list[dict[str, float]]:
    scene = _synthetic_scene(0x0001)
    samples: dict[float, dict[str, list[float]]] = {}
    for phase in range(8):
        camera = BASE_ROW + phase / 8.0
        trace = trace_original_projection(replace(
            scene, track_position=BASE_ROW * TRACK_ROW_UNITS + phase * 0x2000,
        ))
        grouped = {}
        for primitive in trace.primitives:
            if primitive.role != "deck/top":
                continue
            key = (primitive.road_row, primitive.lane, primitive.pass_index)
            bucket = grouped.setdefault(key, [primitive.world_bounds, []])
            bucket[1].extend(span for span in primitive.spans if not span.clipped)
        for world_bounds, spans in grouped.values():
            if not spans:
                continue
            far_y = float(min(span.y for span in spans))
            near_y = float(max(span.y for span in spans) + 1)
            x0, _, z0, x1, _, z1 = world_bounds
            for depth, y, side_y in (
                (z1 - camera, far_y, far_y),
                (z0 - camera, near_y, near_y),
            ):
                key = round(depth, 3)
                bucket = samples.setdefault(key, {"ys": [], "scales": [], "centres": []})
                bucket["ys"].append(y)
                left = _linear_edge(spans, "x0", side_y)
                right = _linear_edge(spans, "x1", side_y)
                for world_x, screen_x in ((x0, left), (x1, right)):
                    if abs(world_x) > 0.01:
                        bucket["scales"].append((screen_x - 160.0) / world_x)
                bucket["centres"].append((left + right) * 0.5)
    result = []
    for depth, bucket in sorted(samples.items()):
        if not bucket["ys"] or not bucket["scales"]:
            continue
        result.append({
            "depth": depth,
            "ground_y": float(median(bucket["ys"])),
            # Shared interior edges dominate this median.  Outer clipping and
            # the original painter's one-sided fill convention cannot pull
            # the recovered lens away from the stable lane lattice.
            "lateral_scale": float(median(bucket["scales"])),
            "sample_count": float(len(bucket["scales"])),
        })
    return result


def recover_height_samples(code: int, role: str, height: float) -> list[dict[str, float]]:
    scene = _synthetic_scene(code)
    samples: dict[float, list[float]] = {}
    for phase in range(8):
        camera = BASE_ROW + phase / 8.0
        trace = trace_original_projection(replace(
            scene, track_position=BASE_ROW * TRACK_ROW_UNITS + phase * 0x2000,
        ))
        grouped = {}
        for primitive in trace.primitives:
            if primitive.role != role:
                continue
            key = (primitive.road_row, primitive.lane, primitive.pass_index)
            bucket = grouped.setdefault(key, [primitive.world_bounds, []])
            bucket[1].extend(span for span in primitive.spans if not span.clipped)
        for world_bounds, spans in grouped.values():
            if not spans:
                continue
            _, _, z0, _, _, z1 = world_bounds
            for depth, y in (
                (z1 - camera, float(min(span.y for span in spans))),
                (z0 - camera, float(max(span.y for span in spans) + 1)),
            ):
                samples.setdefault(round(depth, 3), []).append(y)
    deck = {item["depth"]: item["ground_y"] for item in recover_deck_samples()}
    result = []
    for depth, values in sorted(samples.items()):
        if depth not in deck:
            continue
        top_y = float(median(values))
        result.append({
            "depth": depth,
            "top_y": top_y,
            "vertical_scale": (deck[depth] - top_y) / height,
        })
    return result


def _stability_report() -> dict:
    scene = _synthetic_scene(0x0101)
    meshes = []
    for step in range(8):
        meshes.append(build_polygon_mesh(replace(
            scene,
            track_position=BASE_ROW * TRACK_ROW_UNITS + step * 0x1000,
        )).digest)
    trajectory = [
        project_world_vertex(
            -2.5, 0.0, BASE_ROW + 4.0,
            BASE_ROW + step / 64.0,
        )
        for step in range(65)
    ]
    steps = [
        math.hypot(right[0] - left[0], right[1] - left[1])
        for left, right in zip(trajectory, trajectory[1:])
    ]
    accelerations = [
        abs(right - left) for left, right in zip(steps, steps[1:])
    ]
    return {
        "subrow_samples": 65,
        "unique_projected_positions": len(set(trajectory)),
        "subrow_mesh_identity_count": len(set(meshes)),
        "monotonic_x": all(
            right[0] < left[0]
            for left, right in zip(trajectory, trajectory[1:])
        ),
        "monotonic_y": all(
            right[1] > left[1]
            for left, right in zip(trajectory, trajectory[1:])
        ),
        "minimum_step_pixels_320x200": min(steps),
        "maximum_step_pixels_320x200": max(steps),
        "maximum_step_delta_pixels_320x200": max(accelerations),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    deck = recover_deck_samples()
    usable = [item for item in deck if 0.0 <= item["depth"] <= 7.0]
    scale_errors = [
        projection_scale(item["depth"]) - item["lateral_scale"]
        for item in usable
    ]
    ground_errors = [
        CALIBRATION.horizon_y
        + CALIBRATION.camera_height * projection_scale(item["depth"])
        - item["ground_y"]
        for item in usable
    ]
    report = {
        "schema": "skyroads:projection-calibration-evidence/v1",
        "source": "synthetic stable grid through recovered TREKDAT/2D1F",
        "stability": _stability_report(),
        "lens": {
            "scale": "gain * max(vanishing_depth-depth, 0) / (depth+near_bias)",
            "ground_y": "horizon_y + camera_height * scale",
            "gain": CALIBRATION.lens_gain,
            "near_bias": CALIBRATION.near_bias,
            "vanishing_depth": CALIBRATION.vanishing_depth,
            "horizon_y": CALIBRATION.horizon_y,
            "camera_height": CALIBRATION.camera_height,
            "scale_rms_pixels": math.sqrt(sum(
                value * value for value in scale_errors
            ) / len(scale_errors)),
            "ground_rms_pixels": math.sqrt(sum(
                value * value for value in ground_errors
            ) / len(ground_errors)),
        },
        "deck": deck,
        "half_height": recover_height_samples(0x0201, "block/top", 10 / 23),
        "full_height": recover_height_samples(0x0401, "raised/top", 20 / 23),
    }
    if args.summary:
        report.pop("deck")
        report.pop("half_height")
        report.pop("full_height")
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
