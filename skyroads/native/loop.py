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

from typing import NamedTuple

from skyroads.bridge.dgroup_view import GameView
from skyroads.native.classify import classify_ship
from skyroads.native.collision import make_visible, ship_fell_off
from skyroads.native.gaps import (
    FallDeathTransition,
    JumpGateGap,
    LevelEndTransition,
    MovementPhysicsGap,
    VerticalVelocityGap,
)
from skyroads.recovered.collision_response import (
    af1c_contact_fixup,
    lateral_wall_bump,
    resolve_landing,
    resolve_lateral_crash,
    vertical_center_nudge,
)
from skyroads.recovered.controls import decode_keyboard
from skyroads.recovered.dynamics import (
    JumpScratch,
    gate_bounce_decay,
    step_jump_steer_gravity,
)
from skyroads.recovered.menu import MenuState, dispatch_menu_action
from skyroads.recovered.movement import resolve_move
from skyroads.recovered.physics import compute_movement_targets
from skyroads.recovered.player import (
    GRAVITY_HEIGHT_GATE,
    advance_ship,
    decay_bounce,
    update_vertical_velocity,
)
from skyroads.recovered.progression import step_level_progression


class GameplayScratch(NamedTuple):
    """The session-persistent gameplay-handler state carried ACROSS sub-steps
    (the `ss:[bp-N]` locals of the one continuous `1010:2280-2B0B` handler that
    are read before they are written each sub-step -- see
    docs/skyroads/run_status.md). Not derivable from DGROUP.

    * ``jump`` -- the :class:`~skyroads.recovered.dynamics.JumpScratch`
      (``bp-6``/``bp-8``/``bp-10``): effect + jump latches and the jump-start height.
    * ``bp12`` -- the gameplay-active flag (set by the landing check, read by the
      classification's reduction gate next sub-step).
    * ``bp14`` -- the persisted classification "skip" flag (kept when ``bp12==0``).
    * ``bp24`` -- the vertical scan's last cell index (read by the decay gate).
    * ``tgt_af2c`` -- the previous sub-step's vertical target (``bp-28``), read by
      the decay gate before this sub-step recomputes it.
    """
    jump: JumpScratch = JumpScratch()
    bp12: int = 0
    bp14: int = 0
    bp24: int = 0
    tgt_af2c: int = 0


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


