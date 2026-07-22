"""Profile long-tail SkyRoads replay latency by semantic point.

This is an offline CPU-side profiler.  It never mutates the source artifact and
does not claim to time the GPU/display driver.  It separates immutable replay
bookkeeping, selected execution, and native presentation preparation, then
prints the slowest semantic intervals with recovered gameplay state.
"""
from __future__ import annotations

import argparse
import cProfile
import io
from pathlib import Path
import pstats
import statistics
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "dos_re")]

from dos_re import player  # noqa: E402
from dos_re.dos import ConsoleInputWouldBlock  # noqa: E402
from dos_re.replay import ReplayArtifact, ReplayPoint  # noqa: E402
from dos_re.replay_input import RealModeInputAdapter  # noqa: E402
from scripts.play import SkyroadsFrontend  # noqa: E402
from skyroads.bridge.dgroup_view import GameView  # noqa: E402
from skyroads.execution import (  # noqa: E402
    GENERATED_VMLESS_CARRIER,
    selected_whole_program_provider,
)
from skyroads.replay import (  # noqa: E402
    SkyroadsReplayDriver,
    capture_base,
    capture_profile,
)
from skyroads.vmless_backend import create_planned_runtime  # noqa: E402


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[round((len(ordered) - 1) * fraction)]


def _arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("replay", type=Path)
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--start", type=int, default=1)
    parser.add_argument("--end", type=int)
    parser.add_argument(
        "--composition", default="faithful-product",
        choices=("faithful-product", "workbench-auto", "oracle"),
    )
    parser.add_argument(
        "--cprofile", action="store_true",
        help="also print the hottest Python call stacks over the interval",
    )
    return parser.parse_args(argv)


def _runtime(artifact: ReplayArtifact, composition: str):
    frontend = SkyroadsFrontend(ROOT)
    args = player.build_arg_parser(frontend).parse_args([
        "--profile", "development",
        "--composition", composition,
        "--play-replay", str(artifact.directory),
        "--headless",
        "--renderer", "native-3d",
        "--widescreen",
        "--tweening",
        "--audio", "native-stereo",
    ])
    frontend.apply_replay_metadata(args, artifact.metadata)
    args.execution_plan = frontend.resolve_execution_plan(args)
    provider = selected_whole_program_provider(args.execution_plan)
    if provider == "baseline:generated-vmless":
        runtime, _manifest = create_planned_runtime(
            args,
            bootstrap_artifacts=args.execution_plan.bootstrap_artifact_paths(),
            bind_plan=lambda current: frontend.bind_execution_plan(
                current,
                args.execution_plan,
                carrier_id=GENERATED_VMLESS_CARRIER,
            ),
        )
    elif provider == "baseline:interpreted-exe":
        runtime = frontend.create_runtime(args)
        frontend.bind_execution_plan(runtime, args.execution_plan)
    else:
        raise RuntimeError(
            f"latency profiler requires a DOS-memory carrier, got {provider!r}"
        )
    requested = frontend.replay_profile(args, runtime)
    source_profile = capture_profile(artifact)
    source_state = capture_base(artifact)
    if requested == source_profile:
        base = source_state
    else:
        base = frontend.materialize_replay_profile_base(
            args,
            runtime,
            artifact,
            source_profile=source_profile,
            requested_profile=requested,
            source_state=source_state,
        )
    driver = SkyroadsReplayDriver(
        frontend, args, runtime, artifact, requested,
    )
    origin = ReplayPoint(0, artifact.timeline_id)
    driver.restore(base, origin)
    return frontend, args, runtime, driver


