"""Run SKYROADS.EXE in the dos_re VM — the skyroads adapter's interactive runner.

A thin :class:`dos_re.player.GameFrontend` over the unified play runner
(``dos_re/dos_re/player.py`` documents the full standard CLI: viewer by
default / ``--headless``; ``--snapshot`` / ``--save-snapshot``;
``--record-demo`` / ``--play-demo`` / ``--demo-continue``; hook-mode flags;
F10 screenshot, F11 demo-record toggle, F12 snapshot).  This file holds only
what is skyroads-specific:

  - SKYROADS busy-waits on the INT 08h timer tick in its title/menu idle loop;
    a driver that never delivers it appears to hang forever — hence
    ``--timer-irqs-per-frame`` defaults to 1 (confirmed during bring-up, see
    skyroads/runtime.py and docs/skyroads/run_status.md).
  - SKYROADS reprograms PIT channel-0 to divisor 6628 (OUT 40h at boot), i.e.
    1193182/6628 = 180.0 Hz IRQ0 — NOT the 18.2 Hz BIOS default. Its INT 08h ISR
    (1010:3B17) runs a software prescaler (ds:[3192]) that advances the
    elapsed-ticks counter (ds:[1600]) once every 6 real IRQs, so game logic ticks
    at 180/6 = 30 Hz — the native frame rate. (An earlier note here read the
    prescaler against the 18.2 Hz default and wrongly concluded ~3 Hz; the PIT
    reprogramming was missed.) ``default_timer_irqs_per_frame = 6`` matches the
    prescaler exactly (6 IRQs = 1 game tick = 1 presented frame), and
    ``default_present_hz = 30`` reproduces 180 Hz IRQ0 / 30 Hz logic in the
    viewer (IRQ0 Hz = 6*present_hz). The base 60 ran music and physics at 2x.
  - The pacing model is the library's fixed (steps-per-frame, timer-irqs-per-
    frame) budget per frame — no wall-clock time source, so the frame index
    alone is the demo clock and record/replay stay deterministic. On top of it,
    ``--frame-park`` (default on) ends a frame the moment the game parks in its
    INT 08h tick-wait (1010:22F8 / 434A): ds:[1600] can't advance again until
    the next frame, so the rest of the budget would only be spun away. This is
    byte-equivalent for the game trajectory (every rendered frame + all game
    state identical across the E2E demo; only fade-loop scratch differs) and
    makes gameplay ~4-6x faster — SKYROADS' analogue of pre2_port's classified
    ``--fast-retrace-waits``. See skyroads/pacing.py.
  - Recovered hooks (skyroads/hooks.py) replace the render/math hot path and the
    timer ISR; ``--no-replacements`` runs the pure ASM oracle instead, and
    ``--safe-hooks``/``--verify-hooks``/``--trace-hooks`` select the hook tier.

Usage:
    python scripts/play.py                                      # live play
    python scripts/play.py --snapshot artifacts/snap_x          # resume a snapshot
    python scripts/play.py --record-demo NAME                   # live play, recording from frame 0
    python scripts/play.py --play-demo artifacts/demo_x         # watch a replay
    python scripts/play.py --play-demo artifacts/demo_x --headless   # fast deterministic replay
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))              # the skyroads adapter package
sys.path.insert(0, str(ROOT / "dos_re"))   # the dos_re submodule's repo root

from dos_re import player  # noqa: E402
from dos_re.interrupts import deliver_interrupt  # noqa: E402
from skyroads.runtime import create_game_runtime, load_game_snapshot  # noqa: E402
from skyroads.pacing import install_frame_park, FrameIdle  # noqa: E402


class SkyroadsFrontend(player.GameFrontend):
    name = "skyroads"
    default_exe = str(ROOT / "assets" / "SKYROADS.EXE")
    default_game_root = str(ROOT / "assets")
    # With --frame-park (default), most frames end at the tick-wait (real work
    # p50 ~9.2k steps), so this is a *ceiling*, not the per-frame cost. Size it
    # ABOVE the game's peak per-frame work so no frame is cut mid-tick: measured
    # peak over the full level is 37,309 steps (113/1906 frames exceed 30,000);
    # 48,000 clears it with ~28% headroom. Do NOT shrink it toward the average
    # -- a budget below peak makes the original ASM see itself lagging and engage
    # its own lag compensation (pre2_port learned this; see run_status.md).
    # steps_per_frame is stored in demo_metadata, so existing demos still replay
    # at their recorded budget regardless of this default.
    default_steps_per_frame = 48_000
    default_timer_irqs_per_frame = 6   # matches SKYROADS' own 6:1 INT 08h software prescaler
    # SKYROADS reprograms PIT channel-0 to divisor 6628 => 1193182/6628 = 180.0 Hz
    # IRQ0 (NOT the 18.2 Hz BIOS default; confirmed by tracing OUT 40h at boot).
    # Its INT 08h ISR prescales /6, so game logic ticks at 180/6 = 30 Hz -- the
    # native frame rate.  The viewer delivers timer_irqs_per_frame (6) INT 08h per
    # presented frame and paces frames at present_hz, so IRQ0 Hz = 6*present_hz and
    # logic Hz = present_hz.  present_hz=30 reproduces the real 180 Hz IRQ0 / 30 Hz
    # logic (1 game tick per frame); the base 60 ran everything -- music, physics --
    # at 2x speed.  (Wall-clock pacing only; headless demo replay ignores present_hz,
    # so determinism is unaffected.)
    default_present_hz = 30

    def add_arguments(self, parser) -> None:
        import argparse
        pace = parser.add_argument_group("skyroads pacing")
        pace.add_argument(
            "--frame-park", action=argparse.BooleanOptionalAction, default=True,
            help="end each frame when the game parks in its INT 08h tick-wait "
                 "(22F8 / 434A) instead of spinning out the step budget. "
                 "Byte-equivalent game trajectory, ~4-6x faster gameplay "
                 "(see skyroads/pacing.py). --no-frame-park forces the full spin.")

    def _install_frame_park(self, args, rt):
        """Install the tick-wait frame-park unless disabled or running the pure
        ASM oracle (--no-replacements has no fade-loop gate to compose with)."""
        if getattr(args, "frame_park", True) and not getattr(args, "no_replacements", False):
            install_frame_park(rt)
        self._pin_demo_mouse_presence(args, rt)
        return rt

    def _pin_demo_mouse_presence(self, args, rt) -> None:
        """Pin INT 33h mouse presence for a REPLAY, centrally.

        dos_re keeps the mouse OFF unless opted in, so a keyboard demo already
        replays correctly; this is what turns it ON for a demo that was recorded
        WITH one (manifest ``metadata.mouse_present``) -- without it, that
        recording would replay against an absent mouse and diverge. Demos
        predating the field are keyboard-only, hence the False fallback.

        Applied here (the one funnel both create_runtime and
        load_snapshot_runtime pass through) so no caller can forget it; the
        interactive viewer sets it itself and never calls apply_demo_metadata.
        """
        pinned = getattr(args, "demo_mouse_present", None)
        if pinned is not None:
            rt.dos.mouse_present = bool(pinned)

    def apply_demo_metadata(self, args, meta: dict) -> None:
        super().apply_demo_metadata(args, meta)
        args.demo_mouse_present = bool(meta.get("mouse_present", False))

    def advance_frame(self, rt, args, frame: int) -> None:
        """Deliver the frame's timer IRQs, then run the step budget — but stop
        early when the game parks in a tick-wait it can't leave this frame."""
        for _ in range(max(0, args.timer_irqs_per_frame)):
            deliver_interrupt(rt, 0x08)
        try:
            rt.cpu.run(args.steps_per_frame)
        except FrameIdle:
            pass

    def _capture_sb(self, args) -> bool:
        """Capture the game's Sound Blaster DMA PCM (digital SFX) only when the
        viewer audio is on.  It is a determinism-safe observer (byte-identical
        CPU timeline), but off by default so headless/demo/test runs keep the
        exact detection-only path and accumulate no captured PCM."""
        return getattr(args, "audio", "off") == "adlib" and not args.headless

    def create_runtime(self, args):
        rt = create_game_runtime(args.exe, game_root=args.game_root,
                                 command_tail=args.dos_args,
                                 capture_sb_pcm=self._capture_sb(args))
        return self._install_frame_park(args, rt)

    def load_snapshot_runtime(self, args, snapshot_dir):
        rt = load_game_snapshot(args.exe, snapshot_dir, game_root=args.game_root,
                                capture_sb_pcm=self._capture_sb(args))
        return self._install_frame_park(args, rt)

    def create_audio_sink(self, pygame, rt, args):
        """SkyRoads viewer audio: OPL music + PC speaker + Sound Blaster PCM SFX.
        Extends the stock AdLib/speaker sink with the game's digital ``*.SND``
        effects (see skyroads/audio.py)."""
        if args.audio != "adlib":
            return None
        from skyroads.audio import SkyroadsAudioSink

        sink = SkyroadsAudioSink(pygame, rt, args.present_hz)
        return sink if sink.available else None


def main(argv: list[str] | None = None) -> int:
    return player.main(SkyroadsFrontend(ROOT), argv, description=__doc__)


if __name__ == "__main__":
    raise SystemExit(main())
