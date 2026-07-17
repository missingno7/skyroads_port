"""play_cpuless.py -- the TRUE standalone CPUless runner (NO CPU, NO interpreter).

Starts at the C-startup root ``1010:61F3`` and drives the recovered corpus
through :class:`dos_re.lift.platform.CPUlessPlatformRuntime` -- pure Python over
the boot-image memory + a device model + a virtual clock.  It NEVER imports or
instantiates the interpreter (``dos_re.cpu``); a runtime import guard is the
dynamic backstop and ``tools/lint_cpuless.py`` is the static proof.

CURRENT STATE -- fails loud at the recorded cold-start frontier.  The
``--observed`` probe trace does not yet cover the earliest C-startup low-level
init, so a from-61F3 cold boot reaches code the corpus marked runtime-dead and
the hard wall fires (a fail-loud raise / UnknownDispatchTarget), by design --
never a silent fallback.  Closing it needs a fuller startup capture (the
cold-boot capture -> close -> promote loop).  The runner reports exactly where
it stopped.

Usage:
    python scripts/play_cpuless.py --headless          # boot; report stop point
    python scripts/play_cpuless.py --headless --frames 30
    python scripts/play_cpuless.py --rebuild           # regenerate the corpus
"""
from __future__ import annotations

import argparse
import builtins
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: The interpreter and every CPU-carrying surface the standalone runtime must
#: never reach.  x86 is the CPU-FREE shared leaf (constants + HaltExecution) and
#: is allowed; dos_re.cpu is the interpreter and is not.
_FORBIDDEN = ("dos_re.cpu", "skyroads.lifted")


def _arm_import_guard() -> None:
    """Hook ``__import__`` so any attempt to pull the interpreter (or a
    CPU-carrying corpus) fails loud.  The STATIC proof is tools/lint_cpuless.py;
    this is the runtime backstop for a path the static walk cannot see."""
    real_import = builtins.__import__

    def guarded(name, *a, **k):
        if any(name == m or name.startswith(m + ".") for m in _FORBIDDEN):
            raise ImportError(
                f"CPUless hard wall: the standalone runner must never import "
                f"{name!r} (a CPU/interpreter carrier)")
        return real_import(name, *a, **k)

    builtins.__import__ = guarded


CANONICAL_ENTRY = (0x1010, 0x61F3)
BOOT_DIR = ROOT / "artifacts" / "boot_image"
STANDALONE_DIR = ROOT / "skyroads" / "recovered"
#: the recovered INT 08h timer ISR's contract inputs (dispatch.py HANDLERS).
_TIMER_INPUTS = ("ax", "bp", "bx", "cx", "di", "ds", "dx", "es", "si", "sp", "ss")


def _ensure_corpus(rebuild: bool) -> None:
    if rebuild or not any(STANDALONE_DIR.glob("func_*.py")):
        print("[cpuless] regenerating the standalone corpus ...")
        r = subprocess.run([sys.executable,
                            str(ROOT / "scripts/build_recovered.py")])
        if r.returncode != 0:
            raise SystemExit(r.returncode)


def _load_boot():
    from dos_re.memory import Memory
    state = json.loads((BOOT_DIR / "state.json").read_text(encoding="utf-8"))
    img = (BOOT_DIR / "memory_1mb.bin").read_bytes()
    mem = Memory()
    mem.data[:len(img)] = img
    manifest = json.loads((BOOT_DIR / "manifest.json").read_text(encoding="utf-8")) \
        if (BOOT_DIR / "manifest.json").exists() else {}
    return mem, state["cpu"], state.get("dos", {}), manifest


class _RtShim:
    """Minimal (mem, dos) view so the CPU-free _restore_dos_state can apply the
    snapshot's device + memory-arena state onto our standalone runtime."""
    def __init__(self, dos, mem):
        import types
        self.dos = dos
        self.program = types.SimpleNamespace(memory=mem)


def _vga_nonzero(mem) -> int:
    base = 0xA000 * 16
    return sum(1 for b in mem.data[base:base + 64000] if b)


