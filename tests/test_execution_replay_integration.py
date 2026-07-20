"""End-to-end proof of the unified player/planner/ReplayArtifact seam."""
from __future__ import annotations

from pathlib import Path

import pytest

from dos_re import player
from dos_re.replay import ReplayRecording, ReplayPoint, verify_interval
from scripts.play import SkyroadsFrontend

ROOT = Path(__file__).resolve().parents[1]
EXE = ROOT / "assets" / "SKYROADS.EXE"


@pytest.mark.skipif(not EXE.exists(), reason="needs SKYROADS.EXE")
def test_generated_plan_verifies_one_real_frame_from_oracle_artifact(tmp_path):
    frontend = SkyroadsFrontend(ROOT)
    oracle_args = player.build_arg_parser(frontend).parse_args([
        "--headless", "--composition", "oracle",
    ])
    oracle_args.execution_plan = frontend.resolve_execution_plan(oracle_args)
    oracle_runtime = frontend.create_runtime(oracle_args)
    profile = frontend.replay_profile(oracle_args, oracle_runtime)
    base = frontend.capture_replay_state(oracle_runtime, event_cursor=0)
    recording = ReplayRecording(
        tmp_path / "replay",
        timeline_id="real-mode-frame-boundaries:skyroads:v1",
        profile=profile,
        base_state=base,
        metadata=frontend.replay_metadata(oracle_args),
    )
    frontend.advance_frame(oracle_runtime, oracle_args, 0)
    artifact = recording.finish(
        1,
        end_state=frontend.capture_replay_state(
            oracle_runtime, event_cursor=0),
    )

    verify_args = player.build_arg_parser(frontend).parse_args([
        "--headless", "--profile", "verification",
        "--composition", "generated-functions",
    ])
    plan = frontend.resolve_execution_plan(verify_args)
    verify_args.execution_plan = plan
    oracle, candidate = frontend.verification_drivers(
        verify_args, plan, artifact)
    result = verify_interval(
        artifact, oracle, candidate,
        ReplayPoint(0, artifact.timeline_id),
        ReplayPoint(1, artifact.timeline_id),
    )
    assert result.equivalent, result.comparison.differences
