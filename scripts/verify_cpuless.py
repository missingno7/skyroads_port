"""verify_cpuless.py -- frame-exact proof that the STANDALONE no-CPU corpus
reproduces the game, diffed against the pure interpreted ASM oracle.

This is the CPUless analogue of ``verify_vmless_demo.py``: same demo, same
oracle, same frame cut -- but the candidate is the committed
``skyroads/recovered/`` corpus driven by ``CPUlessPlatformRuntime`` with NO CPU,
NO interpreter, NO lifted graph.  The candidate's per-frame loop lives inside
its ``boundary_cb`` (a synchronous plat.boundary call), so both machines are run
independently and their per-frame VGA plane + DAC palette are compared.

Frame model (shared with the VMless differential):
  * a frame delivers ``--irqs`` timer IRQs, then runs until a boundary head is
    reached the 2nd time (park-on-re-arrival: pass 1 runs the tick-wait body to
    steady state, pass 2 proves the wait unsatisfied with nothing left to do);
  * input is applied ONCE per frame (at the frame boundary), via the game's own
    keyboard path -- the CPUless candidate runs the recovered INT 09h ISR
    (func_1010_3bcc); the oracle runs it through the interpreter.

The candidate side imports no CPU; this harness (the oracle half) does, because
proving equivalence needs the reference.  It is a dev/verification tool, never
the shipped runner (that is play_cpuless.py, which the purity lint covers).

Usage:
    python scripts/verify_cpuless.py artifacts/demos/<cold-demo>
    python scripts/verify_cpuless.py <demo> --frames 120
"""
from __future__ import annotations

import argparse
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "dos_re"))
sys.path.insert(0, str(ROOT / "scripts"))

# --- oracle machinery (interpreter side) -- reused from the VMless differential
from verify_vmless_demo import (   # noqa: E402
    build_oracle_cold, build_oracle, _is_predecompression, run_stub,
    read_heads, VGA, VGA_LEN, CANONICAL_ENTRY)
from dos_re.input_demo import InputDemoPlayback   # noqa: E402
from dos_re.interrupts import deliver_interrupt   # noqa: E402
from dos_re.dos import ConsoleInputWouldBlock, DOSMachine   # noqa: E402
from dos_re.x86 import HaltExecution   # noqa: E402
# --- candidate machinery (NO CPU) --------------------------------------------
from dos_re.memory import Memory   # noqa: E402
from dos_re.lift.platform import CPUlessPlatformRuntime   # noqa: E402
from dos_re.snapshot_headless import _restore_dos_state   # noqa: E402
from dos_re.runtime import BIOS_INT9_ENTRY   # noqa: E402
import json   # noqa: E402

BOOT_DIR = ROOT / "artifacts" / "boot_image"
#: the recovered INT 08h timer ISR (flags_livein) + INT 09h keyboard ISR (not).
_TIMER_IN = ("ax", "bp", "bx", "cx", "di", "ds", "dx", "es", "si", "sp", "ss")
_KEY_IN = ("ax", "bp", "bx", "cx", "di", "ds", "dx", "si", "sp", "ss")


def _capture_oracle(demo, pb, heads, irqs, step_budget, end):
    """Run the interpreter oracle; return [(vga_bytes, palette_tuple)] per frame."""
    if getattr(pb, "is_cold_start", False):
        f, a, rt = build_oracle_cold(demo, pb)
    else:
        f, a, rt = build_oracle(demo, pb)
    run_stub(rt.cpu, *CANONICAL_ENTRY)

    def run_to_cut(cpu, budget):
        hit = {}
        for _ in range(budget):
            key = (cpu.s.cs, cpu.s.ip)
            if key in heads:
                hit[key] = hit.get(key, 0) + 1
                if hit[key] >= 2:
                    cpu.step()
                    return
            cpu.step()

    frames = []
    for frame in range(end):
        if pb.finished(frame):
            break
        pb.apply_to_runtime(frame, rt, deliver=lambda r, sc: f.deliver_input(r, sc))
        for _ in range(irqs):
            deliver_interrupt(rt, 0x08)
        try:
            run_to_cut(rt.cpu, step_budget)
        except (ConsoleInputWouldBlock, HaltExecution):
            pass
        frames.append((bytes(rt.cpu.mem.data[VGA:VGA + VGA_LEN]),
                       tuple(map(tuple, rt.dos.vga_palette))))
    return frames


