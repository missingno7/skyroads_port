"""Crash bundles for generated SkyRoads backends.

:mod:`dos_re.crash` already saves the broken machine and extracts the recovered
call chain.  What it cannot know is how to REPRODUCE the fault in this port, and
that is the part that actually shortens the fix:

CPU-FREE, like everything the CPUless runner touches (``tools/lint_cpuless.py``
proves it).
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from dos_re.crash import (crash_dir, recovered_call_chain, save_crash_headless,
                          witness_address)


def write_crash_bundle(out_root, exc: BaseException, *, mem, dos, frame: int,
                       head=None, stage: str = "cpuless",
                       extra: dict | None = None) -> Path:
    """Save the failed backend state and return the bundle directory."""
    out = crash_dir(out_root, stage, datetime.now().strftime("%Y%m%d_%H%M%S"))

    save_crash_headless(
        out, mem=mem, dos=dos, exc=exc, status=f"{stage}-crash",
        frame=frame,
        boundary_head=(f"{head[0]:04X}:{head[1]:04X}" if head else None),
        video_mode=getattr(dos, "video_mode", None),
        reproduce=[
            "python scripts/play.py --profile detached --composition cpuless "
            "--headless",
        ],
        **(extra or {}))
    return out


def print_crash_summary(bundle: Path, exc: BaseException, *, frame: int) -> None:
    """Print the bits worth reading immediately (crash.json has the rest)."""
    chain = recovered_call_chain(exc)
    addr = witness_address(exc)
    print(f"\n[crash] {type(exc).__name__}: {exc}")
    if addr:
        print(f"[crash] refused at {addr} on frame {frame}")
    if chain:
        shown = chain[-12:]
        lead = "" if len(chain) == len(shown) else \
            f"... ({len(chain) - len(shown)} more) -> "
        print(f"[crash] recovered call chain: {lead}{' -> '.join(shown)}")
    print(f"[crash] bundle: {bundle}")
    print("[crash]   crash.json (witness + call chain), memory_1mb.bin, state.json")
