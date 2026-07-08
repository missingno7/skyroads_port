"""Run SKYROADS.EXE in the dos_re VM — the skyroads adapter's interactive runner.

This is a bring-up/recovery workbench, not a finished game frontend: the original
SKYROADS.EXE runs as the pure ASM oracle (no recovered hooks exist yet — see
skyroads/hooks.py). It grows from tools/view.py's generic template (reused
directly, not duplicated — see the ``import view`` below) with the game-specific
pacing this executable needs plus snapshot/demo capture and replay:

  - SKYROADS busy-waits on the INT 08h timer tick in its title/menu idle loop; a
    driver that never delivers it will appear to hang forever (this is what
    ``--timer-irqs-per-frame`` is for — confirmed during bring-up, see
    skyroads/runtime.py and docs/skyroads/run_status.md once started).
  - Both live play and demo record/replay use the SAME fixed
    (steps-per-frame, timer-irqs-per-frame) budget per displayed/simulated frame,
    with no wall-clock time source — so, unlike a fully-tuned adapter (e.g.
    pre2_port's scripts/play.py), record and replay are trivially deterministic:
    the frame index alone is the demo clock.

Controls (live view only): arrow/letter/number keys forward as XT scancodes to
the game; F11 starts/stops an input-demo recording; F12 saves a VM snapshot;
F10 saves a PNG screenshot of the current frame.

Usage:
    python scripts/play.py                                    # live play
    python scripts/play.py --snapshot artifacts/snap_x          # resume a snapshot
    python scripts/play.py --record-demo NAME                   # live play, recording from frame 0
    python scripts/play.py --play-demo artifacts/demo_x --view  # watch a replay
    python scripts/play.py --play-demo artifacts/demo_x         # headless replay (fast, deterministic)
"""
from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))  # tools/view.py + tools/display.py, reused rather than duplicated

from dos_re.cpu import HaltExecution, UnsupportedInstruction  # noqa: E402
from dos_re.dos import ConsoleInputWouldBlock  # noqa: E402
from dos_re.input_demo import InputDemoPlayback, InputDemoRecorder  # noqa: E402
from dos_re.interrupts import deliver_interrupt, deliver_scancode  # noqa: E402
from dos_re.keyboard import KeyDispatcher  # noqa: E402
from dos_re.snapshot import write_snapshot  # noqa: E402
from skyroads.runtime import create_game_runtime, load_game_snapshot  # noqa: E402

import view as _view  # noqa: E402 — decode_frame() and the pygame scan-code table


def _default_dir(root: Path, prefix: str) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return root / f"{prefix}_{stamp}"


def _demo_metadata(args: argparse.Namespace) -> dict[str, object]:
    """Reproducibility knobs a replay must match to stay deterministic."""
    return {
        "game": "skyroads",
        "exe": str(Path(args.exe).name),
        "command_tail": args.tail,
        "steps_per_frame": int(args.steps_per_frame),
        "timer_irqs_per_frame": int(args.timer_irqs_per_frame),
    }


def _advance_frame(rt, *, steps_per_frame: int, timer_irqs_per_frame: int) -> None:
    for _ in range(max(0, timer_irqs_per_frame)):
        deliver_interrupt(rt, 0x08)
    rt.cpu.run(steps_per_frame)


def _make_runtime(args: argparse.Namespace):
    if args.snapshot:
        return load_game_snapshot(args.exe, args.snapshot, game_root=args.game_root)
    return create_game_runtime(args.exe, game_root=args.game_root, command_tail=args.tail)


def _make_replay_runtime(args: argparse.Namespace, playback: InputDemoPlayback):
    """Build a runtime from the demo's start snapshot (or a fresh boot for a
    cold-start demo), honouring the recorded pacing knobs."""
    meta = playback.manifest.get("metadata", {})
    if "steps_per_frame" in meta:
        args.steps_per_frame = int(meta["steps_per_frame"])
    if "timer_irqs_per_frame" in meta:
        args.timer_irqs_per_frame = int(meta["timer_irqs_per_frame"])
    if playback.is_cold_start:
        return create_game_runtime(args.exe, game_root=args.game_root, command_tail=args.tail)
    return load_game_snapshot(args.exe, playback.snapshot_path(), game_root=args.game_root)


def _save_snapshot(rt, args: argparse.Namespace, *, status: str, steps: int) -> None:
    if not args.save_snapshot:
        return
    out = (_default_dir(ROOT / "artifacts", "snapshot_skyroads") if args.save_snapshot == "auto"
           else Path(args.save_snapshot))
    write_snapshot(rt, out, status=status, steps=steps, trace_tail=())
    print(f"snapshot: {out}")


