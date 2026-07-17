"""Typed gaps the native frame steppers raise instead of guessing.

Mirrors pre2_port's ``pre2/gaps.py``: a gap is raised LOUDLY the instant the
stepper needs a routine that is not yet recovered, rather than silently
skipping it or approximating its effect. A silent fallback would hide missing
recovery work and could quietly desync native state from the real game (see
the "fail-fast over guessed fallback" rule dos_re's methodology docs use
throughout). Pure (no dos_re/cpu/mem imports) so native/loop.py's import
closure stays VM-free.
"""
from __future__ import annotations


class SkyroadsGap(RuntimeError):
    """The native stepper reached something not yet recovered."""


class LevelEndTransition(SkyroadsGap):
    """The level ended: ``game_state`` left the in-level set ``{0, 3}`` (active /
    resume-frozen) for a transition/post-level state -- ``2`` (level-select, the
    ship reached the end and `dispatch_menu_action` action 0xC set it), ``4``
    (distance/"fuel" timer expired), ``5`` (time/"oxygen" timer expired), or
    ``1`` (the wall-crash flag). The gameplay stepper stops here; the transition
    itself (level load / menu return / respawn) is a separate subsystem. A
    fail-loud boundary, not a silent continuation into a non-gameplay state."""


class FallDeathTransition(SkyroadsGap):
    """The ship fell off the road: the `1010:23CA-2421` out-of-bounds check fired
    (``skyroads.recovered_native.collision.ship_fell_off`` is true past the `[41C0]`
    lateral threshold while ``game_state == 0``), which in the VM calls the death
    handler `0F05` and exits the frame. The gameplay stepper stops here; the
    death consequence (respawn) is a separate subsystem."""


class MovementPhysicsGap(SkyroadsGap):
    """The lateral/vertical movement MATH is now COMPLETE and proven: the
    pipeline ``compute_movement_targets`` (``1010:2635-26E6``,
    skyroads.recovered.physics) -> ``resolve_move`` (``1010:186B``,
    skyroads.recovered.movement) with the ``skyroads.recovered_native.collision``
    predicate reproduces the real VM's post-move ``(lateral, af1c, af2c)``
    300/300 over real gameplay frames (tests/test_native_movement_pipeline.py;
    ``af1c_base_offset`` is the constant ``0x0618`` in all observed gameplay,
    see physics.py -- an earlier "unrecovered selector" reading was corrected).

    The pipeline's ``lateral_accel`` (``ds:[4568]``) input -- stateful steering
    momentum -- is now ALSO recovered: ``skyroads.recovered.dynamics.
    step_jump_steer_gravity`` (``1010:252B-2635``) derives it (along with the
    jump latch and gravity) 415/416 vs the VM. So the movement math AND its
    inputs are recovered.

    The ``bp-14``/``bp-18`` classification flags ``step_jump_steer_gravity``
    needs are now RECOVERED too -- ``skyroads.recovered.classify`` /
    ``skyroads.recovered_native.classify`` (the ``1010:2324-23BF`` block, 682/682 vs VM).
    So the classification, dynamics, and movement pipeline are ALL recovered
    and proven. What remains before a full native frame can be stood up: the
    ``26E9-2B0B`` post-move TAIL state machine (drives ``bp-12`` and clears the
    jump latch ``bp-8`` on landing at ``28F2-2901`` -- see
    :class:`JumpGateGap`), the upstream ``decay_bounce`` region
    (``1010:2421-24BA``) and early visibility check (``23CA-2421``), and the
    ``1B49`` gameplay side effect (``classify`` flags it but doesn't model it).
    Fired on every real gameplay frame until those are closed."""


class JumpGateGap(SkyroadsGap):
    """The jump-impulse LATCH (``ss:[bp-8]``/``ss:[bp-18]``, guarding
    ``1010:2582``'s jump block so a held jump key impulses once, not every
    frame) is not recovered -- see
    skyroads/recovered/player.py::update_vertical_velocity's docstring.
    Raised whenever the frame's decoded controls request a jump; frames with
    no jump held are unaffected (``jumped=False`` needs no gate).

    2026-07-11: the latch itself is now RECOVERED --
    ``skyroads.recovered.dynamics.step_jump_steer_gravity`` (``1010:2570-25A9``,
    part of the ``252B-2635`` block) fires the impulse (``bounce := 0x480``),
    sets ``bp-8 := 1``, and records the jump-start height ``bp-10 := af2c``,
    matching the real ASM 415/416 over the full E2E demo (jump-fire frames
    exact). ``bp-8``/``bp-10`` are carried in a ``dynamics.JumpScratch``. Two
    things remain before this replaces the gap in ``native_gameplay_frame``:
    (a) the tail state machine that CLEARS ``bp-8`` -- now LOCATED at
    ``1010:28F2-2901`` (``bp-6 := 0``, ``bp-8 := 0``, ``bp-12 := 1``), reached
    when ``ds:[AF2C] != bp-28`` (the af2c target) AND ``ds:[9336] < 0``
    (descending) -- i.e. the landing/collision-resolved condition; recovering
    that block (the ``26E9-2B0B`` post-move tail) is the remaining island; and
    (b) the ``bp-14``/``bp-18`` classification flags the block needs -- now
    RECOVERED (``skyroads.recovered.classify`` / ``skyroads.recovered_native.classify``,
    682/682 vs VM), leaving only ``bp-12`` (the gameplay-active latch that same
    tail state machine drives) as their upstream input. Confirmed earlier that
    the latch locals are
    genuine session state: ``SS:BP`` was IDENTICAL (``0x1686:0xB910``) on all
    274 real ``1010:186B`` visits across ~1900 frames -- the ``2280-2B0B``
    handler is one continuous ``enter`` that loops via ``jmp`` across displayed
    frames. So the scratch belongs alongside ``GameView`` (``JumpScratch``
    already is that), not in DGROUP -- the wiring just isn't stood up yet."""


class VerticalVelocityGap(SkyroadsGap):
    """SUPERSEDED by ``skyroads.recovered.dynamics.step_jump_steer_gravity``.

    This gap existed because composing ``decay_bounce`` +
    ``update_vertical_velocity`` unconditionally was proven wrong on real
    E2E-demo data (``ds:[9336]`` frozen for 8 straight frames while airborne
    with ``af2c < 0x2800``). The cause is now understood and modelled: the
    per-frame gravity/velocity update (``1010:25DB-2635``) is GATED by
    ``grounded`` (``ds:[456A]``) and ``af2c``, and the jump path is gated by
    the session-persistent jump latch (``bp-8``) -- all captured in
    ``step_jump_steer_gravity``, which matches the real ASM 415/416 including
    every previously-"frozen" frame. ``native_gameplay_frame`` still raises
    this today only because ``step_jump_steer_gravity`` needs the
    ``bp-14``/``bp-18`` classification flags that aren't derived natively yet
    (see :class:`MovementPhysicsGap`); the gap is no longer in the
    vertical-velocity MATH. See docs/skyroads/run_status.md's 2026-07-11
    dynamics-block entry."""
