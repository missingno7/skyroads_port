"""check_all.py -- every gate that must hold, in one command.

The port's guarantees are spread across four different tools, and checking them
by hand means running six commands and remembering which ones matter. Worse, the
cheap ones (lint, unit tests) pass in seconds while the ones that actually prove
the port correct -- the frame-exact differentials -- take minutes, so they are
the ones that get skipped.  They are also the ones that catch real breakage: the
smoke tests stayed green through a corpus change that the 672-frame differential
would have caught immediately.

So: run them all, cheapest first (fail fast on the cheap ones), and print one
verdict.  ``--quick`` stops before the differentials for an inner-loop check.

Usage:
    python scripts/check_all.py              # everything (minutes)
    python scripts/check_all.py --quick      # lint + tests only (seconds)
    python scripts/check_all.py --demo DIR   # differentials over another demo
    python scripts/check_all.py --no-pypy    # force CPython for every gate

THE INTERPRETER SPLIT (2026-07-18).  The oracle-stepping gates are pure-Python
instruction interpretation, which is exactly PyPy's best case; the test suites
are fixture-bound and want CPython + xdist instead (every PyPy worker re-pays
JIT warmup -- see dos_re/docs/performance.md).  So this script runs the
differentials under ``pypy3`` when it is on PATH and everything else under the
CPython that launched it.  It is an OPTIMISATION, NEVER A GATE CHANGE: same
script, same demo, same comparison, same exit status.  The choice is PRINTED
per gate so a fast run can never be mistaken for a different run, and
``--no-pypy`` forces CPython everywhere.

Evidence that the two agree (2026-07-18): the full 5,109-frame attract
differential was run end-to-end under both -- CPython 500.8s, PyPy 47.3s
(10.6x) -- and their output hashes the same (sha1 1da10d27..., heartbeat lines
excluded since those carry elapsed seconds), verdict included.  Re-run that
comparison before trusting a new PyPy build.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DEMO = ROOT / "artifacts" / "demos" / "demo_cold_20260718_003412"


def fast_python(enabled: bool = True) -> str:
    """Interpreter for oracle-heavy gates: PyPy when present, else CPython.

    ``SKYROADS_PYPY`` overrides the search (point it at a pypy3 executable, or
    set it empty to opt out).  Falling back to CPython is a slowdown and
    nothing else -- the gate is identical either way -- so the fallback is
    silent-but-reported rather than an error.
    """
    if not enabled:
        return sys.executable
    env = os.environ.get("SKYROADS_PYPY")
    if env is not None:
        return env or sys.executable
    return shutil.which("pypy3") or sys.executable


def _run(name: str, argv: list[str], *, expect: str | None = None,
         python: str | None = None) -> tuple[bool, str]:
    exe = python or sys.executable
    t0 = time.time()
    r = subprocess.run([exe, *argv], cwd=ROOT, text=True, capture_output=True)
    out = r.stdout + r.stderr
    ok = r.returncode == 0 and (expect is None or expect in out)
    dt = time.time() - t0
    tag = "pypy" if "pypy" in Path(exe).name.lower() else "cpython"
    print(f"  {'PASS' if ok else 'FAIL'}  {name}  ({dt:.0f}s, {tag})")
    if not ok:
        tail = "\n".join(out.strip().splitlines()[-12:])
        print("\n".join("        " + ln for ln in tail.splitlines()))
    return ok, out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--quick", action="store_true",
                    help="skip the frame-exact differentials")
    ap.add_argument("--demo", default=str(DEFAULT_DEMO))
    ap.add_argument("--no-pypy", action="store_true",
                    help="run the differentials under CPython too (identical "
                         "results, ~10x slower -- measured)")
    args = ap.parse_args(argv)

    fast = fast_python(not args.no_pypy)
    if fast == sys.executable:
        print(f"[check] oracle gates: CPython ({sys.executable}) -- "
              f"{'--no-pypy' if args.no_pypy else 'pypy3 not on PATH'}; "
              f"same gates, slower")
    else:
        print(f"[check] oracle gates: PyPy ({fast}) -- same gates, ~10x faster")

    results = []
    print("[check] cheap gates first")
    results.append(_run("cpuless purity (no path reaches a CPU)",
                        ["tools/lint_cpuless.py"], expect="PASS"))
    # `-n auto` is a free ~5x on this suite (263s serial -> ~54s). It also means
    # the corpus-rebuilding smoke test races the corpus-importing one, which is
    # why those two now serialize on a lock file rather than on luck.
    results.append(_run("port test suite", ["-m", "pytest", "tests/", "-q",
                                            "-n", "auto"]))
    results.append(_run("dos_re test suite",
                        ["-m", "pytest", "dos_re/tests/", "-q", "-n", "auto"]))
    results.append(_run("unified player boots CPUless (no CPU)",
                        ["scripts/play.py", "--profile", "detached",
                         "--composition", "cpuless", "--headless", "--frames", "12"],
                        expect="REACHED FIRST FRAME BOUNDARY"))

    if not args.quick:
        # The shadow rung, GATED. It used to run only when a human typed
        # --shadow-islands, which means a checker could rot indefinitely without
        # anything going red -- and the checker it replaced HAD rotted, comparing
        # one register out of a ten-part contract.
        #
        # It runs oracle-free (--shadow-only) because a shadow compares the
        # candidate against the generated body IN PROCESS: the oracle proves
        # nothing extra about it and is most of the wall clock. This is also the
        # gate that catches an override which is never CALLED -- that reports
        # INCONCLUSIVE, not success.
        print("[check] ReplayArtifact differential (selected faithful overrides)")
        results.append(_run(
            "faithful composition vs untouched oracle",
            ["scripts/play.py", "--profile", "verification",
             "--composition", "faithful", "--play-demo", args.demo],
            expect="PASS", python=fast))
    else:
        print("[check] --quick: differentials SKIPPED (they are the real proof)")

    failed = [ok for ok, _ in results].count(False)
    print(f"\n[check] {len(results) - failed}/{len(results)} gates passed")
    if failed:
        print("[check] FAILED -- do not commit on this state")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