# --- Headless demo replay (no pygame; fast, deterministic) --------------------

def _run_replay_headless(rt, args: argparse.Namespace, playback: InputDemoPlayback) -> int:
    frame = 0
    status = "demo replay complete"
    while not playback.finished(frame):
        playback.apply_to_runtime(frame, rt, deliver=deliver_scancode)
        try:
            _advance_frame(rt, steps_per_frame=args.steps_per_frame,
                           timer_irqs_per_frame=args.timer_irqs_per_frame)
        except HaltExecution:
            status = "program halted"
            break
        except UnsupportedInstruction as exc:
            status = f"unsupported instruction: {exc}"
            break
        except Exception as exc:  # noqa: BLE001 — keep bring-up useful
            status = f"exception: {type(exc).__name__}: {exc}"
            break
        frame += 1
    print(f"status: {status}")
    print(f"frames: {frame}  steps: {rt.cpu.instruction_count:,}  "
          f"events_applied={playback.next_event_index}/{len(playback.events)}")
    print(f"cpu: {rt.cpu.s.snapshot()}")
    _save_snapshot(rt, args, status=status, steps=rt.cpu.instruction_count)
    return 0 if not status.startswith(("unsupported", "exception")) else 1


# --- Live viewer (pygame) ------------------------------------------------------