def run(frames: int, rebuild: bool) -> int:
    _arm_import_guard()
    _ensure_corpus(rebuild)
    sys.path.insert(0, str(ROOT / "dos_re"))
    sys.path.insert(0, str(ROOT))

    import inspect
    from dos_re.lift.platform import (CPUlessPlatformRuntime,
                                      UnsupportedPlatformEffect)
    from dos_re.dos import DOSMachine
    from dos_re.snapshot_headless import _restore_dos_state   # runtime CPU-free
    from skyroads.recovered.func_1010_61f3 import func_1010_61f3
    from skyroads.recovered.func_1010_3b17 import func_1010_3b17
    try:
        from skyroads.recovered._dyncall import UnknownDispatchTarget
    except Exception:                       # noqa: BLE001
        UnknownDispatchTarget = ()

    mem, regs0, dos_meta, boot_manifest = _load_boot()
    poisoned = boot_manifest.get("code_bytes_present_after")
    if poisoned is not None:
        print(f"Recovered code present in boot image: {poisoned} bytes "
              f"(0 = severed from the original EXE)")

    dos = DOSMachine(ROOT)
    # Restore the snapshot's DOS/device + memory-arena state (allocations,
    # next_alloc_segment, video/PIT/OPL/EGA). Without this the C-runtime heap
    # allocation (int 21/48h) fails against a fresh arena and the startup takes
    # its out-of-memory error path -- the real reason the cold boot diverged.
    _restore_dos_state(_RtShim(dos, mem), dos_meta)
    rt = CPUlessPlatformRuntime(mem, game_root=ROOT, dos=dos)

    #: SYNCHRONOUS frame scheduler.  A boundary head is a plat.boundary call
    #: from INSIDE the running recovered program; this callback IS the per-frame
    #: loop.  SkyRoads paces off ds:[1600], the tick its INT 08h ISR bumps once
    #: per 6 IRQs; a frame is: deliver this frame's timer IRQs through the game's
    #: OWN recovered INT 08h ISR (func_1010_3b17), which advances the tick so the
    #: boundary's tick-wait exits, then let the recovered body render the frame.
    #: NO CPU, NO interpreter -- the ISR is recovered Python.
    TIMER_IRQS_PER_FRAME = 6
    state = {"frames": 0}
    limit = frames or 30

    def boundary_cb(head_cs, head_ip, resume_ip, regs, cost):
        state["frames"] += 1
        for _ in range(TIMER_IRQS_PER_FRAME):
            kw = {k: regs[k] for k in _TIMER_INPUTS if k in regs}
            kw["_flags_in"] = regs.get("_flags_in", 2)
            func_1010_3b17(mem, rt, **kw)         # advances ds:[1600] (recovered)
        if state["frames"] == 1:
            print(f"[cpuless] REACHED FIRST FRAME BOUNDARY {head_cs:04X}:"
                  f"{head_ip:04X} -- CPU-free cold boot to the frame loop")
        if state["frames"] >= limit:
            raise _Done()
        return regs, regs.get("_flags_in", 2), 0
    rt.boundary_cb = boundary_cb

    entry_kw = {k: v for k, v in regs0.items()
                if k in inspect.signature(func_1010_61f3).parameters}
    entry_kw["_flags_in"] = regs0.get("flags", 2)
    print(f"[cpuless] boot: CPUlessPlatformRuntime.call(1010:61F3) -- NO CPU, "
          f"NO interpreter (guard armed)")
    try:
        out, _ = rt.call(func_1010_61f3, **entry_kw)
        print(f"[cpuless] program terminated after {state['frames']} frame(s) "
              f"-- no CPU, no interpreter")
        return 0
    except _Done:
        print(f"[cpuless] rendered {state['frames']} frames (VGA nonzero "
              f"px={_vga_nonzero(mem)}) CPU-free -- no CPU, no interpreter")
        return 0
    except (UnsupportedPlatformEffect, *([UnknownDispatchTarget]
                                         if UnknownDispatchTarget else [])) as e:
        print(f"\n[cpuless] HARD-WALL FRONTIER (fail-loud, by design):\n  {e}")
        print("[cpuless] the cold boot reached code beyond the current "
              "--observed coverage; close it with a fuller startup capture.")
        return 3
    except RuntimeError as e:
        if "CPUless" in str(e):
            print(f"\n[cpuless] HARD-WALL FRONTIER (fail-loud, by design):\n  {e}")
            print("[cpuless] a runtime-dead path (per the current --observed "
                  "trace) was reached; close it with a fuller startup capture.")
            return 3
        raise


class _Done(Exception):
    pass


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--headless", action="store_true",
                    help="no window (the only mode until the frame driver lands)")
    ap.add_argument("--frames", type=int, default=0,
                    help="stop after N frame boundaries (0 = run to end/frontier)")
    ap.add_argument("--rebuild", action="store_true",
                    help="regenerate the standalone corpus first")
    args = ap.parse_args(argv)
    return run(args.frames, args.rebuild)


if __name__ == "__main__":
    raise SystemExit(main())
