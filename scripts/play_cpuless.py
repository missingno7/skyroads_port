"""play_cpuless.py -- the TRUE standalone CPUless runner (NO CPU, NO interpreter).

Starts at the C-startup root ``1010:61F3`` and drives the recovered corpus in
``skyroads/recovered/`` through :class:`dos_re.lift.platform.CPUlessPlatformRuntime`
-- pure Python over the boot-image memory + a device model + a virtual clock.  It
NEVER imports or instantiates the interpreter (``dos_re.cpu``); a runtime import
guard is the dynamic backstop and ``tools/lint_cpuless.py`` is the static proof.

This is a PLAYABLE game, not a boot probe: the default is an interactive pygame
window with live keyboard, running until you quit.  ``--headless`` is the opt-in
for agents/CI (no window, frame-capped, no input source).

The frame model is the one proven byte-exact by ``scripts/verify_cpuless.py``
over a full 672-frame cold playthrough, and both share the single implementation
in :mod:`skyroads.cpuless_driver` so they cannot drift.

Usage:
    python scripts/play_cpuless.py                     # play it (window)
    python scripts/play_cpuless.py --scale 4
    python scripts/play_cpuless.py --headless --frames 30
    python scripts/play_cpuless.py --rebuild           # regenerate the corpus
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: This port's own CPU-carrying surface, on top of the framework's BASE_FORBIDDEN
#: (dos_re.cpu / cpu386 / lift.install / lift.runtime / runtime).  x86 is the
#: CPU-FREE shared leaf (constants + HaltExecution) and stays allowed.
_EXTRA_FORBIDDEN = ("skyroads.lifted",)


def _arm_import_guard() -> None:
    """Arm the CPUless wall via the framework's shared guard.

    This used to be a hand-rolled hook that matched only the ``name`` argument.
    That has a BLIND SPOT: a relative ``from .cpu import CPU8086`` inside dos_re
    arrives as ``name='cpu', level=1`` WITHOUT the package, so it never matched
    'dos_re.cpu' and sailed straight through -- exactly where the framework's own
    intra-package imports live.  (Verified against this runner before switching:
    the relative form passed the old guard.)  ``install_import_guard`` resolves
    the absolute dotted name first, so the wall actually holds.

    Fires only on an EXECUTED import; tools/lint_cpuless.py remains the STATIC
    proof for paths a given run does not take."""
    from dos_re.lift.standalone import install_import_guard
    install_import_guard(extra_forbidden=_EXTRA_FORBIDDEN)


CANONICAL_ENTRY = (0x1010, 0x61F3)
BOOT_DIR = ROOT / "artifacts" / "boot_image"
STANDALONE_DIR = ROOT / "skyroads" / "recovered"
#: SkyRoads presents at 30 Hz with 6 IRQ0 ticks per frame (= 180 Hz IRQ0), the
#: ratio every recorded demo carries as ``timer_irqs_per_frame``.
PRESENT_HZ = 30


def _ensure_corpus(rebuild: bool) -> None:
    if rebuild or not any(STANDALONE_DIR.glob("func_*.py")):
        print("[cpuless] regenerating the standalone corpus ...")
        r = subprocess.run([sys.executable,
                            str(ROOT / "scripts/build_recovered.py")])
        if r.returncode != 0:
            raise SystemExit(r.returncode)


def _load_boot():
    from dos_re.memory import Memory
    state = json.loads((BOOT_DIR / "state.json").read_text(encoding="utf-8"))
    img = (BOOT_DIR / "memory_1mb.bin").read_bytes()
    mem = Memory()
    mem.data[:len(img)] = img
    manifest = json.loads((BOOT_DIR / "manifest.json").read_text(encoding="utf-8")) \
        if (BOOT_DIR / "manifest.json").exists() else {}
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


def _end_session(recorder, frame: int, keep: bool) -> None:
    """Finish a session that did NOT crash.

    The recording only exists to reproduce a crash, so a clean run drops it --
    otherwise every play session litters artifacts/demos with a demo nobody
    asked for.  ``--keep-demo`` keeps it (useful for capturing a coverage demo
    on purpose: play the path, keep it, feed it to build_codemap)."""
    if recorder is None or not getattr(recorder, "active", False):
        return
    demo_dir = recorder.directory
    try:
        recorder.stop(boundary=frame)
    except Exception:                                # noqa: BLE001
        pass          # finalising failed -- still drop it below, see note
    if keep:
        print(f"[cpuless] session demo kept: {demo_dir}")
        return
    # Delete even when stop() failed. Returning early on that path left a
    # half-written recording behind, and artifacts/demos is a TRACKED directory,
    # so the next `git add -A` swept it into a commit (it did).
    import shutil
    shutil.rmtree(demo_dir, ignore_errors=True)


class _Done(Exception):
    """Frame budget reached (headless)."""


class _Quit(Exception):
    """The player closed the window."""


def _boot(rebuild: bool, *, mouse_present: bool):
    """Build the CPU-free runtime: boot image + device model + platform."""
    _ensure_corpus(rebuild)
    # THE STITCH, before anything imports the corpus.  Generated modules bind
    # their callees with direct imports at import time, so the module object is
    # the only seam and it must be shadowed first.  With no overrides registered
    # this is a provable no-op -- the composite stays bit-for-bit the generated
    # program -- which is why adopting the seam needs no new gate: the existing
    # cold-start differential already covers it.
    from skyroads.cpuless_overrides import install_overrides
    stitched = install_overrides()
    if stitched:
        print(f"[cpuless] stitched {len(stitched)} hand-recovered override(s): "
              f"{', '.join(stitched)}")

    from dos_re.lift.platform import CPUlessPlatformRuntime
    from dos_re.dos import DOSMachine
    from dos_re.snapshot_headless import _restore_dos_state   # runtime CPU-free

    mem, regs0, dos_meta, boot_manifest = _load_boot()
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


def run_headless(frames: int, rebuild: bool) -> int:
    """No window, no input, frame-capped: the CI/agent probe."""
    from skyroads.cpuless_driver import CPUlessFrameDriver
    from skyroads.recovered.func_1010_3b17 import func_1010_3b17
    from dos_re.x86 import HaltExecution          # CPU-FREE shared leaf

    mem, dos, rt, regs0 = _boot(rebuild, mouse_present=False)
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
        # No input source here, so no demo to record; the machine state and the
        # recovered call chain are still worth having.
        from skyroads.crash_report import write_crash_bundle, print_crash_summary
        bundle = write_crash_bundle(ROOT / "artifacts" / "crashes", e, mem=mem,
                                    dos=dos, frame=driver.frame, head=driver.head,
                                    stage="cpuless-headless")
        print_crash_summary(bundle, e, frame=driver.frame)
        raise
    return 0


def run_interactive(scale: int, square_pixels: bool, present_hz: int,
                    rebuild: bool, keep_demo: bool = False) -> int:
    """The playable window: live keyboard, running until you quit."""
    import numpy as np
    import pygame
    from dos_re.display import Display
    from dos_re.framebuffer import WIDTH, HEIGHT, decode_frame_default
    from dos_re.keyboard import KeyDispatcher, scancode_table
    from skyroads.replay import SkyroadsReplayRecorder
    from skyroads.cpuless_driver import (CPUlessFrameDriver,
                                         TIMER_IRQS_PER_FRAME)
    from skyroads.recovered.func_1010_3b17 import func_1010_3b17
    from skyroads.crash_report import write_crash_bundle, print_crash_summary
    from dos_re.x86 import HaltExecution          # CPU-FREE shared leaf

    mem, dos, rt, regs0 = _boot(rebuild, mouse_present=True)
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

    # Record EVERY session, unconditionally.  A hard-wall stop is a coverage
    # bug, and the cheapest way to fix one is to replay the exact session that
    # found it -- impossible if recording were opt-in, because nobody opts in
    # before the crash they did not expect.  A cold-start demo needs no start
    # snapshot (we boot from the C-startup root every time) and no CPU.
    recorder = SkyroadsReplayRecorder(
        root=ROOT / "artifacts" / "demos", name="cpuless_session",
        metadata={"game": "skyroads", "exe": "SKYROADS.EXE", "command_tail": "",
                  "timer_irqs_per_frame": TIMER_IRQS_PER_FRAME,
                  "mouse_present": True, "runner": "play_cpuless"})
    recorder.start_cpuless(
        rt, lambda: live["regs"] or regs0, boundary=0)

    def deliver_and_record(sc: int) -> None:
        # Record at the frame the key is DELIVERED at, not when it was pressed:
        # the dispatcher defers breaks, and a replay re-delivers at the recorded
        # boundary, so recording the press frame would shift held keys by a frame.
        recorder.record_scan(boundary=driver.frame, scancode=sc)
        deliver(sc, live["regs"])

    # A key must be HELD for at least one frame or a quick tap can be set and
    # cleared before the game polls it (see dos_re.keyboard).  The dispatcher
    # defers each break by a frame; ``regs`` is rebound per frame below.
    dispatcher = KeyDispatcher(deliver_and_record)

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
        _end_session(recorder, driver.frame, keep_demo)
        print(f"[cpuless] the game exited normally after {driver.frame} frames "
              f"-- no CPU, no interpreter")
    except HaltExecution:                # int 21h/4Ch: the game itself exited
        _end_session(recorder, driver.frame, keep_demo)
        print(f"[cpuless] the game exited (int 21/4C) after {driver.frame} "
              f"frames -- no CPU, no interpreter")
    except _Quit:
        _end_session(recorder, driver.frame, keep_demo)
        print(f"[cpuless] quit after {driver.frame} frames -- no CPU, "
              f"no interpreter")
    except BaseException as e:           # noqa: BLE001 -- report ANY stop, then re-raise
        # Includes the fail-loud hard wall.  The bundle is written HERE, where
        # the machine and the session recording are still in scope; run() below
        # only formats the frontier message.
        bundle = write_crash_bundle(ROOT / "artifacts" / "crashes", e, mem=mem,
                                    dos=dos, frame=driver.frame, head=driver.head,
                                    recorder=recorder, stage="cpuless")
        print_crash_summary(bundle, e, frame=driver.frame)
        raise
    finally:
        pygame.quit()
    return 0


def run(args) -> int:
    # Path first: the guard itself now lives in dos_re (the shared standalone
    # host), so it must be importable before we arm it.
    sys.path.insert(0, str(ROOT / "dos_re"))
    sys.path.insert(0, str(ROOT))
    _arm_import_guard()
    from dos_re.lift.platform import UnsupportedPlatformEffect
    try:
        from skyroads.recovered._dyncall import UnknownDispatchTarget
    except Exception:                       # noqa: BLE001
        UnknownDispatchTarget = ()
    try:
        if args.headless:
            return run_headless(args.frames, args.rebuild)
        return run_interactive(args.scale, args.square_pixels,
                               args.present_hz, args.rebuild, args.keep_demo)
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


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--headless", action="store_true",
                    help="no window, no input, frame-capped (agents/CI); "
                         "default is the interactive window")
    ap.add_argument("--frames", type=int, default=0,
                    help="headless: stop after N frames (0 = 30)")
    ap.add_argument("--scale", type=int, default=3, help="window pixel scale")
    ap.add_argument("--square-pixels", action="store_true",
                    help="1:1 pixels instead of the 1.2 CRT aspect")
    ap.add_argument("--present-hz", type=int, default=PRESENT_HZ,
                    help="frames presented per second")
    ap.add_argument("--keep-demo", action="store_true",
                    help="keep the session's input demo after a clean run "
                         "(a crash always keeps it, in the crash bundle)")
    ap.add_argument("--rebuild", action="store_true",
                    help="regenerate the standalone corpus first")
    return run(ap.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
