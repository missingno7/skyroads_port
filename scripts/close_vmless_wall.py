"""Converge the strict-VMless wall — the closure loop.

The observation census (``scripts/build_codemap.py``) only knows what the demos
actually EXECUTED. Booting the data-only image from the canonical entry runs
code no recording reaches (the game's own startup, error paths, and routines
whose callers were hooked). Each such address makes the armed wall fail loud:

    VMLESS WALL VIOLATION: attempted to interpret an original instruction at
    1010:XXXX -- no lifted hook covers this address.

That failure IS the work list. This script turns it into a fixed point: boot,
catch the address, add it to the census, regenerate the IR + corpus, boot again
-- until the image runs clean for a step budget or nothing new appears.

Every address it adds was reached by REAL execution of the recovered program, so
the census stays evidence-backed: this discovers entries, it never invents them.
The added entries are written to ``artifacts/codemap/closure_extra.txt`` so the
next full rebuild keeps them.

Usage:
    python scripts/close_vmless_wall.py                 # converge
    python scripts/close_vmless_wall.py --rounds 5 --steps 20000
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CODEMAP = ROOT / "artifacts" / "codemap"
EXTRA_FILE = CODEMAP / "closure_extra.txt"

VIOLATION_RE = re.compile(r"interpret an original instruction at ([0-9A-Fa-f]{4}:[0-9A-Fa-f]{4})")


def read_extras() -> list[str]:
    if not EXTRA_FILE.exists():
        return []
    return [ln.strip() for ln in EXTRA_FILE.read_text().splitlines()
            if ln.strip() and not ln.startswith("#")]


def write_extras(extras: list[str]) -> None:
    EXTRA_FILE.parent.mkdir(parents=True, exist_ok=True)
    EXTRA_FILE.write_text(
        "# Census entries discovered by scripts/close_vmless_wall.py: addresses the\n"
        "# recovered program REACHES at runtime but no recorded demo ever executed\n"
        "# (startup paths, and routines whose callers were hooked during observation).\n"
        + "".join(f"{a}\n" for a in sorted(set(extras))))


def regenerate(lift_dir: Path, extras: list[str]) -> None:
    """census -> IR -> corpus, with the discovered extras folded in."""
    extra_args: list[str] = []
    for a in extras:
        extra_args += ["--extra", a]
    subprocess.run([sys.executable, str(ROOT / "dos_re/tools/codemap.py"),
                    "--observed", str(CODEMAP / "observed.json"),
                    "--out", str(CODEMAP / "entries.txt"),
                    "--seg", "1010", "--extra", "1010:61F3", *extra_args],
                   check=True, capture_output=True, text=True)
    subprocess.run([sys.executable, str(ROOT / "dos_re/tools/irgen.py"),
                    "--exe", str(ROOT / "assets/SKYROADS.EXE"),
                    "--snapshot", str(ROOT / "artifacts/snapshots/menu_code_live_f250"),
                    "--game-root", str(ROOT / "assets"),
                    "--entries-file", str(CODEMAP / "entries.txt"),
                    "--boundary-heads", f"@{CODEMAP / 'boundary_heads.txt'}",
                    "--out", str(CODEMAP / "recovery_ir.json")],
                   check=True, capture_output=True, text=True)
    # --desmc: the SMC routines (LZS decoder, blit threshold, timer-ISR far
    # chain) lift as operand-from-memory transforms; without it they are
    # refused and the boot dies in the startup decode.  The raised iteration
    # guard is for the decompressors, which legitimately loop far past the
    # emitter's default.
    subprocess.run([sys.executable, str(ROOT / "dos_re/tools/liftemit.py"),
                    "--from-ir", str(CODEMAP / "recovery_ir.json"),
                    "--boundary-heads", f"@{CODEMAP / 'boundary_heads.txt'}",
                    "--desmc", "--max-iterations", "8000000",
                    "--emit-dir", str(lift_dir)],
                   check=True, capture_output=True, text=True)


def try_boot(lift_dir: Path, steps: int) -> tuple[bool, str]:
    """Drive FRAMES through the image and report the first wall violation.

    Frames, not raw steps: the corpus parks at tick-wait boundary heads and
    only makes progress when the next frame's timer IRQs arrive (see
    scripts/play_vmless.py).  A raw step loop stalls at the first park and so
    never reaches the code the game runs from its ISRs and later screens --
    which is exactly the code the census is missing.  ``steps`` is read as a
    FRAME budget here.
    """
    code = f'''
import sys; sys.path.insert(0, r"{ROOT / 'dos_re'}"); sys.path.insert(0, r"{ROOT}")
sys.path.insert(0, r"{ROOT / 'scripts'}")
from pathlib import Path
from play_vmless import build, VmlessDriver
rt, m = build(Path(r"{ROOT / 'artifacts/boot_image'}"), Path(r"{lift_dir}"),
              Path(r"{ROOT / 'assets'}"))
drv = VmlessDriver(rt)
n = 0
try:
    for _ in range({steps}):
        if not drv.frame():
            break
        n += 1
except Exception as e:
    print("VIOLATION", type(e).__name__, str(e).replace(chr(10), " "))
else:
    print("CLEAN", n)
'''
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       cwd=str(ROOT), timeout=1800)
    out = (r.stdout or "") + (r.stderr or "")
    if "CLEAN" in out:
        return True, out.strip().splitlines()[-1]
    m = VIOLATION_RE.search(out)
    if m:
        return False, m.group(1).upper()
    return False, out.strip()[-300:]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--rounds", type=int, default=25)
    ap.add_argument("--steps", type=int, default=400,
                    help="FRAME budget per probe run (the driver ticks frames)")
    ap.add_argument("--lift-dir", default=str(ROOT / "artifacts" / "lifted_full"))
    args = ap.parse_args(argv)

    lift_dir = Path(args.lift_dir)
    extras = read_extras()
    print(f"[closure] starting with {len(extras)} previously-discovered entries")

    for rnd in range(args.rounds):
        regenerate(lift_dir, extras)
        clean, info = try_boot(lift_dir, args.steps)
        if clean:
            print(f"[closure] round {rnd}: {info} -- WALL CLOSED "
                  f"({len(extras)} discovered entries)")
            write_extras(extras)
            return 0
        if not VIOLATION_RE.search(f"interpret an original instruction at {info}") \
                and ":" not in info:
            print(f"[closure] round {rnd}: stopped on a NON-wall failure:\n{info}")
            write_extras(extras)
            return 1
        if info in extras:
            print(f"[closure] round {rnd}: {info} already added but still uncovered "
                  f"-- needs a resume/dispatch entry, not a census entry. Stopping.")
            write_extras(extras)
            return 1
        extras.append(info)
        print(f"[closure] round {rnd}: + {info}  ({len(extras)} total)")
        write_extras(extras)

    print(f"[closure] {args.rounds} rounds without closing; {len(extras)} entries added")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
