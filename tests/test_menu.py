"""Verify the recovered level-select action dispatcher
(skyroads.handrecovered.menu) against real ASM I/O captured over the E2E replay.

318/318 real 1010:1B49 calls matched byte-exact across every action code the
replay exercises (0, 1, 3 -- all no-op/default -> clamp-only; 0xA -- scroll
right; 0xC -- enter level-select). Actions 2 (scroll left) and 9 (confirm) are
transcribed from the same disassembly pattern but not exercised by any replay
-- see the ASM_MATCHED caveat in skyroads/handrecovered/menu.py.
"""
from __future__ import annotations

import json
from pathlib import Path

from skyroads.handrecovered.menu import (
    ACTION_CONFIRM, ACTION_ENTER_LEVEL_SELECT, ACTION_SCROLL_LEFT,
    ACTION_SCROLL_RIGHT, CONFIRM_TIMER_RESET, SCROLL_STEP, MenuState,
    dispatch_menu_action,
)
from skyroads.handrecovered.player import LEVEL_END

_CASES = json.loads((Path(__file__).parent / "fixtures" / "menu_dispatch_trace.json").read_text())


def _s32(lo: int, hi: int) -> int:
    v = lo | (hi << 16)
    return v - 0x100000000 if v & 0x80000000 else v


def _state(fields: dict) -> MenuState:
    return MenuState(fields["456E"], fields["456A"],
                     _s32(fields["54AC"], fields["54AE"]),
                     fields["5494"], fields["B13C"])


def test_dispatch_menu_action_matches_asm() -> None:
    assert _CASES, "fixture empty"
    for case in _CASES:
        pre = _state(case["pre"])
        got = dispatch_menu_action(case["action"], pre)
        expected = _state(case["post"])
        assert got == expected, case


def test_dispatch_menu_action_exercises_scroll_and_enter() -> None:
    actions = {c["action"] for c in _CASES}
    assert 0xA in actions, "fixture should include a scroll-right sample"
    assert 0xC in actions, "fixture should include an enter-level-select sample"


def test_dispatch_menu_action_scroll_clamps_to_road_ends() -> None:
    state = MenuState(game_state=0, entered=0, scroll_pos=SCROLL_STEP - 1,
                      timer_a=0, timer_b=0)
    assert dispatch_menu_action(ACTION_SCROLL_LEFT, state).scroll_pos == 0
    state = MenuState(game_state=0, entered=0, scroll_pos=LEVEL_END - SCROLL_STEP + 1,
                      timer_a=0, timer_b=0)
    assert dispatch_menu_action(ACTION_SCROLL_RIGHT, state).scroll_pos == LEVEL_END


def test_dispatch_menu_action_scroll_guarded_by_entered() -> None:
    state = MenuState(game_state=0, entered=1, scroll_pos=1000, timer_a=0, timer_b=0)
    assert dispatch_menu_action(ACTION_SCROLL_RIGHT, state).scroll_pos == 1000
    assert dispatch_menu_action(ACTION_SCROLL_LEFT, state).scroll_pos == 1000


def test_dispatch_menu_action_enter_latches_once() -> None:
    state = MenuState(game_state=0, entered=0, scroll_pos=0, timer_a=0, timer_b=0)
    got = dispatch_menu_action(ACTION_ENTER_LEVEL_SELECT, state)
    assert got.game_state == 2 and got.entered == 1
    got2 = dispatch_menu_action(ACTION_ENTER_LEVEL_SELECT, got)
    assert got2.entered == 1  # stays latched


def test_dispatch_menu_action_confirm_resets_timers() -> None:
    state = MenuState(game_state=0, entered=1, scroll_pos=0, timer_a=100, timer_b=0x7530)
    got = dispatch_menu_action(ACTION_CONFIRM, state)
    assert got.timer_a == CONFIRM_TIMER_RESET and got.timer_b == CONFIRM_TIMER_RESET
    # guarded by game_state==0
    state2 = state._replace(game_state=3)
    got2 = dispatch_menu_action(ACTION_CONFIRM, state2)
    assert got2.timer_a == 100 and got2.timer_b == 0x7530
