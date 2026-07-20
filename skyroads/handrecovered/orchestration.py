"""SkyRoads per-frame orchestration gate — `1010:229D-22E9`.

The top of the frame handler decides, from the game state, whether to run the
gameplay sub-step loop (`2317` onward) or to EXIT the handler (`22E3 -> 2B0B`,
returning ``game_state`` to the outer game loop, which then does the transition:
respawn `201F`, menu return, or level load). This is the gameplay-vs-transition
gate -- the piece that tells a native stepper when a level has actually ended
vs is still in progress.

The two in-level game states are 0 (active) and 3 (resume-frozen after landing),
but even game_state 3 exits to a transition once the ship has settled
(`[456A] != 0`), which is how a completed/failed run hands back to the front
end. This gate captures that exactly (verified 571/571, incl. the game_state
3 -> exit cases).
"""
from __future__ import annotations


#: `ds:[456A]` values in 1..0x2A keep the frame in gameplay regardless of
#: game_state (the just-landed settle window; 1010:22A7 `cmp [456A],0x2A; ja`).
SETTLE_WINDOW_MAX = 0x2A
#: While `ds:[4558] < 0x6C` a not-yet-settled frame still runs gameplay
#: (1010:22D9 `cmp [4558],0x6C; jnb -> exit`).
FRAME_CTR_GAMEPLAY_MAX = 0x6C


def should_run_gameplay(game_state: int, f456a: int, frame_ctr: int) -> bool:
    """Whether the frame runs the gameplay sub-step (True) or exits to a
    transition (False). See the module docstring for the state meanings."""
    f456a &= 0xFFFF
    if 0 < f456a <= SETTLE_WINDOW_MAX:              # 229D-22A7 settle window
        return True
    if (game_state & 0xFFFF) in (1, 2):            # 22B1/22BB -> exit
        return False
    if (game_state & 0xFFFF) == 3 and f456a != 0:  # 22CF -> exit (settled resume)
        return False
    return (frame_ctr & 0xFFFF) < FRAME_CTR_GAMEPLAY_MAX  # 22D9
