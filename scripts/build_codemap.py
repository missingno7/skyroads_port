"""Collect oracle execution observations for SkyRoads corpus generation.

Drives the recorded replays on the pure interpreted oracle under a step wrapper
and writes the local generation input ``artifacts/codemap/observed.json``:

    executed          every stepped instruction start (CS:IP)
    call_targets      {CS:IP: count} -- dynamically observed CALL targets
    int_entries       ISR entries reached by INT dispatch
    ivt_game_vectors  {vector: CS:IP} -- final IVT vectors pointing into game code

This is one optional dynamic-evidence producer for the generated VMless and
ABI-recovered corpora. It neither outranks static/manual evidence nor proves
closed-world coverage. ReplayArtifact owns retained replay execution evidence,
and the Execution Atlas combines retained sources for navigation and planning.
An address absent here means only “not observed in this replay set.”

Usage:
    python scripts/build_codemap.py --replay DIR [--replay DIR ...]
    python scripts/build_codemap.py --replay DIR --max-frames 200
    python scripts/build_codemap.py --replay DIR --cold-boot-frames 400
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
from dos_re.replay_input import RealModeInputAdapter  # noqa: E402
from dos_re.replay import ReplayArtifact  # noqa: E402
from dos_re.snapshot import apply_runtime_continuation  # noqa: E402
from skyroads.replay import capture_base  # noqa: E402

#: The game's code segment. Entries outside it are DOS/BIOS/framework, not game
#: code to recover.
GAME_SEG = 0x1010


def observe_replay(replay_dir: Path, *, max_frames: int = 0,
                 executed: set, call_targets: Counter, int_entries: set) -> dict:
    """Replay one replay on the pure ASM oracle, accumulating the observation."""
    frontend = sp.SkyroadsFrontend(ROOT)
    args = player.build_arg_parser(frontend).parse_args(
        ["--play-replay", str(replay_dir), "--headless", "--composition", "oracle"])
    artifact = ReplayArtifact.open(replay_dir)
    frontend.apply_replay_metadata(args, artifact.metadata)
    args.execution_plan = frontend.resolve_execution_plan(args)
    rt = frontend.create_runtime(args)
    apply_runtime_continuation(rt, capture_base(artifact))
    inputs = RealModeInputAdapter(artifact.events)
    rt.dos.console_input_fallback = None

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
        end = artifact.end_point.ordinal
        if max_frames:
            end = min(end, max_frames)
        while frames < end:
            # apply_to_runtime STEPS THE CPU (it runs the INT 09h delivery), so a
            # blocking console read can surface from it, not only from the frame
            # advance -- an escape here killed the whole census mid-replay.
            try:
                inputs.apply_to_runtime(
                    frames, rt,
                    deliver=lambda r, sc: frontend.deliver_input(r, sc))
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


def observe_cold_boot(*, frames: int, executed: set, call_targets: Counter) -> dict:
    """Observe a genuine from-EXE COLD BOOT -- the game's own startup path.

    Every recorded replay resumes from a snapshot taken mid-game, so none of them
    ever executes the startup sequence that runs between the packer stub's
    hand-off (``1010:61F3``) and the first interactive frame. The boot image
    starts at exactly that hand-off, so without this pass the census misses the
    startup code and an interpreter-free provider reports it on the first step:
    `1010:64AB`, "no lifted hook covers this address"). Booting from the EXE
    here is a RECOVERY-TIME input; the shipped runtime never does it.
    """
    frontend = sp.SkyroadsFrontend(ROOT)
    args = player.build_arg_parser(frontend).parse_args(
        ["--headless", "--composition", "oracle"])
    args.execution_plan = frontend.resolve_execution_plan(args)
    rt = frontend.create_runtime(args)
    rt.dos.console_input_fallback = None

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
    ap.add_argument(
        "--replay", action="append", default=[], metavar="DIR",
        help="ReplayArtifact evidence input (repeatable)",
    )
    ap.add_argument("--cold-boot-frames", type=int, default=0,
                    help="frames of a genuine from-EXE cold boot to observe "
                         "(explicit optional evidence input; default: 0)")
    ap.add_argument("--max-frames", type=int, default=0,
                    help="stop each replay early (smoke runs)")
    ap.add_argument("--out", default=str(ROOT / "artifacts" / "codemap" / "observed.json"))
    args = ap.parse_args(argv)

    if not args.replay and args.cold_boot_frames <= 0:
        ap.error(
            "no observation evidence selected; pass at least one "
            "--replay DIR and/or --cold-boot-frames N"
        )

    replays = [Path(d) for d in args.replay]
    replays = [d if d.is_absolute() else ROOT / d for d in replays]
    missing = [replay for replay in replays if not replay.is_dir()]
    if missing:
        ap.error(
            "ReplayArtifact input(s) missing:\n  "
            + "\n  ".join(str(path) for path in missing)
        )
    invalid = [
        replay for replay in replays
        if not (replay / "replay.json").is_file()
    ]
    if invalid:
        ap.error(
            "ReplayArtifact manifest missing from:\n  "
            + "\n  ".join(str(path) for path in invalid)
        )

    executed: set = set()
    call_targets: Counter = Counter()
    int_entries: set = set()
    ivt_all: dict = {}

    if args.cold_boot_frames:
        info = observe_cold_boot(frames=args.cold_boot_frames, executed=executed,
                                 call_targets=call_targets)
        print(f"[codemap] cold boot (from EXE): {info['frames']} frames, "
              f"{len(executed)} addrs -- the startup path no replay covers")

    for replay in replays:
        before = len(executed)
        info = observe_replay(
            replay, max_frames=args.max_frames, executed=executed,
            call_targets=call_targets, int_entries=int_entries)
        ivt_all.update(info["ivt"])
        print(f"[codemap] {replay.name}: {info['frames']} frames, "
              f"+{len(executed) - before} new addrs (total {len(executed)})")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "executed": sorted(f"{cs:04X}:{ip:04X}" for cs, ip in executed),
        "call_targets": {f"{cs:04X}:{ip:04X}": n for (cs, ip), n in sorted(call_targets.items())},
        "int_entries": sorted(f"{cs:04X}:{ip:04X}" for cs, ip in int_entries),
        "ivt_game_vectors": ivt_all,
        "replays": [d.name for d in replays],
    }
    out.write_text(json.dumps(doc, indent=1))
    game_ex = sum(1 for cs, _ in executed if cs == GAME_SEG)
    print(f"[codemap] wrote {out}: {len(executed)} executed "
          f"({game_ex} in game seg {GAME_SEG:04X}), "
          f"{len(call_targets)} call targets, {len(ivt_all)} IVT game vectors")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
