"""SkyRoads per-frame jump-latch + steering + gravity block — `1010:252B-2635`.

This is the middle of the gameplay handler, between forward motion
(`player.advance_ship`, `24C4`) and the movement-target computation
(`physics.compute_movement_targets`, `2635`). It updates three things from the
session-persistent jump state:

* **lateral steering momentum** `ds:[4568]` (`lateral_accel`, `steer*29`) —
  latched, only recomputed under specific gates (`252B-256D`);
* **the jump latch itself** — fires an up-impulse and records the jump-start
  height (`2570-25A9`);
* **vertical velocity** `ds:[9336]` (`bounce`) — gravity while airborne, or a
  ground ramp (`25DB-2635`).

It supersedes the earlier naive `player.decay_bounce` +
`player.update_vertical_velocity` composition that
`skyroads.native.gaps.VerticalVelocityGap` had to guard: those were an
attempt to capture the jump+gravity stage as a stateless function, but the
real block is **gated by session-persistent stack locals** the earlier
functions couldn't see (the jump latch `ss:[bp-8]`, jump-start height
`ss:[bp-10]`) plus two per-frame classification flags (`ss:[bp-14]`,
`ss:[bp-18]`). Modelled here explicitly as a small `JumpScratch` carried
across frames, this block matches the real ASM 415/416 over the full E2E demo
(the one miss is a frame where the rare `25AC-25D6` effect path — a `1DFA`
call gated by `ds:[4570]`/`bp-6`/`af2c>=0x3700` — separately rewrote
`lateral_accel`; that path is flagged, not modelled, see `hit_effect_path`).

## Inputs this block still needs from elsewhere (documented gaps)

* `JumpScratch.jumping` (`bp-8`) is SET here but never CLEARED here — it must
  reset on landing/respawn (not yet located; `player.respawn` is a candidate).
* `class_skip` (`bp-14`) and `class_zero` (`bp-18`) come from the perspective
  classification at `1010:2324-23BF` (`bp-14 = (bp-20 & 0xF == 8)`,
  `bp-18 = (bp-20 == 0)`, `bp-20` from `renderer.perspective_row_offset`) —
  recovered inputs, but the classification function itself is not landed yet,
  so a native caller must supply them.
"""
from __future__ import annotations

from typing import NamedTuple

from skyroads.islands import oracle_link
from skyroads.recovered.player import (
    GRAVITY_HEIGHT_GATE,
    JUMP_IMPULSE,
    TERMINAL_VVEL,
    decay_bounce,
)

#: `steer` multiplier forming lateral_accel (1010:2568 `imul [95F4],29`).
STEER_ACCEL_MUL = 29
#: The small-bounce kill threshold is `low16(0x104*jump_gate)//8` (1010:2458-2464).
BOUNCE_KILL_MUL = 0x104
#: Per-level jump gate: a jump only fires while `ds:[4562] < 0x14` (1010:258C).
JUMP_GATE_MAX = 0x14
#: The steering "check path" only recomputes accel while the ship is within this
#: height window above its jump-start height (`af2c - bp10 < 0x0F00`, 1010:2560).
STEER_HEIGHT_WINDOW = 0x0F00
#: Grounded vertical ramp (1010:261D-262F): step and ceiling.
GROUND_RAMP_STEP = 0x27
GROUND_RAMP_MAX = 0x47


def _s16(v: int) -> int:
    v &= 0xFFFF
    return v - 0x10000 if v & 0x8000 else v


@oracle_link(
    boundary="1010:2421",
    contract="gate_bounce_decay(bounce, af2c, tgt_af2c, cur_5496, scan_cell, "
             "jump_gate, grounded): the pre-move bounce-decay gate. If "
             "af2c == tgt_af2c, bounce is untouched. Otherwise bounce := 0 when "
             "(cur_5496 != 0 and scan_cell < 2), or |bounce| < "
             "(low16(0x104*jump_gate) // 8), or grounded != 0; else "
             "bounce := decay_bounce(bounce). scan_cell is ss:[bp-24] (the "
             "vertical scan's last cell index, session state); jump_gate is "
             "ds:[4562]; grounded is ds:[456A].",
    status="ASM_MATCHED",  # 682/682 real E2E-demo frames byte-exact
    # (unchanged 236, zero-small 439, decay 6, zero-5496 1). The grounded!=0
    # zero branch was decoded but not exercised by the demo; the ASM also plays
    # a landing SFX (03C2(1), gated by a 0476 predicate) on some decay frames --
    # audio only, so not modelled here.
    merge_target="skyroads.native.dynamics (future)",
)
def gate_bounce_decay(
    bounce: int, af2c: int, tgt_af2c: int, cur_5496: int, scan_cell: int,
    jump_gate: int, grounded: int,
) -> int:
    """The `1010:2421-24BA` bounce-decay gate. Returns the new ``ds:[9336]``.

    Runs BEFORE :func:`step_jump_steer_gravity` each sub-step; its output bounce
    is that block's input.
    """
    if (af2c & 0xFFFF) == (tgt_af2c & 0xFFFF):          # 2424: at vertical target
        return bounce & 0xFFFF
    if (cur_5496 & 0xFFFF) != 0 and (scan_cell & 0xFFFF) < 2:   # 242D-243D
        return 0
    threshold = ((BOUNCE_KILL_MUL * (jump_gate & 0xFFFF)) & 0xFFFF) // 8  # 2458-2464
    if abs(_s16(bounce)) < threshold:                    # 2466-246D: kill small bounce
        return 0
    if (grounded & 0xFFFF) != 0:                         # 2470: grounded -> no bounce
        return 0
    return decay_bounce(bounce)                           # 24A1: damped oscillation


