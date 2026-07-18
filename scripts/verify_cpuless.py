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
from dos_re.hooks import assert_pure_oracle   # noqa: E402
from dos_re.input_demo import InputDemoPlayback   # noqa: E402
from dos_re.interrupts import deliver_interrupt   # noqa: E402
from dos_re.dos import ConsoleInputWouldBlock, DOSMachine   # noqa: E402
from dos_re.x86 import HaltExecution   # noqa: E402
# --- candidate machinery (NO CPU) --------------------------------------------
from dos_re.memory import Memory   # noqa: E402
from dos_re.lift.platform import CPUlessPlatformRuntime   # noqa: E402
from dos_re.snapshot_headless import _restore_dos_state   # noqa: E402
from dos_re.keyboard import BIOS_INT9_ENTRY   # noqa: E402  (CPU-free leaf)
from skyroads.cpuless_driver import CPUlessFrameDriver   # noqa: E402
import json   # noqa: E402

BOOT_DIR = ROOT / "artifacts" / "boot_image"
#: the recovered INT 09h keyboard ISR is NOT flags-live (the timer ISR is; the
#: shared driver owns that bundle -- see skyroads.cpuless_driver.TIMER_INPUTS).
_KEY_IN = ("ax", "bp", "bx", "cx", "di", "ds", "dx", "si", "sp", "ss")


def _capture_oracle(demo, pb, heads, irqs, step_budget, end):
    """Run the interpreter oracle; return [(vga_bytes, palette_tuple)] per frame."""
    if getattr(pb, "is_cold_start", False):
        f, a, rt = build_oracle_cold(demo, pb)
    else:
        f, a, rt = build_oracle(demo, pb)
    # PROVE the reference side is the original program, do not assume it.
    # allow= is empty on purpose: this oracle needs no environment stand-ins.
    # The synthetic hardware it does need (BIOS INT 09h ISR, dummy IRET stub)
    # is installed OUTSIDE the registry by install_bios_environment_hooks, so
    # it is not in scope here and survives the strip either way.
    # Historically this ran with 31 replacements live -- create_game_runtime
    # guarded the hooks IMPORT, which gates nothing (skyroads.hooks is already
    # in sys.modules via this script's own imports) -- and the deliberately
    # behaviour-changing fade_loop_tick_gate optimisation made the ORACLE skip
    # a per-frame call, which the differential then blamed on the candidate.
    assert_pure_oracle(rt.cpu, allow=frozenset())
    run_stub(rt.cpu, *CANONICAL_ENTRY)

    peak = [0]

    def run_to_cut(cpu, budget, frame):
        """Run to this frame's cut, or FAIL LOUD.

        Exhausting the budget without reaching the cut used to return quietly,
        leaving the oracle parked mid-frame -- a TRUNCATED reference that still
        looks authoritative, so the differential reports the candidate as
        diverged when the oracle simply did not finish.  That is exactly the
        failure this harness exists to detect, so it must never be silent.

        The budget matters much more now that the oracle is genuinely pure: the
        stripped replacements include accelerators (lzs_decode_loop,
        intro_anim_unpack, the blitters), so the interpreter now runs the real
        instruction sequence and needs roughly an order of magnitude more steps
        per frame than the hooked oracle did.
        """
        hit = {}
        for used in range(budget):
            key = (cpu.s.cs, cpu.s.ip)
            if key in heads:
                hit[key] = hit.get(key, 0) + 1
                if hit[key] >= 2:
                    cpu.step()
                    peak[0] = max(peak[0], used)
                    return
            cpu.step()
        raise RuntimeError(
            f"oracle step budget exhausted at frame {frame}: {budget} steps "
            f"without reaching a boundary head twice (cs:ip="
            f"{cpu.s.cs:04X}:{cpu.s.ip:04X}).  The oracle is parked mid-frame; "
            f"any comparison from here on blames the candidate for the "
            f"oracle's truncation.  Raise --step-budget.")

    frames = []
    for frame in range(end):
        if pb.finished(frame):
            break
        pb.apply_to_runtime(frame, rt, deliver=lambda r, sc: f.deliver_input(r, sc))
        for _ in range(irqs):
            deliver_interrupt(rt, 0x08)
        try:
            run_to_cut(rt.cpu, step_budget, frame)
        except (ConsoleInputWouldBlock, HaltExecution):
            pass
        frames.append((bytes(rt.cpu.mem.data[VGA:VGA + VGA_LEN]),
                       tuple(map(tuple, rt.dos.vga_palette))))
    print(f"[verify-cpuless] oracle peak {peak[0]} steps/frame "
          f"(budget {step_budget})")
    return frames


