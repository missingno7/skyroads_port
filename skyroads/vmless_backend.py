"""Generated VMless provider for the unified SkyRoads player.

Every selected instruction in this provider is generated Python from Recovery
IR. State still lives in DOS-layout memory behind a CPU-shaped register model,
so “VMless” is an implementation property rather than a player or recovery
stage. The execution plan and exported dependency closure decide detachment;
optional code poisoning supplies additional no-fallback evidence.

What makes frames tick
----------------------
The lifted corpus PARKS instead of spinning. SkyRoads paces itself off
``ds:[1600]``, the tick counter its INT 08h ISR bumps; the viewer delivers a
frame's IRQs only at frame start, so ``[1600]`` is architecturally constant for
the whole frame and any loop waiting on it can never exit. Generated boundary
observers and ``RESUME_ENTRIES`` preserve the corpus continuation at the
retained Recovery IR boundary heads.

So a frame is: deliver the timer IRQs (through the game's OWN recovered INT 08h
ISR), run until the corpus reports a boundary head, END THE FRAME there. The
park unwinds Python, but the machine state does not live in Python — it is in
the emulated stack and CS:IP — so the next frame's ``run()`` re-dispatches at
the resume entry that ``activate_generated_graph`` registered, and the lifted
body continues from the right basic block. Re-arrival parking over this complete
generated boundary set is its own replay-verified provider contract. It is
separate from the interpreted frame-parking runtime service, which intercepts
only two proven side-effect-free waits.

Selected with ``scripts/play.py --composition faithful-product``.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dos_re"))
sys.path.insert(0, str(ROOT))

from dos_re.cpu import HaltExecution  # noqa: E402
from dos_re.dos import ConsoleInputWouldBlock  # noqa: E402
from dos_re.independence import (boot_generated_graph_image,  # noqa: E402
                                 generated_graph_boot_report)
from dos_re.replay_input import mouse_sample  # noqa: E402
from dos_re.keyboard import KeyDispatcher, scancode_table  # noqa: E402
from dos_re.interrupts import deliver_interrupt  # noqa: E402
# From runtime_core, NOT dos_re.runtime / skyroads.runtime / dos_re.player:
# those reach create_runtime -> load_mz_program, and importing them here
# would put the EXE loader on this provider's module graph -- exactly what
# lint_independence forbids. runtime_core is loader-free by construction.
from dos_re.crash import crash_dir, save_crash  # noqa: E402
from dos_re.runtime_core import enable_sound_blaster  # noqa: E402
from dos_re.regions import RegionHandoff, ensure_region_dispatcher  # noqa: E402

#: Measured from the oracle: the PIT reload is 6628, so the
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
        self.last_boundary_kind = ""
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
        wait is still unsatisfied and nothing it reads can change. One extra
        iteration per generated boundary head per frame preserves the body
        effects before the continuation is parked.

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
        from skyroads.gameplay_region import maybe_enter_gameplay_region
        if maybe_enter_gameplay_region(self.rt, cpu, head_cs, head_ip):
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
        ``self.crash_root``. An unresolved frontier, iteration guard, bad lift,
        each of those has cost a bespoke probe and a replay from frame 0 to get
        back to a machine that was standing right here when it broke.
        """
        # A guest-fallback point is only a diagnostic slice through one
        # unfinished semantic frame.  Preserve boundary visits across those
        # slices: otherwise a long body (notably the 434A palette blend) is
        # mistaken for a fresh first arrival and runs twice before parking.
        # Completed semantic boundaries start a genuinely new frame.
        if getattr(
            self.rt, "_skyroads_replay_boundary_kind", None,
        ) != "guest-fallback":
            self._seen.clear()
        self.last_boundary_kind = ""
        self._in_isr = True
        try:
            for _ in range(self.irqs_per_frame):
                deliver_interrupt(self.rt, 0x08)
        finally:
            self._in_isr = False
        dispatcher = ensure_region_dispatcher(self.rt)
        try:
            for _handoff_guard in range(8):
                if dispatcher.active:
                    progress = dispatcher.advance()
                    if progress.boundary_id:
                        self.last_boundary_kind = "frame-park"
                        break
                    # A named exit has restored a generated continuation. Run
                    # that surrounding graph until its next semantic boundary.
                    continue
                # A ReplayArtifact continuation captured while the authored
                # region was active contains the stable entry coordinate and
                # materialized stack scratch, not a Python session object.
                # Re-enter directly before dispatching the generated resume
                # hook: that hook starts by re-executing the already-observed
                # 2317 comparison and would otherwise add one virtual
                # instruction (and shift timer/device effects) after restore.
                from skyroads.gameplay_region import maybe_enter_gameplay_region
                state = getattr(self.rt.cpu, "s", None)
                if state is not None:
                    try:
                        entered = maybe_enter_gameplay_region(
                            self.rt,
                            self.rt.cpu,
                            state.cs,
                            state.ip,
                        )
                    except RegionHandoff:
                        continue
                    if entered:
                        continue
                try:
                    self.rt.cpu.run(STEP_BUDGET)
                except RegionHandoff:
                    continue
                except FrameIdle:
                    self.last_boundary_kind = "frame-park"
                    break
                except ConsoleInputWouldBlock:
                    self.last_boundary_kind = "input-block"
                    break
                except HaltExecution:
                    return False
                else:
                    self.last_boundary_kind = "guest-fallback"
                    break
            else:
                raise RuntimeError(
                    "too many execution-region handoffs in one frame"
                )
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
            self.last_boundary_kind = "input-block"
        except BaseException as exc:            # noqa: BLE001 -- then re-raised
            self.crash(exc)
            raise
        self.frames += 1
        self.rt._skyroads_replay_boundary_kind = self.last_boundary_kind
        return True

    def crash(self, exc: BaseException) -> Path:
        """Leave the broken machine on disk, resumable, and say where it is."""
        out = save_crash(
            self.rt, crash_dir(self.crash_root, "vmless", self.stamp), exc=exc,
            status="vmless-crash", frame=self.frames,
            parks={f"{k:04X}": v for k, v in sorted(self.parks.items())})
        print(f"\n[vmless] CRASHED at frame {self.frames}: "
              f"{type(exc).__name__}: {str(exc)[:200]}")
        # The lifted call chain is the game's own path to the fault; CS:IP in the
        # snapshot names one instruction, this names how it got there.
        from dos_re.crash import recovered_call_chain, witness_address
        addr, chain = witness_address(exc), recovered_call_chain(exc)
        if addr:
            print(f"[vmless] refused at {addr}")
        if chain:
            shown = chain[-12:]
            lead = "" if len(chain) == len(shown) else \
                f"... ({len(chain) - len(shown)} more) -> "
            print(f"[vmless] lifted call chain: {lead}{' -> '.join(shown)}")
        print(f"[vmless] machine saved -> {out}")
        print(f"[vmless] resume it (you land ON the fault, no replay):\n"
              f"           from dos_re.snapshot_headless import load_snapshot_headless\n"
              f"           rt = load_snapshot_headless(r'{out}', game_root='assets')")
        return out


