"""Frame parking for SkyRoads' proven timer-driven semantic boundaries.

The frontend delivers a frame's timer IRQs before executing that frame, so
``ds:[1600]`` cannot change during the remaining instruction budget. Empty
waits park immediately; side-effecting fade waits run exactly one body before
parking. In both cases the continuation remains reconstructible from machine
state.

The fade loop at ``1010:434A`` is not an empty spin: one palette blend must run
before it becomes idle. Its one-bit phase is derived anew within each frame;
the persistent boundary itself is the pre-comparison machine point.
"""
from __future__ import annotations

from contextlib import contextmanager

from dos_re.cpu import CPU8086
from skyroads.hooks import CODE_SEG


TICK_ADDR = 0x1600
PACING_SPIN_IP = 0x22F8
MENU_ANIM_WAIT_IP = 0x47CD
MENU_ANIM_WAIT_THRESHOLD = 2
ROAD_DEPARTURE_WAIT_IP = 0x0EF8
FADE_BLEND_WAIT_IP = 0x434A
FADE_WAIT_COMPARE_IP = 0x4468
MENU_SCENE_FRAME_IP = 0x4866


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
            c._skyroads_frame_park_identity = "1010:22F8"
            raise FrameIdle
        s.ax = saved
        s.ip = (s.ip + 3) & 0xFFFF

    def park_menu_anim_wait(c: CPU8086) -> None:
        tick = c.mem.rw(c.s.ds, TICK_ADDR)
        if tick < MENU_ANIM_WAIT_THRESHOLD:
            c._skyroads_frame_park_identity = "1010:47CD"
            raise FrameIdle
        c.s.ip = (c.s.ip + 5) & 0xFFFF
        c.set_sub_flags(
            tick,
            MENU_ANIM_WAIT_THRESHOLD,
            tick - MENU_ANIM_WAIT_THRESHOLD,
            16,
        )

    def park_road_departure_wait(c: CPU8086) -> None:
        # 0EEB saved the entry tick in SI, then 0EF8 repeatedly compares it
        # with the interrupt-owned clock.  Nothing in this loop can change the
        # clock, so an equal value is an exact resumable frame boundary.
        tick = c.mem.rw(c.s.ds, TICK_ADDR)
        if tick == c.s.si:
            c._skyroads_frame_park_identity = "1010:0EF8"
            raise FrameIdle
        # A replacement hook owns the instruction at its address.  Once the
        # clock changes, reproduce 0EF8 `cmp [1600],si` + 0EFC `jnz 0F01`
        # rather than returning onto the same hook forever.
        c.set_sub_flags(tick, c.s.si, tick - c.s.si, 16)
        c.s.ip = 0x0F01
        c.instruction_count += 1  # CPU.step accounts the second instruction

    def park_fade_blend_after_body(c: CPU8086) -> None:
        # 434A starts one complete blend/present iteration.  With a non-zero
        # duration, returning to 434A in the same host frame means the virtual
        # timer is unchanged and another iteration would reproduce the same
        # externally visible state.  The parked continuation is the head before
        # CMP so the next frame re-evaluates the guard after timer delivery.
        duration = c.mem.rw(c.s.ss, (c.s.bp + 8) & 0xFFFF)
        if duration and getattr(c, "_skyroads_fade_blend_seen", False):
            c._skyroads_frame_park_identity = "1010:434A"
            c.s.ip = FADE_BLEND_WAIT_IP
            raise FrameIdle
        c.set_sub_flags(duration, 0, duration, 16)
        c.s.ip = 0x434E
        c._skyroads_fade_blend_seen = bool(duration)

    def park_fade_wait_after_body(c: CPU8086) -> None:
        tick = c.mem.rw(c.s.ds, TICK_ADDR)
        target = c.s.ax & 0xFFFF
        if tick < target:
            if getattr(c, "_skyroads_fade_wait_seen", False):
                c._skyroads_frame_park_identity = "1010:4468"
                raise FrameIdle
            c._skyroads_fade_wait_seen = True
            c.set_sub_flags(tick, target, tick - target, 16)
            c.s.ip = 0x4471  # cmp + jb taken: execute one palette body
            c.instruction_count += 1
            return
        c._skyroads_fade_wait_seen = False
        c.set_sub_flags(tick, target, tick - target, 16)
        c.s.ip = 0x4481  # cmp + jb not taken + jmp 4481
        c.instruction_count += 2

    def park_menu_scene_after_body(c: CPU8086) -> None:
        # 4860 resets the virtual clock; 4866 begins one complete menu/scene
        # animation body whose progress derives from that clock. Re-arrival in
        # the same host frame cannot observe a newer tick, so the next body is
        # the following semantic frame.
        if getattr(c, "_skyroads_menu_scene_seen", False):
            c._skyroads_frame_park_identity = "1010:4866"
            c.s.ip = MENU_SCENE_FRAME_IP
            raise FrameIdle
        c._skyroads_menu_scene_seen = True
        c.s.ax = 0x013F
        c.s.ip = 0x4869

    pacing_key = (CODE_SEG, PACING_SPIN_IP)
    menu_key = (CODE_SEG, MENU_ANIM_WAIT_IP)
    departure_key = (CODE_SEG, ROAD_DEPARTURE_WAIT_IP)
    fade_blend_key = (CODE_SEG, FADE_BLEND_WAIT_IP)
    fade_key = (CODE_SEG, FADE_WAIT_COMPARE_IP)
    menu_scene_key = (CODE_SEG, MENU_SCENE_FRAME_IP)
    hooks = (
        (pacing_key, park_pacing_spin, "frame_park_pacing_spin"),
        (menu_key, park_menu_anim_wait, "frame_park_menu_anim_wait"),
        (departure_key, park_road_departure_wait,
         "frame_park_road_departure_wait"),
        (fade_blend_key, park_fade_blend_after_body,
         "frame_park_fade_blend_after_body"),
        (fade_key, park_fade_wait_after_body,
         "frame_park_fade_wait_after_body"),
        (menu_scene_key, park_menu_scene_after_body,
         "frame_park_menu_scene_after_body"),
    )
    for key, implementation, name in hooks:
        # Runtime services never silently replace a selected implementation.
        # Generated carriers observe the same heads through boundary_hook.
        if key in cpu.replacement_hooks:
            continue
        cpu.replacement_hooks[key] = implementation
        cpu.hook_names[key] = name
    cpu._skyroads_frame_park_installed = True