def _capture_cpuless(pb, heads, irqs, end):
    """Run the NO-CPU candidate; return [(vga_bytes, palette_tuple)] per frame.

    The boundary_cb is the per-frame loop.  Park-on-re-arrival: the 1st pass at
    a head this frame lets the tick-wait body run (return, deliver nothing); the
    2nd pass IS the frame -- apply input once, deliver this frame's timer IRQs
    through the recovered INT 08h ISR (advancing ds:[1600]), capture, advance.
    """
    from skyroads.recovered.func_1010_61f3 import func_1010_61f3
    from skyroads.recovered.func_1010_3b17 import func_1010_3b17
    from skyroads.recovered.func_1010_3bcc import func_1010_3bcc

    mem = Memory()
    img = (BOOT_DIR / "memory_1mb.bin").read_bytes()
    mem.data[:len(img)] = img
    st = json.loads((BOOT_DIR / "state.json").read_text(encoding="utf-8"))
    dos = DOSMachine(ROOT)
    _restore_dos_state(types.SimpleNamespace(
        dos=dos, program=types.SimpleNamespace(memory=mem)), st["dos"])
    dos.mouse_present = pb.mouse_present_hint
    # A console read (INT 21h AH=07 "press any key") must BLOCK on an empty
    # buffer -- as the oracle does -- not synthesise the phantom Esc that the
    # DOSMachine default (0x011B) leaks into the boot snapshot.  The harness's
    # blocking_read_cb below advances frames until the awaited key arrives.
    dos.console_input_fallback = None
    rt = CPUlessPlatformRuntime(mem, game_root=ROOT, dos=dos)

    def deliver_key(_r, sc, regs):
        dos.current_scancode = sc & 0xFF
        dos.kbd_output_buffer_full = True
        voff, vseg = mem.rw(0, 0x24), mem.rw(0, 0x26)
        installed = (vseg, voff) != BIOS_INT9_ENTRY   # game's own INT 09h ISR?
        dos.note_bios_keystroke(sc & 0xFF)            # fill the BIOS type-ahead buffer
        if installed:
            kw = {k: regs[k] for k in _KEY_IN if k in regs}
            func_1010_3bcc(mem, rt, **kw)             # run the game's recovered ISR

    frames = []
    state = {"frame": 0, "seen": set()}
    done = _Stop

    def _advance(regs):
        """One frame boundary: capture the frame that just finished, then
        prepare the NEXT frame -- apply ITS input before it renders (so input N
        affects frame N, matching the oracle's apply-before-run) and deliver its
        timer IRQs through the recovered INT 08h ISR.  Shared by the tick-wait
        boundary (2nd pass) AND the blocking-read wait, so both frame drivers
        keep one counter and one capture rule."""
        f = state["frame"]
        frames.append((bytes(mem.data[VGA:VGA + VGA_LEN]),
                       tuple(map(tuple, dos.vga_palette))))
        state["frame"] = f + 1
        state["seen"].clear()           # each frame starts a fresh pass count
        if state["frame"] >= end or pb.finished(f):
            raise done()
        pb.apply_to_runtime(state["frame"], rt,
                            deliver=lambda r, sc: deliver_key(r, sc, regs))
        for _ in range(irqs):
            kw = {k: regs[k] for k in _TIMER_IN if k in regs}
            kw["_flags_in"] = regs.get("_flags_in", 2)
            func_1010_3b17(mem, rt, **kw)

    def boundary_cb(hcs, hip, rip, regs, cost):
        key = (hcs, hip)
        if key not in state["seen"]:
            state["seen"].add(key)          # 1st pass: let the body run
            return regs, regs.get("_flags_in", 2), 0
        _advance(regs)                      # 2nd pass -> a frame just rendered
        return regs, regs.get("_flags_in", 2), 0
    rt.boundary_cb = boundary_cb
    #: a blocking console read (press-any-key) advances a frame in place so the
    #: awaited demo key can arrive; the frozen screen + IRQ-driven palette fade
    #: are captured per frame exactly as the oracle captures its blocked frames.
    rt.blocking_read_cb = _advance

    regs0 = st["cpu"]
    import inspect
    kw = {k: v for k, v in regs0.items()
          if k in inspect.signature(func_1010_61f3).parameters}
    kw["_flags_in"] = regs0.get("flags", 2)
    try:
        rt.call(func_1010_61f3, **kw)
    except done:
        pass
    except HaltExecution:
        print(f"[verify-cpuless] candidate program terminated (int 21/4C) "
              f"after {state['frame']} frames")
    return frames