def _capture_cpuless(pb, heads, irqs, end):
    """Run the NO-CPU candidate; return [(vga_bytes, palette_tuple)] per frame.

    Frames are driven by :class:`skyroads.cpuless_driver.CPUlessFrameDriver` --
    the SAME model the shipped runner uses -- so this differential proves the
    runner's behaviour, not a verification-only lookalike.
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
    done = _Stop

    def present(frame):
        """Capture the frame that just finished (the candidate's 'display')."""
        frames.append((bytes(mem.data[VGA:VGA + VGA_LEN]),
                       tuple(map(tuple, dos.vga_palette))))
        if frame + 1 >= end or pb.finished(frame):
            raise done()

    def supply_input(frame, regs):
        pb.apply_to_runtime(frame, rt,
                            deliver=lambda r, sc: deliver_key(r, sc, regs))

    # The SAME frame model the shipped runner uses (skyroads.cpuless_driver):
    # park-on-2nd-pass at a boundary head, input applied before the frame it
    # affects, timer IRQs through the recovered INT 08h ISR, and blocking console
    # reads serviced by advancing a frame in place.  Sharing one implementation
    # is the point -- a differential that proves a DIFFERENT model than the one
    # that ships proves nothing about the shipped runner.
    driver = CPUlessFrameDriver(mem, rt, func_1010_3b17, present=present,
                                supply_input=supply_input,
                                irqs=irqs).install(rt)

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
              f"after {driver.frame} frames")
    return frames


class _Stop(Exception):
    pass


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("demo")
    ap.add_argument("--frames", type=int, default=0)
    ap.add_argument("--shadow-islands", action="store_true",
                    help="check hand-recovered islands against the generated "
                         "bodies on every call (skyroads/island_shadows.py). The "
                         "generated code still drives; a disagreement raises.")
    ap.add_argument("--shadow-only", action="store_true",
                    help="run ONLY the shadow check: the CPUless corpus alone, "
                         "no oracle, no frame comparison. Implies "
                         "--shadow-islands. A shadow compares the candidate "
                         "against the generated body IN PROCESS, so the oracle "
                         "proves nothing extra about it and costs most of the "
                         "runtime -- this is the same evidence for a quarter of "
                         "the wall clock, which is what makes it gateable.")
    ap.add_argument("--irqs", type=int, default=6)
    # 4_000_000 was sized for the (contaminated) hooked oracle.  A genuinely
    # pure oracle interprets the real blitters/decompressors instead of calling
    # their Python replacements: measured peak is 17.1M steps/frame over the
    # spine demo, so this leaves ~3.7x headroom and run_to_cut fails loud rather
    # than truncating if a frame ever exceeds it.
    ap.add_argument("--step-budget", type=int, default=64_000_000)
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
    args.shadow_islands = args.shadow_islands or args.shadow_only
    if args.shadow_islands:
        # BEFORE the corpus is imported: generated modules bind callees at import
        # time, so the module object is the only seam.
        from skyroads.island_shadows import install_all
        print(f"[verify-cpuless] shadowing islands: {', '.join(install_all())}")
    heads = read_heads(Path(args.heads))
    print(f"[verify-cpuless] demo={demo.name} frames={end} "
          f"mouse_present={pb_o.mouse_present_hint}; cut = 2nd pass at "
          f"{len(heads)} boundary heads")

    if args.shadow_only:
        oracle = []
    else:
        print("[verify-cpuless] running the interpreted ASM oracle ...")
        oracle = _capture_oracle(demo, pb_o, heads, args.irqs, args.step_budget, end)
        print(f"[verify-cpuless] oracle captured {len(oracle)} frames")
    print("[verify-cpuless] running the NO-CPU recovered corpus ...")
    cand = _capture_cpuless(pb_c, heads, args.irqs, end)
    print(f"[verify-cpuless] candidate captured {len(cand)} frames")

    if args.shadow_islands:
        from skyroads.island_shadows import Verdict, report, verdict
        print(f"[verify-cpuless] island shadows -- {report()}")
        # GATED, not merely printed. Anything short of VERIFIED is a failure,
        # INCONCLUSIVE included: a shadow that was never called established
        # nothing, and a zero that reads like a pass is the false green this
        # project has produced twice.
        if verdict() is not Verdict.VERIFIED:
            print("[verify-cpuless] FAIL -- island shadows did not verify")
            return 1
        if args.shadow_only:
            print(f"[verify-cpuless] PASS -- island shadows VERIFIED over "
                  f"{demo.name} ({len(cand)} frames, NO oracle: this gate proves "
                  f"the candidates, not the corpus)")
            return 0
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
            # WHICH entries, and by how much. A contiguous run points at one
            # fade/load writing a block; scattered singles point at timing.
            for j in bad[:12]:
                o, c = po[j], pc[j]
                print(f"    DAC[{j:3d}] oracle={tuple(o)} corpus={tuple(c)} "
                      f"delta={tuple(int(a) - int(b) for a, b in zip(o, c))}")
            if len(bad) > 12:
                print(f"    ... and {len(bad) - 12} more")
            print(f"    indices: {bad}")
            # Was the previous frame clean?  If so this is the FIRST bad frame
            # and the write that caused it happened during it.
            if i and oracle[i - 1][1] == cand[i - 1][1]:
                print(f"    (frame {i - 1} palette was identical -- the "
                      f"divergent write happened during frame {i})")
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
