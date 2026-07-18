"""Crash bundles for the recovered runners -- the port-specific half.

:mod:`dos_re.crash` already saves the broken machine and extracts the recovered
call chain.  What it cannot know is how to REPRODUCE the fault in this port, and
that is the part that actually shortens the fix:

A hard-wall stop is almost always a COVERAGE bug -- a real exit the ``--observed``
census never reached, so the build stubbed it fail-loud (``1010:2EFC`` and
``1010:2F57`` both were).  Fixing one means replaying the exact session that
found it.  So the runner records every session, and a crash keeps that recording
next to the machine state, with the commands that turn it into census coverage.

That closes the loop: the crash hands you the demo that reproduces it, and the
same demo is the input that makes it stop happening.

CPU-FREE, like everything the CPUless runner touches (``tools/lint_cpuless.py``
proves it).
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from dos_re.crash import (crash_dir, recovered_call_chain, save_crash_headless,
                          witness_address)


def write_crash_bundle(out_root, exc: BaseException, *, mem, dos, frame: int,
                       head=None, recorder=None, stage: str = "cpuless",
                       extra: dict | None = None) -> Path:
    """Save the machine + the session recording; return the bundle directory.

    ``recorder`` is a live :class:`dos_re.input_demo.InputDemoRecorder`, stopped
    here so the session's cold-start demo survives beside the crash.  Never
    raises: a failure while reporting a failure must not replace the original
    error, so each part is best-effort.
    """
    out = crash_dir(out_root, stage, datetime.now().strftime("%Y%m%d_%H%M%S"))

    demo_dir = None
    if recorder is not None and getattr(recorder, "active", False):
        try:
            demo_dir = recorder.stop(boundary=frame)
        except Exception:                            # noqa: BLE001
            demo_dir = None

    save_crash_headless(
        out, mem=mem, dos=dos, exc=exc, status=f"{stage}-crash",
        frame=frame,
        boundary_head=(f"{head[0]:04X}:{head[1]:04X}" if head else None),
        video_mode=getattr(dos, "video_mode", None),
        input_demo=(demo_dir.as_posix() if demo_dir else None),
        # Self-describing a month later: the bundle carries its own next steps.
        reproduce=(None if demo_dir is None else [
            f"python scripts/verify_cpuless.py {demo_dir.as_posix()}",
            "# to CLOSE a false-dead exit, census the demo then rebuild:",
            f"#   add '{demo_dir.name}' to DEFAULT_DEMOS and BOUNDARY_DEMOS in "
            "scripts/build_codemap.py",
            "#   python scripts/build_codemap.py && python scripts/build_recovered.py",
        ]),
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
    try:
        demo = json.loads((bundle / "crash.json").read_text(encoding="utf-8")
                          )["context"].get("input_demo")
    except Exception:                                # noqa: BLE001
        demo = None
    if demo:
        print(f"[crash]   input demo (replays this session cold): {demo}")
        print(f"[crash]   reproduce: python scripts/verify_cpuless.py {demo}")
