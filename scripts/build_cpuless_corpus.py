"""Build the CPUless corpus — the stage-2 (CPULESS LIFTED) play surface.

The lifted corpus (``artifacts/lifted_full``) is an interpreter-per-instruction
lift: every hook still steps the recovered ASM through ``dos_re``'s CPU. This
step replaces the PROMOTABLE subset of it with CPUless recovered functions —
pure Python over ``(mem[, plat], *regs)`` — plus a generated CPU-ABI adapter
that occupies each lifted slot. The result is a HYBRID play surface:

    skyroads/recovered/func_CCCC_IIII.py   the recovered implementations
                                           (imports nothing; semantic outputs)
    artifacts/cpuless/lifted_CCCC_IIII.py  = the lifted corpus, with each
                                             promoted slot overwritten by its
                                             CPU-ABI adapter (imports the
                                             recovered module); the rest stay
                                             literal lifts

``scripts/play_cpuless.py`` (and ``verify_vmless_demo --lift-dir
artifacts/cpuless``) run the game through it, diffed byte-exact against the
same ASM oracle as the lifted corpus.

WHY THE EXCLUSIONS. A promoted function runs MONOLITHICALLY — one Python call,
no yield to the frame scheduler mid-body. Two constructs need that yield and so
must stay lifted:

  * boundary heads (``artifacts/codemap/boundary_heads.txt``): a tick-wait loop
    (`reads ds:[1600] and never writes it`) only terminates when the timer IRQ
    advances the tick at a scheduler park. Monolithic, it spins forever.
  * snapshot re-entry points (``snapshot_entries.txt``): a function the runtime
    RESUMES at an interior block cannot be entered top-and-run.

``cpuless_promote --exclude`` refuses any function containing one of those
addresses (``boundary-or-dispatch-address``), so it keeps its interpreter lift.
This is the documented canonical invocation — the same exclusion the lifted
corpus's own scheduler contract implies.

KNOWN FRONTIER — gameplay dynamic dispatch. A promoted near-indirect dispatcher
(e.g. the per-object render dispatch 1010:2E6C) resolves its target through the
recovered DISPATCH registry, which only holds PROMOTED targets. When a target
is excluded — 1010:3190 is, because the snapshot resume point 1010:3199 sits
inside it — the dispatch is not registry-CLOSED and the runtime fails loud
(``UnknownDispatchTarget``, a deliberate frontier witness). The front-end /
menu path is closed and plays byte-exact; closing the gameplay dispatch set
needs ``--dyn-evidence`` (per-site target evidence) so the promotion fixpoint
either promotes every target or refuses the dispatcher. Until then, run the
front-end demos through the CPUless corpus and gameplay through play_vmless.

Usage:
    python scripts/build_cpuless_corpus.py            # build (regenerates the
                                                      # lifted corpus if absent)
    python scripts/build_cpuless_corpus.py --clean    # force a fresh lifted lift
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CODEMAP = ROOT / "artifacts" / "codemap"
LIFTED_FULL = ROOT / "artifacts" / "lifted_full"
CPULESS_DIR = ROOT / "artifacts" / "cpuless"
RECOVERED_DIR = ROOT / "skyroads" / "recovered"
IMPORT_BASE = "skyroads.recovered"

#: Interior addresses whose containing function must stay lifted (they need the
#: frame scheduler; a monolithic CPUless body cannot yield to it). See module docstring.
EXCLUDE_FILES = (CODEMAP / "boundary_heads.txt", CODEMAP / "snapshot_entries.txt")


def ensure_lifted_corpus(clean: bool) -> None:
    """The CPUless corpus is an overlay on the lifted one; regenerate it first
    if missing (or --clean) so the two never drift."""
    if clean or not any(LIFTED_FULL.glob("lifted_*.py")):
        sys.path.insert(0, str(ROOT / "scripts"))
        from close_vmless_wall import read_extras, regenerate
        print(f"[cpuless] (re)generating the lifted corpus -> {LIFTED_FULL}")
        LIFTED_FULL.mkdir(parents=True, exist_ok=True)
        regenerate(LIFTED_FULL, read_extras())


def build() -> int:
    if CPULESS_DIR.exists():
        shutil.rmtree(CPULESS_DIR)
    shutil.copytree(LIFTED_FULL, CPULESS_DIR)
    # a fresh recovered package each build -- a stale module for a since-refused
    # function would import-resolve to dead code.
    if RECOVERED_DIR.exists():
        shutil.rmtree(RECOVERED_DIR)
    RECOVERED_DIR.mkdir(parents=True)
    (RECOVERED_DIR / "__init__.py").write_text(
        '"""Generated CPUless recovered functions (dos_re stage 2).\n\n'
        "Build artifact -- regenerate with scripts/build_cpuless_corpus.py, do\n"
        'not hand-edit. Reserved slot per skyroads/__init__.py."""\n',
        encoding="utf-8")

    cmd = [sys.executable, str(ROOT / "dos_re/tools/cpuless_promote.py"),
           "--ir", str(CODEMAP / "recovery_ir.json"),
           "--recovered-dir", str(RECOVERED_DIR),
           "--adapter-dir", str(CPULESS_DIR),
           "--import-base", IMPORT_BASE]
    for f in EXCLUDE_FILES:
        cmd += ["--exclude", f"@{f}"]
    cmd += ["--apply"]
    print("[cpuless] " + " ".join(str(c) for c in cmd))
    r = subprocess.run(cmd, text=True)
    if r.returncode != 0:
        return r.returncode

    n_rec = len(list(RECOVERED_DIR.glob("func_*.py")))
    n_slots = len(list(CPULESS_DIR.glob("lifted_*.py")))
    print(f"\n[cpuless] corpus ready: {n_slots} lifted slots, {n_rec} now CPUless "
          f"adapters over recovered bodies ({n_slots - n_rec} still literal lifts)")
    print(f"[cpuless]   play:   python scripts/play_cpuless.py")
    print(f"[cpuless]   verify: python scripts/verify_vmless_demo.py <demo> "
          f"--lift-dir {CPULESS_DIR}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--clean", action="store_true",
                    help="regenerate the lifted corpus from the IR first")
    args = ap.parse_args(argv)
    ensure_lifted_corpus(args.clean)
    return build()


if __name__ == "__main__":
    raise SystemExit(main())
