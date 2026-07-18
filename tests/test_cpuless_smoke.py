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
import re
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
#:
#: 182 -> 180 when the block-type coverage demos landed: censusing block types 3
#: and 5 (levels 14 and 8) regenerated the IR, which reclassified two functions
#: as dead-unreachable -- they are proven unreachable, not missing. The runtime
#: closure stays COMPLETE, which is the property that actually matters.
EXPECTED_FUNCTIONS = 180
#: IR functions proven unreachable at runtime (emitted as nothing, not stubs).
EXPECTED_DEAD_UNREACHABLE = 2
#: the boundary head the CPU-free cold boot reaches (C startup + intro
#: decompression + first frame render, all with no CPU).
FIRST_FRAME_BOUNDARY = "434A"


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
    assert man["counts"].get("dead-unreachable", 0) == EXPECTED_DEAD_UNREACHABLE
    # Nothing runtime-reachable may be left unpromoted: a fail-loud-unsupported
    # here is the wall firing during play (that is how 1010:2F57 reached a
    # player), so it is the assertion that actually protects the runner.
    assert man["counts"].get("fail-loud-unsupported", 0) == 0
    assert man["runtime_closure_complete"] is True
    assert man["runtime_frontier"] == []          # closed vs the observed trace

    # 3. purity lint: no CPU on any import path.
    lint = _run("tools/lint_cpuless.py")
    assert lint.returncode == 0, lint.stdout + lint.stderr
    assert "PASS" in lint.stdout

    # 4. the runner cold-boots from 61F3 with NO CPU / NO interpreter, runs the
    #    whole C startup + intro decompression to the frame loop, and RENDERS
    #    frames (timer IRQs delivered through the recovered INT 08h ISR).
    play = _run("scripts/play_cpuless.py", "--headless", "--frames", "12",
                timeout=600)
    assert play.returncode == 0, (play.stdout + play.stderr)[-2000:]
    assert "REACHED FIRST FRAME BOUNDARY" in play.stdout
    assert FIRST_FRAME_BOUNDARY in play.stdout
    m = re.search(r"rendered \d+ frames \(VGA nonzero px=(\d+)\)", play.stdout)
    assert m and int(m.group(1)) > 0, "expected a rendered (nonzero) frame"


#: Drive the INTERACTIVE runner (the default, user-facing path) off-screen: a
#: dummy SDL video driver plus an injected QUIT, so CI exercises the real window
#: loop -- Display sizing, frame decode, key dispatch, quit handling -- without a
#: display.  The headless test above cannot cover any of that.
_INTERACTIVE_DRIVER = """
import sys
sys.path.insert(0, "scripts"); sys.path.insert(0, "."); sys.path.insert(0, "dos_re")
import pygame
import play_cpuless as P
n = [0]
real_get = pygame.event.get
def fake_get(*a, **k):
    n[0] += 1
    if n[0] >= %d:
        return [pygame.event.Event(pygame.QUIT)]
    return real_get(*a, **k)
pygame.event.get = fake_get
rc = P.run_interactive(2, False, 1000, False)   # high present-hz: no pacing stall
print("RC", rc, "PUMPS", n[0])
"""


@pytest.mark.skipif(not BOOT.exists(),
                    reason="boot image absent (needs the user's game files)")
def test_interactive_runner_renders_and_quits_cleanly():
    pytest.importorskip("pygame")
    pytest.importorskip("numpy")
    import os
    env = dict(os.environ, SDL_VIDEODRIVER="dummy", SDL_AUDIODRIVER="dummy")
    r = subprocess.run([sys.executable, "-u", "-c", _INTERACTIVE_DRIVER % 40],
                       cwd=ROOT, text=True, capture_output=True, timeout=900,
                       env=env)
    assert r.returncode == 0, (r.stdout + r.stderr)[-2000:]
    assert "NO CPU and NO interpreter" in r.stdout
    # The window is sized for the GAME's framebuffer, not the boot text console.
    assert "320x200" in r.stdout
    m = re.search(r"quit after (\d+) frames", r.stdout)
    assert m and int(m.group(1)) > 0, "interactive loop never advanced a frame"
    assert "RC 0" in r.stdout