def _run_view(rt, args: argparse.Namespace, *, playback: InputDemoPlayback | None = None) -> int:
    import pygame
    from display import Display

    replaying = playback is not None
    pygame.init()
    scale = 3
    display = Display((_view.WIDTH * scale, int(_view.HEIGHT * 1.2) * scale),
                      title=f"SKYROADS.EXE — dos_re VM ({'replay' if replaying else 'live'})")
    display.par = 1.0 if args.square_pixels else 1.2
    scancodes = _view._scancode_table(pygame)
    clock = pygame.time.Clock()

    demo: dict[str, InputDemoRecorder | None] = {"rec": None}

    def start_recording(name: str) -> None:
        rec = InputDemoRecorder(root=Path(args.demo_dir), name=name, metadata=_demo_metadata(args))
        out = rec.start(rt, boundary=frame_box["n"])
        demo["rec"] = rec
        print(f"recording demo -> {out}")

    def stop_recording() -> None:
        rec = demo["rec"]
        if rec is not None and rec.active:
            out = rec.stop(boundary=frame_box["n"])
            print(f"saved demo ({rec.event_count} events) -> {out}")
        demo["rec"] = None

    def deliver_input(scancode: int) -> None:
        deliver_scancode(rt, scancode)
        rec = demo["rec"]
        if rec is not None and rec.active:
            rec.record_scan(boundary=frame_box["n"], scancode=scancode)

    dispatcher = KeyDispatcher(deliver_input)
    frame_box = {"n": 0}
    last_rgb = [None]
    running = True
    status = "replaying" if replaying else "running"

    if not replaying and args.record_demo:
        start_recording(args.record_demo)

    try:
        while running and (args.frames == 0 or frame_box["n"] < args.frames):
            if replaying and playback.finished(frame_box["n"]):
                status = "demo replay complete"
                break

            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
                elif event.type == pygame.VIDEORESIZE:
                    display.resize(event.w, event.h)
                elif event.type == pygame.KEYDOWN and event.key == pygame.K_F12:
                    _save_snapshot(rt, argparse.Namespace(save_snapshot="auto"),
                                   status="manual viewer snapshot", steps=rt.cpu.instruction_count)
                elif event.type == pygame.KEYDOWN and event.key == pygame.K_F10:
                    rgb = last_rgb[0]
                    if rgb is not None:
                        import numpy as np
                        h, w = rgb.shape[0], rgb.shape[1]
                        surf = pygame.image.frombuffer(np.ascontiguousarray(rgb).tobytes(), (w, h), "RGB")
                        out = ROOT / "artifacts" / f"shot_skyroads_{datetime.now():%Y%m%d_%H%M%S}.png"
                        out.parent.mkdir(parents=True, exist_ok=True)
                        pygame.image.save(surf, str(out))
                        print(f"screenshot: {out}")
                elif replaying:
                    continue  # ignore host keys while a demo drives input
                elif event.type == pygame.KEYDOWN and event.key == pygame.K_F11:
                    if demo["rec"] is None:
                        start_recording(args.record_demo or "skyroads")
                    else:
                        stop_recording()
                elif event.type == pygame.KEYDOWN:
                    sc = scancodes.get(event.key)
                    if sc is not None:
                        dispatcher.post_down(sc)
                elif event.type == pygame.KEYUP:
                    sc = scancodes.get(event.key)
                    if sc is not None:
                        dispatcher.post_up(sc)

            if replaying:
                playback.apply_to_runtime(frame_box["n"], rt, deliver=deliver_scancode)
            else:
                dispatcher.pump()

            try:
                _advance_frame(rt, steps_per_frame=args.steps_per_frame,
                               timer_irqs_per_frame=args.timer_irqs_per_frame)
            except ConsoleInputWouldBlock:
                status = "waiting for DOS key"
            except HaltExecution:
                status = "program halted"
                running = False
            except UnsupportedInstruction as exc:
                status = f"unsupported instruction: {exc}"
                running = False
            except Exception as exc:  # noqa: BLE001 — keep bring-up useful
                status = f"exception: {type(exc).__name__}: {exc}"
                running = False
                import traceback
                traceback.print_exc()
                try:
                    gap_dir = _default_dir(ROOT / "artifacts", "gap_snapshot_skyroads")
                    write_snapshot(rt, gap_dir, status=status, steps=rt.cpu.instruction_count, trace_tail=())
                    print(f"gap snapshot saved: {gap_dir}")
                except Exception as save_exc:  # noqa: BLE001
                    print(f"(could not save gap snapshot: {save_exc})")

            rgb = _view.decode_frame(rt)
            last_rgb[0] = rgb
            display.draw_game(rgb)
            display.flip()
            pygame.display.set_caption(
                f"SKYROADS.EXE VM | {status} | frame={frame_box['n']} steps={rt.cpu.instruction_count:,} | "
                f"CS:IP={rt.cpu.s.cs:04X}:{rt.cpu.s.ip:04X}"
                + (" | REC" if demo["rec"] is not None else "")
            )
            frame_box["n"] += 1
            clock.tick(args.fps)
    finally:
        if not replaying:
            stop_recording()
        pygame.quit()

    print(f"status: {status}")
    print(f"frames: {frame_box['n']}  steps: {rt.cpu.instruction_count:,}")
    print(f"cpu: {rt.cpu.s.snapshot()}")
    _save_snapshot(rt, args, status=status, steps=rt.cpu.instruction_count)
    return 0 if not status.startswith(("unsupported", "exception")) else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--exe", default=str(ROOT / "assets" / "SKYROADS.EXE"), help="path to SKYROADS.EXE")
    p.add_argument("--game-root", default=str(ROOT / "assets"), help="directory containing SkyRoads assets")
    p.add_argument("--tail", default="", help="raw DOS command tail")
    p.add_argument("--snapshot", help="continue from an existing snapshot directory")
    p.add_argument("--save-snapshot", nargs="?", const="auto",
                   help="save a VM snapshot on exit; optional directory path")
    p.add_argument("--view", action="store_true",
                   help="(with --play-demo) watch the replay in the pygame viewer instead of the "
                        "default fast headless replay; ignored otherwise (live play always opens the viewer)")
    p.add_argument("--record-demo", metavar="NAME", help="(viewer) start recording an input demo immediately")
    p.add_argument("--play-demo", metavar="DIR", help="replay a recorded demo dir (headless unless --view)")
    p.add_argument("--demo-dir", default=str(ROOT / "artifacts"), help="directory to write recorded demos into")
    p.add_argument("--fps", type=int, default=60, help="live viewer display rate")
    p.add_argument("--steps-per-frame", type=int, default=200_000,
                   help="VM instructions to run per displayed/simulated frame")
    p.add_argument("--timer-irqs-per-frame", type=int, default=1,
                   help="INT 08h timer ticks delivered per frame — SKYROADS's title/menu idle loop "
                        "waits on this and never returns without it (see skyroads/runtime.py)")
    p.add_argument("--frames", type=int, default=0, help="(viewer) exit after N frames (0 = run until closed)")
    p.add_argument("--square-pixels", action="store_true", help="par=1.0 instead of the DOS 4:3 look (par=1.2)")
    args = p.parse_args(argv)

    if args.play_demo:
        playback = InputDemoPlayback.load(args.play_demo)
        rt = _make_replay_runtime(args, playback)
        if args.view:
            return _run_view(rt, args, playback=playback)
        return _run_replay_headless(rt, args, playback)

    rt = _make_runtime(args)
    return _run_view(rt, args)


if __name__ == "__main__":
    raise SystemExit(main())
