"""rebuild_all.py -- the recovery pipeline, in the one order that is correct.

The stages are strictly ordered and each consumes the previous one's output:

    build_codemap.py     replays            -> observed.json      (what EXECUTED)
    close_vmless_wall.py observed.json    -> entries.txt
                                          -> recovery_ir.json   (the IR)
                                          -> skyroads/lifted/   (VMless corpus)
    build_recovered.py   recovery_ir.json -> skyroads/recovered/ (CPUless corpus)

Running them out of order, or skipping the middle one, fails SILENTLY and
expensively.  Adding coverage and then rebuilding only the recovered corpus left
the newly-discovered functions with no IR entry; every caller of one then refused
``contains-call``, which cascaded to five refusals including ``1010:61F3`` -- the
C-startup root -- so the standalone runner could not even import its entry point.
The output said "5 refused", not "you skipped a stage".

So the order lives in one place, here, instead of in whoever remembers it.
``build_recovered.py`` also fails loud on an IR older than the census, which is
the same mistake caught from the other side.

Usage:
    python scripts/rebuild_all.py                # full pipeline
    python scripts/rebuild_all.py --from-ir      # skip the census (replays unchanged)
    python scripts/rebuild_all.py --check        # then run every gate
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: (script, why it must run here) in dependency order.
STAGES = (
    ("build_codemap.py",
     "observe the replays -> artifacts/codemap/observed.json"),
    ("close_vmless_wall.py",
     "census + recovery_ir.json + the lifted (VMless) corpus"),
    ("build_recovered.py",
     "the recovered (CPUless) corpus + cpuless_manifest.json"),
)


def _run(script: str, why: str) -> None:
    print(f"\n=== {script} -- {why}")
    t0 = time.time()
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / script)],
                       cwd=ROOT)
    if r.returncode != 0:
        raise SystemExit(f"[rebuild_all] {script} FAILED (exit {r.returncode}) "
                         f"-- stopping; later stages would consume its stale output")
    print(f"=== {script} ok ({time.time() - t0:.0f}s)")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--from-ir", action="store_true",
                    help="skip the census (use when the REPLAY SET is unchanged "
                         "and only dos_re/codegen moved)")
    ap.add_argument("--check", action="store_true",
                    help="run scripts/check_all.py afterwards")
    args = ap.parse_args(argv)

    stages = STAGES[2:] if args.from_ir else STAGES
    if args.from_ir:
        print("[rebuild_all] --from-ir: census and IR assumed current")
    for script, why in stages:
        _run(script, why)
    print("\n[rebuild_all] pipeline complete")
    if args.check:
        return subprocess.run([sys.executable,
                               str(ROOT / "scripts/check_all.py")],
                              cwd=ROOT).returncode
    print("[rebuild_all] verify it with: python scripts/check_all.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
