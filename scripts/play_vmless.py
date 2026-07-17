"""Play SKYROADS from the data-only BOOT IMAGE — no SKYROADS.EXE, no interpreter.

The runner named for the wall its artifacts satisfy (DOS_RE 2.0's rule; see
``dos_re/docs/migration_1.0_to_2.0.md``): every instruction executed here is
GENERATED Python from the recovery IR, the original code bytes in the image are
ZEROED, and the interpreter is poisoned — so this cannot silently fall back to
running the binary. It is not yet the *native* wall (state still lives in a
DOS-layout memory image behind a CPU-shaped struct), hence ``vmless``, not
``native``.

What makes frames tick
----------------------
The lifted corpus PARKS instead of spinning. SkyRoads paces itself off
``ds:[1600]``, the tick counter its INT 08h ISR bumps; the viewer delivers a
frame's IRQs only at frame start, so ``[1600]`` is architecturally constant for
the whole frame and any loop waiting on it can never exit (skyroads/pacing.py
measured ~88% of interpreted steps sitting in exactly these spins). The three
wait heads are recovery facts — ``pacing.PACING_SPIN_IP`` / ``FADE_WAIT_IP`` /
``MENU_ANIM_WAIT_IP`` — and ``irgen --boundary-heads`` turned them into emitted
boundary observers plus ``RESUME_ENTRIES``.

So a frame is: deliver the timer IRQs (through the game's OWN recovered INT 08h
ISR), run until the corpus reports a boundary head, END THE FRAME there. The
park unwinds Python, but the machine state does not live in Python — it is in
the emulated stack and CS:IP — so the next frame's ``run()`` re-dispatches at
the resume entry that ``install_vmless_graph`` registered, and the lifted body
continues from the right basic block. That is the same "end the frame at the
tick-wait" semantics pacing.py already proved byte-equivalent to burning the
full step budget.

Usage:
    python scripts/play_vmless.py                     # play it (window)
    python scripts/play_vmless.py --headless --frames 400
    python scripts/play_vmless.py --verify-demo artifacts/demos/demo_cold_20260713_213510
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dos_re"))
sys.path.insert(0, str(ROOT))

from dos_re.cpu import HaltExecution  # noqa: E402
from dos_re.independence import boot_vmless_image, independence_report  # noqa: E402
from dos_re.interrupts import deliver_interrupt  # noqa: E402

#: Measured from the VM (see play_native.py): the PIT reload is 6628, so the
#: timer runs at 1193182/6628 = 180 Hz and the music ISR fires once per IRQ —
#: 6 IRQs per displayed frame, i.e. 30 fps.
GAME_FPS = 30
TIMER_IRQS_PER_FRAME = 6
#: Runaway guard per frame. A frame that neither parks nor halts within this
#: many lifted dispatches is a bug (a spin with no boundary head), not a slow
#: frame — fail loud rather than hang.
STEP_BUDGET = 2_000_000


class FrameIdle(Exception):
    """The corpus parked in a tick-wait it cannot leave until the next frame."""


class VmlessDriver:
    """Drives the generated corpus one displayed frame at a time."""

    def __init__(self, rt, *, irqs_per_frame: int = TIMER_IRQS_PER_FRAME):
        self.rt = rt
        self.irqs_per_frame = irqs_per_frame
        self.frames = 0
        self.parks: dict[int, int] = {}      # head_ip -> times parked
        self._in_isr = False
        rt.cpu.boundary_hook = self._boundary

    def _boundary(self, cpu, head_cs, head_ip, resume_ip):
        """Boundary-head observer (ABI: cpu, head_cs, head_ip, resume_ip).

        Reaching ANY tick-wait ends the frame: the game has consumed the work
        this frame's IRQs released, and ``[1600]`` cannot change again until
        the next frame's IRQs — so the spin provably cannot exit. Re-point
        CS:IP at the resume entry (the contract in dos_re/lift/emit.py) so the
        next frame re-dispatches inside the lifted body, then unwind.

        EXCEPT inside an ISR: an interrupt handler must run to its IRET or the
        machine is left mid-frame with a half-delivered interrupt (and the
        emulated stack still holding its frame). ISRs do not wait on the tick
        they are themselves bumping, so letting the observer through there is
        safe — and NOT doing so deadlocks: an unparked head that is never
        re-entered spins to MAX_ITERATIONS.
        """
        if self._in_isr:
            return
        self.parks[head_ip] = self.parks.get(head_ip, 0) + 1
        cpu.s.cs, cpu.s.ip = head_cs & 0xFFFF, resume_ip & 0xFFFF
        raise FrameIdle

    def frame(self) -> bool:
        """Advance one displayed frame. False once the program has exited."""
        self._in_isr = True
        try:
            for _ in range(self.irqs_per_frame):
                deliver_interrupt(self.rt, 0x08)
        finally:
            self._in_isr = False
        try:
            self.rt.cpu.run(STEP_BUDGET)
        except FrameIdle:
            pass
        except HaltExecution:
            return False
        self.frames += 1
        return True


def build(boot_dir: Path, lift_dir: Path, game_root: Path):
    rt, manifest = boot_vmless_image(boot_dir, game_root=game_root,
                                     lift_dir=lift_dir)
    return rt, manifest


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--boot-dir", default=str(ROOT / "artifacts" / "boot_image"))
    ap.add_argument("--lift-dir", default=str(ROOT / "artifacts" / "lifted_full"))
    ap.add_argument("--game-root", default=str(ROOT / "assets"))
    ap.add_argument("--frames", type=int, default=0, help="stop after N frames")
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--report", action="store_true",
                    help="print the independence hard-gate banner and exit")
    args = ap.parse_args(argv)

    rt, manifest = build(Path(args.boot_dir), Path(args.lift_dir),
                         Path(args.game_root))
    if args.report:
        print(independence_report(manifest))
        return 0
    print(independence_report(manifest))
    drv = VmlessDriver(rt)

    if args.headless:
        n = args.frames or 400
        for _ in range(n):
            if not drv.frame():
                print(f"[vmless] program exited after {drv.frames} frames")
                break
        plane = rt.cpu.mem.data[0xA0000:0xA0000 + 64000]
        print(f"[vmless] {drv.frames} frames; VGA nonzero px={sum(1 for b in plane if b)}; "
              f"parks={ {f'{k:04X}': v for k, v in sorted(drv.parks.items())} }")
        print(f"[vmless] stdout: {''.join(rt.dos.stdout)[:120] or '(none)'}")
        return 0

    return _window(rt, drv, args)


def _window(rt, drv, args) -> int:
    import numpy as np
    import pygame
    from dos_re.display import Display

    pygame.init()
    disp = Display((960, 720), title="SkyRoads — VMless (no EXE)")
    disp.par = 1.2
    clock = pygame.time.Clock()
    scancodes = _scancode_map(pygame)
    running = True
    while running and (not args.frames or drv.frames < args.frames):
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT or (ev.type == pygame.KEYDOWN
                                          and ev.key == pygame.K_ESCAPE
                                          and pygame.key.get_mods() & pygame.KMOD_SHIFT):
                running = False
            elif ev.type in (pygame.KEYDOWN, pygame.KEYUP):
                sc = scancodes.get(ev.key)
                if sc is not None:
                    # Through the game's OWN recovered INT 09h ISR: push the
                    # scan code the way the 8042 would, then vector.
                    rt.dos.current_scancode = sc if ev.type == pygame.KEYDOWN else (sc | 0x80)
                    rt.dos.kbd_output_buffer_full = True
                    deliver_interrupt(rt, 0x09)
        if not drv.frame():
            print(f"[vmless] program exited after {drv.frames} frames")
            break
        pal = np.asarray(rt.dos.vga_palette + [(0, 0, 0)] * (256 - len(rt.dos.vga_palette)),
                         dtype=np.uint8)
        idx = np.frombuffer(bytes(rt.cpu.mem.data[0xA0000:0xA0000 + 64000]), dtype=np.uint8)
        disp.draw_game(pal[idx].reshape(200, 320, 3))
        disp.flip()
        clock.tick(GAME_FPS)
    pygame.quit()
    print(f"[vmless] closed after {drv.frames} frames; "
          f"parks={ {f'{k:04X}': v for k, v in sorted(drv.parks.items())} }")
    return 0


def _scancode_map(pygame):
    return {pygame.K_UP: 0x48, pygame.K_DOWN: 0x50, pygame.K_LEFT: 0x4B,
            pygame.K_RIGHT: 0x4D, pygame.K_SPACE: 0x39, pygame.K_RETURN: 0x1C,
            pygame.K_ESCAPE: 0x01}


if __name__ == "__main__":
    raise SystemExit(main())
