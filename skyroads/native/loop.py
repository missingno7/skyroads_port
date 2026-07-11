"""Native (VM-less) per-frame steppers -- composing the currently-recovered
islands (skyroads/recovered/*) against a ``GameView`` (skyroads/bridge), in
ASM spine order, the way pre2_port's ``pre2/native/loop.py::native_gameplay_frame``
composes its own recovered subsystems.

Two steppers today, matching how much of the game is actually recovered
(see docs/skyroads/vmless_roadmap.md's coverage table):

* :func:`native_menu_frame` -- the level-select/menu action dispatch
  (``1010:1B49``). Complete: every state transition ``dispatch_menu_action``
  needs is recovered, so this stepper never raises.
* :func:`native_gameplay_frame` -- the per-frame gameplay update. Commits
  ONLY forward motion (``advance_ship``, real-demo-proven: 0 mismatches over
  8 real gameplay samples in the 2026-07-11 integration proof, see
  run_status.md) and then raises a typed gap (skyroads.native.gaps) the
  instant it reaches something not yet SAFE to compute -- not just "not
  recovered", but proven wrong when tried unconditionally (the
  ``decay_bounce``/``update_vertical_velocity`` composition; see
  :class:`~skyroads.native.gaps.VerticalVelocityGap`), the jump latch, or the
  movement step. Note the movement MATH is now complete and proven (the
  ``compute_movement_targets`` -> ``resolve_move`` pipeline, 300/300 vs VM,
  tests/test_native_movement_pipeline.py) -- the remaining
  :class:`~skyroads.native.gaps.MovementPhysicsGap` is specifically its
  ``lateral_accel`` input (stateful steering momentum), not the math. This is
  the honest current recovery ceiling, not a limitation of this module --
  every real gameplay frame hits one of these gaps today.

Nothing here decides WHEN each stepper runs (the ``ds:[456E]`` game-state
dispatch that sequences intro -> menu -> gameplay -> death is itself an
unrecovered gap, see vmless_roadmap.md item 2) -- callers already know which
mode they're in, exactly like pre2_port's per-mode ``native_*`` functions.
"""
from __future__ import annotations

from skyroads.bridge.dgroup_view import GameView
from skyroads.native.gaps import JumpGateGap, MovementPhysicsGap, VerticalVelocityGap
from skyroads.recovered.controls import decode_keyboard
from skyroads.recovered.menu import MenuState, dispatch_menu_action
from skyroads.recovered.player import (
    GRAVITY_HEIGHT_GATE,
    advance_ship,
    decay_bounce,
    update_vertical_velocity,
)


def native_menu_frame(view: GameView, action: int) -> None:
    """Apply one level-select action (``1010:1B49``) to ``view`` in place."""
    before = MenuState(
        game_state=view.game_state,
        entered=view.entered,
        scroll_pos=view.ship_pos,
        timer_a=view.timer_a,
        timer_b=view.timer_b,
    )
    after = dispatch_menu_action(action, before)
    view.game_state = after.game_state
    view.entered = after.entered
    view.ship_pos = after.scroll_pos
    view.timer_a = after.timer_a
    view.timer_b = after.timer_b


def native_gameplay_frame(view: GameView) -> None:
    """Advance one gameplay frame (``1010:24A1`` onward) on ``view`` in place.

    Raises :class:`~skyroads.native.gaps.SkyroadsGap` (loudly, before
    committing anything the raised gap's own logic would need) the moment it
    reaches something not safe to compute yet -- see the module docstring.
    Once the movement-target gap closes, the next call this makes is
    ``resolve_move(..., visible=skyroads.native.collision.make_visible(rw))``
    for some DGROUP word-reader ``rw`` (a ``NativeGameState.rw`` or a VM's
    ``mem.rw`` bound to ``ds``).
    """
    controls = decode_keyboard(view.key_row)

    # Forward motion never depends on anything below -- real-demo-proven.
    new_ship_pos = advance_ship(view.ship_pos, controls.speed)
    view.ship_pos = new_ship_pos

    if controls.jump:
        raise JumpGateGap(
            "jump requested (controls.jump=1) but the impulse latch "
            "(ss:[bp-8]/[bp-18], guarding 1010:2582) is not recovered"
        )
    # decay_bounce + update_vertical_velocity are individually ASM_MATCHED,
    # but composing them UNCONDITIONALLY every frame is proven wrong outside
    # the one directly-verified envelope (airborne, af2c already at/above the
    # gravity gate -- player.py's 238/238 deaths-demo match). Below the gate,
    # real E2E-demo data shows the block gets skipped for multiple frames by
    # something this session hasn't recovered (most likely jump-in-flight
    # state persisting past the frame the key was released) -- see
    # VerticalVelocityGap. Don't guess there; only commit the proven case.
    if view.grounded != 0 or view.af2c < GRAVITY_HEIGHT_GATE:
        raise VerticalVelocityGap(
            f"af2c={view.af2c:#06x} grounded={view.grounded} -- outside the "
            "one directly-verified envelope (airborne, af2c >= "
            "GRAVITY_HEIGHT_GATE); decay_bounce/update_vertical_velocity are "
            "not safe to compose unconditionally here (see the class docstring)"
        )
    vvel = update_vertical_velocity(
        decay_bounce(view.bounce), jumped=False, af2c=view.af2c,
        gravity=view.gravity, grounded=False,
    )
    view.bounce = vvel

    # The movement pipeline itself is complete and proven (compute_movement_
    # targets -> resolve_move -> collision.make_visible, 300/300 vs VM; see
    # tests/test_native_movement_pipeline.py). It is NOT called here only
    # because one of its inputs -- lateral_accel (ds:[4568]) -- is stateful
    # steering momentum this frame cannot derive from frame-start state
    # (updated mid-frame at 1010:2568, jump-latch-gated). See MovementPhysicsGap.
    raise MovementPhysicsGap(
        f"movement math is recovered (pipeline proven 300/300), but "
        f"lateral_accel (ds:[4568]={view.lateral_accel:#06x}) is stateful "
        f"steering momentum not yet derivable from frame-start state -- "
        f"see MovementPhysicsGap / the 1010:2534-256D steering block"
    )