def build(boot_dir: Path, lift_dir: Path, game_root: Path, *,
          sound: bool = True, capture_sb: bool = False):
    """Boot the image AND set up the machine around it.

    ``boot_generated_graph_image`` builds a CPU + DOS + BIOS from the image; it does not
    and should not know what THIS game needs attached. Every other entry point
    does that part via skyroads.runtime (create_game_runtime /
    load_game_snapshot) -- this one bypassed them to avoid the EXE, and quietly
    inherited a bare machine. All three of the following were missing, and all
    three are things the runtime module has always done:
    """
    rt, manifest = boot_generated_graph_image(
        boot_dir,
        game_root=game_root,
        lift_dir=lift_dir,
    )

    # 1. THE PHANTOM ESC. DOSMachine defaults console_input_fallback to 0x011B
    #    so a bare cpu.run() with no driver loop cannot hang on a blocking read.
    #    SkyRoads reads its menu keys with INT 21h AH=07h, so it receives that
    #    Esc, reads it as "quit", and calls exit(0) -- the game appearing to
    #    quit itself seconds after the menu appears, with no keypress. This
    #    driver has a frame loop and handles ConsoleInputWouldBlock, so the
    #    synthesis is not needed here and is only harmful. (dos_re's
    #    the canonical real-mode player documents this exact failure; this was
    #    the one path that never called it.)
    rt.dos.console_input_fallback = None

    # 2. SOUND HARDWARE. Without it there is no OPL to play music through --
    #    and SkyRoads' own detection can take its "not enough sound hardware"
    #    exit.
    #
    #    detection_only attaches a stub: the game detects a digital device and
    #    emits its audio commands, but no PCM is streamed -- music (OPL) plays,
    #    digital SFX do not. Capture mode streams the DMA PCM too, and is a
    #    determinism-safe OBSERVER (byte-identical CPU timeline), but it stays
    #    OFF for headless/replay runs so the differential keeps the exact
    #    detection-only path and accumulates no captured PCM. Same rule as
    #    scripts/play.py::_capture_sb -- audio on and interactive.
    if sound:
        enable_sound_blaster(rt, detection_only=not capture_sb)

    # 3. THE MOUSE. dos_re keeps INT 33h absent unless a front-end opts in;
    #    the interactive viewer opts in, so the interactive VMless one must too,
    #    or the menus behave as they do on a machine with no mouse.
    rt.dos.mouse_present = True
    return rt, manifest


