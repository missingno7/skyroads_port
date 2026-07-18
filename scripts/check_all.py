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
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DEMO = ROOT / "artifacts" / "demos" / "demo_cold_20260718_003412"


def _run(name: str, argv: list[str], *, expect: str | None = None) -> tuple[bool, str]:
    t0 = time.time()
    r = subprocess.run([sys.executable, *argv], cwd=ROOT, text=True,
                       capture_output=True)
    out = r.stdout + r.stderr
    ok = r.returncode == 0 and (expect is None or expect in out)
    dt = time.time() - t0
    print(f"  {'PASS' if ok else 'FAIL'}  {name}  ({dt:.0f}s)")
    if not ok:
        tail = "\n".join(out.strip().splitlines()[-12:])
        print("\n".join("        " + ln for ln in tail.splitlines()))
    return ok, out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--quick", action="store_true",
                    help="skip the frame-exact differentials")
    ap.add_argument("--demo", default=str(DEFAULT_DEMO))
    args = ap.parse_args(argv)

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
                        ["-m", "pytest", "dos_re/tests/", "-q"]))
    results.append(_run("play_cpuless boots (no CPU)",
                        ["scripts/play_cpuless.py", "--headless", "--frames", "12"],
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
        results.append(_run("island shadows (candidates vs generated, full contract)",
                            ["scripts/verify_cpuless.py", args.demo, "--shadow-only"],
                            expect="PASS"))
        print("[check] frame-exact differentials (the ones that actually prove it)")
        results.append(_run("verify_vmless  (lifted corpus vs ASM oracle)",
                            ["scripts/verify_vmless_demo.py", args.demo],
                            expect="PASS"))
        results.append(_run("verify_cpuless (recovered corpus, NO CPU, overrides DRIVING)",
                            ["scripts/verify_cpuless.py", args.demo],
                            expect="PASS"))
    else:
        print("[check] --quick: differentials SKIPPED (they are the real proof)")

    failed = [ok for ok, _ in results].count(False)
    print(f"\n[check] {len(results) - failed}/{len(results)} gates passed")
    if failed:
        print("[check] FAILED -- do not commit on this state")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
