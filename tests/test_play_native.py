"""Smoke test for scripts/play_native.py -- the standalone VM-free entry point
(following pre2_port's play_native.py model). Exercises its core functions
directly (not a subprocess/CLI test) to catch import/logic regressions.

The real proof of correctness is tests/test_native_driver.py and
tests/test_native_loop_lockstep.py, which this script's functions are built
from; this test just confirms the script itself still wires them together and
runs end to end.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXE = ROOT / "assets" / "SKYROADS.EXE"
DEMO = ROOT / "artifacts" / "demos" / "demo_e2e_20260710_132930"

pytestmark = pytest.mark.skipif(
    not (EXE.exists() and DEMO.exists()),
    reason="needs SKYROADS.EXE + the E2E demo",
)


@pytest.fixture(scope="module")
def play_native_module():
    sys.path.insert(0, str(ROOT / "scripts"))
    import play_native
    return play_native


def test_boot_and_seed_reaches_real_gameplay(play_native_module) -> None:
    seed, inputs, _live = play_native_module.boot_and_seed(ROOT, DEMO)
    assert seed["state"] is not None
    assert seed["jump_level_gate"] > 0
    assert len(inputs) > 100


def test_run_offline_plays_the_whole_demo_without_crashing(play_native_module, capsys) -> None:
    seed, inputs, _live = play_native_module.boot_and_seed(ROOT, DEMO)
    play_native_module.run_offline(
        seed["state"], seed["scratch"], seed["jump_level_gate"], inputs, extra_ticks=200)
    out = capsys.readouterr().out
    assert "ticks=" in out
    assert "transitions=" in out


def test_run_cold_completes_the_gate_8_level_with_zero_input(play_native_module, capsys) -> None:
    """THE MILESTONE regression test: a genuine cold level-init, zero player
    input, reaches a real level-complete transition purely natively."""
    seed, _inputs, _live = play_native_module.boot_and_seed(ROOT, DEMO)
    play_native_module.run_cold(seed["state"], seed["jump_level_gate"], max_ticks=200)
    out = capsys.readouterr().out
    assert "COLD RUN COMPLETE" in out


def test_run_cold_verify_confirms_against_the_real_vm(play_native_module, capsys) -> None:
    """The independent proof: reset the real, unmodified VM to the same cold
    state and confirm it reaches the same level-complete conclusion.

    max_ticks budgets the frame loop AFTER the reset fires, but the reset
    itself only fires once the demo's own boot/seed replay naturally reaches
    the first game_state==0 sub-step (~frame 566 for this demo) -- so the
    budget needs enough headroom to cover that warm-up too, not just the
    ~57 gameplay ticks the level itself needs to complete.
    """
    play_native_module.run_cold_verify(ROOT, DEMO, max_ticks=700)
    out = capsys.readouterr().out
    assert "VM independently confirms: same cold start -> level complete" in out