def create_planned_runtime(
    args,
    *,
    bootstrap_artifacts: dict[str, Path],
    bind_plan=None,
):
    """Build and bind the generated carrier without starting a player loop.

    Interactive launch and differential replay must construct the identical
    candidate runtime.  Keeping that lifecycle here prevents verification
    from quietly falling back to the interpreted runtime.
    """
    # Capture the DMA PCM only for the interactive viewer: it is a
    # determinism-safe observer, but headless must keep the detection-only
    # path so replay replay and the differential stay byte-identical.
    boot_files = (
        bootstrap_artifacts["skyroads-boot-state"],
        bootstrap_artifacts["skyroads-boot-memory"],
        bootstrap_artifacts["skyroads-boot-manifest"],
    )
    boot_dirs = {path.parent.resolve() for path in boot_files}
    if len(boot_dirs) != 1:
        raise RuntimeError(
            "SkyRoads bootstrap artifacts do not share one boot-image directory"
        )
    rt, manifest = build(boot_dirs.pop(),
                         ROOT / "skyroads" / "lifted" / "functions",
                         Path(args.game_root), sound=not args.no_sound,
                         capture_sb=not args.no_sound and not args.headless)
    rt._skyroads_no_sound = bool(args.no_sound)
    rt._skyroads_direct_level_request = getattr(args, "level", None)
    drv = VmlessDriver(
        rt, crash_root=ROOT / "artifacts" / "crashes", stamp=_stamp())
    rt._skyroads_vmless_driver = drv
    if bind_plan is not None:
        # The generated graph uses the same CPU-shaped carrier as the
        # interpreter, so plan-selected faithful implementations cross through
        # their declared CPU adapter here.  The graph remains the baseline for
        # every unselected identity; this is composition, not a second runner.
        bind_plan(rt)
    return rt, manifest


