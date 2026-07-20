"""SkyRoads keyboard control decode — input -> ship control axes.

The gameplay input handler (`1010:074C`) dispatches on the selected control
device `ds:[95F6]` (0 = keyboard, 1/2 = other devices, 3 = attract-mode
autopilot that reads a packed control track at `ds:0x961E`). This module
recovers the **keyboard** case (`95F6 == 0`, block `1010:0758`), the one live
play uses.

It reads a per-key pressed-state row the timer ISR maintains at `ds:0x0BD0`
(the ISR polls the keyboard each tick and sets **bit 7** of a key's byte while
it is held), and folds nine of those keys into three control outputs:

    ds:[9330]  speed   forward/back axis   (fed to advance_ship)
    ds:[95F4]  steer   left/right axis
    ds:[547A]  jump    jump request (0/1)

The nine keys form an 8-direction pad plus jump; the diagonals contribute to
*both* axes, which is why several offsets appear in both sums:

    0BD2 up        0BD3 down       0BD4 left      0BD5 right
    0BD6 up-left   0BD7 up-right   0BD8 down-left 0BD9 down-right
    0BDB jump

Each axis is `(OR of its positive-direction keys) - (OR of its negative keys)`,
so both land in {-1, 0, +1}. (Scancode -> row-offset mapping lives in the ISR
keyboard poll at `1010:3BE5`; recovering that is separate host-input plumbing.)
"""
from __future__ import annotations

from typing import NamedTuple, Sequence

from skyroads.islands import oracle_link

#: DGROUP base of the per-key pressed-state row the ISR maintains.
KEY_ROW_BASE = 0x0BD0

# Row offsets (relative to ds:0) of the nine keys this decode reads.
K_UP, K_DOWN = 0x0BD2, 0x0BD3
K_LEFT, K_RIGHT = 0x0BD4, 0x0BD5
K_UPLEFT, K_UPRIGHT = 0x0BD6, 0x0BD7
K_DOWNLEFT, K_DOWNRIGHT = 0x0BD8, 0x0BD9
K_JUMP = 0x0BDB

#: A key's byte has bit 7 set while the key is held (set by the ISR poll).
KEY_DOWN_BIT = 0x80


class Controls(NamedTuple):
    speed: int   # ds:[9330]  forward(+)/back(-) axis, in {-1,0,1}
    steer: int   # ds:[95F4]  right(+)/left(-) axis, in {-1,0,1}
    jump: int    # ds:[547A]  jump request, 0 or 1


@oracle_link(
    boundary="1010:0758",
    contract="decode_keyboard(key_row): fold the ISR key-state row at ds:0x0BD0 "
             "into (speed=[9330], steer=[95F4], jump=[547A]). A key is down iff "
             "bit7 of its byte is set. speed=(up|upL|upR)-(down|dnL|dnR); "
             "steer=(right|upR|dnR)-(left|upL|dnL); jump=jumpkey. This is the "
             "95F6==0 (keyboard) case of the 074C control dispatcher.",
    status="ASM_MATCHED",  # 1466/1466 full-replay 074C calls (497 with keys down) byte-exact
    merge_target="skyroads.native.controls (future)",
)
def decode_keyboard(key_row: Sequence[int]) -> Controls:
    """Decode the keyboard row into the ship control axes.

    ``key_row`` is indexable by DGROUP offset (e.g. the raw memory, or any
    sequence where ``key_row[0x0BD2]`` is the up key's byte); only the nine
    offsets above are read.
    """
    def down(off: int) -> int:
        return 1 if key_row[off] & KEY_DOWN_BIT else 0

    up, dn = down(K_UP), down(K_DOWN)
    left, right = down(K_LEFT), down(K_RIGHT)
    ul, ur = down(K_UPLEFT), down(K_UPRIGHT)
    dl, dr = down(K_DOWNLEFT), down(K_DOWNRIGHT)

    speed = (up | ul | ur) - (dn | dl | dr)
    steer = (right | ur | dr) - (left | ul | dl)
    return Controls(speed=speed, steer=steer, jump=down(K_JUMP))
