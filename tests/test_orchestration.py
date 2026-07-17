"""Verify the per-frame orchestration gate
(skyroads.handrecovered.orchestration.should_run_gameplay) -- the 1010:229D-22E9
decision between running the gameplay sub-step and exiting to a transition.

The full-path match (571/571 real E2E frames, including the game_state 3 -> exit
cases) is proven by the lockstep loop (tests/test_native_loop_lockstep.py); this
pins the branch logic directly.
"""
from __future__ import annotations

from skyroads.handrecovered.orchestration import (
    FRAME_CTR_GAMEPLAY_MAX,
    SETTLE_WINDOW_MAX,
    should_run_gameplay,
)


def test_settle_window_always_plays() -> None:
    # f456a in 1..0x2A -> gameplay regardless of game_state.
    for gs in (0, 1, 2, 3, 4, 5):
        assert should_run_gameplay(gs, f456a=1, frame_ctr=0x1000) is True
        assert should_run_gameplay(gs, f456a=SETTLE_WINDOW_MAX, frame_ctr=0x1000) is True


def test_states_1_and_2_exit() -> None:
    assert should_run_gameplay(1, f456a=0, frame_ctr=0) is False
    assert should_run_gameplay(2, f456a=0, frame_ctr=0) is False


def test_state_3_settled_exits_but_unsettled_plays() -> None:
    # game_state 3 with f456a != 0 (past the settle window) -> exit.
    assert should_run_gameplay(3, f456a=0x2B, frame_ctr=0) is False
    # game_state 3 with f456a == 0 and a fresh frame counter -> play.
    assert should_run_gameplay(3, f456a=0, frame_ctr=0) is True


def test_state_0_plays_until_frame_counter_cap() -> None:
    assert should_run_gameplay(0, f456a=0, frame_ctr=FRAME_CTR_GAMEPLAY_MAX - 1) is True
    assert should_run_gameplay(0, f456a=0, frame_ctr=FRAME_CTR_GAMEPLAY_MAX) is False


def test_f456a_above_settle_window_falls_through() -> None:
    # f456a > 0x2A is NOT the settle window -> the state/counter rules apply.
    assert should_run_gameplay(0, f456a=0x30, frame_ctr=0) is True        # gs 0, low ctr
    assert should_run_gameplay(0, f456a=0x30, frame_ctr=0x100) is False   # ctr past cap
