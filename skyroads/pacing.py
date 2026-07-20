"""Stateless frame parking for SkyRoads' side-effect-free timer waits.

The frontend delivers a frame's timer IRQs before executing that frame, so
``ds:[1600]`` cannot change during the remaining instruction budget. The two
waits intercepted here are empty spins: ending the frame when their guard is
still blocked preserves machine continuation state while avoiding wasted work.

The fade loop at ``1010:434A`` is intentionally not intercepted. Its body
performs a palette blend before it becomes an idle wait, and recognizing a
later visit would require host-local state that is absent from snapshots and
ReplayArtifact continuations.
"""
from __future__ import annotations

from dos_re.cpu import CPU8086
from skyroads.hooks import CODE_SEG


TICK_ADDR = 0x1600
PACING_SPIN_IP = 0x22F8
MENU_ANIM_WAIT_IP = 0x47CD
MENU_ANIM_WAIT_THRESHOLD = 2


class FrameIdle(Exception):
    """The game cannot progress until the next frame's timer IRQs."""


def install_frame_park(rt) -> None:
    """Install the plan-selected, product-safe wait-parking runtime service."""
    cpu = rt.cpu
    if getattr(cpu, "_skyroads_frame_park_installed", False):
        return

    def park_pacing_spin(c: CPU8086) -> None:
        s = c.s
        saved = c.mem.rw(s.ss, (s.bp - 4) & 0xFFFF)
        if c.mem.rw(s.ds, TICK_ADDR) == saved:
            raise FrameIdle
        s.ax = saved
        s.ip = (s.ip + 3) & 0xFFFF

    def park_menu_anim_wait(c: CPU8086) -> None:
        tick = c.mem.rw(c.s.ds, TICK_ADDR)
        if tick < MENU_ANIM_WAIT_THRESHOLD:
            raise FrameIdle
        c.s.ip = (c.s.ip + 5) & 0xFFFF
        c.set_sub_flags(
            tick,
            MENU_ANIM_WAIT_THRESHOLD,
            tick - MENU_ANIM_WAIT_THRESHOLD,
            16,
        )

    pacing_key = (CODE_SEG, PACING_SPIN_IP)
    menu_key = (CODE_SEG, MENU_ANIM_WAIT_IP)
    cpu.replacement_hooks[pacing_key] = park_pacing_spin
    cpu.replacement_hooks[menu_key] = park_menu_anim_wait
    cpu.hook_names[pacing_key] = "frame_park_pacing_spin"
    cpu.hook_names[menu_key] = "frame_park_menu_anim_wait"
    cpu._skyroads_frame_park_installed = True