class JumpScratch(NamedTuple):
    """The session-persistent jump state (ss:[bp-8]/[bp-10]/[bp-6]) this block
    reads and writes -- carried ACROSS frames, not derivable from DGROUP."""
    jumping: int = 0        # bp-8: jump-in-progress latch (0/1), set at 25A1
    jump_start_y: int = 0   # bp-10: the af2c recorded when the jump fired (25A6)
    effect_latch: int = 0   # bp-6: the 25AC-25D6 one-shot effect latch (25D6)


class DynamicsResult(NamedTuple):
    bounce: int             # new ds:[9336]
    lateral_accel: int      # new ds:[4568] (UNRELIABLE if hit_effect_path -- see below)
    scratch: JumpScratch    # new jump state to carry to next frame
    hit_effect_path: bool   # True iff the 25AC-25D6 (1DFA) effect path fired --
    #                         that call separately rewrites lateral_accel in ways
    #                         this block does not model, so treat lateral_accel as
    #                         unmodelled for this frame.


@oracle_link(
    boundary="1010:252B",
    contract="step_jump_steer_gravity(scratch, class_skip, class_zero, bounce, "
             "lateral_accel, af2c, steer, jump_req, jump_gate, grounded, "
             "gravity, effect_gate): the 252B-2635 block. (steering 2534-256D) "
             "if class_skip==0 and [ (not scratch.jumping and class_zero==0) or "
             "(lateral_accel==0 and s16(bounce)>0 and (af2c-jump_start_y)&0xFFFF "
             "< 0x0F00) ]: lateral_accel = s16(steer)*29. (jump 2570-25A9) if "
             "not jumping and class_zero==0 and jump_req!=0 and jump_gate<0x14: "
             "bounce=0x480, jumping=1, jump_start_y=af2c. (effect 25AC-25D6) if "
             "effect_gate!=0 and jumping and effect_latch==0 and af2c>=0x3700: "
             "effect_latch=1 and flag hit_effect_path (a 1DFA call, unmodelled). "
             "(gravity 25DB-2635) if grounded==0: af2c>=0x2800 -> bounce+=gravity "
             "else clamp bounce down to -106 if above; grounded -> ramp bounce to "
             "+0x47 (>=0, +0x27/step). Returns new bounce/lateral_accel/scratch. "
             "When moving is False (game_state != 0, the 24BA->25AC frozen path), "
             "the steering and jump-latch stages (2534-25A9) are SKIPPED -- only "
             "the effect + gravity stages (25AC-2635) run.",
    status="ASM_MATCHED",  # 415/416 real E2E-demo frames byte-exact on
    # (bounce, lateral_accel, bp-8, bp-10); the single miss is a hit_effect_path
    # frame whose 1DFA call rewrote lateral_accel (correctly flagged, not
    # modelled). The grounded ramp (260D-262F) and the airborne terminal clamp
    # (af2c<0x2800) branches are transcribed from the ASM; whether the demo
    # exercised each is asserted by tests/test_dynamics.py. The moving=False
    # (frozen) path is verified via the lockstep loop (test_native_loop_lockstep).
    merge_target="skyroads.native.dynamics (future)",
)
def step_jump_steer_gravity(
    scratch: JumpScratch, class_skip: int, class_zero: int,
    bounce: int, lateral_accel: int, af2c: int, steer: int,
    jump_req: int, jump_gate: int, grounded: int, gravity: int,
    effect_gate: int, moving: bool = True,
) -> DynamicsResult:
    jumping = scratch.jumping
    jump_start_y = scratch.jump_start_y
    effect_latch = scratch.effect_latch
    bounce &= 0xFFFF
    lateral_accel &= 0xFFFF

    # steering (2534-256D) + jump latch (2570-25A9) run ONLY on the moving path
    # (game_state == 0); the frozen path (24BA -> 25AC) enters below them.
    if moving:
        # --- steering momentum (1010:2534-256D) ---
        if class_skip == 0:
            do_update = False
            if jumping == 0 and class_zero == 0:
                do_update = True                                # 2534->253D->2543
            elif (lateral_accel == 0 and _s16(bounce) > 0
                  and ((af2c - jump_start_y) & 0xFFFF) < STEER_HEIGHT_WINDOW):
                do_update = True                                # 2546->2550->255A
            if do_update:
                lateral_accel = (_s16(steer) * STEER_ACCEL_MUL) & 0xFFFF

        # --- jump latch (1010:2570-25A9) ---
        if jumping == 0 and class_zero == 0 and jump_req != 0 and jump_gate < JUMP_GATE_MAX:
            bounce = JUMP_IMPULSE & 0xFFFF
            jumping = 1
            jump_start_y = af2c & 0xFFFF

    # --- the 25AC-25D6 one-shot effect (a 1DFA call; unmodelled) ---
    hit_effect_path = False
    if effect_gate != 0 and jumping != 0 and effect_latch == 0 and af2c >= 0x3700:
        effect_latch = 1
        hit_effect_path = True

    # --- gravity / vertical velocity (1010:25DB-2635) ---
    if grounded == 0:                                            # airborne
        if af2c >= GRAVITY_HEIGHT_GATE:
            bounce = (bounce + gravity) & 0xFFFF                 # +gravity (25F0)
        elif _s16(bounce) > TERMINAL_VVEL:
            bounce = TERMINAL_VVEL & 0xFFFF                      # clamp to -106 (2604)
    else:                                                        # grounded ramp
        if _s16(bounce) < 0:
            bounce = 0
        bounce = (bounce + GROUND_RAMP_STEP) & 0xFFFF if _s16(bounce) < GROUND_RAMP_MAX else GROUND_RAMP_MAX

    return DynamicsResult(
        bounce, lateral_accel,
        JumpScratch(jumping, jump_start_y, effect_latch), hit_effect_path,
    )