class _Stop(Exception):
    pass


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("demo")
    ap.add_argument("--frames", type=int, default=0)
    ap.add_argument("--irqs", type=int, default=6)
    ap.add_argument("--step-budget", type=int, default=4_000_000)
    ap.add_argument("--heads", default=str(ROOT / "artifacts" / "codemap"
                                           / "boundary_heads.txt"))
    args = ap.parse_args(argv)

    demo = Path(args.demo)
    pb_o = InputDemoPlayback.load(str(demo))
    pb_c = InputDemoPlayback.load(str(demo))
    if not (getattr(pb_o, "is_cold_start", False) or _is_predecompression(pb_o)):
        print("[verify-cpuless] this tool is for COLD demos (no snapshot).")
        return 2
    end = pb_o.end_boundary or 100000
    if args.frames:
        end = min(end, args.frames)
    heads = read_heads(Path(args.heads))
    print(f"[verify-cpuless] demo={demo.name} frames={end} "
          f"mouse_present={pb_o.mouse_present_hint}; cut = 2nd pass at "
          f"{len(heads)} boundary heads")

    print("[verify-cpuless] running the interpreted ASM oracle ...")
    oracle = _capture_oracle(demo, pb_o, heads, args.irqs, args.step_budget, end)
    print(f"[verify-cpuless] oracle captured {len(oracle)} frames")
    print("[verify-cpuless] running the NO-CPU recovered corpus ...")
    cand = _capture_cpuless(pb_c, heads, args.irqs, end)
    print(f"[verify-cpuless] candidate captured {len(cand)} frames")

    n = min(len(oracle), len(cand))
    for i in range(n):
        vo, po = oracle[i]
        vc, pc = cand[i]
        if vo != vc:
            diff = sum(1 for a, b in zip(vo, vc) if a != b)
            first = next(j for j, (a, b) in enumerate(zip(vo, vc)) if a != b)
            print(f"\n[verify-cpuless] VGA DIVERGED at frame {i}: {diff} px differ; "
                  f"first at row {first // 320} col {first % 320} "
                  f"(oracle={vo[first]:02X} corpus={vc[first]:02X})")
            return 1
        if po != pc:
            bad = [j for j, (a, b) in enumerate(zip(po, pc)) if a != b]
            print(f"\n[verify-cpuless] PALETTE DIVERGED at frame {i}: "
                  f"{len(bad)} DAC entries differ.")
            return 1
        if i % 50 == 0:
            print(f"  frame {i:4d}: VGA + palette identical")
    if len(oracle) != len(cand):
        print(f"\n[verify-cpuless] FRAME COUNT MISMATCH: oracle {len(oracle)}, "
              f"candidate {len(cand)} (diverged after frame {n})")
        return 1
    print(f"\n[verify-cpuless] PASS -- {n} frames: VGA plane AND DAC palette "
          f"identical to the ASM oracle, NO CPU, over {demo.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
