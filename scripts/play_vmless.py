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
from dos_re.dos import ConsoleInputWouldBlock  # noqa: E402
from dos_re.independence import boot_vmless_image, independence_report  # noqa: E402
from dos_re.interrupts import deliver_interrupt  # noqa: E402
# From runtime_core, NOT dos_re.runtime / skyroads.runtime / dos_re.player:
# those reach create_runtime -> load_mz_program, and importing them here
# would put the EXE loader on this runner's module graph -- exactly what
# lint_independence forbids. runtime_core is loader-free by construction.
from dos_re.crash import crash_dir, save_crash  # noqa: E402
from dos_re.runtime_core import (enable_sound_blaster,  # noqa: E402
                                 use_real_console_input)

#: Measured from the VM (see play_native.py): the PIT reload is 6628, so the
#: timer runs at 1193182/6628 = 180 Hz and the music ISR fires once per IRQ —
#: 6 IRQs per displayed frame, i.e. 30 fps.
GAME_FPS = 30
TIMER_IRQS_PER_FRAME = 6
#: Runaway guard per frame. A frame that neither parks nor halts within this
#: many lifted dispatches is a bug (a spin with no boundary head), not a slow
#: frame — fail loud rather than hang.
STEP_BUDGET = 2_000_000


def _stamp() -> str:
    """Wall-clock, for the crash directory name only."""
    from datetime import datetime
    return datetime.now().strftime("%Y%m%d_%H%M%S")


class FrameIdle(Exception):
    """The corpus parked in a tick-wait it cannot leave until the next frame."""


class VmlessDriver:
    """Drives the generated corpus one displayed frame at a time."""

    def __init__(self, rt, *, irqs_per_frame: int = TIMER_IRQS_PER_FRAME,
                 crash_root: str | Path = 'artifacts/crashes',
                 stamp: str = 'run'):
        self.rt = rt
        self.irqs_per_frame = irqs_per_frame
        self.crash_root = crash_root
        self.stamp = stamp
        self.frames = 0
        self.parks: dict[int, int] = {}      # head_ip -> times parked
        self._in_isr = False
        self._seen: set = set()              # heads passed THIS frame
        rt.cpu.boundary_hook = self._boundary

    def _boundary(self, cpu, head_cs, head_ip, resume_ip):
        """Boundary-head observer (ABI: cpu, head_cs, head_ip, resume_ip).

        PARK ON RE-ARRIVAL, NOT ON ARRIVAL. ``[1600]`` cannot change until the
        next frame's IRQs, so a tick-wait provably cannot exit this frame — but
        that does NOT mean it has nothing left to do. Its BODY may not have run
        yet, and a tick-wait body is not always empty: 1010:434A's is the fade's
        palette re-blend. Parking on the first pass skipped the blend outright
        and left its buffer (DGROUP 31AB) zeroed — the whole fade rendered as
        flat fill, and it took a frame-by-frame diff against the ASM oracle to
        see it, because nothing failed.

        So let pass 1 run the body to its steady state, and park on pass 2: the
        wait is still unsatisfied and nothing it reads can change, which is the
        proof. One extra iteration per head per frame. This is the same rule
        skyroads/pacing.py's verified park_fade_wait encodes — park only once
        _fade_loop_cache holds a blend for the CURRENT tick.

        Re-point CS:IP at the resume entry (the contract in dos_re/lift/emit.py)
        so the next frame re-dispatches inside the lifted body, then unwind.

        EXCEPT inside an ISR: an interrupt handler must run to its IRET or the
        machine is left mid-frame with a half-delivered interrupt (and the
        emulated stack still holding its frame). ISRs do not wait on the tick
        they are themselves bumping, so letting the observer through there is
        safe — and NOT doing so deadlocks: an unparked head that is never
        re-entered spins to MAX_ITERATIONS.
        """
        if self._in_isr:
            return
        key = (head_cs, head_ip)
        if key not in self._seen:
            self._seen.add(key)              # pass 1: let the body run
            return
        self.parks[head_ip] = self.parks.get(head_ip, 0) + 1
        cpu.s.cs, cpu.s.ip = head_cs & 0xFFFF, resume_ip & 0xFFFF
        raise FrameIdle

    def frame(self) -> bool:
        """Advance one displayed frame. False once the program has exited.

        Anything that is NOT one of the three expected outcomes (park, waiting
        for a key, program exit) leaves a crash snapshot behind: see
        ``self.crash_root``. A wall violation, an iteration guard, a bad lift --
        each of those has cost a bespoke probe and a replay from frame 0 to get
        back to a machine that was standing right here when it broke.
        """
        self._seen.clear()       # "re-arrival" is per frame: new tick, new pass
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
        except ConsoleInputWouldBlock:
            # The menu is waiting for a key (INT 21h AH=07h) and there is none.
            # That ends the frame exactly as a tick-wait park does: the DOS layer
            # has rewound CS:IP back ONTO the int, so next frame re-dispatches
            # there and re-issues the read -- which is why the int is a declared
            # resume entry in the emitted corpus.
            #
            # This except is the OTHER HALF of clearing console_input_fallback:
            # the fallback exists so a driver with no frame loop cannot hang, so
            # a driver that clears it must handle the block itself. Without both,
            # you get one of the two failures -- a phantom Esc that quits the
            # game, or an unhandled exception at the menu.
            pass
        except HaltExecution:
            return False
        except BaseException as exc:            # noqa: BLE001 -- then re-raised
            self.crash(exc)
            raise
        self.frames += 1
        return True

    def crash(self, exc: BaseException) -> Path:
        """Leave the broken machine on disk, resumable, and say where it is."""
        out = save_crash(
            self.rt, crash_dir(self.crash_root, "vmless", self.stamp), exc=exc,
            status="vmless-crash", frame=self.frames,
            parks={f"{k:04X}": v for k, v in sorted(self.parks.items())})
        print(f"\n[vmless] CRASHED at frame {self.frames}: "
              f"{type(exc).__name__}: {str(exc)[:200]}")
        print(f"[vmless] machine saved -> {out}")
        print(f"[vmless] resume it (you land ON the fault, no replay):\n"
              f"           from dos_re.snapshot_headless import load_snapshot_headless\n"
              f"           rt = load_snapshot_headless(r'{out}', game_root='assets')")
        return out


