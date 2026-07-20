"""Enrich an existing ReplayArtifact by replaying it on the oracle.

Capture and evidence collection are independent. This command leaves the
immutable event stream and capture base untouched, then idempotently attaches
function visits and observed transfers produced by an exact oracle execution
plan and observer implementation.

Usage:
    python scripts/enrich_replay.py artifacts/replays/REPLAY
    python scripts/enrich_replay.py artifacts/replays/REPLAY --frames 120
    python scripts/enrich_replay.py artifacts/replays/REPLAY --benchmark-frames 30
"""
from __future__ import annotations

import argparse
from contextlib import nullcontext
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "dos_re"))

from dos_re import player  # noqa: E402
from dos_re.replay_input import RealModeInputAdapter  # noqa: E402
from dos_re.replay import ReplayArtifact, ReplayPoint  # noqa: E402
from dos_re.snapshot import (  # noqa: E402
    apply_runtime_continuation,
)
from scripts.play import SkyroadsFrontend  # noqa: E402
from skyroads.atlas_evidence import OracleAtlasObserver  # noqa: E402
from skyroads.content_identity import content_digest  # noqa: E402
from skyroads.replay import capture_base, capture_profile  # noqa: E402

IR = ROOT / "recovery" / "recovery_ir.json"
OBSERVER_DIGEST = content_digest(
    (ROOT / "skyroads" / "atlas_evidence.py", IR),
    repository_root=ROOT,
)


def _launch_args(
    artifact: ReplayArtifact, *, audio: str | None,
):
    frontend = SkyroadsFrontend(ROOT)
    launch_args = player.build_arg_parser(frontend).parse_args([
        "--composition", "oracle",
    ])
    frontend.apply_replay_metadata(launch_args, artifact.metadata)
    if audio is not None:
        launch_args.audio = audio
    # This command restores ReplayArtifact state directly, so its plan must
    # explicitly retain replay/snapshot services.
    launch_args.play_replay = str(artifact.directory)
    launch_args.execution_plan = frontend.resolve_execution_plan(launch_args)
    return frontend, launch_args


def _runtime(frontend, launch_args, artifact):
    runtime = frontend.create_runtime(launch_args)
    base = capture_base(artifact)
    apply_runtime_continuation(runtime, base)
    frontend.bind_execution_plan(runtime, launch_args.execution_plan)
    runtime.dos.save_dir = None
    return runtime


def _observe(
    frontend, launch_args, artifact, *, frames: int, collect: bool,
):
    runtime = _runtime(frontend, launch_args, artifact)
    profile = frontend.replay_profile(launch_args, runtime)
    inputs = RealModeInputAdapter(artifact.events)
    ordinal = {"value": 0}
    observer = OracleAtlasObserver(
        IR, timeline_id=artifact.timeline_id,
        ordinal=lambda: ordinal["value"],
    )
    context = observer.observe(runtime.cpu) if collect else nullcontext()
    started = time.perf_counter()
    with context:
        for ordinal["value"] in range(frames):
            inputs.apply_to_runtime(
                ordinal["value"], runtime,
                deliver=lambda rt, scancode: frontend.deliver_input(rt, scancode))
            frontend.advance_frame(runtime, launch_args, ordinal["value"])
    return time.perf_counter() - started, observer.recorder, profile


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("replay", type=Path)
    parser.add_argument(
        "--frames", type=int,
        help="enrich only this prefix (defaults to the complete replay)",
    )
    parser.add_argument(
        "--audio", choices=("off", "adlib"),
        help="device topology for recordings that predate audio metadata",
    )
    parser.add_argument(
        "--benchmark-frames", type=int, default=0,
        help="measure inline observer overhead over a replay prefix",
    )
    args = parser.parse_args(argv)
    artifact = ReplayArtifact.open(args.replay.resolve())
    end = ReplayPoint.from_json(artifact.metadata["end_point"]).ordinal
    frames = end if args.frames is None else args.frames
    if frames <= 0 or frames > end:
        parser.error(f"--frames must be in 1..{end}")
    if args.benchmark_frames < 0 or args.benchmark_frames > end:
        parser.error(f"--benchmark-frames must be in 0..{end}")

    frontend, launch_args = _launch_args(artifact, audio=args.audio)
    if args.benchmark_frames:
        baseline, _, _ = _observe(
            frontend, launch_args, artifact,
            frames=args.benchmark_frames, collect=False,
        )
        observed, _, _ = _observe(
            frontend, launch_args, artifact,
            frames=args.benchmark_frames, collect=True,
        )
        overhead = (
            float("inf") if baseline == 0 else (observed / baseline - 1.0) * 100.0
        )
        print(
            f"observer benchmark: {args.benchmark_frames} frames; "
            f"baseline={baseline:.3f}s observed={observed:.3f}s "
            f"overhead={overhead:.1f}%"
        )

    _, recorder, profile = _observe(
        frontend, launch_args, artifact, frames=frames, collect=True)
    known = {item.profile_id for item, _ in artifact.profiles()}
    if profile.profile_id not in known:
        artifact.register_profile(
            profile,
            base_point=artifact.cached_points(capture_profile(artifact))[0],
            base_state=capture_base(artifact),
        )
    else:
        artifact.require_profile(profile)
    changed = artifact.set_execution_evidence(
        profile,
        recorder.evidence(
            profile,
            provenance={
                "kind": "post-hoc-oracle-replay",
                "observer": "skyroads.OracleAtlasObserver",
                "observer_digest": OBSERVER_DIGEST,
                "execution_plan_identity": profile.identity_digest,
                "event_stream_sha256": artifact.event_stream_sha256,
                "start_ordinal": 0,
                "end_ordinal": frames,
            },
        ),
        visits=recorder.visits,
    )
    print(
        f"{artifact.directory}: "
        f"{len(artifact.function_visits())} visited functions, "
        f"{len(artifact.execution_evidence().transfers)} observed edges; "
        f"{'updated' if changed else 'unchanged'}; "
        f"oracle-backed-timeline={'yes' if artifact.trusted else 'no'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