def launch(args, *, bootstrap_artifacts: dict[str, Path], bind_plan=None,
           frontend=None) -> int:
    """Launch the selected generated VMless region provider."""
    rt, manifest = create_planned_runtime(
        args,
        bootstrap_artifacts=bootstrap_artifacts,
        bind_plan=bind_plan,
    )
    drv = rt._skyroads_vmless_driver
    print(generated_graph_boot_report(manifest))
    if frontend is not None:
        from dos_re.player import run_view
        return run_view(frontend, rt, args)

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
    # display is: this is the viewer's business, not the generated provider's,
    # and a local import keeps the provider's module graph minimal. The sink is an
    # OBSERVER -- the lifted corpus writes the OPL ports exactly as the original
    # did, and the sink turns that write stream into sound; it decides nothing
    # about what plays. (skyroads.audio reaches only dos_re.audio_sink, so it is
    # loader-free and does not add the EXE loader to the dependency closure.)
    from skyroads.audio.sink import SkyroadsAudioSink

    pygame.init()
    disp = Display((960, 720), title="SkyRoads — VMless (no EXE)")
    disp.par = 1.2
    clock = pygame.time.Clock()
    # The FULL pygame->XT map, not a hand-rolled subset: this used to carry
    # 7 keys (arrows/space/enter/esc), so every letter, digit and function
    # key simply did not exist -- and SkyRoads lets you REBIND its controls.
    scancodes = scancode_table(pygame)
    # And through a KeyDispatcher, because SkyRoads polls its key table once
    # per frame: a make and its break applied between two frames set and
    # clear the key before the game ever looks, and the tap is silently
    # LOST. The dispatcher holds each make for a full frame before releasing
    # it. Delivering both on the pygame event, as this did, drops fast taps.
    dispatcher = KeyDispatcher(lambda sc: _deliver_scancode(rt, sc))
    audio = SkyroadsAudioSink(pygame, rt, args.present_hz)
    if not audio.available:
        print("[vmless] audio unavailable -- running silent")
        audio = None

    # THE MOUSE. build() reports one PRESENT (INT 33h answers), but presence is
    # not position: something has to tell the driver where the pointer IS, every
    # frame, or the game sees a mouse that exists and never moves. That is this.
    #
    # Map through the LETTERBOX, not the window: the frame does not fill the
    # window whenever the aspects differ, so mapping against the window skews the
    # cursor and offsets it by the bar size (dos_re 511f173). window_to_frame_norm
    # maps against the rect draw_game actually drew into, and returns None until
    # there is one.
    #
    # mouse_sample() quantizes exactly as the recorder does, so what the game
    # sees here is what a replay would replay -- live play and replay cannot drift.
    set_mouse = getattr(rt.dos, "set_mouse_norm", None)
    mouse_btn = [0]           # Microsoft mask: bit0=left, bit1=right, bit2=middle
    running = True
    while running and (not args.frames or drv.frames < args.frames):
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT or (ev.type == pygame.KEYDOWN
                                          and ev.key == pygame.K_ESCAPE
                                          and pygame.key.get_mods() & pygame.KMOD_SHIFT):
                running = False
            elif ev.type in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP):
                bit = {1: 0x01, 3: 0x02, 2: 0x04}.get(ev.button)
                if bit:
                    if ev.type == pygame.MOUSEBUTTONDOWN:
                        mouse_btn[0] |= bit
                    else:
                        mouse_btn[0] &= ~bit
            elif ev.type in (pygame.KEYDOWN, pygame.KEYUP):
                sc = scancodes.get(ev.key)
                if sc is not None:
                    if ev.type == pygame.KEYDOWN:
                        dispatcher.post_down(sc)
                    else:
                        dispatcher.post_up(sc)
        # Every frame, changed or not: set_mouse_norm re-maps through the
        # game's CURRENT INT 33h range, which the game itself changes.
        dispatcher.pump()          # makes/breaks, frame-accurate
        if set_mouse is not None:
            uv = disp.window_to_frame_norm(pygame.mouse.get_pos())
            if uv is not None:
                set_mouse(*mouse_sample(uv[0], uv[1], mouse_btn[0]))
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


def _deliver_scancode(rt, sc: int) -> None:
    """One XT scan code through the game's OWN recovered INT 09h ISR: present
    it the way the 8042 would, then vector."""
    rt.dos.current_scancode = sc & 0xFF
    rt.dos.kbd_output_buffer_full = True
    deliver_interrupt(rt, 0x09)
