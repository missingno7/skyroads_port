"""SkyRoads' canonical generated-carrier/native-gameplay handoff slice."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from dos_re.execution import (
    PlanBinding,
    RegionEntryPoint,
    RegionExitPoint,
    RegionStateOwnership,
    ResolvedExecutionRegion,
)
from dos_re.cpu import CPU8086
from dos_re.memory import Memory
from dos_re.regions import RegionHandoff, RegionProgress

from skyroads.gameplay_region import (
    GAMEPLAY_ABORTED_EXIT,
    GAMEPLAY_ENTRY_ID,
    GAMEPLAY_ENTRY_IP,
    GAMEPLAY_RESUME_ENTRY_ID,
    GAMEPLAY_RESUME_IP,
    GAMEPLAY_RESULT_EXIT,
    GAMEPLAY_CALLER_IP,
    GAMEPLAY_TICK_BOUNDARY,
    ROAD_DEPARTURE_EXIT,
    SkyroadsGameplaySession,
    _GeneratedGameplayServices,
    activate_gameplay_region,
    maybe_enter_gameplay_region,
    reset_gameplay_region_for_restore,
)
from skyroads.identities import (
    CODE_SEG,
    GAMEPLAY_ENTRY_POINT,
    GAMEPLAY_RESUME_POINT,
    GAMEPLAY_CALLER_CONTINUATION,
    GAMEPLAY_ROAD_DEPARTURE_CONTINUATION,
    GAMEPLAY_REGION,
)
from skyroads.handrecovered.dynamics import JumpScratch
from skyroads.native.gaps import RoadDepartureTransition
from skyroads.native.loop import GameplayScratch, road_departure_threshold


@pytest.mark.parametrize(
    ("base", "expected"),
    (
        (0x0000, 0xFFFF8000),
        (0x0001, 0x00008000),
        (0x003C, 0x003B8000),
        (0xFFFF, 0xFFFE8000),
    ),
)
def test_road_departure_threshold_matches_23ca_shift(base, expected) -> None:
    assert road_departure_threshold(base) == expected


def test_generated_sound_seam_preserves_native_cpu_context() -> None:
    cpu = CPU8086(Memory())
    cpu.s.cs = CODE_SEG
    cpu.s.ip = 0x2317
    cpu.s.ds = cpu.s.ss = 0x1686
    cpu.s.sp = 0xB8F0
    cpu.s.ax = 0x1111
    cpu.s.bx = 0x2222
    cpu.instruction_count = 12345
    cpu.call_depth = 4
    boundary = object()
    cpu.boundary_hook = boundary
    seen = []

    def generated_sfx(current_cpu) -> None:
        seen.append(current_cpu.mem.rw(
            current_cpu.s.ss, current_cpu.s.sp + 2,
        ))
        current_cpu.mem.ww(current_cpu.s.ds, 0x4000, 0xCAFE)
        current_cpu.s.ax = 0xFFFF
        current_cpu.s.ip = current_cpu.pop()

    generated_sfx.owns_time = True
    cpu.replacement_hooks[(CODE_SEG, 0x03C2)] = generated_sfx
    before = {
        name: getattr(cpu.s, name)
        for name in (
            "ax", "bx", "cx", "dx", "sp", "bp", "si", "di",
            "cs", "ds", "es", "ss", "ip", "flags", "fsw", "fcw",
        )
    }

    _GeneratedGameplayServices(SimpleNamespace(cpu=cpu)).emit_sfx(2)

    assert seen == [2]
    assert cpu.mem.rw(cpu.s.ds, 0x4000) == 0xCAFE
    assert {name: getattr(cpu.s, name) for name in before} == before
    assert cpu.instruction_count == 12345
    assert cpu.call_depth == 4
    assert cpu.boundary_hook is boundary


def test_native_tick_boundary_materializes_restorable_stack_scratch(
    monkeypatch,
) -> None:
    cpu = CPU8086(Memory())
    cpu.s.ds = cpu.s.ss = 0x1686
    cpu.s.bp = 0xB910
    runtime = SimpleNamespace(
        cpu=cpu,
        _skyroads_gameplay_services=SimpleNamespace(emit_sfx=lambda _effect: None),
    )
    session = SkyroadsGameplaySession(runtime)
    expected = GameplayScratch(
        JumpScratch(1, 0x2345, 1),
        2,
        3,
        4,
        0x5678,
    )
    session.scratch = expected
    monkeypatch.setattr(
        "skyroads.gameplay_region.native_gameplay_body",
        lambda _view, scratch, **_kwargs: scratch,
    )
    cpu.mem.ww(cpu.s.ss, cpu.s.bp - 2, 0)
    cpu.mem.ww(cpu.s.ds, 0x1600, 1)
    session._render = lambda: None

    assert session.advance() == RegionProgress.yielded(GAMEPLAY_TICK_BOUNDARY)

    reconstructed = SkyroadsGameplaySession(runtime)
    assert reconstructed.scratch == expected
    assert (cpu.s.cs, cpu.s.ip) == (CODE_SEG, GAMEPLAY_RESUME_IP)


def test_region_batches_body_until_the_original_local_tick_catches_up(
    monkeypatch,
) -> None:
    cpu = CPU8086(Memory())
    cpu.s.ds = cpu.s.ss = 0x1686
    cpu.s.bp = 0xB910
    runtime = SimpleNamespace(
        cpu=cpu,
        _skyroads_gameplay_services=SimpleNamespace(
            emit_sfx=lambda _effect: None,
        ),
    )
    calls = []

    def body(_view, scratch, **_kwargs):
        calls.append(_stack_tick(cpu))
        return scratch

    monkeypatch.setattr("skyroads.gameplay_region.native_gameplay_body", body)
    session = SkyroadsGameplaySession(runtime)
    session._render = lambda: None
    cpu.mem.ww(cpu.s.ss, cpu.s.bp - 2, 0)
    cpu.mem.ww(cpu.s.ds, 0x1600, 2)

    assert session.advance().boundary_id == GAMEPLAY_TICK_BOUNDARY
    assert calls == [0, 1]
    assert _stack_tick(cpu) == 2
    assert cpu.mem.rw(cpu.s.ss, cpu.s.bp - 4) == 2

    # The original 22F8 pre-comparison wait does not run another body until virtual time
    # changes, then catches up all missed ticks in one displayed frame.
    assert session.advance().boundary_id == GAMEPLAY_TICK_BOUNDARY
    assert calls == [0, 1]
    cpu.mem.ww(cpu.s.ds, 0x1600, 5)
    assert session.advance().boundary_id == GAMEPLAY_TICK_BOUNDARY
    assert calls == [0, 1, 2, 3, 4]
    assert _stack_tick(cpu) == 5


def _stack_tick(cpu) -> int:
    return cpu.mem.rw(cpu.s.ss, cpu.s.bp - 2)


def test_region_collapses_and_restores_internal_generated_hooks(monkeypatch) -> None:
    cpu = CPU8086(Memory())
    cpu.s.cs = CODE_SEG
    cpu.s.ip = GAMEPLAY_ENTRY_IP
    cpu.s.ds = cpu.s.ss = 0x1686
    cpu.s.bp = 0xB910
    cpu.s.sp = 0xB8F0
    cpu.mem.ww(cpu.s.ss, cpu.s.sp, 0x1111)
    cpu.mem.ww(cpu.s.ss, cpu.s.sp + 2, 0x2222)
    cpu.mem.ww(cpu.s.ss, cpu.s.bp, 0xB920)
    cpu.mem.ww(cpu.s.ss, cpu.s.bp + 2, GAMEPLAY_CALLER_IP)
    internal = lambda _cpu: None
    cpu.replacement_hooks[(CODE_SEG, 0x04C0)] = internal
    cpu.hook_names[(CODE_SEG, 0x04C0)] = "lifted_1010_04c0"
    cpu.replacement_hooks[(CODE_SEG, 0x03C2)] = lambda _cpu: None
    cpu.hook_names[(CODE_SEG, 0x03C2)] = "lifted_1010_03c2"
    runtime = SimpleNamespace(cpu=cpu)
    activate_gameplay_region(runtime, _binding())

    with pytest.raises(RegionHandoff):
        maybe_enter_gameplay_region(runtime, cpu, CODE_SEG, GAMEPLAY_ENTRY_IP)

    assert (CODE_SEG, 0x04C0) not in cpu.replacement_hooks
    assert (CODE_SEG, 0x03C2) in cpu.replacement_hooks
    session = runtime.execution_regions._active.session
    monkeypatch.setattr(
        "skyroads.gameplay_region.native_gameplay_body",
        lambda _view, scratch, **_kwargs: scratch,
    )
    session.view.game_state = 2
    session._render = lambda: None
    runtime.execution_regions.advance()

    assert cpu.replacement_hooks[(CODE_SEG, 0x04C0)] is internal
    assert runtime._skyroads_collapsed_runtime_hooks == ()

    with pytest.raises(RegionHandoff):
        maybe_enter_gameplay_region(runtime, cpu, CODE_SEG, GAMEPLAY_ENTRY_IP)
    assert (CODE_SEG, 0x04C0) not in cpu.replacement_hooks
    reset_gameplay_region_for_restore(runtime)
    assert not runtime.execution_regions.active
    assert cpu.replacement_hooks[(CODE_SEG, 0x04C0)] is internal


def test_generated_boundary_returns_the_original_raw_game_state(monkeypatch) -> None:
    cpu = CPU8086(Memory())
    cpu.s.cs = CODE_SEG
    cpu.s.ip = GAMEPLAY_ENTRY_IP
    cpu.s.ds = cpu.s.ss = 0x1686
    cpu.s.bp = 0xB910
    cpu.s.sp = 0xB8F0
    cpu.mem.ww(cpu.s.ss, cpu.s.sp, 0x1111)
    cpu.mem.ww(cpu.s.ss, cpu.s.sp + 2, 0x2222)
    cpu.mem.ww(cpu.s.ss, cpu.s.bp, 0xB920)
    cpu.mem.ww(cpu.s.ss, cpu.s.bp + 2, GAMEPLAY_CALLER_IP)
    cpu.replacement_hooks[(CODE_SEG, 0x03C2)] = lambda current_cpu: None
    runtime = SimpleNamespace(cpu=cpu)
    mem = cpu.mem
    state = cpu.s
    binding = _binding()
    activate_gameplay_region(runtime, binding)

    with pytest.raises(RegionHandoff):
        maybe_enter_gameplay_region(
            runtime, runtime.cpu, CODE_SEG, GAMEPLAY_ENTRY_IP,
        )

    dispatcher = runtime.execution_regions
    assert dispatcher.active_region_id == GAMEPLAY_REGION
    assert dispatcher.last_entry_id == GAMEPLAY_ENTRY_ID

    # Force the original 22E3 gate. The region must not reinterpret state two
    # as completion: 1FD9 returns DS:[456E] verbatim and outer 01B8 owns it.
    session = dispatcher._active.session
    session.view.game_state = 2
    monkeypatch.setattr(
        "skyroads.gameplay_region.native_gameplay_body",
        lambda _view, scratch, **_kwargs: scratch,
    )
    session._render = lambda: None
    mem.ww(state.ds, 0x1600, 0x1234)

    progress = dispatcher.advance()

    assert progress.exit_id == GAMEPLAY_RESULT_EXIT
    assert not dispatcher.active
    assert dispatcher.last_exit_id == GAMEPLAY_RESULT_EXIT
    assert (state.cs, state.ip) == (CODE_SEG, GAMEPLAY_CALLER_IP)
    assert state.ax == 2
    assert mem.rw(state.ds, 0x456E) == 2
    assert runtime._skyroads_last_region_exit == GAMEPLAY_RESULT_EXIT


@pytest.mark.parametrize("game_state", (1, 2, 4, 5))
def test_handler_gate_returns_each_raw_game_state(monkeypatch, game_state) -> None:
    cpu = CPU8086(Memory())
    cpu.s.cs = CODE_SEG
    cpu.s.ip = GAMEPLAY_ENTRY_IP
    cpu.s.ds = cpu.s.ss = 0x1686
    cpu.s.bp = 0xB910
    cpu.s.sp = 0xB8F0
    cpu.mem.ww(cpu.s.ss, cpu.s.sp, 0x1111)
    cpu.mem.ww(cpu.s.ss, cpu.s.sp + 2, 0x2222)
    cpu.mem.ww(cpu.s.ss, cpu.s.bp, 0xB920)
    cpu.mem.ww(cpu.s.ss, cpu.s.bp + 2, GAMEPLAY_CALLER_IP)
    cpu.replacement_hooks[(CODE_SEG, 0x03C2)] = lambda current_cpu: None
    runtime = SimpleNamespace(cpu=cpu)
    activate_gameplay_region(runtime, _binding())
    with pytest.raises(RegionHandoff):
        maybe_enter_gameplay_region(runtime, cpu, CODE_SEG, GAMEPLAY_ENTRY_IP)
    session = runtime.execution_regions._active.session
    session.view.game_state = game_state
    if game_state in (4, 5):
        session.view.frame_ctr = 0x6C
    monkeypatch.setattr(
        "skyroads.gameplay_region.native_gameplay_body",
        lambda _view, scratch, **_kwargs: scratch,
    )
    session._render = lambda: None

    assert runtime.execution_regions.advance().exit_id == GAMEPLAY_RESULT_EXIT
    assert runtime._skyroads_last_region_exit == GAMEPLAY_RESULT_EXIT
    assert cpu.s.ax == game_state


def test_escape_returns_through_original_gameplay_caller(monkeypatch) -> None:
    cpu = CPU8086(Memory())
    cpu.s.cs = CODE_SEG
    cpu.s.ip = GAMEPLAY_ENTRY_IP
    cpu.s.ds = cpu.s.ss = 0x1686
    cpu.s.bp = 0xB910
    cpu.s.sp = 0xB8F0
    cpu.s.di = 0xAAAA
    cpu.s.si = 0xBBBB
    cpu.call_depth = 2
    cpu.replacement_hooks[(CODE_SEG, 0x03C2)] = lambda current_cpu: None
    cpu.mem.ww(cpu.s.ss, cpu.s.sp, 0x1111)
    cpu.mem.ww(cpu.s.ss, cpu.s.sp + 2, 0x2222)
    cpu.mem.ww(cpu.s.ss, cpu.s.bp, 0xB920)
    cpu.mem.ww(cpu.s.ss, cpu.s.bp + 2, 0x2C61)
    cpu.mem.wb(cpu.s.ds, 0x0BDA, 0x80)
    runtime = SimpleNamespace(cpu=cpu)
    activate_gameplay_region(runtime, _binding())
    with pytest.raises(RegionHandoff):
        maybe_enter_gameplay_region(runtime, cpu, CODE_SEG, GAMEPLAY_ENTRY_IP)
    monkeypatch.setattr(
        "skyroads.gameplay_region.native_gameplay_body",
        lambda _view, scratch, **_kwargs: scratch,
    )

    progress = runtime.execution_regions.advance()

    assert progress.exit_id == GAMEPLAY_ABORTED_EXIT
    assert (cpu.s.cs, cpu.s.ip, cpu.s.ax) == (CODE_SEG, 0x2C61, 7)
    assert (cpu.s.di, cpu.s.si, cpu.s.bp, cpu.s.sp) == (
        0x1111, 0x2222, 0xB920, 0xB914,
    )
    assert cpu.call_depth == 1


def test_road_departure_hands_0f05_back_to_generated_code(monkeypatch) -> None:
    cpu = CPU8086(Memory())
    cpu.s.cs = CODE_SEG
    cpu.s.ip = GAMEPLAY_ENTRY_IP
    cpu.s.ds = cpu.s.ss = 0x1686
    cpu.s.bp = 0xB910
    cpu.s.sp = 0xB8F0
    cpu.replacement_hooks[(CODE_SEG, 0x03C2)] = lambda current_cpu: None
    runtime = SimpleNamespace(cpu=cpu)
    activate_gameplay_region(runtime, _binding())
    with pytest.raises(RegionHandoff):
        maybe_enter_gameplay_region(runtime, cpu, CODE_SEG, GAMEPLAY_ENTRY_IP)
    def depart(_view, _scratch, **_kwargs):
        raise RoadDepartureTransition("observed 23CA-241E road departure")

    monkeypatch.setattr(
        "skyroads.gameplay_region.native_gameplay_body", depart,
    )

    assert runtime.execution_regions.advance().exit_id == ROAD_DEPARTURE_EXIT
    assert (cpu.s.cs, cpu.s.ip) == (CODE_SEG, 0x0F05)
    assert cpu.pop() == 0x241E


def _binding() -> ResolvedExecutionRegion:
    return ResolvedExecutionRegion(
        region_id=GAMEPLAY_REGION,
        implementation_id="faithful-region:skyroads.gameplay",
        host_carrier_id="generated-vmless-cpu",
        region_carrier_id="dos-memory",
        adapter_id="region-adapter:skyroads.gameplay:generated-vmless",
        adapter_digest="test-adapter",
        state_ownership=RegionStateOwnership.SHARED_DOS_MEMORY,
        entries=(
            RegionEntryPoint(GAMEPLAY_ENTRY_ID, GAMEPLAY_ENTRY_POINT),
            RegionEntryPoint(
                GAMEPLAY_RESUME_ENTRY_ID, GAMEPLAY_RESUME_POINT,
            ),
        ),
        exits=(
            RegionExitPoint(GAMEPLAY_RESULT_EXIT, GAMEPLAY_CALLER_CONTINUATION),
            RegionExitPoint(
                ROAD_DEPARTURE_EXIT,
                GAMEPLAY_ROAD_DEPARTURE_CONTINUATION,
            ),
            RegionExitPoint(GAMEPLAY_ABORTED_EXIT, GAMEPLAY_CALLER_CONTINUATION),
        ),
        covered_targets=(GAMEPLAY_REGION, "function:1010:04c0"),
        suppressed_bindings=(
            PlanBinding(GAMEPLAY_REGION, "generated"),
            PlanBinding("function:1010:04c0", "generated"),
        ),
        replay_boundaries=(GAMEPLAY_TICK_BOUNDARY,),
    )
