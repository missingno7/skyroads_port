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
  - SKYROADS' own INT 08h ISR (1010:3B17) runs a software prescaler
    (ds:[3192]) that only advances its elapsed-ticks counter (ds:[1600]) once
    every 6 REAL timer interrupts — an intentional ~3 Hz game-tick rate
    divided down from the 18.2 Hz BIOS timer, confirmed by live-tracing the
    ISR (2026-07-09, see docs/skyroads/symbol_ledger.md). Delivering only 1
    IRQ per driver frame means 5 out of every 6 frames make ZERO real-time
    progress on anything gated by that counter (most of the game's own
    wait/pacing loops) while still burning a full interpreted step budget
    busy-spinning. ``default_timer_irqs_per_frame = 6`` matches the real
    prescaler exactly; ``default_steps_per_frame`` is lowered so IRQ bursts
    land more often per wall-clock second instead of once every giant chunk.
    Measured on an intro-fade snapshot: ~4.7x more real elapsed-tick progress
    per wall-clock second (7 -> 33 ticks in 3s) versus the prior 1-IRQ/200K-step
    defaults — not yet re-validated against real gameplay (still unreached).
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

    def create_runtime(self, args):
        return create_game_runtime(args.exe, game_root=args.game_root,
                                   command_tail=args.dos_args)

    def load_snapshot_runtime(self, args, snapshot_dir):
        return load_game_snapshot(args.exe, snapshot_dir, game_root=args.game_root)


def main(argv: list[str] | None = None) -> int:
    return player.main(SkyroadsFrontend(ROOT), argv, description=__doc__)


if __name__ == "__main__":
    raise SystemExit(main())
