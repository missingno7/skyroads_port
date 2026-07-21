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
    FELL_OFF_ROAD_EXIT,
    FUEL_EXPIRED_EXIT,
    GAMEPLAY_ABORTED_EXIT,
    GAMEPLAY_ENTRY_ID,
    GAMEPLAY_ENTRY_IP,
    GAMEPLAY_CALLER_IP,
    GAMEPLAY_TICK_BOUNDARY,
    LEVEL_COMPLETED_EXIT,
    OXYGEN_EXPIRED_EXIT,
    SkyroadsGameplaySession,
    WALL_CRASH_EXIT,
    _GeneratedGameplayServices,
    activate_gameplay_region,
    maybe_enter_gameplay_region,
    reset_gameplay_region_for_restore,
)
from skyroads.identities import (
    CODE_SEG,
    GAMEPLAY_ENTRY_POINT,
    GAMEPLAY_CALLER_CONTINUATION,
    GAMEPLAY_FALL_CONTINUATION,
    GAMEPLAY_REGION,
)
from skyroads.handrecovered.dynamics import JumpScratch
from skyroads.native.loop import GameplayScratch, TickOutcome


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


def test_native_tick_boundary_materializes_restorable_stack_scratch() -> None:
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
    session.driver.scratch = expected
    session.driver.tick = lambda: TickOutcome(False, "", "", 0)
    session._render = lambda: None

    assert session.advance() == RegionProgress.yielded(GAMEPLAY_TICK_BOUNDARY)

    reconstructed = SkyroadsGameplaySession(runtime)
    assert reconstructed.driver.scratch == expected


def test_region_collapses_and_restores_internal_generated_hooks() -> None:
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
    session.driver.pending = TickOutcome(True, "complete", "finish", 2)
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


def test_generated_boundary_enters_native_region_and_returns() -> None:
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

    # Force the region's already-declared level-complete outcome. The native
    # gameplay algorithms have their own exact focused tests; this test owns
    # the external lifecycle seam and continuation mapping only.
    session = dispatcher._active.session
    session.view.game_state = 2
    session.driver.pending = TickOutcome(True, "complete", "finish", 2)
    session._render = lambda: None
    mem.ww(state.ds, 0x1600, 0x1234)

    progress = dispatcher.advance()

    assert progress.exit_id == LEVEL_COMPLETED_EXIT
    assert not dispatcher.active
    assert dispatcher.last_exit_id == LEVEL_COMPLETED_EXIT
    assert (state.cs, state.ip) == (CODE_SEG, GAMEPLAY_CALLER_IP)
    # The outer 01B8 product loop consumes zero as "completed: advance and
    # return to level selection".  The inner game_state value remains 2.
    assert state.ax == 0
    assert mem.rw(state.ds, 0x456E) == 2
    assert runtime._skyroads_last_region_exit == LEVEL_COMPLETED_EXIT


@pytest.mark.parametrize(("kind", "exit_id"), (
    ("crash", WALL_CRASH_EXIT),
    ("timeout_fuel", FUEL_EXPIRED_EXIT),
    ("timeout_oxygen", OXYGEN_EXPIRED_EXIT),
))
def test_terminal_outcomes_keep_distinct_named_exit_seams(kind, exit_id) -> None:
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
    session.view.game_state = {
        "crash": 1,
        "timeout_fuel": 4,
        "timeout_oxygen": 5,
    }[kind]
    session.driver.pending = TickOutcome(True, "terminal", kind, 1)
    session._render = lambda: None

    assert runtime.execution_regions.advance().exit_id == exit_id
    assert runtime._skyroads_last_region_exit == exit_id


def test_escape_returns_through_original_gameplay_caller() -> None:
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

    progress = runtime.execution_regions.advance()

    assert progress.exit_id == GAMEPLAY_ABORTED_EXIT
    assert (cpu.s.cs, cpu.s.ip, cpu.s.ax) == (CODE_SEG, 0x2C61, 7)
    assert (cpu.s.di, cpu.s.si, cpu.s.bp, cpu.s.sp) == (
        0x1111, 0x2222, 0xB920, 0xB914,
    )
    assert cpu.call_depth == 1


def test_fall_hands_the_long_death_transition_back_to_generated_code() -> None:
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
    session = runtime.execution_regions._active.session
    session.driver.pending = TickOutcome(True, "fell", "fall", 0)
    session._render = lambda: None

    assert runtime.execution_regions.advance().exit_id == FELL_OFF_ROAD_EXIT
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
        entries=(RegionEntryPoint(GAMEPLAY_ENTRY_ID, GAMEPLAY_ENTRY_POINT),),
        exits=(
            RegionExitPoint(LEVEL_COMPLETED_EXIT, GAMEPLAY_CALLER_CONTINUATION),
            RegionExitPoint(WALL_CRASH_EXIT, GAMEPLAY_CALLER_CONTINUATION),
            RegionExitPoint(FUEL_EXPIRED_EXIT, GAMEPLAY_CALLER_CONTINUATION),
            RegionExitPoint(OXYGEN_EXPIRED_EXIT, GAMEPLAY_CALLER_CONTINUATION),
            RegionExitPoint(FELL_OFF_ROAD_EXIT, GAMEPLAY_FALL_CONTINUATION),
            RegionExitPoint(GAMEPLAY_ABORTED_EXIT, GAMEPLAY_CALLER_CONTINUATION),
        ),
        covered_targets=(GAMEPLAY_REGION, "function:1010:04c0"),
        suppressed_bindings=(
            PlanBinding(GAMEPLAY_REGION, "generated"),
            PlanBinding("function:1010:04c0", "generated"),
        ),
        replay_boundaries=(GAMEPLAY_TICK_BOUNDARY,),
    )
