"""tools/lint_cpuless.py -- prove the standalone CPUless runner is CPU-free.

A thin, documented wrapper over dos_re's generic ``lint_cpuless.py`` pinned to
THIS port's layout: the standalone runner ``scripts/play_cpuless.py``, the
generated corpus ``skyroads/cpuless_standalone/``, and the interpreter/lifted
carriers this port must never reach.  Static import-graph proof (AST): no path
from the runner or the recovered corpus imports a CPU.

Usage:
    python tools/lint_cpuless.py             # exit 0 = CPU-free
    python tools/lint_cpuless.py --print-payload
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = [sys.executable, str(ROOT / "dos_re/tools/lint_cpuless.py"),
           "--repo-root", str(ROOT),
           "--root", "scripts/play_cpuless.py",
           "--recovered-root", "skyroads/cpuless_standalone",
           "--recovered-prefix", "skyroads.cpuless_standalone",
           # the interpreter and every CPU-carrying corpus the runner forbids:
           "--forbidden-module", "dos_re.cpu",
           "--forbidden-module", "skyroads.lifted",
           "--forbidden-module", "skyroads.recovered",
           "--local-prefix", "dos_re", "--local-prefix", "skyroads",
           "--package-dir", "dos_re=dos_re/dos_re",
           "--package-dir", "skyroads=skyroads",
           *argv]
    return subprocess.run(cmd).returncode


if __name__ == "__main__":
    raise SystemExit(main())
