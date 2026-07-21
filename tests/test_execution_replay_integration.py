"""End-to-end proof of the unified player/planner/ReplayArtifact seam."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from dos_re import player
from dos_re.replay import (
    ReplayRecording,
    ReplayPoint,
    verify_checkpointed,
    verify_interval,
)
from scripts.play import SkyroadsFrontend
from skyroads.hooks import CODE_SEG
from skyroads.pacing import PACING_SPIN_IP, TICK_ADDR
from skyroads import vmless_backend

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
    frontend.bind_execution_plan(oracle_runtime, oracle_args.execution_plan)
    profile = frontend.replay_profile(oracle_args, oracle_runtime)
    base = frontend.capture_replay_state(oracle_runtime, event_cursor=0)
    recording = ReplayRecording(
        tmp_path / "replay",
        timeline_id="real-mode-frame-boundaries:skyroads:v1",
        profile=profile,
        base_state=base,
        metadata=frontend.replay_metadata(oracle_args),
    )
    schema, value = frontend.replay_point_coordinate(
        oracle_runtime, oracle_args, point_ordinal=0, event_cursor=0)
    recording.mark(0, schema_id=schema, value=value)
    frontend.advance_frame(oracle_runtime, oracle_args, 0)
    schema, value = frontend.replay_point_coordinate(
        oracle_runtime, oracle_args, point_ordinal=1, event_cursor=0)
    recording.mark(1, schema_id=schema, value=value)
    artifact = recording.finish(
        1,
        end_state=frontend.capture_replay_state(
            oracle_runtime, event_cursor=0),
    )

    verify_args = player.build_arg_parser(frontend).parse_args([
        "--headless", "--profile", "verification",
        "--composition", "workbench-auto",
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


@pytest.mark.skipif(not EXE.exists(), reason="needs SKYROADS.EXE")
def test_semantic_frame_park_is_stable_across_oracle_and_generated(tmp_path):
    frontend = SkyroadsFrontend(ROOT)
    oracle_args = player.build_arg_parser(frontend).parse_args([
        "--headless", "--composition", "oracle",
        "--timer-irqs-per-frame", "0",
    ])
    oracle_args.execution_plan = frontend.resolve_execution_plan(oracle_args)
    runtime = frontend.create_runtime(oracle_args)
    frontend.bind_execution_plan(runtime, oracle_args.execution_plan)
    runtime.cpu.s.cs = CODE_SEG
    runtime.cpu.s.ip = PACING_SPIN_IP
    runtime.cpu.s.bp = 0x0200
    runtime.cpu.s.sp = 0x0300
    runtime.cpu.s.ss = runtime.cpu.s.ds
    tick = runtime.cpu.mem.rw(runtime.cpu.s.ds, TICK_ADDR)
    runtime.cpu.mem.ww(runtime.cpu.s.ss, runtime.cpu.s.bp - 4, tick)
    profile = frontend.replay_profile(oracle_args, runtime)
    recording = ReplayRecording(
        tmp_path / "semantic-replay",
        timeline_id="skyroads-semantic-frame-park-test-v1",
        profile=profile,
        base_state=frontend.capture_replay_state(runtime, event_cursor=0),
        metadata=frontend.replay_metadata(oracle_args),
    )
    schema, value = frontend.replay_point_coordinate(
        runtime, oracle_args, point_ordinal=0, event_cursor=0)
    recording.mark(0, schema_id=schema, value=value)
    frontend.advance_frame(runtime, oracle_args, 0)
    schema, value = frontend.replay_point_coordinate(
        runtime, oracle_args, point_ordinal=1, event_cursor=0)
    assert value == {
        "sequence": 1,
        "timeline_position": 1,
        "event_cursor": 0,
        "kind": "frame-park",
    }
    recording.mark(1, schema_id=schema, value=value)
    artifact = recording.finish(
        1,
        end_state=frontend.capture_replay_state(runtime, event_cursor=0),
    )

    verify_args = player.build_arg_parser(frontend).parse_args([
        "--headless", "--profile", "verification",
        "--composition", "workbench-auto",
        "--timer-irqs-per-frame", "0",
    ])
    plan = frontend.resolve_execution_plan(verify_args)
    verify_args.execution_plan = plan
    oracle, candidate = frontend.verification_drivers(
        verify_args, plan, artifact)
    checked = verify_checkpointed(
        artifact, oracle, candidate,
        ReplayPoint(0, artifact.timeline_id),
        ReplayPoint(1, artifact.timeline_id),
        checkpoint_span=64,
        observable_effects=True,
    )
    assert checked.equivalent, checked.comparison.differences


def test_interactive_semantic_seek_uses_one_guest_budget() -> None:
    class CountingCpu:
        def __init__(self) -> None:
            self.instruction_count = 100
            self.s = SimpleNamespace(cs=0x1010, ip=0x43A9)
            self.budgets = []

        def run(self, budget: int) -> None:
            self.budgets.append(budget)
            self.instruction_count += budget
            self.s.ip = 0x43B1

    frontend = SkyroadsFrontend(ROOT)
    runtime = SimpleNamespace(cpu=CountingCpu())
    args = SimpleNamespace(timer_irqs_per_frame=0, steps_per_frame=48_000)

    assert frontend._advance_to_semantic_boundary(runtime, args) == "guest-fallback"
    assert runtime.cpu.budgets == [48_000]
    schema, value = frontend.replay_point_coordinate(
        runtime, args, point_ordinal=7, event_cursor=13)
    assert schema == frontend.semantic_replay_coordinate
    assert value == {
        "sequence": 7,
        "timeline_position": 7,
        "event_cursor": 13,
        "kind": "guest-fallback",
        "guest_instruction_count": 48_100,
        "guest_budget": 48_000,
        "fallback_reason": "semantic-boundary-not-reached-within-budget",
        "machine_position": {"cs": 0x1010, "ip": 0x43B1},
    }


def test_generated_driver_preserves_boundary_phase_across_guest_slice(
    monkeypatch,
) -> None:
    class ParkingCpu:
        boundary_hook = None

        @staticmethod
        def run(_budget: int) -> None:
            raise vmless_backend.FrameIdle

    runtime = SimpleNamespace(
        cpu=ParkingCpu(),
        _skyroads_replay_boundary_kind="guest-fallback",
    )
    monkeypatch.setattr(vmless_backend, "deliver_interrupt", lambda *_: None)
    driver = vmless_backend.VmlessDriver(runtime, irqs_per_frame=0)
    started = (CODE_SEG, 0x434A)
    driver._seen.add(started)

    assert driver.frame()
    assert started in driver._seen
    assert runtime._skyroads_replay_boundary_kind == "frame-park"

    # A completed semantic boundary starts a fresh phase.
    assert driver.frame()
    assert started not in driver._seen