def native_gameplay_substep(view: GameView, scratch: GameplayScratch) -> GameplayScratch:
    """Run ONE gameplay sub-step (`1010:2324-2AE2`) on ``view`` in place, the
    full assembly of every recovered island in ASM spine order, and return the
    new :class:`GameplayScratch` to carry to the next sub-step.

    This is the convergence of the recovered leaves into a running native
    stepper (the pre2_port model). Verified against the VM: over real
    ``game_state == 0`` sub-steps, the full gameplay DGROUP this composes --
    INCLUDING the forward advance of ``ship_pos``/``lateral`` -- matches the
    oracle 230/232 (`tests/test_native_substep.py`). The forward motion is the
    classification's ``dispatch_menu_action`` (`1B49`) call: action ``0xA``
    advances ``ship_pos`` by ``SCROLL_STEP`` (`0x12F`) when ``[456A] == 0``
    (`1010:1BDC`). The residual misses are documented edge cases (a rare
    ``[AF2E]`` landing adjustment).

    Both the active-gameplay path (``game_state == 0``) and the frozen-ship path
    (``game_state != 0``: forward motion / steering / jump are skipped at
    ``24BA -> 25AC``) are handled. The out-of-bounds death check (`23CA-2421`)
    and the ``1DFA`` effect are not, so they raise
    :class:`~skyroads.native.gaps.SkyroadsGap`.
    """
    # In-level game states: 0 (active) and 3 (resume-frozen). Anything else is a
    # level boundary (2 level-select, 4/5 timer-expired, 1 crash) the gameplay
    # stepper doesn't own.
    if view.game_state not in (0, 3):
        raise LevelEndTransition(
            f"entered with game_state={view.game_state} (not an in-level state)")
    moving = view.game_state == 0

    rw = view.rw
    visible = make_visible(rw)

    # Fall-off-the-road death check (23CA-2421): fires past the [41C0] lateral
    # threshold while game_state == 0. (Verified no false positives; a real fall
    # was not exercised by the demos -- see FallDeathTransition.)
    if moving:
        thr = (((view.f41c0 // 0x10) + 0xFFFF8000) & 0xFFFFFFFF)
        if view.lateral >= thr and ship_fell_off(rw, view.lateral, view.af1c, view.af2c):
            raise FallDeathTransition(
                f"ship fell off the road at lateral={view.lateral:#x}")

    # 1. perspective classification (2324-23BF) -> class flags. Its reduction
    #    path makes a live dispatch_menu_action call (2385-238B) whose effect,
    #    during gameplay, IS the forward motion: action 0xA advances ship_pos by
    #    SCROLL_STEP (0x12F) when [456A]==0 (the 1B49 body at 1BDC). So apply it.
    cls = classify_ship(rw, view.lateral, view.af1c, view.af2c,
                        scratch.bp12, scratch.bp14)
    if cls.calls_1b49:
        ms = dispatch_menu_action(cls.reduced_word, MenuState(
            view.game_state, view.grounded, view.ship_pos, view.timer_a, view.timer_b))
        view.game_state = ms.game_state
        view.grounded = ms.entered
        view.ship_pos = ms.scroll_pos
        view.timer_a = ms.timer_a
        view.timer_b = ms.timer_b
    # (the out-of-bounds death check 23CA-2421 falls through while game_state==0)

    # 2. bounce-decay gate (2421-24BA) -- uses the PRIOR sub-step's tgt_af2c/bp24
    view.bounce = gate_bounce_decay(
        view.bounce, view.af2c, scratch.tgt_af2c, view.unknown_5496,
        scratch.bp24, view.jump_level_gate, view.grounded)

    # 3. forward motion (24C4-2528) -- ONLY on the moving path (24BA gate).
    if moving:
        view.ship_pos = advance_ship(view.ship_pos, view.speed)

    # 4. steering + jump latch + gravity (252B-2635); the frozen path (moving
    #    False) skips steering/jump and runs only effect + gravity (25AC-2635).
    dyn = step_jump_steer_gravity(
        scratch.jump, cls.class_skip, cls.class_zero, view.bounce,
        view.lateral_accel, view.af2c, view.steer, view.jump,
        view.jump_level_gate, view.grounded, view.gravity, view.effect_gate,
        moving=moving)
    if dyn.hit_effect_path:
        raise MovementPhysicsGap(
            "the 25AC-25D6 effect path fired (a 1DFA call) -- lateral_accel is "
            "not modelled for this sub-step")
    view.bounce = dyn.bounce
    view.lateral_accel = dyn.lateral_accel
    jump = dyn.scratch

    # 5. movement targets + swept collision (2635-26E9)
    tgt = compute_movement_targets(
        view.ship_pos, view.lateral, view.af1c, view.af2c, view.bounce,
        view.lateral_accel, view.unknown_5496)
    new_tgt_af2c = tgt.tgt_af2c
    lat, af1c, af2c = resolve_move(
        view.lateral, view.af1c, view.af2c,
        tgt.tgt_lateral, tgt.tgt_af1c, tgt.tgt_af2c, visible)
    view.lateral, view.af1c, view.af2c = lat, af1c, af2c

    # 6. collision response (26EC-2A24), in spine order
    new_af1c, tgt_lateral = lateral_wall_bump(
        visible, view.lateral, tgt.tgt_lateral, view.af1c, tgt.tgt_af1c, view.af2c)
    view.af1c = new_af1c
    crash = resolve_lateral_crash(
        view.lateral, tgt_lateral, view.ship_pos, view.grounded, view.game_state)
    view.ship_pos, view.grounded, view.game_state = (
        crash.ship_pos, crash.f456a, crash.game_state)
    accel, c5496, pos = af1c_contact_fixup(
        view.af1c, tgt.tgt_af1c, view.unknown_5496, view.lateral_accel, view.ship_pos)
    view.lateral_accel, view.unknown_5496, view.ship_pos = accel, c5496, pos
    land = resolve_landing(
        jump, tgt.tgt_af2c, view.af2c, view.bounce, view.unknown_af2e,
        view.unknown_af30, view.unknown_455a, view.ship_pos)
    jump = land.scratch
    view.unknown_455a, view.ship_pos = land.f455a, land.ship_pos
    bp24 = scratch.bp24
    if land.landed:
        view.unknown_5496 = vertical_center_nudge(
            visible, view.lateral, view.af1c, view.af2c, view.unknown_5496)
        view.unknown_af2e = 0
        view.unknown_af30 = 0
        bp24 = _vertical_scan_cell(visible, view.lateral, view.af1c, view.af2c)

    # af2c floor clamp (2A24-2A2F): if it wrapped past 0x7FFF (went "negative"
    # under gravity), reset to 0 -- between the collision tail and progression.
    if view.af2c > 0x7FFF:
        view.af2c = 0

    # 7. level progression (2A35-2AE2)
    prog = step_level_progression(
        view.game_state, view.af2c, view.timer_a, view.timer_b,
        view.timer_a_param, view.timer_b_param, view.ship_pos, view.frame_ctr)
    view.game_state = prog.game_state
    view.timer_a = prog.level_timer_a
    view.timer_b = prog.level_timer_b
    view.frame_ctr = prog.frame_ctr
    if view.grounded != 0:                       # 2AEA frame-end 456A bump
        view.grounded = view.grounded + 1

    # If this step ended the level (game_state left the in-level set), stop: the
    # transition (level load / respawn) is a separate subsystem.
    if view.game_state not in (0, 3):
        raise LevelEndTransition(
            f"game_state became {view.game_state} this step (level ended)")

    return GameplayScratch(
        jump=jump, bp12=land.gameplay_active, bp14=cls.class_skip,
        bp24=bp24, tgt_af2c=new_tgt_af2c)


def _vertical_scan_cell(visible, lateral: int, af1c: int, af2c: int) -> int:
    """The scan cell index (``bp-24``) `vertical_center_nudge` settles on -- the
    first unblocked cell above, else below (1010:29AB/29F4). Session state the
    next sub-step's decay gate reads."""
    screen_y = (af2c - 1) & 0xFFFF
    for k in range(1, 15):
        if visible(lateral, (af1c + (k << 7)) & 0xFFFF, screen_y) == 0:
            return k
    for k in range(1, 15):
        if visible(lateral, (af1c - (k << 7)) & 0xFFFF, screen_y) == 0:
            return k
    return 0
