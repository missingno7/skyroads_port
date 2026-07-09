"""Generic any-EXE live viewer — a thin shim over ``dos_re.player``.

The real runner for THIS port is ``scripts/play.py`` (a GameFrontend with the
game's pacing/runtime wiring); this tool is the zero-setup fallback for
eyeballing an arbitrary EXE with the generic runtime and the standard CLI
(viewer default / --headless, snapshots, demos, F10/F11/F12).  It stays a
shim so it can never drift from the canonical dos_re implementation again
(this file used to be a full pre-unification copy).

Usage:
    python tools/view.py --exe assets/GAME.EXE [--dos-args "..."]
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "dos_re"))   # the dos_re submodule's repo root

from dos_re.player import (  # noqa: E402,F401 — re-exports kept for old importers
    HEIGHT,
    WIDTH,
    GameFrontend,
    decode_frame_default as decode_frame,
    main as player_main,
    scancode_table as _scancode_table,
)


def main(argv: list[str] | None = None) -> int:
    return player_main(GameFrontend(ROOT), argv, description=__doc__)


if __name__ == "__main__":
    raise SystemExit(main())