def begin_frame_park(rt) -> None:
    """Reset host phase that is derived anew for each semantic frame."""
    rt.cpu._skyroads_fade_blend_seen = False
    rt.cpu._skyroads_fade_wait_seen = False
    rt.cpu._skyroads_menu_scene_seen = False
    rt.cpu._skyroads_frame_park_identity = None


@contextmanager
def suspend_frame_park(rt):
    """Temporarily expose literal execution for a low-level replay point.

    Guest-instruction fallback coordinates predate (or intentionally cross)
    the semantic park.  Raising ``FrameIdle`` there would make the diagnostic
    coordinate unreachable.  The suspension is scoped to that one advance and
    restores the exact hook/name objects afterward.
    """
    cpu = rt.cpu
    keys = (
        (CODE_SEG, PACING_SPIN_IP),
        (CODE_SEG, MENU_ANIM_WAIT_IP),
        (CODE_SEG, ROAD_DEPARTURE_WAIT_IP),
        (CODE_SEG, FADE_BLEND_WAIT_IP),
        (CODE_SEG, FADE_WAIT_COMPARE_IP),
        (CODE_SEG, MENU_SCENE_FRAME_IP),
    )
    hooks = {key: cpu.replacement_hooks.pop(key) for key in keys
             if key in cpu.replacement_hooks}
    names = {key: cpu.hook_names.pop(key) for key in keys
             if key in cpu.hook_names}
    try:
        yield
    finally:
        cpu.replacement_hooks.update(hooks)
        cpu.hook_names.update(names)
