"""Linear disassembler: static lengths from dos_re.lift, text from the interpreter.

Loads either an explicit machine snapshot or an authoritative ReplayArtifact,
then linearly decodes a CS:offset..offset range.
Instruction LENGTHS come from the static decoder (``dos_re.lift.decode`` — the
lifter's, unit-tested against the interpreter); the human-readable text still
comes from executing each instruction once on a throwaway runtime and capturing
what ``execute_opcode`` returns. Per-instruction exceptions are swallowed so an
odd opcode does not stop the sweep (the static length keeps the walk aligned).

History: this tool used to measure lengths by counting ``cpu.fetch8`` calls
through one step(). The 2026-07-09 interpreter optimization rounds inlined the
hot fetch paths, which silently broke that trick (opcode/modrm/displacement
bytes no longer route through fetch8). The static decoder is now the length
authority here — and unlike the old trick it does not require the instruction
to be executable.

**SKYROADS.EXE overlays/decompresses its own code segment at runtime** (found
2026-07-11 while chasing garbage output on `1010:1B49`, a known-good, heavily
verified address — its bytes at snapshot-load time were `D5 75...` (AAD,
nonsensical), but by the time live execution actually reaches that address
they're `C8 00 00 00...` (a real ENTER-based function prologue, matching the
already-recovered `dispatch_menu_action`). So a plain snapshot load only sees
whatever churn happened to be sitting at an address BEFORE the relevant code/
overlay was loaded into place -- garbage in, garbage out, no bug in the decoder
itself. `--replay` below works around this by driving an oracle replay forward
until execution actually reaches the target address, then disassembling from
the LIVE, correctly-populated memory instead of a cold snapshot.

Usage:
    python tools/lindis.py <exe_path> <CS> <START> <END> --snapshot <snapshot_dir>

    python tools/lindis.py <exe_path> <CS> <START> <END> \\
        --replay <artifact_dir> [--max-frames N]

    Replay mode restores the artifact's declared oracle recording base. The
    game-specific frontend drives its immutable event stream forward, pure ASM
    oracle (no overrides), until CS:IP first reaches (CS, START) or the point
    budget runs out; disassembly then reads from that live, populated memory.

Origin: adapted from the Overkill port's scripts/lindis.py (its game-specific
snapshot loader replaced by the generic dos_re.snapshot.load_snapshot).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "dos_re"))

from dos_re.lift.decode import decode_one  # noqa: E402
from dos_re.snapshot import load_snapshot  # noqa: E402


def _print_range(cpu, cs: int, start: int, end: int) -> None:
    cpu.replacement_hooks.clear()
    cpu.hook_verifier = None
    cpu.trace_enabled = True
    cpu.pending_irq = None

    # Capture the asm text the interpreter produces, without trace parsing.
    orig_exec = cpu.execute_opcode
    last = {"asm": "?"}

    def capturing_exec(op, seg_override, rep):
        res = orig_exec(op, seg_override, rep)
        last["asm"] = res
        return res

    cpu.execute_opcode = capturing_exec
    mem = cpu.mem

    ip = start
    while ip <= end:
        inst = decode_one(lambda off: mem.rb(cs, off & 0xFFFF), ip)
        cpu.s.cs = cs
        cpu.s.ip = ip
        last["asm"] = "?"
        try:
            cpu.step()
            asm = last["asm"] or inst.mnemonic
        except Exception as exc:  # noqa: BLE001
            asm = f"{inst.mnemonic}  <exec-exc {type(exc).__name__}: {exc}>"
        print(f"{cs:04X}:{ip:04X}  {inst.raw.hex():<16}  {str(asm).strip()}")
        ip = (ip + inst.length) & 0xFFFF

    cpu.execute_opcode = orig_exec


def main_static(exe: str, snap: str, cs: int, start: int, end: int) -> None:
    rt = load_snapshot(exe, snap)
    _print_range(rt.cpu, cs, start, end)


def main_live(exe: str, cs: int, start: int, end: int, replay_dir: str, max_frames: int) -> None:
    import scripts.play as sp
    from dos_re import player
    from dos_re.cpu import CPU8086, HaltExecution
    from dos_re.dos import ConsoleInputWouldBlock
    from dos_re.replay_input import RealModeInputAdapter
    from dos_re.replay import ReplayArtifact
    from dos_re.snapshot import apply_runtime_continuation
    from skyroads.replay import recording_base

    replay_path = Path(replay_dir)
    frontend = sp.SkyroadsFrontend(ROOT)
    args = player.build_arg_parser(frontend).parse_args(
        ["--play-replay", str(replay_path), "--headless", "--composition", "oracle"])
    artifact = ReplayArtifact.open(replay_path)
    frontend.apply_replay_metadata(args, artifact.metadata)
    rt = frontend.create_runtime(args)
    apply_runtime_continuation(rt, recording_base(artifact))
    inputs = RealModeInputAdapter(artifact.events)
    rt.dos.console_input_fallback = None

    reached = {"frame": None}

    orig_step = CPU8086.step

    def patched(self):
        if reached["frame"] is None and self.s.cs == cs and self.s.ip == start:
            reached["frame"] = reached.get("_frame", 0)
        return orig_step(self)

    CPU8086.step = patched
    try:
        frame = 0
        while (
            frame < artifact.end_point.ordinal
            and frame < max_frames
            and reached["frame"] is None
        ):
            reached["_frame"] = frame
            inputs.apply_to_runtime(
                frame, rt,
                deliver=lambda r, sc: frontend.deliver_input(r, sc))
            try:
                frontend.advance_frame(rt, args, frame)
            except ConsoleInputWouldBlock:
                pass
            except HaltExecution:
                break
            frame += 1
    finally:
        CPU8086.step = orig_step

    if reached["frame"] is None:
        print(f"never reached {cs:04X}:{start:04X} within {max_frames} frames "
              f"of {replay_path.name} -- try another replay or a larger --max-frames",
              file=sys.stderr)
        raise SystemExit(1)
    print(f"; reached {cs:04X}:{start:04X} at frame {reached['frame']} "
          f"of {replay_path.name} -- disassembling from LIVE memory", file=sys.stderr)
    _print_range(rt.cpu, cs, start, end)


def main(argv) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("exe")
    p.add_argument("cs")
    p.add_argument("start")
    p.add_argument("end")
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument("--snapshot", help="load an explicit machine snapshot")
    source.add_argument("--replay", help="drive this ReplayArtifact on the pure "
                                        "oracle until CS:START is reached")
    p.add_argument("--max-frames", type=int, default=3000,
                    help="point budget for --replay (default 3000)")
    args = p.parse_args(argv)

    cs = int(args.cs, 16) & 0xFFFF
    start = int(args.start, 16) & 0xFFFF
    end = int(args.end, 16) & 0xFFFF

    if args.replay:
        main_live(args.exe, cs, start, end, args.replay, args.max_frames)
    else:
        main_static(args.exe, args.snapshot, cs, start, end)


if __name__ == "__main__":
    main(sys.argv[1:])
