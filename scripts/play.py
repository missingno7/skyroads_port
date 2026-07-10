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
  - The pacing model is the library's simple deterministic default: a fixed
    (steps-per-frame, timer-irqs-per-frame) budget per frame, no wall-clock
    time source — the frame index alone is the demo clock, so record and
    replay are trivially deterministic.
  - No recovered hooks exist yet (see skyroads/hooks.py): the game runs as the
    pure ASM oracle, and the ``--safe-hooks``/``--verify-hooks``/
    ``--trace-hooks`` tiers fail loud until the port grows them.

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
from skyroads.runtime import create_game_runtime, load_game_snapshot  # noqa: E402


class SkyroadsFrontend(player.GameFrontend):
    name = "skyroads"
    default_exe = str(ROOT / "assets" / "SKYROADS.EXE")
    default_game_root = str(ROOT / "assets")
    default_steps_per_frame = 30_000
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

    def _capture_sb(self, args) -> bool:
        """Capture the game's Sound Blaster DMA PCM (digital SFX) only when the
        viewer audio is on.  It is a determinism-safe observer (byte-identical
        CPU timeline), but off by default so headless/demo/test runs keep the
        exact detection-only path and accumulate no captured PCM."""
        return getattr(args, "audio", "off") == "adlib" and not args.headless

    def create_runtime(self, args):
        return create_game_runtime(args.exe, game_root=args.game_root,
                                   command_tail=args.dos_args,
                                   capture_sb_pcm=self._capture_sb(args))

    def load_snapshot_runtime(self, args, snapshot_dir):
        return load_game_snapshot(args.exe, snapshot_dir, game_root=args.game_root,
                                  capture_sb_pcm=self._capture_sb(args))

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
