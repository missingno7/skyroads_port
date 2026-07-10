"""SkyRoads player / gameplay-state update — the first game-logic island.

This begins the *game-logic* recovery (as opposed to the render/asset hooks in
skyroads/hooks.py), following the pre2_port endgame model: clean, VM-independent
logic operating on **named game state**, verified byte-exact against the ASM,
grown until the whole game runs native and the VM is retired (see
docs/skyroads/vmless_roadmap.md).

Unlike the renderer, the gameplay update is not a set of small callable
routines — it is one large monolithic handler inline in the main loop's
gameplay branch (the game state dispatch on ds:[456A]/[456E]/[4558]). So these
functions are recovered as clean rules first (from disassembly, `world7` /
menu→gameplay demos); byte-exact verification will come when the whole gameplay
handler is stood up as an island (hybrid mode) or under frame-verify, the same
way pre2 proves its player/collision islands.

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

from skyroads.islands import oracle_link

#: Road length in forward-position units; ship_pos is clamped to [0, LEVEL_END],
#: and reaching LEVEL_END is level-complete (1010:2514 `cmp [54AC],0x2AAA`).
LEVEL_END = 0x2AAA

#: Forward-position units advanced per unit of speed per frame (1010:24C8 `mov cx,75`).
SPEED_TO_POS = 75

STATE_GAMEPLAY = 3  # ds:[456E] value while in the gameplay update loop


@oracle_link(
    boundary="1010:24C4",
    contract="advance_ship(pos, speed): pos += sign_extend16(speed)*75 (32-bit), "
             "then clamp the signed result to [0, 0x2AAA]. Reaching 0x2AAA "
             "completes the level. The ASM sign-extends speed (cwd at 24C7) into "
             "a 32-bit value before the ulong_mul by 75, so a negative speed "
             "moves the ship backward.",
    status="ASM_MATCHED",  # reproduces the ASM post-clamp pos over all full-demo samples
    merge_target="skyroads.native.player (future)",
)
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


@oracle_link(
    boundary="1010:24A1",
    contract="decay_bounce(bounce) = -(bounce*5)/10 = trunc(-bounce/2), signed "
             "toward zero -- a damped alternating-sign oscillation (the landing "
             "bounce). Added to the view Y base to make the road bounce when the "
             "ship lands; decays to 0.",
    status="ASM_MATCHED",  # matches the ASM on the (rare) bounce events sampled (-1148->+574, -461->+230)
    merge_target="skyroads.native.player (future)",
)
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