def main(argv=None) -> int:
    options = _arguments(argv)
    artifact = ReplayArtifact.open(options.replay.resolve())
    frontend, args, runtime, driver = _runtime(
        artifact, options.composition,
    )
    end = artifact.end_point.ordinal
    if options.end is not None:
        end = min(end, int(options.end))
    if not 0 < end <= artifact.end_point.ordinal:
        raise SystemExit("--end must select at least one replay point")
    start = max(1, int(options.start))
    if start > end:
        raise SystemExit("--start must not be after --end")

    input_adapter = RealModeInputAdapter(artifact.events)
    input_adapter.seek(driver.input.event_cursor)
    presentation = runtime._skyroads_presentation
    profiler = cProfile.Profile() if options.cprofile else None
    records = []
    last_mesh = None
    last_palette = None
    last_opl = 0
    opl_count = [0]

    def count_opl(_register, _value) -> None:
        opl_count[0] += 1

    runtime.dos.set_adlib_callback(count_opl, emit_current=False)

    for ordinal in range(end):
        point = ReplayPoint(ordinal + 1, artifact.timeline_id)
        recording = point.ordinal >= start
        if profiler is not None and point.ordinal == start:
            profiler.enable()
        instructions = int(runtime.cpu.instruction_count)
        event_cursor = input_adapter.event_cursor

        started = time.perf_counter_ns()
        input_adapter.apply_to_runtime(
            ordinal,
            runtime,
            deliver=lambda current, scancode: frontend.deliver_input(
                current, scancode,
            ),
        )
        input_ns = time.perf_counter_ns() - started

        coordinate = artifact.timeline_coordinate(point)
        started = time.perf_counter_ns()
        try:
            frontend.advance_replay_frame(
                runtime, args, ordinal, coordinate,
            )
        except ConsoleInputWouldBlock:
            pass
        execution_ns = time.perf_counter_ns() - started

        # Keep the driver's cursor/point coherent without replaying the work a
        # second time.  These assignments mirror SkyroadsReplayDriver's public
        # sequential contract solely for later state capture/diagnostics.
        driver.input.seek(input_adapter.event_cursor)
        driver._point = point

        started = time.perf_counter_ns()
        presentation.frame(lambda: None, interpolation=0.5)
        presentation_ns = time.perf_counter_ns() - started

        cpu = runtime.cpu
        view = GameView(cpu.mem.data, base=(cpu.s.ds & 0xFFFF) << 4)
        packet = presentation.polygon_frame
        mesh = None if packet is None else packet.mesh.digest
        shadow_alpha = (
            () if packet is None else packet.shadow_rgba[3::4]
        )
        palette = tuple(runtime.dos.vga_palette)
        opl = opl_count[0]
        dispatcher = getattr(runtime, "execution_regions", None)
        record = {
            "point": ordinal + 1,
            "input_ms": input_ns / 1_000_000.0,
            "execution_ms": execution_ns / 1_000_000.0,
            "presentation_ms": presentation_ns / 1_000_000.0,
            "total_ms": (input_ns + execution_ns + presentation_ns) / 1_000_000.0,
            "instructions": int(cpu.instruction_count) - instructions,
            "events": input_adapter.event_cursor - event_cursor,
            "machine": f"{int(cpu.s.cs):04X}:{int(cpu.s.ip):04X}",
            "boundary": str(getattr(runtime, "_skyroads_replay_boundary_kind", "-")),
            "region": (
                None if dispatcher is None else dispatcher.active_region_id
            ),
            "game_state": int(view.game_state),
            "track_row": int(view.lateral) >> 16,
            "height": int(view.af2c),
            "shadow_band": int(view.rw(0x0E34)) // 5,
            "shadow_offset": int(view.rw(0x0E70)),
            "shadow_pixels": sum(1 for alpha in shadow_alpha if alpha),
            "shadow_alpha": max(shadow_alpha, default=0),
            "mesh_changed": mesh != last_mesh,
            "palette_changed": palette != last_palette,
            "opl_writes": opl - last_opl,
        }
        if recording:
            records.append(record)
        last_mesh = mesh
        last_palette = palette
        last_opl = opl
    if profiler is not None:
        profiler.disable()

    for field in ("total_ms", "execution_ms", "presentation_ms", "input_ms"):
        values = [float(record[field]) for record in records]
        print(
            f"{field[:-3]}: median={statistics.median(values):.3f}ms "
            f"p95={_percentile(values, .95):.3f}ms "
            f"p99={_percentile(values, .99):.3f}ms "
            f"max={max(values):.3f}ms"
        )

    print("\nslowest semantic points:")
    for item in sorted(
        records, key=lambda record: record["total_ms"], reverse=True,
    )[:max(1, options.top)]:
        print(
            f"{item['point']:4d} total={item['total_ms']:8.3f}ms "
            f"exec={item['execution_ms']:8.3f} prep={item['presentation_ms']:7.3f} "
            f"input={item['input_ms']:6.3f} ins={item['instructions']:8d} "
            f"events={item['events']} opl={item['opl_writes']:3d} "
            f"state={item['game_state']} row={item['track_row']:3d} "
            f"height={item['height']:04X} shadow={item['shadow_band']}:"
            f"{item['shadow_offset']:04X}/{item['shadow_pixels']}px@"
            f"{item['shadow_alpha']} mesh={'new' if item['mesh_changed'] else 'same'} "
            f"palette={'new' if item['palette_changed'] else 'same'} "
            f"at={item['machine']} boundary={item['boundary']}"
        )

    if profiler is not None:
        output = io.StringIO()
        pstats.Stats(profiler, stream=output).strip_dirs().sort_stats(
            "cumulative",
        ).print_stats(35)
        print("\nPython cumulative profile:")
        print(output.getvalue())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
