"""Exercise every direct-launch level through the planned native gameplay island.

This is an integration probe, not an oracle promotion command. It uses a
recorded menu-prefix only to reach the generated selector, replaces that first
selection with each requested level through ``--level``, and requires the same
long-lived faithful region to become active. Record/verify gameplay trajectories
separately before adding them to the trusted corpus.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "dos_re"))

from dos_re import player  # noqa: E402
from dos_re.dos import ConsoleInputWouldBlock  # noqa: E402
from dos_re.replay import ReplayArtifact  # noqa: E402
from dos_re.replay_input import RealModeInputAdapter  # noqa: E402
from dos_re.x86 import HaltExecution  # noqa: E402
from scripts.play import SkyroadsFrontend  # noqa: E402
from skyroads.execution import (  # noqa: E402
    GENERATED_VMLESS_CARRIER,
    INTERPRETED_CPU_CARRIER,
)
from skyroads.launch_inputs import LEVEL_COUNT  # noqa: E402
from skyroads.replay import capture_base, capture_profile  # noqa: E402
from skyroads.vmless_backend import create_planned_runtime  # noqa: E402


DEFAULT_REPLAY = ROOT / "artifacts" / "replays" / "replay_candidate_smoke_20260720_214152"


def _enter_level(frontend, artifact: ReplayArtifact, level: int, max_frames: int) -> None:
    args = player.build_arg_parser(frontend).parse_args((
        "--headless", "--composition", frontend._gameplay_probe_composition,
        "--level", str(level),
    ))
    args.execution_plan = frontend.resolve_execution_plan(args)
    if frontend._gameplay_probe_composition == "faithful-product":
        runtime, _manifest = create_planned_runtime(
            args,
            bootstrap_artifacts=args.execution_plan.bootstrap_artifact_paths(),
            bind_plan=lambda current: frontend.bind_execution_plan(
                current, args.execution_plan,
                carrier_id=GENERATED_VMLESS_CARRIER,
            ),
        )
    else:
        runtime = frontend.create_runtime(args)
        frontend.bind_execution_plan(
            runtime, args.execution_plan, carrier_id=INTERPRETED_CPU_CARRIER,
        )
    # A recording starts from its own complete continuation, not necessarily
    # from the generated carrier's post-unpack boot image.  In particular, the
    # smoke corpus captures point zero at the original packer entry.  Restore
    # the profile-projected base before applying point-zero inputs; otherwise a
    # "level probe" would execute a different startup path than the artifact
    # it claims to exercise.
    source_profile = capture_profile(artifact)
    source_base = capture_base(artifact)
    candidate_profile = frontend.replay_profile(args, runtime)
    projected_base = frontend.materialize_replay_profile_base(
        args, runtime, artifact,
        source_profile=source_profile,
        requested_profile=candidate_profile,
        source_state=source_base,
    )
    base_point = artifact.cached_points(source_profile)[0]
    frontend.apply_replay_state(runtime, projected_base)
    runtime.dos.console_input_fallback = None
    runtime.dos.mouse_present = bool(artifact.metadata.get("mouse_present", False))
    inputs = RealModeInputAdapter(artifact.events)
    inputs.seek(projected_base.event_cursor)
    started_menu = False
    for frame in range(base_point.ordinal, max_frames):
        if frame < artifact.end_point.ordinal:
            inputs.apply_to_runtime(
                frame, runtime,
                deliver=lambda current, scancode: frontend.deliver_input(
                    current, scancode),
            )
        try:
            frontend.advance_frame(runtime, args, frame)
        except ConsoleInputWouldBlock:
            # ``--level`` deliberately adapts only the original level
            # selector.  Reach that selector through the game's own main-menu
            # action once, then the adapter supplies the requested level.
            if not started_menu:
                frontend.deliver_input(runtime, 0x1C)  # Enter: default Start
                started_menu = True
        except HaltExecution as exc:
            state = runtime.cpu.s
            raise RuntimeError(
                f"level {level}: generated shell halted before native gameplay "
                f"at replay point {frame}; direct-level-applied="
                f"{getattr(runtime, '_skyroads_direct_level_applied', None)!r}; "
                f"machine={state.cs:04X}:{state.ip:04X}"
            ) from exc
        if getattr(runtime, "_skyroads_gameplay_entries", 0):
            break
    dispatcher = getattr(runtime, "execution_regions", None)
    active = dispatcher is not None and dispatcher.active
    actual = getattr(runtime, "_skyroads_gameplay_level", None)
    if not active or actual != level:
        state = runtime.cpu.s
        raise RuntimeError(
            f"level {level}: island did not enter requested level; active={active} "
            f"actual={actual} machine={state.cs:04X}:{state.ip:04X}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--replay", default=str(DEFAULT_REPLAY))
    parser.add_argument("--max-frames", type=int, default=900)
    parser.add_argument(
        "--composition", choices=("workbench-auto", "faithful-product"),
        default="workbench-auto",
        help=("workbench-auto exercises the native island over the original "
              "frontend; faithful-product additionally stress-tests generated shell coverage"),
    )
    parser.add_argument("--level", type=int, action="append",
                        help="probe only this level (repeatable)")
    args = parser.parse_args(argv)
    artifact = ReplayArtifact.open(args.replay)
    frontend = SkyroadsFrontend(ROOT)
    frontend._gameplay_probe_composition = args.composition
    levels = tuple(args.level) if args.level else tuple(range(LEVEL_COUNT))
    for level in levels:
        _enter_level(frontend, artifact, int(level), int(args.max_frames))
        print(f"PASS level {level}: generated selector -> native gameplay island")
    print(f"PASS {len(levels)} direct-launch level(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
