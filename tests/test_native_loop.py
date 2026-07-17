"""Unit tests for skyroads.native.loop -- the frame steppers composing
currently-recovered islands against a GameView. No real demo needed here
(synthetic states); the real-demo cross-check against the ASM oracle lives in
test_native_loop_integration.py.
"""
from __future__ import annotations

import pytest

from skyroads.bridge.dgroup_view import GameView
from skyroads.native.gaps import JumpGateGap, MovementPhysicsGap, VerticalVelocityGap
from skyroads.native.loop import native_gameplay_frame, native_menu_frame
from skyroads.native.state import NativeGameState
from skyroads.handrecovered.player import GRAVITY_HEIGHT_GATE


def _state_and_view() -> tuple[NativeGameState, GameView]:
    st = NativeGameState()
    return st, GameView(st)


def test_native_menu_frame_scroll_right_moves_scroll_pos() -> None:
    _, view = _state_and_view()
    view.ship_pos = 100
    view.entered = 0
    native_menu_frame(view, 0xA)  # ACTION_SCROLL_RIGHT
    assert view.ship_pos == 100 + 0x012F


def test_native_menu_frame_enter_level_select_latches() -> None:
    _, view = _state_and_view()
    native_menu_frame(view, 0xC)  # ACTION_ENTER_LEVEL_SELECT
    assert view.game_state == 2
    assert view.entered == 1


def test_native_menu_frame_never_raises() -> None:
    _, view = _state_and_view()
    for action in range(16):
        native_menu_frame(view, action)  # must not raise for any 4-bit action code


def test_native_gameplay_frame_advances_ship_pos_before_any_gap() -> None:
    st, view = _state_and_view()
    st.wb(0x0BD2, 0x80)  # hold "up" -- forward speed +1
    # default state (af2c=0, grounded=0) is outside the verified vvel envelope,
    # so this hits VerticalVelocityGap -- but ship_pos must already be committed.
    with pytest.raises(VerticalVelocityGap):
        native_gameplay_frame(view)
    assert view.ship_pos == 75  # advance_ship(0, +1) == 0 + 1*75


def test_native_gameplay_frame_raises_jump_gate_gap_when_jump_held() -> None:
    st, view = _state_and_view()
    st.wb(0x0BDB, 0x80)  # hold jump
    with pytest.raises(JumpGateGap):
        native_gameplay_frame(view)
    # forward motion is still committed even though the jump gate isn't recovered
    assert view.ship_pos == 0  # no forward key held, so no motion this frame


def test_native_gameplay_frame_raises_vertical_velocity_gap_when_grounded() -> None:
    _, view = _state_and_view()
    view.grounded = 1
    with pytest.raises(VerticalVelocityGap):
        native_gameplay_frame(view)


def test_native_gameplay_frame_raises_vertical_velocity_gap_below_gravity_gate() -> None:
    _, view = _state_and_view()
    view.grounded = 0
    view.af2c = GRAVITY_HEIGHT_GATE - 1
    with pytest.raises(VerticalVelocityGap):
        native_gameplay_frame(view)


def test_native_gameplay_frame_commits_vvel_inside_the_verified_envelope() -> None:
    _, view = _state_and_view()
    view.grounded = 0
    view.af2c = GRAVITY_HEIGHT_GATE
    view.gravity = 0xFF8D  # -115
    view.bounce = 0  # decay_bounce(0) == 0
    with pytest.raises(MovementPhysicsGap):
        native_gameplay_frame(view)
    assert view.bounce == 0xFF8D  # 0 (decayed) + gravity, matching update_vertical_velocity
