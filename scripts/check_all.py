"""Run the current SkyRoads static, test, boot, and replay-verification gates.

The replay gate works on a temporary artifact copy so lazy boundary caching
never mutates the retained replay. ``--quick`` skips that differential for an
inner-loop check.

Usage:
    python scripts/check_all.py              # everything (minutes)
    python scripts/check_all.py --quick      # static checks + tests (seconds)
    python scripts/check_all.py --replay DIR   # verify another ReplayArtifact
    python scripts/check_all.py --no-pypy    # force CPython for every gate

Oracle-heavy replay runs use PyPy when available; all other gates use the
interpreter that launched this script. The selected executable is reported and
does not change the verification policy.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPLAY = ROOT / "recovery" / "replays" / "oracle_atlas_smoke"
BOOTSTRAP_ARTIFACTS = (
    ROOT / "artifacts" / "boot_image" / "state.json",
    ROOT / "artifacts" / "boot_image" / "memory_1mb.bin",
    ROOT / "artifacts" / "boot_image" / "manifest.json",
)


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


def pytest_argv(target: str, *, xdist_available: bool | None = None) -> list[str]:
    """Build a pytest command, using xdist only when it is installed."""
    if xdist_available is None:
        xdist_available = importlib.util.find_spec("xdist") is not None
    argv = ["-m", "pytest", target, "-q"]
    if xdist_available:
        argv.extend(("-n", "auto"))
    return argv


def _release_plan_expectations() -> tuple[str, ...]:
    """Diagnostics required from the intentionally unready release plan."""
    expected = [
        "execution profile 'release' cannot be planned",
        "unresolved control-flow edges",
    ]
    if not all(path.is_file() for path in BOOTSTRAP_ARTIFACTS):
        expected.extend((
            "missing bootstrap artifacts",
            "python scripts/build_boot_image.py",
        ))
    return tuple(expected)


def _development_plan_expectations() -> tuple[int, tuple[str, ...]]:
    """Expected default-product preflight with or without its build image."""
    if all(path.is_file() for path in BOOTSTRAP_ARTIFACTS):
        return 0, ("execution profile: development", "bound identities:")
    return 2, (
        "missing bootstrap artifacts",
        "python scripts/build_boot_image.py",
    )


def _run(
    name: str,
    argv: list[str],
    *,
    expect: str | tuple[str, ...] | None = None,
    expected_returncode: int = 0,
    python: str | None = None,
) -> tuple[bool, str]:
    exe = python or sys.executable
    t0 = time.time()
    r = subprocess.run([exe, *argv], cwd=ROOT, text=True, capture_output=True)
    out = r.stdout + r.stderr
    expectations = (expect,) if isinstance(expect, str) else (expect or ())
    ok = (
        r.returncode == expected_returncode
        and all(fragment in out for fragment in expectations)
    )
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
                    help="skip the retained ReplayArtifact differential")
    ap.add_argument("--replay", default=str(DEFAULT_REPLAY))
    ap.add_argument("--verify-start", type=int, default=0)
    ap.add_argument("--verify-end", type=int)
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
    results.append(_run("project lint", ["tools/lint.py"],
                        expect="lint passed"))
    results.append(_run(
        "undefined-name analysis",
        ["tools/check_undefined_names.py", "skyroads"],
        expect="check_undefined_names passed",
    ))
    results.append(_run("cpuless purity (no path reaches a CPU)",
                        ["tools/lint_cpuless.py"], expect="PASS"))
    results.append(_run(
        "active documentation links",
        ["dos_re/tools/check_doc_links.py", ".",
         "--exclude", "dos_re", "--exclude", "history"],
        expect="all relative links resolve",
    ))
    if importlib.util.find_spec("xdist") is None:
        print("[check] pytest-xdist not installed; test suites run serially")
    results.append(_run("port test suite", pytest_argv("tests/")))
    results.append(_run("dos_re test suite", pytest_argv("dos_re/tests/")))
    development_returncode, development_expectations = (
        _development_plan_expectations()
    )
    results.append(_run(
        "development bootstrap preflight",
        ["scripts/play.py", "--plan-only"],
        expect=development_expectations,
        expected_returncode=development_returncode,
    ))
    results.append(_run(
        "release/generated ABI rejects unresolved coverage before launch",
        ["scripts/play.py", "--profile", "release",
                         "--composition", "generated-detached",
                         "--plan-only"],
        expect=_release_plan_expectations(),
        expected_returncode=2,
    ))

    if not args.quick:
        print("[check] ReplayArtifact differential (literal generated functions)")
        source = Path(args.replay).resolve()
        manifest = json.loads(
            (source / "replay.json").read_text(encoding="utf-8"))
        end = args.verify_end
        if end is None:
            end = int(manifest["metadata"]["end_point"]["ordinal"])
        with tempfile.TemporaryDirectory(prefix="skyroads-check-replay-") as td:
            replay = Path(td) / "replay"
            shutil.copytree(source, replay)
            results.append(_run(
                "generated functions vs untouched oracle",
                ["scripts/play.py", "--profile", "verification",
                 "--composition", "workbench-auto",
                 "--play-replay", str(replay),
                 "--verify-start", str(args.verify_start),
                 "--verify-end", str(end)],
                expect="EQUIVALENT", python=fast))
    else:
        print("[check] --quick: differentials SKIPPED (they are the real proof)")

    failed = [ok for ok, _ in results].count(False)
    print(f"\n[check] {len(results) - failed}/{len(results)} gates passed")
    if failed:
        print("[check] FAILED -- do not commit on this state")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
