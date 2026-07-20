"""Authored SkyRoads gameplay-state implementations over named state.

These rules are independent of CPU instruction interpretation and are verified
against the oracle at their declared boundaries. Selection and whole-program
coverage belong to the implementation catalog and Execution Atlas.

Unlike the renderer, the gameplay update is not a set of small callable
routines — it is one large monolithic handler inline in the main loop's
gameplay branch (the game state dispatch on ds:[456A]/[456E]/[4558]). So these
functions are represented as focused rules over the named state view.

## Game-state field map (the "state-view" seam — DOS DGROUP offsets -> names)

All in the game data segment (ds == 0x1686 in the captured runtime):

    ds:0x54AC  dword  ship_pos      forward position along the road (0..LEVEL_END)
    ds:0x9330  word   speed         forward speed (pos advances by speed*75/frame)
    ds:0x9336  word   bounce        vertical landing-bounce offset (signed, damped)
    ds:0xAF2C  word   view_y_base   screen-Y base the bounce is added to
    ds:0x456E  word   game_state    3 == in gameplay (else this update is skipped)
    ds:0x9618  dword  lateral_x     lateral (lane) position, 32-bit (see renderer)

Constant `LEVEL_END = 0x2AAA` is the road length; reaching it completes the level.
"""
from __future__ import annotations

from typing import NamedTuple

from skyroads.handrecovered.movement import _ulong_div, _ulong_mul

#: Per-level gravity is `-((jump_level_gate * 0x1680) / 0x190)` (1010:1FFA-201C).
GRAVITY_LEVEL_MUL = 0x1680
GRAVITY_LEVEL_DIV = 0x190

#: Road length in forward-position units; ship_pos is clamped to [0, LEVEL_END],
#: and reaching LEVEL_END is level-complete (1010:2514 `cmp [54AC],0x2AAA`).
LEVEL_END = 0x2AAA

#: Forward-position units advanced per unit of speed per frame (1010:24C8 `mov cx,75`).
SPEED_TO_POS = 75

STATE_GAMEPLAY = 3  # ds:[456E] value while in the gameplay update loop

#: Jump up-impulse written to the vertical velocity when a jump fires (1010:2596).
JUMP_IMPULSE = 0x0480
#: Height threshold that gates gravity vs the terminal-velocity clamp (1010:25E5).
GRAVITY_HEIGHT_GATE = 0x2800
#: Terminal vertical velocity, -106 (1010:25FA `cmp [9336],0xFF96`).
TERMINAL_VVEL = -106


def _s16(v: int) -> int:
    v &= 0xFFFF
    return v - 0x10000 if v & 0x8000 else v


def advance_ship(pos: int, speed: int) -> int:
    """The per-frame forward-motion rule (1010:24C4-2528).

    ``pos`` and the return are the 32-bit forward position (ds:[54AC:54AE]);
    ``speed`` is ds:[9330]. Reaching ``LEVEL_END`` means the level is complete.
    """
    s = speed & 0xFFFF
    if s & 0x8000:                  # cwd: sign-extend speed to 32-bit before *75
        s -= 0x10000
    pos = (pos + s * SPEED_TO_POS) & 0xFFFFFFFF
    if pos & 0x80000000:            # went negative (high bit set) -> clamp to start
        pos = 0
    elif pos > LEVEL_END:           # 1010:2505-2528 clamp to the road end
        pos = LEVEL_END
    return pos


