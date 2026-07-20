"""Refresh SkyRoads' two generated representations from current local evidence.

This convenience recipe runs the dependencies needed for the current generated
VMless and ABI-recovered corpora:

    build_codemap.py     replays            -> observed.json      (what EXECUTED)
    expand_vmless_frontier.py observed.json -> local entries/IR
                                          -> skyroads/lifted/ (VMless corpus)
    build_recovered.py   retained IR + local evidence
                                          -> skyroads/recovered/ (ABI corpus)

This order belongs only to this reproducible generation recipe. Either
representation can remain selected indefinitely, and static recovery,
ReplayArtifact evidence, Atlas updates, and authored implementations are
independently useful and composable.

Usage:
    python scripts/rebuild_all.py --replay DIR [--replay DIR ...]
    python scripts/rebuild_all.py --abi-only     # rebuild only the ABI corpus
    python scripts/rebuild_all.py --replay DIR --check
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: (script, output) for this convenience recipe.
STEPS = (
    ("build_codemap.py",
     "observe the replays -> artifacts/codemap/observed.json"),
    ("expand_vmless_frontier.py",
     "local census/IR + the generated VMless corpus"),
    ("build_recovered.py",
     "the generated ABI corpus + its diagnostic manifest"),
)


def _run(script: str, why: str, extra_args: tuple[str, ...] = ()) -> None:
    print(f"\n=== {script} -- {why}")
    t0 = time.time()
    r = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / script), *extra_args],
        cwd=ROOT,
    )
    if r.returncode != 0:
        raise SystemExit(f"[rebuild_all] {script} FAILED (exit {r.returncode}) "
                         f"-- stopping; later recipe steps need its output")
    print(f"=== {script} ok ({time.time() - t0:.0f}s)")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--abi-only", action="store_true",
                    help="rebuild only the ABI-recovered representation from "
                         "the retained IR and current local evidence")
    ap.add_argument(
        "--replay", action="append", default=[], metavar="DIR",
        help="ReplayArtifact evidence passed to build_codemap.py (repeatable)",
    )
    ap.add_argument(
        "--cold-boot-frames", type=int, default=0,
        help="explicit optional cold-boot observation passed to build_codemap.py",
    )
    ap.add_argument("--check", action="store_true",
                    help="run scripts/check_all.py afterwards")
    args = ap.parse_args(argv)

    if args.abi_only:
        print("[rebuild_all] --abi-only: using retained IR and local evidence")
        steps = STEPS[2:]
    else:
        if not args.replay and args.cold_boot_frames <= 0:
            ap.error(
                "full regeneration requires explicit observation evidence; "
                "pass --replay DIR and/or --cold-boot-frames N"
            )
        codemap_args = tuple(
            part
            for replay in args.replay
            for part in ("--replay", replay)
        )
        if args.cold_boot_frames > 0:
            codemap_args += (
                "--cold-boot-frames", str(args.cold_boot_frames),
            )
        script, why = STEPS[0]
        _run(script, why, codemap_args)
        steps = STEPS[1:]
    for script, why in steps:
        _run(script, why)
    print("\n[rebuild_all] generated-corpus recipe complete")
    if args.check:
        return subprocess.run([sys.executable,
                               str(ROOT / "scripts/check_all.py")],
                              cwd=ROOT).returncode
    print("[rebuild_all] verify it with: python scripts/check_all.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
