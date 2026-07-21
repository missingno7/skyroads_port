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
from dos_re.memory import Memory
from dos_re.regions import RegionHandoff

from skyroads.gameplay_region import (
    GAMEPLAY_ENTRY_ID,
    GAMEPLAY_ENTRY_IP,
    GAMEPLAY_RETURN_IP,
    GAMEPLAY_TICK_BOUNDARY,
    LEVEL_COMPLETED_EXIT,
    PLAYER_DIED_EXIT,
    activate_gameplay_region,
    maybe_enter_gameplay_region,
)
from skyroads.identities import (
    CODE_SEG,
    GAMEPLAY_ENTRY_POINT,
    GAMEPLAY_REGION,
    GAMEPLAY_RETURN_POINT,
)
from skyroads.native.loop import TickOutcome


def test_generated_boundary_enters_native_region_and_returns() -> None:
    mem = Memory()
    state = SimpleNamespace(
        cs=CODE_SEG, ip=GAMEPLAY_ENTRY_IP, ds=0x1686, ss=0x1686,
        bp=0xB910,
    )
    runtime = SimpleNamespace(
        cpu=SimpleNamespace(mem=mem, s=state),
        _skyroads_no_sound=True,
    )
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
    session.driver.pending = TickOutcome(True, "complete", "finish", 2)
    session._render = lambda: None
    mem.ww(state.ds, 0x1600, 0x1234)

    progress = dispatcher.advance()

    assert progress.exit_id == LEVEL_COMPLETED_EXIT
    assert not dispatcher.active
    assert dispatcher.last_exit_id == LEVEL_COMPLETED_EXIT
    assert (state.cs, state.ip) == (CODE_SEG, GAMEPLAY_RETURN_IP)
    assert state.ax == 0x1234
    assert mem.rw(state.ss, state.bp - 2) == 0x1234
    assert runtime._skyroads_last_region_exit == LEVEL_COMPLETED_EXIT


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
            RegionExitPoint(LEVEL_COMPLETED_EXIT, GAMEPLAY_RETURN_POINT),
            RegionExitPoint(PLAYER_DIED_EXIT, GAMEPLAY_RETURN_POINT),
        ),
        covered_targets=(GAMEPLAY_REGION,),
        suppressed_bindings=(PlanBinding(GAMEPLAY_REGION, "generated"),),
        replay_boundaries=(GAMEPLAY_TICK_BOUNDARY,),
    )
