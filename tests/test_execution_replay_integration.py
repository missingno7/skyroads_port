"""End-to-end proof of the unified player/planner/ReplayArtifact seam."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from dos_re import player
from dos_re.runtime_miss import RuntimeExecutionFrontier
from dos_re.replay import (
    ReplayRecording,
    ReplayPoint,
    verify_checkpointed,
    verify_interval,
)
from scripts.play import SkyroadsFrontend
from skyroads.hooks import CODE_SEG
from skyroads.pacing import (
    begin_frame_park,
    FADE_BLEND_WAIT_IP,
    FADE_WAIT_COMPARE_IP,
    FrameIdle as PacingFrameIdle,
    MENU_SCENE_FRAME_IP,
    PACING_SPIN_IP,
    ROAD_DEPARTURE_WAIT_IP,
    TICK_ADDR,
    install_frame_park,
)
from skyroads import vmless_backend

ROOT = Path(__file__).resolve().parents[1]
EXE = ROOT / "assets" / "SKYROADS.EXE"


def test_generated_carrier_delegates_to_canonical_replay_lifecycle(
    monkeypatch,
) -> None:
    args = SimpleNamespace()
    frontend = object()
    binder = lambda runtime: None
    seen = {}

    def launch_real_mode(selected_frontend, selected_args, **kwargs):
        seen.update(kwargs)
        assert selected_frontend is frontend
        assert selected_args is args
        return 23

    monkeypatch.setattr(player, "launch_real_mode", launch_real_mode)

    assert vmless_backend.launch(
        args,
        bootstrap_artifacts={},
        bind_plan=binder,
        frontend=frontend,
    ) == 23
    assert callable(seen["create_runtime"])
    assert seen["bind_execution_plan"] is binder


def test_interpreter_parks_the_road_departure_tick_wait() -> None:
    words = {TICK_ADDR: 72}
    cpu = SimpleNamespace(
        replacement_hooks={},
        hook_names={},
        s=SimpleNamespace(ds=0x1686, si=72, ip=ROAD_DEPARTURE_WAIT_IP),
        mem=SimpleNamespace(rw=lambda segment, offset: words[offset]),
        instruction_count=100,
        set_sub_flags=lambda *args: None,
    )
    install_frame_park(SimpleNamespace(cpu=cpu))
    hook = cpu.replacement_hooks[(CODE_SEG, ROAD_DEPARTURE_WAIT_IP)]

    with pytest.raises(PacingFrameIdle):
        hook(cpu)
    words[TICK_ADDR] = 73
    hook(cpu)
    assert cpu.s.ip == 0x0F01
    assert cpu.instruction_count == 101


def test_interpreter_fade_wait_runs_one_body_then_parks_per_frame() -> None:
    words = {TICK_ADDR: 3}
    cpu = SimpleNamespace(
        replacement_hooks={},
        hook_names={},
        s=SimpleNamespace(ds=0x1686, ax=10, ip=FADE_WAIT_COMPARE_IP),
        mem=SimpleNamespace(rw=lambda segment, offset: words[offset]),
        instruction_count=100,
        set_sub_flags=lambda *args: None,
    )
    runtime = SimpleNamespace(cpu=cpu)
    install_frame_park(runtime)
    hook = cpu.replacement_hooks[(CODE_SEG, FADE_WAIT_COMPARE_IP)]

    begin_frame_park(runtime)
    hook(cpu)
    assert cpu.s.ip == 0x4471
    cpu.s.ip = FADE_WAIT_COMPARE_IP
    with pytest.raises(PacingFrameIdle):
        hook(cpu)

    begin_frame_park(runtime)
    hook(cpu)
    assert cpu.s.ip == 0x4471

    words[TICK_ADDR] = 10
    cpu.s.ip = FADE_WAIT_COMPARE_IP
    hook(cpu)
    assert cpu.s.ip == 0x4481


def test_interpreter_fade_blend_parks_after_one_body_and_restores_phase() -> None:
    words = {(0xB900 + 8) & 0xFFFF: 50}
    cpu = SimpleNamespace(
        replacement_hooks={},
        hook_names={},
        s=SimpleNamespace(
            cs=CODE_SEG, ip=FADE_BLEND_WAIT_IP,
            ds=0x1686, ss=0x1686, bp=0xB900,
        ),
        mem=SimpleNamespace(rw=lambda segment, offset: words[offset]),
        set_sub_flags=lambda *args: None,
    )
    runtime = SimpleNamespace(cpu=cpu)
    install_frame_park(runtime)
    hook = cpu.replacement_hooks[(CODE_SEG, FADE_BLEND_WAIT_IP)]

    begin_frame_park(runtime)
    hook(cpu)
    assert cpu.s.ip == 0x434E
    cpu.s.ip = FADE_BLEND_WAIT_IP
    with pytest.raises(PacingFrameIdle):
        hook(cpu)
    assert cpu._skyroads_frame_park_identity == "1010:434A"
    assert cpu.s.ip == FADE_BLEND_WAIT_IP

    # The restorable point is before the comparison. A new frame re-evaluates
    # it, runs one body, and parks only when that body returns to the head.
    begin_frame_park(runtime)
    assert not cpu._skyroads_fade_blend_seen
    hook(cpu)
    assert cpu.s.ip == 0x434E
    cpu.s.ip = FADE_BLEND_WAIT_IP
    with pytest.raises(PacingFrameIdle):
        hook(cpu)


def test_interpreter_menu_scene_runs_one_animation_body_per_frame() -> None:
    cpu = SimpleNamespace(
        replacement_hooks={},
        hook_names={},
        s=SimpleNamespace(
            cs=CODE_SEG, ip=MENU_SCENE_FRAME_IP,
            ax=0, ds=0x1686, ss=0x1686, bp=0xB900,
        ),
        mem=SimpleNamespace(rw=lambda _segment, _offset: 0),
        set_sub_flags=lambda *args: None,
    )
    runtime = SimpleNamespace(cpu=cpu)
    install_frame_park(runtime)
    hook = cpu.replacement_hooks[(CODE_SEG, MENU_SCENE_FRAME_IP)]

    begin_frame_park(runtime)
    hook(cpu)
    assert cpu.s.ax == 0x013F
    assert cpu.s.ip == 0x4869
    cpu.s.ip = MENU_SCENE_FRAME_IP
    with pytest.raises(PacingFrameIdle):
        hook(cpu)
    assert cpu.s.ip == MENU_SCENE_FRAME_IP
    assert cpu._skyroads_frame_park_identity == "1010:4866"


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
        "boundary_identity": "1010:22F8",
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

    # Negative proof: the same end-to-end verifier must reject an intentional
    # authoritative game-state error at the exact first semantic transition.
    oracle, candidate = frontend.verification_drivers(
        verify_args, plan, artifact)
    replay_correctly = candidate.replay_to

    def replay_with_wrong_ship_position(current_artifact, target):
        replay_correctly(current_artifact, target)
        if target.ordinal:
            state = candidate.runtime.cpu.s
            memory = candidate.runtime.cpu.mem
            low = memory.rw(state.ds, 0x54AC)
            memory.ww(state.ds, 0x54AC, (low + 1) & 0xFFFF)

    candidate.replay_to = replay_with_wrong_ship_position
    rejected = verify_checkpointed(
        artifact, oracle, candidate,
        ReplayPoint(0, artifact.timeline_id),
        ReplayPoint(1, artifact.timeline_id),
        checkpoint_span=64,
        observable_effects=True,
    )
    assert not rejected.equivalent
    assert rejected.failed_interval == (
        ReplayPoint(0, artifact.timeline_id),
        ReplayPoint(1, artifact.timeline_id),
    )
    assert any(
        "gameplay.ship_pos" in difference
        for difference in rejected.comparison.differences
    ), rejected.comparison.differences


def test_interactive_semantic_seek_cooperates_without_creating_guest_points() -> None:
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
    frontend.offline_semantic_seek_budget = 96
    frontend.offline_semantic_seek_chunks = 2
    yielded: list[None] = []
    runtime = SimpleNamespace(
        cpu=CountingCpu(),
        _dos_re_host_yield=lambda: yielded.append(None),
    )
    args = SimpleNamespace(timer_irqs_per_frame=0, steps_per_frame=48)

    assert frontend._advance_to_semantic_boundary(runtime, args) == "guest-fallback"
    assert runtime.cpu.budgets == [48, 48, 48, 48]
    assert yielded == [None, None, None]
    schema, value = frontend.replay_point_coordinate(
        runtime, args, point_ordinal=7, event_cursor=13)
    assert schema == frontend.semantic_replay_coordinate
    assert value == {
        "sequence": 7,
        "timeline_position": 7,
        "event_cursor": 13,
        "kind": "guest-fallback",
        "guest_instruction_count": 292,
        "guest_budget": 48,
        "fallback_reason": "semantic-boundary-not-reached-within-budget",
        "machine_position": {"cs": 0x1010, "ip": 0x43B1},
    }

    runtime.cpu.budgets.clear()
    yielded.clear()
    assert frontend._advance_to_semantic_boundary(
        runtime, args, offline_replay=True,
    ) == "guest-fallback"
    assert runtime.cpu.budgets == [96, 96]
    assert yielded == [None]


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


def test_generated_driver_ignores_nonsemantic_recovery_heads() -> None:
    cpu = SimpleNamespace(
        boundary_hook=None,
        s=SimpleNamespace(cs=CODE_SEG, ip=0x444C),
    )
    runtime = SimpleNamespace(cpu=cpu)
    driver = vmless_backend.VmlessDriver(runtime, irqs_per_frame=0)

    driver._boundary(cpu, CODE_SEG, 0x444C, 0x4452)
    driver._boundary(cpu, CODE_SEG, 0x444C, 0x4452)

    assert not driver._seen
    assert not driver.parks


def test_generated_timer_boundary_restores_before_external_comparison() -> None:
    cpu = SimpleNamespace(
        boundary_hook=None,
        s=SimpleNamespace(cs=CODE_SEG, ip=ROAD_DEPARTURE_WAIT_IP),
    )
    runtime = SimpleNamespace(cpu=cpu)
    driver = vmless_backend.VmlessDriver(runtime, irqs_per_frame=0)

    driver._boundary(
        cpu, CODE_SEG, ROAD_DEPARTURE_WAIT_IP, 0x0EFC,
    )
    with pytest.raises(vmless_backend.FrameIdle):
        driver._boundary(
            cpu, CODE_SEG, ROAD_DEPARTURE_WAIT_IP, 0x0EFC,
        )

    # Timer delivery happens after this park. Re-entering at 0EFC would reuse
    # pre-interrupt flags and delay the road transition by one replay point.
    assert (cpu.s.cs, cpu.s.ip) == (CODE_SEG, ROAD_DEPARTURE_WAIT_IP)
    assert driver.last_boundary_identity == "1010:0EF8"


def test_generated_driver_routes_actual_miss_to_recovery_frontier(
    tmp_path, monkeypatch,
) -> None:
    captured = {}
    out = tmp_path / "frontier"

    def save_frontier(_runtime, _out, **context):
        out.mkdir()
        captured.update(context)
        return out

    monkeypatch.setattr(
        vmless_backend, "save_recovery_frontier", save_frontier,
    )
    monkeypatch.setattr(
        vmless_backend,
        "save_crash",
        lambda *_args, **_kwargs: pytest.fail(
            "an actual runtime miss must use the recovery-frontier artifact"
        ),
    )
    runtime = SimpleNamespace(
        cpu=SimpleNamespace(boundary_hook=None),
        execution_plan=SimpleNamespace(bindings=()),
    )
    driver = vmless_backend.VmlessDriver(
        runtime,
        irqs_per_frame=0,
        crash_root=tmp_path,
        stamp="deterministic",
    )
    driver._recent_path.append((CODE_SEG, 0x22FB))
    exc = RuntimeExecutionFrontier(target_address="1010:1234")

    assert driver.crash(exc) == out
    assert runtime._dos_re_last_recovery_frontier == str(out)
    assert captured["status"] == "vmless-runtime-frontier"
    assert "1234" in captured["target_identity"]
    assert captured["recent_atlas_path"]
