"""Untouched interpreted-baseline construction for the unified player.

This module only constructs or restores the oracle runtime. Implementation
selection and bootstrap-provider policy belong to :mod:`skyroads.execution`;
the post-unpack build image used by detached compositions is materialized by
``scripts/build_boot_image.py``.

SkyRoads requires Sound Blaster detection during a fresh boot. Detection-only
mode is used by deterministic headless execution. Interactive capture mode
exposes PCM blocks to the presentation adapter and remains an explicit device
configuration rather than an undeclared replay side effect.
"""
from __future__ import annotations

from pathlib import Path

from dos_re.runtime import Runtime, create_runtime, enable_sound_blaster
from dos_re.snapshot import load_snapshot

EXE_NAME = "SKYROADS.EXE"


def create_game_runtime(
    exe_path: str | Path,
    *,
    game_root: str | Path | None = None,
    command_tail: bytes | str = b"",
    enable_sound: bool = True,
    capture_sb_pcm: bool = False,
) -> Runtime:
    """Boot the untouched interpreted baseline runtime.

    Implementations are selected by :mod:`skyroads.execution` and activated
    only after dos_re has resolved an immutable execution plan. This runtime
    factory deliberately has no hook or override switches.

    ``capture_sb_pcm`` attaches the Sound Blaster in *capture* mode instead of
    the detection-only stub: single-cycle DMA-out blocks (the game's digital
    ``*.SND`` sound effects) are copied into ``sb.pcm_out`` and their sample
    rate logged, so :mod:`skyroads.audio.sink` can play them.
    The Sound Blaster retains its emulated DMA/IRQ behavior, and its selected
    mode is part of replay profile identity and complete continuation state.
    Capture is off by default so headless recording and verification use the
    deterministic detection-only profile and do not accumulate presentation
    output."""
    rt = create_runtime(exe_path, game_root=game_root, command_tail=command_tail)
    if enable_sound:
        enable_sound_blaster(rt, detection_only=not capture_sb_pcm)
    return rt


def load_game_snapshot(
    exe_path: str | Path,
    snapshot_dir: str | Path,
    *,
    game_root: str | Path | None = None,
    enable_sound: bool = True,
    capture_sb_pcm: bool = False,
) -> Runtime:
    """Resume the untouched interpreted baseline from a snapshot.

    The unified player activates the resolved plan after restoration.
    ``enable_sound`` here only helps
    snapshots taken before SKYROADS' own sound-detection ran; a snapshot
    where detection already failed keeps that outcome regardless (it's
    already recorded in the snapshot's own game memory)."""
    rt = load_snapshot(exe_path, snapshot_dir, game_root=game_root)
    if enable_sound:
        enable_sound_blaster(rt, detection_only=not capture_sb_pcm)
    return rt
