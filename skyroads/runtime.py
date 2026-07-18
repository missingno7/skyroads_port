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

Sound Blaster (2026-07-09): SKYROADS probes for an SB at boot (ports
0x220-0x270, standard DSP reset handshake) and, once one responds, assumes
its onboard OPL is present too and starts loading FM instrument patches —
there is no separate AdLib-only probe. With no SB attached the probe finds
nothing on any candidate port and the game hard-exits (`mov ah,4Ch`) with no
error message, sometimes not until well past the intro (found via
docs/skyroads/run_status.md's halt-diagnostics work). enable_sound_blaster()
must run on a FRESH boot, before detection — attaching it to an already
-failed snapshot does nothing, since "no sound" is already baked into game
memory by then. Detection-only mode (no PCM streaming) is enough to satisfy
the probe; a front-end that wants real audio replaces this with a full
attach.
"""
from __future__ import annotations

from pathlib import Path

from dos_re.hooks import registry
from dos_re.runtime import Runtime, create_runtime, enable_sound_blaster
from dos_re.snapshot import load_snapshot

EXE_NAME = "SKYROADS.EXE"


def create_game_runtime(
    exe_path: str | Path,
    *,
    game_root: str | Path | None = None,
    command_tail: bytes | str = b"",
    install_replacements: bool = True,
    enable_sound: bool = True,
    capture_sb_pcm: bool = False,
) -> Runtime:
    """Boot a fresh runtime. ``install_replacements=False`` is the pure-ASM
    oracle: no recovered hooks, the CPU runs the original code verbatim.
    ``enable_sound=False`` reproduces the original "Not enough sound
    hardware" exit path for study; leave it on for normal play/bring-up.

    The flag is passed DOWN to dos_re.runtime.create_runtime, which strips the
    registry after boot. Guarding the ``from . import hooks`` import instead —
    which this function used to do — does NOT gate anything: hooks register at
    decoration time and ``skyroads.hooks`` is transitively imported by nearly
    every entry point long before we get here, so the registry is already
    populated and create_runtime wired all of it onto the "pure" CPU anyway.
    That silently put 31 replacements on scripts/verify_cpuless.py's oracle,
    including the deliberately behaviour-changing ``fade_loop_tick_gate``
    optimisation, and the differential then blamed its candidate for a
    palette divergence the ORACLE was producing. The import stays
    unconditional because it is what populates the registry for the True case;
    installation, not import, is what the flag gates. Same shape as
    load_game_snapshot below.

    ``capture_sb_pcm`` attaches the Sound Blaster in *capture* mode instead of
    the detection-only stub: single-cycle DMA-out blocks (the game's digital
    ``*.SND`` sound effects) are copied into ``sb.pcm_out`` and their sample
    rate logged, so a viewer audio sink can play them (see skyroads/audio.py).
    No block-complete IRQ is delivered, so the CPU timeline stays byte-identical
    to the detection-only stub (verified differentially over the full E2E demo)
    -- demos still replay deterministically.  Off by default so the headless/
    test path keeps the exact detection-only behaviour and does not accumulate
    captured PCM."""
    from . import hooks  # noqa: F401  (populates the registry; see docstring)
    rt = create_runtime(exe_path, game_root=game_root, command_tail=command_tail,
                        install_replacements=install_replacements)
    if enable_sound:
        enable_sound_blaster(rt, detection_only=not capture_sb_pcm)
    return rt


def load_game_snapshot(
    exe_path: str | Path,
    snapshot_dir: str | Path,
    *,
    game_root: str | Path | None = None,
    install_replacements: bool = True,
    enable_sound: bool = True,
    capture_sb_pcm: bool = False,
) -> Runtime:
    """Resume a snapshot. Unlike dos_re.runtime.create_runtime,
    dos_re.snapshot.load_snapshot does NOT install the hook registry on the
    restored CPU by itself — a snapshot resume that skipped this silently ran
    pure ASM regardless of which hooks were registered (found during the
    palette-fade hook's performance validation, 2026-07-08: identical step
    counts with and without the hook "installed" turned out to mean it was
    never actually wired onto the resumed CPU). enable_sound here only helps
    snapshots taken before SKYROADS' own sound-detection ran; a snapshot
    where detection already failed keeps that outcome regardless (it's
    already recorded in the snapshot's own game memory)."""
    rt = load_snapshot(exe_path, snapshot_dir, game_root=game_root)
    if install_replacements:
        from . import hooks  # noqa: F401
        registry.install(rt.cpu)
    if enable_sound:
        enable_sound_blaster(rt, detection_only=not capture_sb_pcm)
    return rt
