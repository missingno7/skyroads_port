"""Generated CPUless provider for the unified SkyRoads player.

Starts at the C-startup root ``1010:61F3`` and drives the recovered corpus in
``skyroads/recovered/`` through :class:`dos_re.lift.platform.CPUlessPlatformRuntime`
-- pure Python over the boot-image memory + a device model + a virtual clock.  It
NEVER imports or instantiates the interpreter (``dos_re.cpu``); a runtime import
guard is the dynamic backstop and ``tools/lint_cpuless.py`` is the static proof.

This is a PLAYABLE game, not a boot probe: the default is an interactive pygame
window with live keyboard, running until you quit.  ``--headless`` is the opt-in
for agents/CI (no window, frame-capped, no input source).

The frame model lives in :mod:`skyroads.cpuless_driver`. Select this provider
with ``scripts/play.py --profile detached --composition generated-abi``.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

CANONICAL_ENTRY = (0x1010, 0x61F3)
STANDALONE_DIR = ROOT / "skyroads" / "recovered"
#: SkyRoads presents at 30 Hz with 6 IRQ0 ticks per frame (= 180 Hz IRQ0), the
#: ratio every recorded replay carries as ``timer_irqs_per_frame``.
PRESENT_HZ = 30


def _ensure_corpus(rebuild: bool) -> None:
    if rebuild or not any(STANDALONE_DIR.glob("func_*.py")):
        print("[cpuless] regenerating the standalone corpus ...")
        r = subprocess.run([sys.executable,
                            str(ROOT / "scripts/build_recovered.py")])
        if r.returncode != 0:
            raise SystemExit(r.returncode)


def _load_boot(bootstrap_artifacts: dict[str, Path]):
    from dos_re.memory import Memory
    state_path = bootstrap_artifacts["skyroads-boot-state"]
    memory_path = bootstrap_artifacts["skyroads-boot-memory"]
    manifest_path = bootstrap_artifacts["skyroads-boot-manifest"]
    state = json.loads(state_path.read_text(encoding="utf-8"))
    img = memory_path.read_bytes()
    mem = Memory()
    mem.data[:len(img)] = img
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return mem, state["cpu"], state.get("dos", {}), manifest


class _RtShim:
    """Minimal ``(cpu.mem, dos)`` view of the CPU-free runtime.

    Two dos_re helpers navigate a Runtime by attribute path but never step a CPU:
    ``snapshot_headless._restore_dos_state`` (``rt.program.memory``/``rt.dos``)
    and ``framebuffer.decode_frame_default`` (``rt.cpu.mem``/``rt.dos``).  This
    shim satisfies both without a CPU existing -- ``.cpu`` here is a namespace
    holding the memory, nothing more."""

    def __init__(self, dos, mem):
        import types
        self.dos = dos
        self.program = types.SimpleNamespace(memory=mem)
        self.cpu = types.SimpleNamespace(mem=mem)


def _vga_nonzero(mem) -> int:
    base = 0xA000 * 16
    return sum(1 for b in mem.data[base:base + 64000] if b)


class _Done(Exception):
    """Frame budget reached (headless)."""


class _Quit(Exception):
    """The player closed the window."""


def _boot(
    rebuild: bool,
    *,
    mouse_present: bool,
    bootstrap_artifacts: dict[str, Path],
):
    """Build the CPU-free runtime: boot image + device model + platform."""
    _ensure_corpus(rebuild)
    from dos_re.lift.platform import CPUlessPlatformRuntime
    from dos_re.dos import DOSMachine
    from dos_re.snapshot_headless import _restore_dos_state   # runtime CPU-free

    mem, regs0, dos_meta, boot_manifest = _load_boot(bootstrap_artifacts)
    poisoned = boot_manifest.get("code_bytes_present_after")
    if poisoned is not None:
        print(f"Recovered code present in boot image: {poisoned} bytes "
              f"(0 = severed from the original EXE)")

    dos = DOSMachine(ROOT)
    # Restore the snapshot's DOS/device + memory-arena state (allocations,
    # next_alloc_segment, video/PIT/OPL/EGA). Without this the C-runtime heap
    # allocation (int 21/48h) fails against a fresh arena and the startup takes
    # its out-of-memory error path -- the real reason the cold boot diverged.
    _restore_dos_state(_RtShim(dos, mem), dos_meta)
    dos.mouse_present = mouse_present
    # A console read on an empty type-ahead buffer must WAIT for the player, not
    # synthesise the DOSMachine's default 0x011B (Esc) -- that phantom key makes
    # every press-any-key screen quit the game.  The frame driver services the
    # wait through blocking_read_cb.
    dos.console_input_fallback = None
    rt = CPUlessPlatformRuntime(mem, game_root=ROOT, dos=dos)
    return mem, dos, rt, regs0


def _enter(rt, regs0):
    """Call the recovered C-startup root -- the whole game runs inside this."""
    import inspect
    from skyroads.recovered.func_1010_61f3 import func_1010_61f3
    kw = {k: v for k, v in regs0.items()
          if k in inspect.signature(func_1010_61f3).parameters}
    kw["_flags_in"] = regs0.get("flags", 2)
    return rt.call(func_1010_61f3, **kw)


def _key_deliverer(mem, dos, rt):
    """CPU-free equivalent of ``dos_re.interrupts.deliver_scancode``.

    Present the code on port 60h and update BIOS-visible keyboard state.  If the
    game installed its OWN INT 09h, also run it -- recovered, no CPU.  (SkyRoads
    does not: IVT[9] stays at the power-on BIOS entry and its menus read the
    type-ahead buffer via INT 21h/16h, while gameplay polls port 60h.)"""
    from dos_re.keyboard import BIOS_INT9_ENTRY          # CPU-free leaf
    from skyroads.recovered.func_1010_3bcc import func_1010_3bcc
    #: the recovered INT 09h ISR is NOT flags-live -- never pass it _flags_in.
    key_in = ("ax", "bp", "bx", "cx", "di", "ds", "dx", "si", "sp", "ss")

    def deliver(scancode: int, regs: dict) -> None:
        dos.current_scancode = scancode & 0xFF
        dos.kbd_output_buffer_full = True
        dos.note_bios_keystroke(scancode & 0xFF)
        voff, vseg = mem.rw(0, 0x24), mem.rw(0, 0x26)
        if (vseg, voff) != BIOS_INT9_ENTRY:             # game's own ISR installed
            kw = {k: regs[k] for k in key_in if k in regs}
            func_1010_3bcc(mem, rt, **kw)

    return deliver


def run_headless(
    frames: int,
    rebuild: bool,
    bootstrap_artifacts: dict[str, Path],
    diagnostics=None,
) -> int:
    """No window, no input, frame-capped: the CI/agent probe."""
    from skyroads.cpuless_driver import CPUlessFrameDriver
    from skyroads.recovered.func_1010_3b17 import func_1010_3b17
    from dos_re.x86 import HaltExecution          # CPU-FREE shared leaf

    mem, dos, rt, regs0 = _boot(
        rebuild,
        mouse_present=False,
        bootstrap_artifacts=bootstrap_artifacts,
    )
    limit = frames or 30
    done = {"n": 0}                 # frames actually presented

    def present(frame):
        if frame == 0:
            cs, ip = driver.head or (0, 0)
            print(f"[cpuless] REACHED FIRST FRAME BOUNDARY {cs:04X}:{ip:04X} "
                  f"-- CPU-free cold boot to the frame loop")
        done["n"] = frame + 1
        if done["n"] >= limit:
            raise _Done()

    driver = CPUlessFrameDriver(mem, rt, func_1010_3b17,
                                present=present).install(rt)
    print("[cpuless] boot: CPUlessPlatformRuntime.call(1010:61F3) -- NO CPU, "
          "NO interpreter (guard armed)")
    try:
        _enter(rt, regs0)
        print(f"[cpuless] program terminated after {done['n']} frame(s) "
              f"-- no CPU, no interpreter")
    except _Done:
        print(f"[cpuless] rendered {done['n']} frames (VGA nonzero "
              f"px={_vga_nonzero(mem)}) CPU-free -- no CPU, no interpreter")
    except HaltExecution:                # int 21h/4Ch: the game itself exited
        print(f"[cpuless] the game exited (int 21/4C) after {done['n']} "
              f"frame(s) -- no CPU, no interpreter")
    except BaseException as e:           # noqa: BLE001 -- report ANY stop, then re-raise
        # No input source here, so no replay to record; the machine state and the
        # recovered call chain are still worth having.
        if diagnostics is not None:
            diagnostics(
                e, mem=mem, dos=dos, driver=driver,
                stage="cpuless-headless")
        raise
    return 0


def run_interactive(scale: int, square_pixels: bool, present_hz: int,
                    rebuild: bool, bootstrap_artifacts: dict[str, Path],
                    diagnostics=None) -> int:
    """The playable window: live keyboard, running until you quit."""
    import numpy as np
    import pygame
    from dos_re.display import Display
    from dos_re.framebuffer import WIDTH, HEIGHT, decode_frame_default
    from dos_re.keyboard import KeyDispatcher, scancode_table
    from skyroads.cpuless_driver import (CPUlessFrameDriver,
                                         TIMER_IRQS_PER_FRAME)
    from skyroads.recovered.func_1010_3b17 import func_1010_3b17
    from dos_re.x86 import HaltExecution          # CPU-FREE shared leaf

    mem, dos, rt, regs0 = _boot(
        rebuild,
        mouse_present=True,
        bootstrap_artifacts=bootstrap_artifacts,
    )
    shim = _RtShim(dos, mem)
    deliver = _key_deliverer(mem, dos, rt)

    pygame.init()
    par = 1.0 if square_pixels else 1.2
    # Size the window for the GAME's framebuffer (mode 13h, 320x200), NOT for
    # whatever the first decode returns: we boot at the C-startup root, where the
    # machine is still in TEXT mode, so decoding now yields an 80x25 text render
    # (640x400) and would size the window to the boot console.  draw_game
    # letterboxes whatever arrives, so the brief text-mode phase still shows.
    display = Display((WIDTH * scale, int(HEIGHT * par) * scale),
                      title="SkyRoads -- CPUless (recovered, no CPU)")
    display.par = par
    scancodes = scancode_table(pygame)
    clock = pygame.time.Clock()

    # A key must be HELD for at least one frame or a quick tap can be set and
    # cleared before the game polls it (see dos_re.keyboard).  The dispatcher
    # defers each break by a frame; ``regs`` is rebound per frame below.
    live = {"regs": {}}

    # A key must be HELD for at least one frame or a quick tap can be set and
    # cleared before the game polls it (see dos_re.keyboard).  The dispatcher
    # defers each break by a frame; ``regs`` is rebound per frame below.
    dispatcher = KeyDispatcher(lambda sc: deliver(sc, live["regs"]))

    def pump_events():
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                raise _Quit()
            if event.type == pygame.VIDEORESIZE:
                display.resize(event.w, event.h)
            elif event.type in (pygame.KEYDOWN, pygame.KEYUP):
                sc = scancodes.get(event.key)
                if sc is None:
                    continue
                if event.type == pygame.KEYDOWN:
                    dispatcher.post_down(sc)
                else:
                    dispatcher.post_up(sc)

    def present(frame):
        rgb = np.asarray(decode_frame_default(shim), np.uint8)
        display.draw_game(rgb)
        display.flip()
        pygame.display.set_caption(
            f"SkyRoads -- CPUless (no CPU) | frame={frame}")
        clock.tick(present_hz)
        pump_events()

    def supply_input(frame, regs):
        live["regs"] = regs        # the ISR needs the live bundle at delivery
        dispatcher.pump()          # makes now, deferred breaks when due

    driver = CPUlessFrameDriver(mem, rt, func_1010_3b17, present=present,
                                supply_input=supply_input).install(rt)
    print(f"[cpuless] SkyRoads running with NO CPU and NO interpreter "
          f"(guard armed) -- {WIDTH}x{HEIGHT} @ {present_hz} Hz, "
          f"close the window to quit")
    try:
        _enter(rt, regs0)
        print(f"[cpuless] the game exited normally after {driver.frame} frames "
              f"-- no CPU, no interpreter")
    except HaltExecution:                # int 21h/4Ch: the game itself exited
        print(f"[cpuless] the game exited (int 21/4C) after {driver.frame} "
              f"frames -- no CPU, no interpreter")
    except _Quit:
        print(f"[cpuless] quit after {driver.frame} frames -- no CPU, "
              f"no interpreter")
    except BaseException as e:           # noqa: BLE001 -- report ANY stop, then re-raise
        # Includes the fail-loud hard wall.  The bundle is written HERE, where
        # the machine and the session recording are still in scope; run() below
        # only formats the frontier message.
        if diagnostics is not None:
            diagnostics(e, mem=mem, dos=dos, driver=driver, stage="cpuless")
        raise
    finally:
        pygame.quit()
    return 0


def run(args, *, bootstrap_artifacts: dict[str, Path], diagnostics=None) -> int:
    from dos_re.lift.platform import UnsupportedPlatformEffect
    try:
        from skyroads.recovered._dyncall import UnknownDispatchTarget
    except Exception:                       # noqa: BLE001
        UnknownDispatchTarget = ()
    try:
        if args.headless:
            return run_headless(
                args.frames,
                args.rebuild,
                bootstrap_artifacts,
                diagnostics,
            )
        return run_interactive(
            args.scale, args.square_pixels, args.present_hz, args.rebuild,
            bootstrap_artifacts, diagnostics)
    except (UnsupportedPlatformEffect, *([UnknownDispatchTarget]
                                         if UnknownDispatchTarget else [])) as e:
        print(f"\n[cpuless] HARD-WALL FRONTIER (fail-loud, by design):\n  {e}")
        print("[cpuless] the run reached code beyond the current --observed "
              "coverage; close it with a fuller capture (see "
              "docs/cpuless_standalone.md).")
        return 3
    except RuntimeError as e:
        if "CPUless" in str(e):
            print(f"\n[cpuless] HARD-WALL FRONTIER (fail-loud, by design):\n  {e}")
            print("[cpuless] a runtime-dead path (per the current --observed "
                  "trace) was reached; close it with a fuller capture.")
            return 3
        raise
