"""SkyRoads level-select / menu action dispatcher — `1010:1B49-1C63`.

A per-call action dispatcher: the caller passes a single 4-bit action code
(the low nibble of a word argument); the handler updates a few DGROUP fields
and always finishes with a clamp. Called very frequently (every menu frame,
mostly with an action code that matches none of the known cases — a no-op
"heartbeat" that still runs the clamp) — this is UI-tier code, not
performance-hot, so it stays a clean recovered rule (verified by sampling,
like `recovered/player.py`) rather than a live VM hook.

## The reused "ship_pos" field

`ds:[54AC:54AE]` is **the same 32-bit field `advance_ship` calls `pos`** —
SkyRoads reuses it as the level-select scroll/selection position while
`[456E] != 3` (not in gameplay). Both readings share the exact same clamp
range `[0, LEVEL_END]` (`0x2AAA`), confirmed by the dispatcher's own tail
clamp using the identical constant.
"""
from __future__ import annotations

from typing import NamedTuple

from skyroads.handrecovered.player import LEVEL_END

#: Scroll increment for the level-select left/right actions (1010:1BC4/1BDC).
SCROLL_STEP = 0x012F

#: Reset value for the two post-level countdown timers (matches
#: RespawnState.level_timer_a/b; 1010:1BAB/1BB1).
CONFIRM_TIMER_RESET = 0x7530
#: Threshold the confirm action checks the timers against (1010:1B8D/1B98).
CONFIRM_TIMER_THRESHOLD = 0x6978

ACTION_SCROLL_LEFT = 0x2
ACTION_CONFIRM = 0x9
ACTION_SCROLL_RIGHT = 0xA
ACTION_ENTER_LEVEL_SELECT = 0xC


class MenuState(NamedTuple):
    game_state: int      # ds:[456E]
    entered: int          # ds:[456A]  (0/1 latch: "level-select already entered")
    scroll_pos: int       # ds:[54AC:54AE]  (reused ship_pos field)
    timer_a: int          # ds:[5494]
    timer_b: int          # ds:[B13C]


def dispatch_menu_action(action: int, state: MenuState) -> MenuState:
    """Apply one level-select action (1010:1B49-1C63) and return the new state."""
    game_state, entered, pos, timer_a, timer_b = state
    action &= 0xF

    if action == ACTION_SCROLL_LEFT:
        if entered == 0:
            pos -= SCROLL_STEP
    elif action == ACTION_SCROLL_RIGHT:
        if entered == 0:
            pos += SCROLL_STEP
    elif action == ACTION_ENTER_LEVEL_SELECT:
        game_state = 2
        if entered == 0:
            entered = 1
    elif action == ACTION_CONFIRM:
        if game_state == 0:
            # the ASM also conditionally calls 1010:03C2(4) here -- not modeled
            timer_a = CONFIRM_TIMER_RESET
            timer_b = CONFIRM_TIMER_RESET
    # any other action code: no state change (the common "heartbeat" case)

    pos = max(0, min(pos, LEVEL_END))
    return MenuState(game_state, entered, pos, timer_a, timer_b)
