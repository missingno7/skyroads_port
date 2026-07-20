"""Authored gameplay-subsystem recovery evidence.

These functions operate through :class:`skyroads.bridge.dgroup_view.GameView`
and do not interpret CPU instructions:

* :func:`native_menu_frame` implements the level-select action dispatch;
* :func:`native_gameplay_substep` implements the observed gameplay substep;
* :func:`apply_level_init` initializes the corresponding level state;
* :class:`NativeGameplayDriver` composes that gameplay subsystem across its
  transition boundaries.

Typed exceptions in :mod:`skyroads.native.gaps` mark unsupported transitions.
These candidates are not currently registered in ``skyroads.execution`` and
therefore are not an alternate player or selectable composition. They remain
focused evidence until a catalog entry assigns stable identities and a
verification policy. Whole-program coverage and release readiness remain
properties of the Execution Atlas, catalog, and immutable execution plan.
"""
from __future__ import annotations

from typing import NamedTuple

from skyroads.bridge.dgroup_view import GameView
from skyroads.native.classify import classify_ship
from skyroads.native.collision import make_visible, ship_fell_off
from skyroads.native.gaps import (
    FallDeathTransition,
    LevelEndTransition,
    MovementPhysicsGap,
)
from skyroads.handrecovered.collision_response import (
    af1c_contact_fixup,
    lateral_wall_bump,
    resolve_landing,
    resolve_lateral_crash,
    vertical_center_nudge,
)
from skyroads.handrecovered.dynamics import (
    JumpScratch,
    gate_bounce_decay,
    step_jump_steer_gravity,
)
from skyroads.handrecovered.menu import MenuState, dispatch_menu_action
from skyroads.handrecovered.movement import resolve_move
from skyroads.handrecovered.orchestration import should_run_gameplay
from skyroads.handrecovered.physics import compute_movement_targets
from skyroads.handrecovered.player import (
    RespawnState,
    advance_ship,
    level_gravity,
)
from skyroads.handrecovered.progression import step_level_progression


