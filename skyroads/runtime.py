"""Boot / snapshot-load wiring for SKYROADS.EXE.

See docs/porting_new_game.md and docs/<game>/run_status.md (once started) for
the bring-up ledger. Boot-up findings so far: the game busy-waits on the raw
PIT channel-0 counter (a direct hardware delay loop, not IRQ0-driven) and also
blocks on real INT 08h ticks elsewhere — an interactive/headless driver must
pump timer interrupts (see tools/view.py --timer-irqs-per-frame) or the VM
will appear to hang. Both gaps were fixed at the framework level (dos_re/dos.py,
dos_re/cpu.py PUSHA/POPA), not here — this file has no SKYROADS-specific
bootstrap accelerator yet (the EXE is not packed: plain MZ header, no LZEXE
signature).
"""
from __future__ import annotations

from pathlib import Path

from dos_re.runtime import Runtime, create_runtime
from dos_re.snapshot import load_snapshot

EXE_NAME = "SKYROADS.EXE"


def create_game_runtime(
    exe_path: str | Path,
    *,
    game_root: str | Path | None = None,
    command_tail: bytes | str = b"",
    install_replacements: bool = True,
) -> Runtime:
    """Boot a fresh runtime. ``install_replacements=False`` is the pure-ASM
    oracle: no recovered hooks, the CPU runs the original code verbatim."""
    if install_replacements:
        from . import hooks  # noqa: F401
    return create_runtime(exe_path, game_root=game_root, command_tail=command_tail)


def load_game_snapshot(
    exe_path: str | Path,
    snapshot_dir: str | Path,
    *,
    game_root: str | Path | None = None,
) -> Runtime:
    return load_snapshot(exe_path, snapshot_dir, game_root=game_root)
