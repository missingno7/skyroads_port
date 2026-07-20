"""SKYROADS.EXE boot smoke test.

CI has no game files: this module skips entirely when assets/SKYROADS.EXE is
missing, so the framework suite stays green without them (same pattern as
test_tiny_frame_game.py's examples/-missing skip).
"""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_EXE = ROOT / "assets" / "SKYROADS.EXE"
if not _EXE.is_file():
    pytest.skip("assets/SKYROADS.EXE not present — game files are never committed",
                allow_module_level=True)

from dos_re.interrupts import deliver_interrupt  # noqa: E402

from skyroads.runtime import create_game_runtime  # noqa: E402


def test_boots_and_reaches_a_stable_wait_without_raising() -> None:
    rt = create_game_runtime(_EXE)
    # The title screen's steady state waits on the PIT/timer tick (INT 08h);
    # a driver must pump it or the wait spins without ever raising (see
    # skyroads/runtime.py docstring). A few simulated frames is enough to
    # prove the oracle boots clean past DOS/BIOS init and video mode set.
    for _ in range(20):
        deliver_interrupt(rt, 0x08)
        rt.cpu.run(200_000)
    assert not rt.cpu.halted
    assert rt.dos.video_mode & 0x7F == 0x13