def decay_bounce(bounce: int) -> int:
    """The per-frame vertical landing-bounce decay (1010:24A1-24AE).

    `imul bounce,5; neg; idiv 10` -> `-(bounce*5)//10` with x86 truncation
    toward zero. Produces the classic SkyRoads damped up/down bounce; `bounce`
    is added to the view's Y base (`ds:[AF2C]`) each frame and rings down to 0.
    """
    b = bounce - 0x10000 if bounce & 0x8000 else bounce   # sign-extend
    prod = -(b * 5)
    q = -((-prod) // 10) if prod < 0 else prod // 10       # trunc toward zero
    return q & 0xFFFF


def update_vertical_velocity(vvel: int, jumped: bool, af2c: int,
                             gravity: int, grounded: bool) -> int:
    """Per-frame vertical-velocity (`ds:[9336]`) jump+gravity update (1010:2582-2635).

    ``vvel`` is the velocity after :func:`decay_bounce` has run this frame;
    ``jumped`` is whether the jump gate fired an impulse. The gate itself is
    NOT fully recovered: it is `ds:[547A]!=0 and ds:[4562]<0x14` (2582/258C —
    ``[4562]`` is a per-level constant, not a per-frame counter; the deaths
    replay has it pinned at 8), **guarded further** by two frame-local flags
    `ss:[bp-8]` and `ss:[bp-18]` (2570/2579 — skip the whole jump block if
    either is nonzero) that this module doesn't yet compute: they are set
    earlier in the same per-frame handler, likely from the collision/height
    classification around 1010:2340-2385, and are why in the deaths replay the
    impulse fires only on the *first* frame of each held jump-key press
    (3 times, not the 29 frames the key was actually held) — almost certainly
    an "already airborne, ignore jump" latch. Recovering it requires tracing
    where bp-8/bp-18 get set, which the current replay corpus hasn't forced yet.
    ``af2c`` is `ds:[AF2C]`, ``gravity`` is `ds:[54AA]` (signed, per-level),
    ``grounded`` is `ds:[456A] != 0`.
    """
    v = _s16(JUMP_IMPULSE if jumped else vvel)
    if not grounded:                                   # airborne
        if af2c >= GRAVITY_HEIGHT_GATE:
            v = _s16(v + _s16(gravity))                # gravity accel  [VERIFIED]
        elif v > TERMINAL_VVEL:
            v = TERMINAL_VVEL                          # terminal clamp [ASM-derived, dark]
    else:                                              # grounded ramp  [ASM-derived, dark]
        if v < 0:
            v = 0
        v = _s16(v + 0x27) if v < 0x47 else 0x47
    return v & 0xFFFF


#: Height threshold at which the reset ship is treated as "landed" and gameplay
#: resumes (`ds:[456E] := 3`, 1010:2AB1). Same value the reset writes ``AF2C``
#: to, so a freshly-respawned ship is immediately resume-eligible.
RESUME_HEIGHT_GATE = 0x2800


def is_landed_for_resume(af2c: int) -> bool:
    """Whether the ship has descended enough (`ds:[AF2C]`) to resume gameplay
    (1010:2AB1 `jb`): True iff af2c < RESUME_HEIGHT_GATE."""
    return (af2c & 0xFFFF) < RESUME_HEIGHT_GATE


class RespawnState(NamedTuple):
    """The fixed field values the respawn/reset block (1010:201F-20A7) writes.

    Every field here is a **constant** -- the block does not read any prior
    state to compute them (the one branch it has, `ds:[95F6]==2` for a
    joystick-recenter call, is a side call this doesn't model and is untested:
    the deaths replay only plays with the keyboard). Call sites: after a death
    (`ds:[456E]` was 1 or 3), to reset the ship to the start of its (fixed)
    spawn position and clear the level's transition timers.
    """
    lateral_lo: int = 0x0000        # ds:[9618]
    lateral_hi: int = 0x0003        # ds:[961A]
    vert_af1c: int = 0x8000         # ds:[AF1C]
    vert_af2c: int = 0x2800         # ds:[AF2C]  == RESUME_HEIGHT_GATE
    unknown_5496: int = 0x0000      # ds:[5496]
    lateral_accel: int = 0x0000     # ds:[4568]  (steer*29 accumulator, see the physics block)
    vvel: int = 0x0000              # ds:[9336]
    ship_pos_lo: int = 0x0000       # ds:[54AC]
    ship_pos_hi: int = 0x0000       # ds:[54AE]
    level_timer_a: int = 0x7530     # ds:[5494]  counts down toward a post-level state
    level_timer_b: int = 0x7530     # ds:[B13C]  counts down toward a post-level state
    game_state: int = 0x0000        # ds:[456E]
    frame_ctr: int = 0x0000         # ds:[4558]
    unknown_456a: int = 0x0000      # ds:[456A]
    unknown_455a: int = 0x0000      # ds:[455A]
    unknown_af2e: int = 0x0000      # ds:[AF2E]
    unknown_af30: int = 0x0000      # ds:[AF30]
    elapsed_ticks: int = 0x0000     # ds:[1600]  the frame-pacing tick counter
    unknown_af38: int = 0x0000      # ds:[AF38]


def respawn() -> RespawnState:
    """The fixed post-death reset state (1010:201F-20A7)."""
    return RespawnState()


def level_gravity(jump_level_gate: int) -> int:
    """The per-level gravity ``ds:[54AA]`` derived from ``jump_level_gate``
    (``ds:[4562]``) at level init (1010:1FFA-201C)."""
    prod = _ulong_mul(jump_level_gate & 0xFFFF, GRAVITY_LEVEL_MUL)
    return (-_ulong_div(prod, GRAVITY_LEVEL_DIV)) & 0xFFFF
