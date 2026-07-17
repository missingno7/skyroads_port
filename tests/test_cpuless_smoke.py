"""Clean-checkout smoke for the STANDALONE (no-CPU) CPUless port.

From tracked inputs only, this:
  1. regenerates the standalone corpus (build_recovered.py);
  2. verifies the expected recovered-function count + the manifest;
  3. runs the purity lint (no path reaches a CPU);
  4. starts the standalone runner (scripts/play_cpuless.py), which boots from
     1010:61F3 through CPUlessPlatformRuntime with NO interpreter and reaches
     the explicitly recorded cold-start frontier (fail-loud, exit 3).

The recovered corpus + boot image are gitignored regenerable artifacts; this
test rebuilds the corpus itself but needs the boot image (which is built from
the user's own game files), so it SKIPS when the boot image is absent (CI
without assets), exactly like the native-sfx smoke.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BOOT = ROOT / "artifacts" / "boot_image" / "memory_1mb.bin"
MANIFEST = ROOT / "artifacts" / "codemap" / "cpuless_manifest.json"
CORPUS = ROOT / "skyroads" / "recovered"

#: the standalone corpus's expected recovered-function count (every
#: runtime-reachable IR function). Bump deliberately when the census changes.
EXPECTED_FUNCTIONS = 182
#: the currently recorded cold-start frontier the runner fails loud at (the
#: --observed trace does not yet cover this early-startup path).
RECORDED_FRONTIER = "5FEA"


def _run(*args, **kw):
    return subprocess.run([sys.executable, *args], cwd=ROOT, text=True,
                          capture_output=True, **kw)


@pytest.mark.skipif(not BOOT.exists(),
                    reason="boot image absent (needs the user's game files)")
def test_standalone_corpus_regenerates_lints_and_boots_to_the_frontier():
    # 1. regenerate the corpus from tracked inputs.
    b = _run("scripts/build_recovered.py")
    assert b.returncode == 0, b.stderr

    # 2. expected function count + manifest.
    n = len(list(CORPUS.glob("func_*.py")))
    assert n == EXPECTED_FUNCTIONS, f"expected {EXPECTED_FUNCTIONS} funcs, got {n}"
    man = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert man["counts"].get("generated-cpuless") == EXPECTED_FUNCTIONS
    assert man["runtime_closure_complete"] is True
    assert man["runtime_frontier"] == []          # closed vs the observed trace

    # 3. purity lint: no CPU on any import path.
    lint = _run("tools/lint_cpuless.py")
    assert lint.returncode == 0, lint.stdout + lint.stderr
    assert "PASS" in lint.stdout

    # 4. the runner boots from 61F3 (no CPU) and fails loud at the recorded
    #    frontier -- a DETERMINISTIC stop point, not a hang or a silent pass.
    play = _run("scripts/play_cpuless.py", "--headless", timeout=600)
    assert play.returncode == 3, (play.stdout + play.stderr)[-2000:]
    assert "HARD-WALL FRONTIER" in play.stdout
    assert RECORDED_FRONTIER in play.stdout