class GameplayScratch(NamedTuple):
    """The session-persistent gameplay-handler state carried ACROSS sub-steps
    (the `ss:[bp-N]` locals of the one continuous `1010:2280-2B0B` handler that
    are read before they are written each sub-step -- see
    docs/history/skyroads/run_status.md). Not derivable from DGROUP.

    * ``jump`` -- the :class:`~skyroads.handrecovered.dynamics.JumpScratch`
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


def _emit_sfx(view: GameView, sfx, sfx_id: int) -> None:
    """The `1010:03C2` entry side effect + callback: stamp `[AF38] = [1600]`
    (the `0476` busy window's reference) and hand the id to the caller's
    player. With no callback installed this is a strict no-op (including the
    stamp), so lockstep verification against the VM is unaffected; the ASM
    itself stamps on every call."""
    if sfx is not None:
        view.unknown_af38 = view.elapsed_ticks
        sfx(sfx_id)


def _sfx_busy(view: GameView) -> bool:
    """`1010:0476` (SB path): non-zero while `[1600] < [AF38] + 8` -- an
    8-tick debounce since the last `03C2` trigger of ANY id."""
    return (view.elapsed_ticks & 0xFFFF) < ((view.unknown_af38 + 8) & 0xFFFF)


def native_gameplay_substep(
    view: GameView, scratch: GameplayScratch, *, allow_unmodelled_effect: bool = False,
    sfx=None,
) -> GameplayScratch:
    """Run ONE gameplay sub-step (`1010:2324-2AE2`) on ``view`` in place, the
    composition of the recovered semantic functions in ASM spine order, and return the
    new :class:`GameplayScratch` to carry to the next sub-step.

    Verified against the oracle: over real
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
    is a boundary (raises :class:`~skyroads.native.gaps.FallDeathTransition`).

    The ``1DFA`` effect (`25AC-25D6`, ~0.7% of real sub-steps, only ever seen
    airborne past ``af2c=0x3700``) rewrites ``lateral_accel`` in a way this
    module doesn't model -- by default this raises
    :class:`~skyroads.native.gaps.MovementPhysicsGap` (the fail-loud, verified
    contract every test in this session relies on). Pass
    ``allow_unmodelled_effect=True`` to instead CONTINUE using
    ``step_jump_steer_gravity``'s own (verified, non-effect) ``lateral_accel`` --
    an explicit, documented approximation for exactly this one rare sub-step,
    for callers (like :class:`NativeGameplayDriver`) that need to keep running
    rather than stop on it. Never the default.
    """
    # The frame runs the gameplay sub-step iff the recovered orchestration gate
    # `should_run_gameplay` (1010:229D-22E9) says so -- this is the VM's OWN
    # decision and nothing more. Crucially its FIRST clause keeps the frame in
    # the handler while the "settle window" counter `[456A]` (grounded) is in
    # 1..0x2A REGARDLESS of game_state, so the crash/finish DISPLAY frames
    # (game_state 1 / 2, ship frozen, the EXPLOSION sprite animating via
    # si=grounded//3 as grounded ramps 2->42) run through this frozen-ship path
    # too. An extra `game_state in {0,3}` guard would exit on the first crash
    # frame and incorrectly skip the explosion settle window.
    if not should_run_gameplay(view.game_state, view.grounded, view.frame_ctr):
        raise LevelEndTransition(
            f"transition: game_state={view.game_state} f456a={view.grounded} "
            f"frame_ctr={view.frame_ctr}")
    moving = view.game_state == 0

    rw = view.rw
    visible = make_visible(rw)

    # Fall-off-the-road death check (23CA-2421): fires past the [41C0] lateral
    # threshold while game_state == 0. (Verified no false positives; a real fall
    # was not exercised by the replays -- see FallDeathTransition.)
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
    # The decay branch's landing SFX (2470-249E): plays 03C2(1) when the decay
    # path is reached (af2c off-target, no 5496-zero, bounce above the kill
    # threshold, not grounded), game_state == 0, bounce is downward, and the
    # `0476` 8-tick debounce window is clear.
    if sfx is not None:
        _b = view.bounce
        _bs = _b - 0x10000 if _b & 0x8000 else _b
        _thr = ((0x104 * (view.jump_level_gate & 0xFFFF)) & 0xFFFF) // 8
        if ((view.af2c & 0xFFFF) != (scratch.tgt_af2c & 0xFFFF)
                and not ((view.unknown_5496 & 0xFFFF) != 0
                         and (scratch.bp24 & 0xFFFF) < 2)
                and abs(_bs) >= _thr
                and view.grounded == 0 and view.game_state == 0
                and _bs < 0 and not _sfx_busy(view)):
            _emit_sfx(view, sfx, 1)                  # bounce landing: 03C2(1)
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
    if dyn.hit_effect_path and not allow_unmodelled_effect:
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
    if new_af1c != view.af1c or tgt_lateral != tgt.tgt_lateral:
        _emit_sfx(view, sfx, 2)                      # wall bump: 03C2(2)
    view.af1c = new_af1c
    grounded_before = view.grounded
    crash = resolve_lateral_crash(
        view.lateral, tgt_lateral, view.ship_pos, view.grounded, view.game_state)
    # The crash handler's SFX (27A3-2828): a real flagged crash calls 03C2(0)
    # at 27E7 -- but `crashed`
    # (any lateral mismatch, ship_pos always resets to 0) is NOT the same as
    # "flagged" (`resolve_lateral_crash`'s own `past_gate and f456a==0`
    # branch, i.e. ds:[54AC] was already past CRASH_MILESTONE_POS AND
    # grounded was still 0) -- using `crashed` alone fired the thud on EVERY
    # wall hit, including a slow/early crash below the milestone position,
    # which the real VM plays silently. Detect "flagged" the same way the
    # ASM does: grounded went 0 -> nonzero this call. A lateral block that
    # does NOT flag (pre-milestone or already flagged) instead runs the 2800
    # distance check and thumps 03C2(2) when lateral has outrun
    # (tgt_lateral - ship_pos).
    if (view.lateral & 0xFFFFFFFF) != (tgt_lateral & 0xFFFFFFFF):
        if grounded_before == 0 and crash.f456a != 0:
            _emit_sfx(view, sfx, 0)                  # crash thud: 03C2(0)
        elif ((view.lateral & 0xFFFFFFFF)
              > ((tgt_lateral - view.ship_pos) & 0xFFFFFFFF)):
            _emit_sfx(view, sfx, 2)                  # repeat thump: 03C2(2)
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

    # If this step ended the level / triggered a transition, stop: the
    # transition (respawn / menu / level load) is a separate subsystem. Mirror
    # the entry gate exactly (should_run_gameplay alone) so we stop only when
    # the VM leaves the handler -- INCLUDING running out the settle window
    # (grounded ramps past 0x2A after the explosion animation completes).
    if not should_run_gameplay(view.game_state, view.grounded, view.frame_ctr):
        raise LevelEndTransition(
            f"step ended in a transition: game_state={view.game_state} "
            f"f456a={view.grounded} frame_ctr={view.frame_ctr}")

    return GameplayScratch(
        jump=jump, bp12=land.gameplay_active, bp14=cls.class_skip,
        bp24=bp24, tgt_af2c=new_tgt_af2c)


#: The three HUD gauge "last-drawn" caches (speed/oxygen/fuel), zeroed by the
#: respawn/level-init flow at `1010:2B62-2B68` so the freshly-drawn (empty)
#: dashboard gets re-FILLED from scratch by the next `12F8` delta pass. Without
#: this, a level entered with stale caches (== the new value) draws nothing and
#: the gauges show only their empty outlines -- see `skyroads/native/hud.py`.
_GAUGE_CACHES = (0x41BE, 0x456C, 0x960C, 0x455C)  # speed, oxygen, fuel [12F8], progress-bar column


def apply_level_init(view: GameView, jump_level_gate: int) -> GameplayScratch:
    """Apply the per-level init (`1010:1FD9-206C`) to ``view`` in place and
    return a fresh :class:`GameplayScratch`: the transition primitive a driver
    runs at the start of each level / after a respawn. Writes the fixed reset
    fields (:class:`~skyroads.handrecovered.player.RespawnState`) plus the per-level
    gravity derived from ``jump_level_gate``, clears ``ds:[516E]``, and zeroes
    the HUD gauge caches (the `2B62-2B68` reset in the same respawn flow) so the
    gauges re-fill on the first frame of the new level.

    (The joystick-recenter side call at 1FDF, for control device 2, is not
    modelled -- keyboard play doesn't take it.)
    """
    r = RespawnState()
    view.lateral = (r.lateral_hi << 16) | r.lateral_lo
    view.af1c = r.vert_af1c
    view.af2c = r.vert_af2c
    view.unknown_5496 = r.unknown_5496
    view.lateral_accel = r.lateral_accel
    view.bounce = r.vvel
    view.ship_pos = (r.ship_pos_hi << 16) | r.ship_pos_lo
    view.timer_a = r.level_timer_a
    view.timer_b = r.level_timer_b
    view.game_state = r.game_state
    view.frame_ctr = r.frame_ctr
    view.grounded = r.unknown_456a
    view.gravity = level_gravity(jump_level_gate)
    for cache_off in _GAUGE_CACHES:              # 2B62-2B68: HUD gauge caches -> 0
        view._backend.ww(cache_off, 0)
    return GameplayScratch(bp12=1)


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


class TickOutcome(NamedTuple):
    """What one :meth:`NativeGameplayDriver.tick` call did."""
    transitioned: bool   # True if this tick crossed a boundary and re-inited
    reason: str          # "" for a normal sub-step; else the transition's cause
    #: Coarse transition classification, "" for a normal sub-step. One of
    #: "crash" / "finish" / "timeout_fuel" / "timeout_oxygen" / "fall". See
    #: :func:`_classify_transition` -- ``game_state`` alone under-classifies a
    #: crash from an already-landed ship (VM-verified: it stays 3, only
    #: ``grounded`` ramps), so this also consults ``view.grounded``.
    kind: str = ""
    #: ``view.game_state`` at the moment of the transition (before any reset).
    game_state: int = 0


def _classify_transition(view: GameView, exc: Exception) -> str:
    """Map a raised :class:`~skyroads.native.gaps.LevelEndTransition` /
    :class:`~skyroads.native.gaps.FallDeathTransition` to a coarse,
    caller-facing ``TickOutcome.kind``.

    A wall crash that happens after the ship has already resumed (``game_state``
    already ``3`` from a prior landing) does NOT flip ``game_state`` to ``1``
    -- ``resolve_lateral_crash`` only sets it when ``game_state`` was ``0``.
    The only observable signal in that case is ``grounded`` (``[456A]``)
    going nonzero and ramping toward :data:`~skyroads.handrecovered.
    orchestration.SETTLE_WINDOW_MAX`, so a crash from state 3 is detected via
    ``grounded != 0`` instead of ``game_state``.
    """
    if isinstance(exc, FallDeathTransition):
        return "fall"
    gs = view.game_state & 0xFFFF
    if gs == 1:
        return "crash"
    if gs == 2:
        return "finish"
    if gs == 4:
        return "timeout_fuel"
    if gs == 5:
        return "timeout_oxygen"
    if gs == 3 and (view.grounded & 0xFFFF) != 0:
        return "crash"
    return ""


class NativeGameplayDriver:
    """Drive the authored gameplay subsystem without interpreting instructions.

    A single sub-step is a focused, verified implementation (see that
    function's docstring). This class composes it with
    :func:`apply_level_init` at every supported boundary
    (level-complete, wall-crash, timer-expired, fall) instead of surfacing the
    boundary as an exception to the caller.

    Two things are deliberately NOT modelled byte-exact against the VM here
    (both out-of-scope for gameplay decision-making, not silent gaps):

    * the post-explosion HOLD's exact multi-frame duration. The settle
      window's EXPLOSION animation (grounded ramps, ``si`` cycles the
      explosion sprites) IS run natively now -- ``native_gameplay_substep``
      plays those frames (game_state 1/2 while grounded is in 1..0x2A) and only
      raises the transition once grounded rolls past 0x2A. What this driver
      still doesn't own byte-exact is the ~60-frame frozen HOLD the VM shows
      after that before the reset -- by default (``auto_respawn=True``) it
      respawns IMMEDIATELY on the boundary; pass ``auto_respawn=False`` for a
      presentation layer that wants to hold the frozen frame first (see
      ``respawn()``);
    * the rare ``1DFA`` effect sub-step (~0.7% of real sub-steps) -- handled
      via :func:`native_gameplay_substep`'s documented
      ``allow_unmodelled_effect`` fallback rather than stopping the loop.

    ``jump_level_gate`` (``ds:[4562]``) is a per-level constant. A caller
    supplies it directly or reads it from the selected bootstrap state.
    """

    def __init__(self, view: GameView, jump_level_gate: int,
                scratch: "GameplayScratch | None" = None, on_sfx=None,
                auto_respawn: bool = True):
        self.view = view
        self.jump_level_gate = jump_level_gate
        self.scratch = scratch if scratch is not None else apply_level_init(view, jump_level_gate)
        self.ticks = 0
        self.transitions = 0
        #: optional callable(sfx_id) -- receives the `03C2` triggers the sim
        #: fires (0 touch-down / 1 bounce landing / 2 bump+crash); see
        #: `skyroads.native.sfx` for the id map and the SFX.SND bank loader.
        self.on_sfx = on_sfx
        #: False: defer the post-transition ``apply_level_init`` to an
        #: explicit :meth:`respawn` call instead of running it inside
        #: :meth:`tick`. Default True preserves the original (headless/
        #: verify) behaviour every existing caller relies on.
        self.auto_respawn = auto_respawn
        #: the held :class:`TickOutcome` while a transition is awaiting an
        #: explicit :meth:`respawn` (``auto_respawn=False`` only); ``tick()``
        #: keeps returning this SAME outcome (without re-running the
        #: sub-step, which would just re-raise) until ``respawn()`` clears it.
        self.pending: "TickOutcome | None" = None

    def tick(self) -> TickOutcome:
        """Advance one gameplay sub-step, transparently driving through any
        transition boundary. Call once per input frame; set the view's input
        fields (``steer``/``jump``/``speed``/the key row/``elapsed_ticks``)
        before calling to drive with real input."""
        self.ticks += 1
        if self.pending is not None:
            return self.pending
        try:
            self.scratch = native_gameplay_substep(
                self.view, self.scratch, allow_unmodelled_effect=True,
                sfx=self.on_sfx)
            return TickOutcome(False, "", "", self.view.game_state)
        except (LevelEndTransition, FallDeathTransition) as exc:
            self.transitions += 1
            outcome = TickOutcome(True, str(exc), _classify_transition(self.view, exc),
                                  self.view.game_state)
            if self.auto_respawn:
                self.scratch = apply_level_init(self.view, self.jump_level_gate)
            else:
                self.pending = outcome
            return outcome

    def respawn(self) -> None:
        """Clear a transition held by ``auto_respawn=False`` and apply the
        per-level init, exactly what ``tick()`` does automatically when
        ``auto_respawn=True``. A no-op if no transition is pending."""
        if self.pending is None:
            return
        self.scratch = apply_level_init(self.view, self.jump_level_gate)
        self.pending = None