def build(boot_dir: Path, lift_dir: Path, game_root: Path, *, sound: bool = True):
    """Boot the image AND set up the machine around it.

    ``boot_vmless_image`` builds a CPU + DOS + BIOS from the image; it does not
    and should not know what THIS game needs attached. Every other entry point
    does that part via skyroads.runtime (create_game_runtime /
    load_game_snapshot) -- this one bypassed them to avoid the EXE, and quietly
    inherited a bare machine. All three of the following were missing, and all
    three are things the runtime module has always done:
    """
    rt, manifest = boot_vmless_image(boot_dir, game_root=game_root,
                                     lift_dir=lift_dir)

    # 1. THE PHANTOM ESC. DOSMachine defaults console_input_fallback to 0x011B
    #    so a bare cpu.run() with no driver loop cannot hang on a blocking read.
    #    SkyRoads reads its menu keys with INT 21h AH=07h, so it receives that
    #    Esc, reads it as "quit", and calls exit(0) -- the game appearing to
    #    quit itself seconds after the menu appears, with no keypress. This
    #    driver has a frame loop and handles ConsoleInputWouldBlock, so the
    #    synthesis is not needed here and is only harmful. (dos_re's
    #    _use_real_console_input documents this exact failure; play_vmless was
    #    the one path that never called it.)
    use_real_console_input(rt)

    # 2. SOUND HARDWARE. Without it there is no OPL to play music through --
    #    and SkyRoads' own detection can take its "not enough sound hardware"
    #    exit. detection_only mirrors what the viewer does for normal play.
    if sound:
        enable_sound_blaster(rt, detection_only=True)

    # 3. THE MOUSE. dos_re keeps INT 33h absent unless a front-end opts in;
    #    the interactive viewer opts in, so the interactive VMless one must too,
    #    or the menus behave as they do on a machine with no mouse.
    rt.dos.mouse_present = True
    return rt, manifest


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--boot-dir", default=str(ROOT / "artifacts" / "boot_image"))
    ap.add_argument("--lift-dir", default=str(ROOT / "artifacts" / "lifted_full"))
    ap.add_argument("--game-root", default=str(ROOT / "assets"))
    ap.add_argument("--frames", type=int, default=0, help="stop after N frames")
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--present-hz", type=int, default=60,
                    help="audio present rate (matches play.py's default)")
    ap.add_argument("--no-sound", action="store_true",
                    help="leave the sound hardware detached (silent run)")
    ap.add_argument("--crash-root", default=str(ROOT / "artifacts" / "crashes"),
                    help="where a crash leaves its resumable snapshot")
    ap.add_argument("--report", action="store_true",
                    help="print the independence hard-gate banner and exit")
    args = ap.parse_args(argv)

    rt, manifest = build(Path(args.boot_dir), Path(args.lift_dir),
                         Path(args.game_root), sound=not args.no_sound)
    if args.report:
        print(independence_report(manifest))
        return 0
    print(independence_report(manifest))
    drv = VmlessDriver(rt, crash_root=args.crash_root, stamp=_stamp())

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
    # The music. Imported HERE and not at module scope for the same reason the
    # display is: this is the viewer's business, not the VMless runner's, and a
    # local import keeps the runner's module graph minimal. The sink is an
    # OBSERVER -- the lifted corpus writes the OPL ports exactly as the original
    # did, and the sink turns that write stream into sound; it decides nothing
    # about what plays. (skyroads.audio reaches only dos_re.audio_sink, so it is
    # loader-free and does not threaten the independence wall.)
    from skyroads.audio import SkyroadsAudioSink

    pygame.init()
    disp = Display((960, 720), title="SkyRoads — VMless (no EXE)")
    disp.par = 1.2
    clock = pygame.time.Clock()
    scancodes = _scancode_map(pygame)
    audio = SkyroadsAudioSink(pygame, rt, args.present_hz)
    if not audio.available:
        print("[vmless] audio unavailable -- running silent")
        audio = None
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
        if audio is not None:
            audio.pump()
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
