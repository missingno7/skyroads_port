"""Play the game through the CPUless corpus — stage 2 (CPULESS LIFTED).

Same driver, boot image, audio, and oracle-checked frame loop as
``play_vmless``; the ONLY difference is the corpus it installs. Where
``play_vmless`` runs ``artifacts/lifted_full`` (every hook steps the recovered
ASM through the interpreter), this runs ``artifacts/cpuless``: the PROMOTABLE
subset replaced by pure-Python recovered functions + CPU-ABI adapters, the rest
still literal lifts. See scripts/build_cpuless_corpus.py for how the corpus is
assembled and why some functions stay lifted.

The corpus is built on demand if it is missing (it is a regenerable artifact,
gitignored like the lifted corpus). Every ``play_vmless`` flag applies —
``--lift-dir`` just defaults to the CPUless corpus here.

Usage:
    python scripts/play_cpuless.py                       # windowed
    python scripts/play_cpuless.py --headless --frames 300
    python scripts/play_cpuless.py --rebuild             # force a corpus rebuild
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dos_re"))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

CPULESS_DIR = ROOT / "artifacts" / "cpuless"


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    rebuild = "--rebuild" in argv
    if rebuild:
        argv.remove("--rebuild")
    import build_cpuless_corpus as bcc
    if rebuild or not any(CPULESS_DIR.glob("lifted_*.py")):
        rc = bcc.main([] if not rebuild else [])
        if rc != 0:
            return rc
    # default the corpus to the CPUless overlay unless the caller overrides it
    if "--lift-dir" not in argv:
        argv = ["--lift-dir", str(CPULESS_DIR)] + argv
    import play_vmless
    return play_vmless.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
