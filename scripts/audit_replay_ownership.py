"""Audit scene ownership and execution crossings in a SkyRoads replay.

This is an offline diagnostic over the immutable replay input stream.  It
counts selected implementation dispatches, native-region handoffs, renderer
ownership changes, instructions, and latency by semantic scene phase.  It does
not mutate the replay or turn profiling output into verification evidence.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
import json
from pathlib import Path
import statistics
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "dos_re")]

from dos_re.dos import ConsoleInputWouldBlock  # noqa: E402
from dos_re.replay import ReplayArtifact, ReplayPoint  # noqa: E402
from dos_re.replay_input import RealModeInputAdapter  # noqa: E402
from scripts.profile_replay_latency import _runtime  # noqa: E402
from skyroads.bridge.dgroup_view import GameView  # noqa: E402
from skyroads.execution import (  # noqa: E402
    provider_diagnostics,
    selected_whole_program_provider,
)


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[round((len(ordered) - 1) * fraction)]


@dataclass
class _HookCounter:
    runtime: object
    current_point: int = 0

    def __post_init__(self) -> None:
        self.total: Counter[str] = Counter()
        self.by_point: dict[int, Counter[str]] = defaultdict(Counter)
        self.by_context: Counter[str] = Counter()
        self.names_by_context: dict[str, Counter[str]] = defaultdict(Counter)
        self.context_by_point: dict[int, Counter[str]] = defaultdict(Counter)

    def record(self, name: str) -> None:
        self.total[name] += 1
        self.by_point[self.current_point][name] += 1
        dispatcher = getattr(self.runtime, "execution_regions", None)
        context = (
            "region-active"
            if dispatcher is not None and dispatcher.active
            else "generated-carrier"
        )
        self.by_context[context] += 1
        self.names_by_context[context][name] += 1
        self.context_by_point[self.current_point][context] += 1


class _CountedHook:
    """Transparent diagnostic wrapper for one already-selected CPU hook."""

    def __init__(self, handler, name: str, counter: _HookCounter) -> None:
        self.handler = handler
        self.name = name
        self.counter = counter
        self.owns_time = bool(getattr(handler, "owns_time", False))

    def __call__(self, cpu):
        self.counter.record(self.name)
        return self.handler(cpu)


def _install_hook_counter(runtime) -> _HookCounter:
    counter = _HookCounter(runtime)
    hooks = runtime.cpu.replacement_hooks
    names = runtime.cpu.hook_names
    for key, handler in tuple(hooks.items()):
        if isinstance(handler, _CountedHook):
            continue
        hooks[key] = _CountedHook(
            handler, str(names.get(key, f"{key[0]:04X}:{key[1]:04X}")),
            counter,
        )
    return counter


def _arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("replay", type=Path)
    parser.add_argument(
        "--composition", default="faithful-product",
        choices=("auto", "faithful-product", "workbench-auto", "oracle"),
    )
    parser.add_argument(
        "--renderer", default="native-3d",
        choices=("original", "native-3d"),
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--top", type=int, default=20)
    return parser.parse_args(argv)


def _execution_owner(runtime, carrier: str) -> str:
    dispatcher = getattr(runtime, "execution_regions", None)
    active = None if dispatcher is None else dispatcher.active_region_id
    if active:
        return f"authored-region:{active}"
    if carrier == "baseline:interpreted-exe":
        return "interpreted-oracle"
    return "generated-carrier"


def _range_summary(records: list[dict]) -> list[dict]:
    ranges = []
    current = None
    for item in records:
        key = (
            item["execution_owner"],
            item["presentation_owner"],
            item["phase"],
            item["level"],
        )
        if current is None or current["key"] != key:
            if current is not None:
                ranges.append(current)
            current = {
                "key": key,
                "start": item["point"],
                "end": item["point"],
                "points": 0,
                "instructions": 0,
                "hook_calls": 0,
                "times": [],
            }
        current["end"] = item["point"]
        current["points"] += 1
        current["instructions"] += item["instructions"]
        current["hook_calls"] += item["hook_calls"]
        current["times"].append(item["total_ms"])
    if current is not None:
        ranges.append(current)
    result = []
    for item in ranges:
        execution, presentation, phase, level = item.pop("key")
        times = item.pop("times")
        result.append({
            **item,
            "execution_owner": execution,
            "presentation_owner": presentation,
            "phase": phase,
            "level": level,
            "median_ms": statistics.median(times),
            "p95_ms": _percentile(times, .95),
            "max_ms": max(times),
        })
    return result


def _phase_summary(records: list[dict]) -> list[dict]:
    groups: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for item in records:
        groups[
            item["execution_owner"],
            item["presentation_owner"],
            item["phase"],
        ].append(item)
    result = []
    for (execution, presentation, phase), items in groups.items():
        times = [item["total_ms"] for item in items]
        result.append({
            "execution_owner": execution,
            "presentation_owner": presentation,
            "phase": phase,
            "points": len(items),
            "instructions": sum(item["instructions"] for item in items),
            "hook_calls": sum(item["hook_calls"] for item in items),
            "median_ms": statistics.median(times),
            "p95_ms": _percentile(times, .95),
            "max_ms": max(times),
        })
    return sorted(result, key=lambda item: item["points"], reverse=True)


def _audit(options) -> dict:
    artifact = ReplayArtifact.open(options.replay.resolve())
    frontend, args, runtime, driver = _runtime(
        artifact, options.composition, options.renderer,
    )
    carrier = selected_whole_program_provider(args.execution_plan)
    provider = provider_diagnostics(args.execution_plan, runtime)
    input_adapter = RealModeInputAdapter(artifact.events)
    input_adapter.seek(driver.input.event_cursor)
    presentation = runtime._skyroads_presentation
    hooks = _install_hook_counter(runtime)
    records = []
    previous_execution = _execution_owner(runtime, carrier)
    previous_presentation = "native" if presentation._owns_gameplay else "original"
    execution_handoffs = []
    presentation_handoffs = []

    for ordinal in range(artifact.end_point.ordinal):
        point = ReplayPoint(ordinal + 1, artifact.timeline_id)
        hooks.current_point = point.ordinal
        instruction_start = int(runtime.cpu.instruction_count)
        hook_start = sum(hooks.total.values())
        started = time.perf_counter_ns()
        input_adapter.apply_to_runtime(
            ordinal,
            runtime,
            deliver=lambda current, scancode: frontend.deliver_input(
                current, scancode,
            ),
        )
        coordinate = artifact.timeline_coordinate(point)
        try:
            frontend.advance_replay_frame(runtime, args, ordinal, coordinate)
        except ConsoleInputWouldBlock:
            pass
        driver.input.seek(input_adapter.event_cursor)
        driver._point = point
        presentation.frame(lambda: None, interpolation=0.5)
        elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000.0

        execution = _execution_owner(runtime, carrier)
        visual = "native" if presentation._owns_gameplay else "original"
        if execution != previous_execution:
            execution_handoffs.append({
                "point": point.ordinal,
                "from": previous_execution,
                "to": execution,
            })
            previous_execution = execution
        if visual != previous_presentation:
            presentation_handoffs.append({
                "point": point.ordinal,
                "from": previous_presentation,
                "to": visual,
                "phase": presentation._ownership_phase,
            })
            previous_presentation = visual

        cpu = runtime.cpu
        view = GameView(cpu.mem.data, base=(int(cpu.s.ds) & 0xFFFF) << 4)
        point_hooks = hooks.by_point.get(point.ordinal, Counter())
        records.append({
            "point": point.ordinal,
            "machine": f"{int(cpu.s.cs):04X}:{int(cpu.s.ip):04X}",
            "level": int(view.rw(0x9332)),
            "phase": str(presentation._ownership_phase),
            "execution_owner": execution,
            "presentation_owner": visual,
            "boundary": str(
                getattr(runtime, "_skyroads_replay_boundary_kind", "-")
            ),
            "instructions": int(cpu.instruction_count) - instruction_start,
            "hook_calls": sum(hooks.total.values()) - hook_start,
            "total_ms": elapsed_ms,
            "hooks": dict(point_hooks),
            "hook_contexts": dict(
                hooks.context_by_point.get(point.ordinal, Counter())
            ),
        })

    top_hooks = [
        {"name": name, "calls": count}
        for name, count in hooks.total.most_common(max(1, options.top))
    ]
    slowest = sorted(
        records, key=lambda item: item["total_ms"], reverse=True,
    )[:max(1, options.top)]
    return {
        "replay": str(artifact.directory),
        "points": artifact.end_point.ordinal,
        "events": len(artifact.events),
        "composition": options.composition,
        "carrier": carrier,
        "plan_id": args.execution_plan.plan_digest,
        "provider": {
            "frontend": provider.frontend_provider,
            "level_selection": provider.level_selection_provider,
            "gameplay": provider.gameplay_provider,
            "renderer": provider.renderer_provider,
            "collapsed_internal_boundaries":
                provider.collapsed_internal_boundaries,
            "remaining_external_seams": provider.remaining_external_seams,
            "generated_fallbacks": provider.selected_generated_fallbacks,
            "interpreted_fallbacks": provider.selected_interpreted_fallbacks,
        },
        "execution_handoffs": execution_handoffs,
        "presentation_handoffs": presentation_handoffs,
        "ranges": _range_summary(records),
        "phases": _phase_summary(records),
        "top_hooks": top_hooks,
        "hook_contexts": dict(hooks.by_context),
        "hooks_by_context": {
            context: [
                {"name": name, "calls": count}
                for name, count in counts.most_common(max(1, options.top))
            ]
            for context, counts in hooks.names_by_context.items()
        },
        "slowest": slowest,
        "totals": {
            "instructions": sum(item["instructions"] for item in records),
            "hook_calls": sum(item["hook_calls"] for item in records),
            "median_ms": statistics.median(
                item["total_ms"] for item in records
            ),
            "p95_ms": _percentile(
                [item["total_ms"] for item in records], .95,
            ),
            "p99_ms": _percentile(
                [item["total_ms"] for item in records], .99,
            ),
            "max_ms": max(item["total_ms"] for item in records),
        },
    }


def _print(report: dict) -> None:
    totals = report["totals"]
    provider = report["provider"]
    print(
        f"replay: {report['points']} points, {report['events']} events; "
        f"composition={report['composition']} carrier={report['carrier']} "
        f"plan={report['plan_id'][:12]}"
    )
    print(
        f"total: median={totals['median_ms']:.3f}ms "
        f"p95={totals['p95_ms']:.3f}ms p99={totals['p99_ms']:.3f}ms "
        f"max={totals['max_ms']:.3f}ms instructions={totals['instructions']:,} "
        f"selected-dispatches={totals['hook_calls']:,}"
    )
    print(
        "providers: "
        f"selector={provider['level_selection']} "
        f"gameplay={provider['gameplay']} renderer={provider['renderer']} "
        f"generated-fallbacks={provider['generated_fallbacks']} "
        f"interpreted-fallbacks={provider['interpreted_fallbacks']} "
        f"collapsed={provider['collapsed_internal_boundaries']}"
    )
    print(
        "selected-dispatch contexts: "
        + ", ".join(
            f"{name}={count:,}"
            for name, count in sorted(report["hook_contexts"].items())
        )
    )

    print("\nscene ownership timeline:")
    for item in report["ranges"]:
        print(
            f"{item['start']:4d}..{item['end']:<4d} "
            f"L{item['level']:<2d} {item['phase']:<23s} "
            f"exec={item['execution_owner']:<36s} "
            f"render={item['presentation_owner']:<8s} "
            f"points={item['points']:3d} ins={item['instructions']:9,d} "
            f"hooks={item['hook_calls']:5,d} "
            f"median={item['median_ms']:7.3f}ms "
            f"p95={item['p95_ms']:7.3f} max={item['max_ms']:7.3f}"
        )

    print("\nexecution ownership handoffs:")
    for item in report["execution_handoffs"]:
        print(f"{item['point']:4d} {item['from']} -> {item['to']}")
    print("\npresentation ownership handoffs:")
    for item in report["presentation_handoffs"]:
        print(
            f"{item['point']:4d} {item['from']} -> {item['to']} "
            f"({item['phase']})"
        )

    print("\nhottest selected implementation boundaries:")
    for item in report["top_hooks"]:
        print(f"{item['calls']:8,d}  {item['name']}")
    for context, items in sorted(report["hooks_by_context"].items()):
        print(f"\n{context} boundaries:")
        for item in items[:10]:
            print(f"{item['calls']:8,d}  {item['name']}")

    print("\nslowest points:")
    for item in report["slowest"]:
        hot = sorted(
            item["hooks"].items(), key=lambda pair: pair[1], reverse=True,
        )[:3]
        print(
            f"{item['point']:4d} {item['total_ms']:8.3f}ms "
            f"ins={item['instructions']:8,d} hooks={item['hook_calls']:4,d} "
            f"{item['execution_owner']} / {item['presentation_owner']}:"
            f"{item['phase']} at {item['machine']} "
            f"top={','.join(f'{name}={count}' for name, count in hot) or '-'}"
        )


def main(argv=None) -> int:
    options = _arguments(argv)
    report = _audit(options)
    if options.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        _print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
