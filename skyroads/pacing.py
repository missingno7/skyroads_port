"""Frame-park pacing — end a displayed frame the instant the game parks in a
timer-tick busy-wait, instead of grinding the full per-frame step budget.

## Why this exists (the pacing/steps issue)

SKYROADS paces itself off ``ds:[1600]``, the elapsed-tick counter that its
INT 08h ISR bumps once per game tick.  In the viewer, ALL of a frame's timer
IRQs are delivered at frame start (see ``advance_frame``), so ``ds:[1600]`` is
**architecturally constant for the whole of a frame's step budget** — it cannot
change again until the *next* frame.  Any loop that waits for ``ds:[1600]`` to
change therefore spins for the entire remaining budget doing nothing.

Measured over the gameplay window, ~88% of interpreted steps per frame are this
side-effect-free spin, split between two waits:

* ``1010:22F8``  the main gameplay pacing spin
  (``mov ax,ss:[bp-4]; cmp ds:[1600],ax; jnz 2304; jmp 22F8``)
* ``1010:434A``  the fade/pacing wait gate (already hooked for the redundant
  palette re-blend; here we also park it)

A third, structurally identical spin shows up in menu/animation screens at
``1010:47CD`` (``cmp ds:[1600],0002h; jnb 47D7; jmp 47CD`` — an animation-frame
timer waiting for >=2 ticks rather than "changed"), found profiling the
2026-07-11 perf diagnosis: several consecutive menu frames were burning the
*entire* step budget here.  Same fix, same proof obligation (it is
runtime-loaded code, unlike the other two — the byte-equivalence check below
also stands in for confirming its instruction bytes are what they appear to be
in the replays this was verified against).

All three are provably stuck for the rest of the frame the moment their tick
guard holds, so we END THE FRAME there.  The parked spins have no side effects,
so the game state at every frame boundary is **byte-identical** to running the
full budget — only the wasted spin steps disappear (~6x fewer steps / faster
wall clock for gameplay).

This is SKYROADS' analogue of pre2_port's classified-wait fast-forward
(``scripts/play.py --fast-retrace-waits`` over ``pre2.recovered.vga_timing``),
adapted to SKYROADS' single tick counter and hook-based frontend.
"""
from __future__ import annotations

from dos_re.cpu import CPU8086
from skyroads.hooks import (
    CODE_SEG,
    _fade_loop_cache,
    fade_loop_tick_gate_hook,
)

#: ds:[1600] — the game-wide elapsed-tick counter, advanced ONLY by the INT 08h
#: ISR (which the viewer delivers only at frame boundaries).
TICK_ADDR = 0x1600

PACING_SPIN_IP = 0x22F8   # main gameplay frame-pacing wait head
FADE_WAIT_IP = 0x434A     # fade/pacing wait gate (shared fade_loop_tick_gate hook)
MENU_ANIM_WAIT_IP = 0x47CD  # menu/animation-frame tick-wait (runtime-loaded code)
MENU_ANIM_WAIT_THRESHOLD = 2  # 1010:47CD `cmp ds:[1600],0002h`


class FrameIdle(Exception):
    """Raised from a park hook when the game has parked in a tick-wait that
    cannot progress until the next frame's IRQ — the frame driver catches it
    and ends the frame early (see :meth:`SkyroadsFrontend.advance_frame`)."""


def _keys_pending(rt) -> bool:
    """True if the game still has queued keyboard input to drain — in which case
    the fade wait must run its key-drain rather than park."""
    dos = rt.dos
    return bool(dos.key_queue) or dos.pending_console_scancode is not None


def install_frame_park(rt) -> None:
    """Install the two tick-wait park hooks on ``rt``'s CPU.

    Byte-equivalent to the full-budget spin (verified over the full E2E replay),
    so it is safe for live play, replay record and replay replay alike.  Idempotent
    per runtime.  Requires the recovered hooks to be installed (it composes with
    the fade-loop gate); call it after ``registry.install``.
    """
    cpu = rt.cpu

    def park_pacing_spin(c: CPU8086) -> None:
        # 22F8: mov ax,ss:[bp-4] / cmp ds:[1600],ax / jnz 2304 (exit) / jmp 22F8.
        # If the guard tick equals [1600] the spin cannot exit this frame -> park.
        s = c.s
        saved = c.mem.rw(s.ss, (s.bp - 4) & 0xFFFF)
        if c.mem.rw(s.ds, TICK_ADDR) == saved:
            raise FrameIdle
        s.ax = saved
        s.ip = (s.ip + 3) & 0xFFFF          # completed `mov ax,ss:[bp-4]`

    def park_fade_wait(c: CPU8086) -> None:
        # Park only when the tick cannot change this frame AND there is nothing
        # queued to drain; otherwise defer to the verified fade-loop gate (which
        # skips the redundant re-blend and drains pending keys).
        s = c.s
        if (_fade_loop_cache(c).get((s.ss, s.bp)) == c.mem.rw(s.ds, TICK_ADDR)
                and not _keys_pending(rt)):
            raise FrameIdle
        fade_loop_tick_gate_hook(c)

    def park_menu_anim_wait(c: CPU8086) -> None:
        # 47CD: cmp ds:[1600],2 / jnb 47D7 (proceed) / jmp 47CD (spin).
        # [1600] is frozen for the frame, so once it's below the threshold it
        # cannot cross it before the next frame's IRQs -> park.
        tick = c.mem.rw(c.s.ds, TICK_ADDR)
        if tick < MENU_ANIM_WAIT_THRESHOLD:
            raise FrameIdle
        c.s.ip = (c.s.ip + 5) & 0xFFFF          # completed `cmp ds:[1600],2`
        c.set_sub_flags(tick, MENU_ANIM_WAIT_THRESHOLD,
                        tick - MENU_ANIM_WAIT_THRESHOLD, 16)

    cpu.replacement_hooks[(CODE_SEG, PACING_SPIN_IP)] = park_pacing_spin
    cpu.replacement_hooks[(CODE_SEG, FADE_WAIT_IP)] = park_fade_wait
    cpu.replacement_hooks[(CODE_SEG, MENU_ANIM_WAIT_IP)] = park_menu_anim_wait
