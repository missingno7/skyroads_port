"""One-shot stable-coordinate materializer for a coordinate-less replay.

This is deliberately not a runtime compatibility path. It replays the
preserved capture composition once using its original dispatch-boundary clock
and records the resulting guest-instruction coordinate for every ordinal.
Normal playback and verification then consume those explicit coordinates.

Usage:
    python scripts/materialize_replay_timeline.py artifacts/replays/REPLAY
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "dos_re"))

from dos_re import player  # noqa: E402
from dos_re.dos import ConsoleInputWouldBlock  # noqa: E402
from dos_re.replay import (  # noqa: E402
    GUEST_INSTRUCTION_COORDINATE,
    ReplayArtifact,
    ReplayPoint,
    ReplayPointCoordinate,
)
from dos_re.replay_input import RealModeInputAdapter  # noqa: E402
from dos_re.snapshot import apply_runtime_continuation  # noqa: E402
from scripts.play import SkyroadsFrontend  # noqa: E402
from skyroads.replay import capture_base, capture_profile  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("replay", type=Path)
    args = parser.parse_args(argv)
    artifact = ReplayArtifact.open(args.replay.resolve())
    if artifact.timeline_coordinates:
        print(
            f"{artifact.directory}: already has "
            f"{len(artifact.timeline_coordinates)} stable coordinates")
        return 0

    frontend = SkyroadsFrontend(ROOT)
    composition = str(artifact.metadata.get("capture_composition", ""))
    if not composition:
        # The retained oracle corpus predates composition metadata, but its
        # registered capture role is unambiguous. Candidate captures must name
        # their exact composition; guessing there would recreate the old
        # parallel-authority problem this one-shot converter removes.
        if capture_profile(artifact).role == "oracle":
            composition = "oracle"
        else:
            raise RuntimeError(
                "coordinate-less candidate replay has no capture composition "
                "metadata")
    launch_args = player.build_arg_parser(frontend).parse_args([
        "--profile", "development",
        "--composition", composition,
        "--play-replay", str(artifact.directory),
    ])
    frontend.apply_replay_metadata(launch_args, artifact.metadata)
    launch_args.execution_plan = frontend.resolve_execution_plan(launch_args)
    runtime = frontend.create_runtime(launch_args)
    apply_runtime_continuation(runtime, capture_base(artifact))
    frontend.bind_execution_plan(runtime, launch_args.execution_plan)
    profile = frontend.replay_profile(launch_args, runtime)
    inputs = RealModeInputAdapter(artifact.events)
    end = ReplayPoint.from_json(artifact.metadata["end_point"]).ordinal
    coordinates = [ReplayPointCoordinate(
        ReplayPoint(0, artifact.timeline_id),
        GUEST_INSTRUCTION_COORDINATE,
        int(runtime.cpu.instruction_count),
    )]
    for ordinal in range(end):
        inputs.apply_to_runtime(
            ordinal,
            runtime,
            deliver=lambda rt, scancode: frontend.deliver_input(rt, scancode),
        )
        try:
            # This is the one explicit reconstruction of the old capture
            # clock. Normal replay never uses dispatch-count boundaries.
            frontend.advance_frame(runtime, launch_args, ordinal)
        except ConsoleInputWouldBlock:
            pass
        coordinates.append(ReplayPointCoordinate(
            ReplayPoint(ordinal + 1, artifact.timeline_id),
            GUEST_INSTRUCTION_COORDINATE,
            int(runtime.cpu.instruction_count),
        ))
        if (ordinal + 1) % 250 == 0:
            print(f"materialized {ordinal + 1}/{end}")
    artifact.set_timeline_coordinates(
        coordinates,
        provenance={
            "kind": "one-shot-capture-clock-materialization",
            "profile_identity_digest": profile.identity_digest,
            "event_stream_sha256": artifact.event_stream_sha256,
            "composition": composition,
            "end_ordinal": end,
        },
    )
    print(
        f"{artifact.directory}: materialized {len(coordinates)} "
        f"{GUEST_INSTRUCTION_COORDINATE} coordinates")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
