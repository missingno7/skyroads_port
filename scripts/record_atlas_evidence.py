"""Create a compact oracle ReplayArtifact with actual Atlas transfer evidence.

This one-shot pilot converts a deterministic event/base recording into a fresh
oracle-owned dos_re 3.0 artifact. The source recording is read only; no legacy
runtime path is retained.

Usage:
    python scripts/record_atlas_evidence.py \
      --source-replay artifacts/replays/REPLAY --frames 12
"""
from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "dos_re"))

from dos_re import player  # noqa: E402
from dos_re.replay_input import (  # noqa: E402
    MOUSE_CHANNEL,
    SCAN_CHANNEL,
    RealModeInputAdapter,
    scan_payload,
)
from dos_re.replay import ReplayArtifact, ReplayEvent, ReplayRecording  # noqa: E402
from dos_re.snapshot import (  # noqa: E402
    apply_runtime_continuation,
    capture_runtime_continuation,
)
from scripts.play import SkyroadsFrontend  # noqa: E402
from skyroads.atlas_evidence import OracleAtlasObserver  # noqa: E402

DEFAULT_OUTPUT = ROOT / "recovery" / "replays" / "oracle_atlas_smoke"
IR = ROOT / "recovery" / "recovery_ir.json"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source-replay", required=True, type=Path)
    parser.add_argument("--frames", type=int, default=12)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args(argv)
    if args.frames <= 0:
        parser.error("--frames must be positive")
    source = ReplayArtifact.open(args.source_replay.resolve())
    source_profiles = source.profiles()
    if len(source_profiles) != 1:
        parser.error("pilot source replay must have one unambiguous base profile")
    source_profile = source_profiles[0][0]
    base_point = source.cached_points(source_profile)[0]
    base = source.restore(source_profile, base_point)

    frontend = SkyroadsFrontend(ROOT)
    launch_args = player.build_arg_parser(frontend).parse_args([
        "--headless", "--composition", "oracle",
        "--steps-per-frame", str(source.metadata["steps_per_frame"]),
        "--timer-irqs-per-frame", str(source.metadata["timer_irqs_per_frame"]),
    ])
    runtime = frontend.create_runtime(launch_args)
    apply_runtime_continuation(runtime, base)
    launch_args.execution_plan = frontend.resolve_execution_plan(launch_args)
    frontend.bind_execution_plan(runtime, launch_args.execution_plan)
    # The source recording may predate replay consolidation and carry the
    # recording machine's interactive save path. Oracle evidence is hermetic:
    # file writes remain in continuation state and never name or touch a host
    # persistence directory.
    runtime.dos.save_dir = None
    profile = frontend.replay_profile(launch_args, runtime)
    events = []
    for event in source.events:
        if event.point.ordinal >= args.frames:
            continue
        # Explicit one-shot conversion of the pre-consolidation pilot. Normal
        # runtime paths accept only the authoritative adapter channel names.
        channel, payload = event.channel, event.payload
        if channel == "scan":
            channel, payload = SCAN_CHANNEL, scan_payload(payload["value"])
        elif channel == "mouse":
            channel = MOUSE_CHANNEL
        events.append(ReplayEvent(
            event.point, len(events), channel, payload))
    events = tuple(events)
    inputs = RealModeInputAdapter(events)
    base_state = capture_runtime_continuation(runtime, event_cursor=0)

    output = args.output.resolve()
    if output.exists():
        if not args.replace:
            parser.error(f"output already exists: {output}; pass --replace")
        shutil.rmtree(output)
    recording = ReplayRecording(
        output, timeline_id=source.timeline_id, profile=profile,
        base_state=base_state,
        metadata={
            "artifact_kind": "oracle-verifiable-replay",
            "game": "skyroads",
            "purpose": "execution-atlas-oracle-evidence",
            "mouse_present": bool(source.metadata["mouse_present"]),
            "source_event_stream_sha256": source.event_stream_sha256,
            "steps_per_frame": launch_args.steps_per_frame,
            "timer_irqs_per_frame": launch_args.timer_irqs_per_frame,
        },
    )
    for event in events:
        recording.add(event.point.ordinal, event.channel, event.payload)

    frame = {"ordinal": 0}
    observer = OracleAtlasObserver(
        IR, timeline_id=source.timeline_id,
        ordinal=lambda: frame["ordinal"])
    with observer.observe(runtime.cpu):
        for frame["ordinal"] in range(args.frames):
            inputs.apply_to_runtime(
                frame["ordinal"], runtime,
                deliver=lambda rt, scancode: frontend.deliver_input(rt, scancode))
            frontend.advance_frame(runtime, launch_args, frame["ordinal"])
    end_state = capture_runtime_continuation(
        runtime, event_cursor=inputs.event_cursor)
    artifact = recording.finish(args.frames, end_state=end_state)
    artifact.set_function_visits(observer.recorder.visits)
    artifact.set_execution_evidence(
        profile, observer.recorder.evidence(profile))
    print(
        f"{output}: {len(events)} events, "
        f"{len(artifact.function_visits())} visited functions, "
        f"{len(artifact.execution_evidence().transfers)} observed transfers")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
