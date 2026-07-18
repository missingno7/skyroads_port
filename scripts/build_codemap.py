"""Whole-program execution observation — the discovery step of the DOS_RE 2.0
recovery pipeline (``dos_re/docs/dos_re_2.0.md``, ``tools/codemap.py``).

Drives the recorded demos on the pure interpreted oracle under a step wrapper
and dumps ``artifacts/codemap/observed.json``:

    executed          every stepped instruction start (CS:IP)
    call_targets      {CS:IP: count} -- dynamically observed CALL targets
    int_entries       ISR entries reached by INT dispatch
    ivt_game_vectors  {vector: CS:IP} -- final IVT vectors pointing into game code

``tools/codemap.py`` turns that into the function-entry census that feeds
``irgen`` (the recovery IR) and ``liftemit`` (the generated corpus). Dynamic
evidence beats static analysis here: an indirect call's targets are
unresolvable statically but show up in ``call_targets`` for free, and every
kept entry provably EXECUTED -- so the census decodes real code, never data.

Coverage is the whole point: pass every demo that exercises a distinct part of
the game (the cold e2e demo alone walks intro -> main menu -> controls -> help
-> level select -> gameplay -> level select). Anything never executed is not in
the census, so it is not lifted -- which is why the run list here matters more
than any single demo's length.

Usage:
    python scripts/build_codemap.py                     # the default demo set
    python scripts/build_codemap.py --demo DIR [--demo DIR ...]
    python scripts/build_codemap.py --max-frames 200    # quick smoke run
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dos_re"))
sys.path.insert(0, str(ROOT))

import scripts.play as sp  # noqa: E402
from dos_re import player  # noqa: E402
from dos_re.cpu import CPU8086, HaltExecution  # noqa: E402
from dos_re.dos import ConsoleInputWouldBlock  # noqa: E402
from dos_re.input_demo import InputDemoPlayback  # noqa: E402
from dos_re.player import _use_real_console_input  # noqa: E402

#: Demos whose union covers the program. The cold e2e demo is the spine (every
#: front-end screen plus a level); the others add paths it does not take.
DEFAULT_DEMOS = (
    "demo_cold_20260713_213510",        # intro -> menu -> controls -> help -> select -> play -> select
    "demo_skyroads_L1FULL_20260713_212417",   # a level start-to-finish
    "demo_death_redtile_20260713_154259",     # red-block death
    "demo_skyroads_20260713_234905",    # level end / finish
    "demo_skyroads_20260713_160506",    # mouse-enabled menu navigation
    "demo_menu_3levels_20260713_144256",      # level-select -> 3 different levels
    "demo_skyroads_20260717_122736",          # menu navigation (Down/Enter/Right...)
    "demo_intro_20260717_125403",       # cold start: full intro -> attract demo -> interrupt -> menu
    "demo_cold_20260718_003412",        # full cold playthrough: intro -> menu -> select -> play -> die -> leave -> intro
)

#: The game's code segment. Entries outside it are DOS/BIOS/framework, not game
#: code to recover.
GAME_SEG = 0x1010


def observe_demo(demo_dir: Path, *, max_frames: int = 0,
                 executed: set, call_targets: Counter, int_entries: set) -> dict:
    """Replay one demo on the pure ASM oracle, accumulating the observation."""
    frontend = sp.SkyroadsFrontend(ROOT)
    # --no-replacements is what actually uninstalls the recovered hooks
    # (apply_hook_mode reads args.no_replacements; `install_replacements` is a
    # create_game_runtime() PARAMETER, not an args field -- setting it on args
    # silently does nothing and leaves every hook live).  It matters here more
    # than anywhere: a hooked routine never interprets, so it is never OBSERVED,
    # never censused, and never lifted -- and the strict wall then fires on it at
    # boot.  That is exactly how 1010:64AB (inside the hooked LZS decoder
    # 1010:6712) went missing from the first census.
    args = player.build_arg_parser(frontend).parse_args(
        ["--play-demo", str(demo_dir), "--headless", "--no-replacements"])
    pb = InputDemoPlayback.load(str(demo_dir))
    frontend.apply_demo_metadata(args, pb.manifest.get("metadata", {}))
    rt = (frontend.create_runtime(args) if pb.is_cold_start
          else frontend.load_snapshot_runtime(args, pb.snapshot_path()))
    frontend.apply_hook_mode(rt, args)   # honours --no-replacements: pure ASM oracle
    _use_real_console_input(rt)

    orig_step = CPU8086.step
    ex_add, ct = executed.add, call_targets

    def step(self):
        s = self.s
        ex_add((s.cs, s.ip))
        depth = self.call_depth
        r = orig_step(self)
        # call_depth rises on CALL and falls on RET (cpu.py), so a rise means the
        # instruction just executed was a call and CS:IP is now the callee entry
        # -- catching INDIRECT targets for free, which is the whole point.
        if self.call_depth > depth:
            s2 = self.s
            ct[(s2.cs, s2.ip)] += 1
        return r

    CPU8086.step = step
    frames = 0
    try:
        end = pb.end_boundary or 100000
        if max_frames:
            end = min(end, max_frames)
        while frames < end and not pb.finished(frames):
            pb.apply_to_runtime(frames, rt, deliver=lambda r, sc: frontend.deliver_input(r, sc))
            try:
                frontend.advance_frame(rt, args, frames)
            except ConsoleInputWouldBlock:
                pass
            except HaltExecution:
                break
            frames += 1
    finally:
        CPU8086.step = orig_step

    # Final IVT vectors that point into game code: the ISRs the game installed
    # (timer/keyboard). They are entered by hardware dispatch, never CALLed, so
    # they would otherwise be missing from the census entirely.
    ivt = {}
    for vec in range(256):
        off = rt.cpu.mem.rw(0, (vec * 4) & 0xFFFFF)
        seg = rt.cpu.mem.rw(0, (vec * 4 + 2) & 0xFFFFF)
        if seg == GAME_SEG and (seg, off) in executed:
            ivt[f"{vec:02X}"] = f"{seg:04X}:{off:04X}"
    return {"frames": frames, "ivt": ivt}


#: Demos timed to the 2nd-pass BOUNDARY cut rather than steps-per-frame.  The
#: verification differentials (verify_vmless / verify_cpuless) drive these with
#: run_to_cut, and the recovered runtime reproduces exactly that execution;
#: replaying them through the steps-per-frame front-end diverges (it quits early
#: at a press-any-key screen), so the real end-game code -- the exact addresses
#: the no-CPU runtime executes -- never gets censused, and the recovered build
#: then fail-loud-stubs those reachable exits as runtime-dead (e.g. 1010:2EFC,
#: the plain `ret` tail of 1010:2EBB).  Observing them through the boundary
#: model closes that false-dead gap.
BOUNDARY_DEMOS = frozenset({"demo_cold_20260718_003412"})


def observe_demo_boundary(demo_dir: Path, *, executed: set, call_targets: Counter,
                          heads: set, step_budget: int = 4_000_000) -> dict:
    """Replay one demo through the boundary-head PARK model -- the faithful
    reproduction the recovered runtime targets (see :data:`BOUNDARY_DEMOS`).

    A frame delivers its input + 6 timer IRQs then runs until a boundary head is
    reached the 2nd time; this matches verify_vmless_demo's oracle exactly, so
    the census covers precisely the code the CPU-free candidate executes."""
    from verify_vmless_demo import (build_oracle_cold, build_oracle, run_stub,
                                    CANONICAL_ENTRY, _is_predecompression)
    from dos_re.interrupts import deliver_interrupt

    pb = InputDemoPlayback.load(str(demo_dir))
    if pb.is_cold_start or _is_predecompression(pb):
        frontend, _a, rt = build_oracle_cold(demo_dir, pb)
    else:
        frontend, _a, rt = build_oracle(demo_dir, pb)
    run_stub(rt.cpu, *CANONICAL_ENTRY)

    orig_step = CPU8086.step
    ex_add, ct = executed.add, call_targets

    def step(self):
        s = self.s
        ex_add((s.cs, s.ip))
        depth = self.call_depth
        r = orig_step(self)
        if self.call_depth > depth:
            s2 = self.s
            ct[(s2.cs, s2.ip)] += 1
        return r

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

    CPU8086.step = step
    frames = 0
    try:
        end = pb.end_boundary or 100000
        while frames < end and not pb.finished(frames):
            pb.apply_to_runtime(frames, rt,
                                deliver=lambda r, sc: frontend.deliver_input(r, sc))
            for _ in range(6):
                deliver_interrupt(rt, 0x08)
            try:
                run_to_cut(rt.cpu, step_budget)
            except (ConsoleInputWouldBlock, HaltExecution):
                pass
            frames += 1
    finally:
        CPU8086.step = orig_step
    return {"frames": frames}


def observe_cold_boot(*, frames: int, executed: set, call_targets: Counter) -> dict:
    """Observe a genuine from-EXE COLD BOOT -- the game's own startup path.

    Every recorded demo resumes from a snapshot taken mid-game, so none of them
    ever executes the startup sequence that runs between the packer stub's
    hand-off (``1010:61F3``) and the first interactive frame. The boot image
    starts at exactly that hand-off, so without this pass the census misses the
    startup code and the strict-VMless wall fires on the first step (it did:
    `1010:64AB`, "no lifted hook covers this address"). Booting from the EXE
    here is a RECOVERY-TIME input; the shipped runtime never does it.
    """
    frontend = sp.SkyroadsFrontend(ROOT)
    args = player.build_arg_parser(frontend).parse_args(["--headless", "--no-replacements"])
    rt = frontend.create_runtime(args)
    frontend.apply_hook_mode(rt, args)   # pure ASM oracle -- see observe_demo
    _use_real_console_input(rt)

    orig_step = CPU8086.step
    ex_add, ct = executed.add, call_targets

    def step(self):
        s = self.s
        ex_add((s.cs, s.ip))
        depth = self.call_depth
        r = orig_step(self)
        if self.call_depth > depth:
            s2 = self.s
            ct[(s2.cs, s2.ip)] += 1
        return r

    CPU8086.step = step
    done = 0
    try:
        for done in range(frames):
            try:
                frontend.advance_frame(rt, args, done)
            except ConsoleInputWouldBlock:
                pass
            except HaltExecution:
                break
    finally:
        CPU8086.step = orig_step
    return {"frames": done + 1}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--demo", action="append", default=[],
                    help="demo dir (repeatable); default: the standard coverage set")
    ap.add_argument("--cold-boot-frames", type=int, default=400,
                    help="frames of a genuine from-EXE cold boot to observe "
                         "(the startup path no snapshot-based demo covers); 0 to skip")
    ap.add_argument("--max-frames", type=int, default=0,
                    help="stop each demo early (smoke runs)")
    ap.add_argument("--out", default=str(ROOT / "artifacts" / "codemap" / "observed.json"))
    args = ap.parse_args(argv)

    demos = [Path(d) for d in args.demo] or [
        ROOT / "artifacts" / "demos" / d for d in DEFAULT_DEMOS]
    demos = [d if d.is_absolute() else ROOT / d for d in demos]

    executed: set = set()
    call_targets: Counter = Counter()
    int_entries: set = set()
    ivt_all: dict = {}

    if args.cold_boot_frames:
        info = observe_cold_boot(frames=args.cold_boot_frames, executed=executed,
                                 call_targets=call_targets)
        print(f"[codemap] cold boot (from EXE): {info['frames']} frames, "
              f"{len(executed)} addrs -- the startup path no demo covers")

    heads = None
    for demo in demos:
        if not demo.exists():
            print(f"[codemap] SKIP (missing): {demo.name}")
            continue
        before = len(executed)
        if demo.name in BOUNDARY_DEMOS:
            if heads is None:
                from verify_vmless_demo import read_heads
                heads = read_heads(ROOT / "artifacts" / "codemap"
                                   / "boundary_heads.txt")
            info = observe_demo_boundary(demo, executed=executed,
                                         call_targets=call_targets, heads=heads)
            tag = " [boundary model]"
        else:
            info = observe_demo(demo, max_frames=args.max_frames, executed=executed,
                                call_targets=call_targets, int_entries=int_entries)
            ivt_all.update(info["ivt"])
            tag = ""
        print(f"[codemap] {demo.name}: {info['frames']} frames, "
              f"+{len(executed) - before} new addrs (total {len(executed)}){tag}")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "executed": sorted(f"{cs:04X}:{ip:04X}" for cs, ip in executed),
        "call_targets": {f"{cs:04X}:{ip:04X}": n for (cs, ip), n in sorted(call_targets.items())},
        "int_entries": sorted(f"{cs:04X}:{ip:04X}" for cs, ip in int_entries),
        "ivt_game_vectors": ivt_all,
        "demos": [d.name for d in demos if d.exists()],
    }
    out.write_text(json.dumps(doc, indent=1))
    game_ex = sum(1 for cs, _ in executed if cs == GAME_SEG)
    print(f"[codemap] wrote {out}: {len(executed)} executed "
          f"({game_ex} in game seg {GAME_SEG:04X}), "
          f"{len(call_targets)} call targets, {len(ivt_all)} IVT game vectors")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
