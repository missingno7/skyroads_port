"""Play SKYROADS gameplay VM-FREE — the standalone native-port entry point.

Following pre2_port's ``play_native.py`` model: seed real level data from the
VM ONCE, then hand off to :class:`skyroads.native.loop.NativeGameplayDriver`
-- every subsequent tick is pure recovered Python, no VM/interpreter, no
original binary. "The first step" (per the project's stated direction): play
any level this way, verifiable against the original game; menu/intro/render
are separate, later extensions of the same model, not needed for this to work
today.

Level selection today is "whichever demo/snapshot you seed from" -- true
from-scratch native level loading (parsing SkyRoads' level file format) is
not recovered yet, so this script always boots the ORIGINAL game briefly to
reach real gameplay, exactly like ``scripts/play.py --play-demo ... --headless``
would, and only THEN switches to the native driver. That boot is the one
VM-touching step; everything after ``--native-from`` is decided is 100% native.

Usage:
    # Play a recorded demo's OWN input, purely natively, once seeded:
    python scripts/play_native.py --demo artifacts/demos/demo_e2e_20260710_132930

    # Same, but ALSO run the VM alongside and report any divergence (the
    # convergence proof, promoted from tests/test_native_loop_lockstep.py):
    python scripts/play_native.py --demo artifacts/demos/demo_e2e_20260710_132930 --verify

    # Keep running past the demo's recorded input (idle input) to see how far
    # the native driver gets on its own, transitions and all:
    python scripts/play_native.py --demo artifacts/demos/demo_e2e_20260710_132930 --extra-ticks 2000
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "dos_re"))

import scripts.play as sp  # noqa: E402
from dos_re import player  # noqa: E402
from dos_re.cpu import CPU8086, HaltExecution  # noqa: E402
from dos_re.dos import ConsoleInputWouldBlock  # noqa: E402
from dos_re.input_demo import InputDemoPlayback  # noqa: E402
from dos_re.player import _use_real_console_input  # noqa: E402

from skyroads.bridge.dgroup_view import GameView  # noqa: E402
from skyroads.native.gaps import SkyroadsGap  # noqa: E402
from skyroads.native.loop import GameplayScratch, NativeGameplayDriver  # noqa: E402
from skyroads.native.state import NativeGameState  # noqa: E402
from skyroads.recovered.dynamics import JumpScratch  # noqa: E402

LOOP_TOP_IP = 0x2324  # the gameplay sub-step's classification entry (1010:2324)
INPUT_OFFS = [0x95F4, 0x547A, 0x9330, 0x1600, 0x95F6] + list(range(0x0BD0, 0x0BE0))


def _bpw(m, ss, bp, o):
    return m.rw(ss, (bp - o) & 0xFFFF)


def boot_and_seed(root: Path, demo_path: Path):
    """Drive the ORIGINAL game (via the VM) to the first real gameplay
    sub-step, then return (NativeGameState, GameplayScratch, jump_level_gate,
    a `next_input()` generator yielding the demo's remaining recorded input
    frame by frame, and the live `rt`/`args`/`frontend`/`pb` for --verify)."""
    frontend = sp.SkyroadsFrontend(root)
    args = player.build_arg_parser(frontend).parse_args(
        ["--play-demo", str(demo_path), "--headless"])
    pb = InputDemoPlayback.load(str(demo_path))
    frontend.apply_demo_metadata(args, pb.manifest.get("metadata", {}))
    rt = (frontend.create_runtime(args) if pb.is_cold_start
          else frontend.load_snapshot_runtime(args, pb.snapshot_path()))
    args.install_replacements = False  # pure ASM oracle while seeding/boot-driving
    frontend.apply_hook_mode(rt, args)
    _use_real_console_input(rt)

    seed = {}

    def _try_seed(cpu):
        if seed:
            return
        m = cpu.mem
        ds = cpu.s.ds
        if m.rw(ds, 0x456E) == 0:
            seed["state"] = NativeGameState(bytearray(m.data[(ds << 4):(ds << 4) + 0x10000]))
            s = cpu.s
            seed["scratch"] = GameplayScratch(
                jump=JumpScratch(_bpw(m, s.ss, s.bp, 8), _bpw(m, s.ss, s.bp, 10),
                                 _bpw(m, s.ss, s.bp, 6)),
                bp12=_bpw(m, s.ss, s.bp, 12), bp14=_bpw(m, s.ss, s.bp, 14),
                bp24=_bpw(m, s.ss, s.bp, 24), tgt_af2c=_bpw(m, s.ss, s.bp, 28))
            seed["jump_level_gate"] = m.rw(ds, 0x4562)
            seed["frame"] = frame_box[0]

    inputs = []
    frame_box = [0]

    def _record_input(cpu):
        if not seed:
            return
        m = cpu.mem
        ds = cpu.s.ds
        inputs.append((
            m.rw(ds, 0x95F4), m.rw(ds, 0x547A), m.rw(ds, 0x9330),
            bytes(m.rb(ds, o) for o in range(0x0BD0, 0x0BE0)),
            m.rw(ds, 0x1600),
        ))

    orig = CPU8086.step

    def patched(self):
        if self.s.cs == 0x1010 and self.s.ip == LOOP_TOP_IP:
            _try_seed(self)
            _record_input(self)
        return orig(self)

    CPU8086.step = patched
    try:
        frame = 0
        while not pb.finished(frame):
            frame_box[0] = frame
            pb.apply_to_runtime(frame, rt, deliver=lambda r, sc: frontend.deliver_input(r, sc))
            try:
                frontend.advance_frame(rt, args, frame)
            except ConsoleInputWouldBlock:
                pass
            except HaltExecution:
                break
            frame += 1
    finally:
        CPU8086.step = orig

    if not seed:
        raise RuntimeError("never reached a game_state==0 gameplay sub-step in this demo")
    return seed, inputs, (rt, args, frontend, pb)


def run_offline(state, scratch, jump_level_gate, inputs, extra_ticks: int) -> None:
    """Pure native replay -- no VM from here on. Prints a summary."""
    view = GameView(state)
    driver = NativeGameplayDriver(view, jump_level_gate, scratch)
    for steer, jump, speed, keys, tick in inputs:
        view.steer = steer
        view.jump = jump
        view.speed = speed
        for i, kb in enumerate(keys):
            view._backend.wb(0x0BD0 + i, kb)
        view.elapsed_ticks = tick
        driver.tick()
    for _ in range(extra_ticks):
        driver.tick()  # idle input: whatever the view already holds
    print(f"[native] ticks={driver.ticks} transitions={driver.transitions} "
          f"final game_state={view.game_state} ship_pos={view.ship_pos:#x}")


def run_verify(root: Path, demo_path: Path) -> None:
    """The convergence proof: run the native driver in LOCKSTEP with the VM,
    injecting only input, and report every run's streak length + why it ended."""
    frontend = sp.SkyroadsFrontend(root)
    args = player.build_arg_parser(frontend).parse_args(
        ["--play-demo", str(demo_path), "--headless"])
    pb = InputDemoPlayback.load(str(demo_path))
    frontend.apply_demo_metadata(args, pb.manifest.get("metadata", {}))
    rt = (frontend.create_runtime(args) if pb.is_cold_start
          else frontend.load_snapshot_runtime(args, pb.snapshot_path()))
    args.install_replacements = False
    frontend.apply_hook_mode(rt, args)
    _use_real_console_input(rt)

    CMP_W = {0x9336: "bounce", 0xAF1C: "af1c", 0xAF2C: "af2c", 0x456E: "game_state",
             0x456A: "f456a", 0x4568: "lateral_accel", 0x5496: "u5496", 0x5494: "timer_a",
             0xB13C: "timer_b", 0x4558: "frame_ctr", 0x455A: "f455a",
             0xAF2E: "af2e", 0xAF30: "af30"}
    CMP_D = {0x54AC: "ship_pos", 0x9618: "lateral"}

    ctx = {"nst": None, "nsc": None, "streak": 0}
    runs = []

    def step(cpu):
        m = cpu.mem
        ds = cpu.s.ds
        ss = cpu.s.ss
        bp = cpu.s.bp
        gs = m.rw(ds, 0x456E)

        if ctx["nst"] is None:
            if gs != 0:
                return
            ctx["nst"] = NativeGameState(bytearray(m.data[(ds << 4):(ds << 4) + 0x10000]))
            ctx["nsc"] = GameplayScratch(
                JumpScratch(_bpw(m, ss, bp, 8), _bpw(m, ss, bp, 10), _bpw(m, ss, bp, 6)),
                _bpw(m, ss, bp, 12), _bpw(m, ss, bp, 14), _bpw(m, ss, bp, 24),
                _bpw(m, ss, bp, 28))
            ctx["streak"] = 0
        else:
            st = ctx["nst"]
            diffs = [n for off, n in CMP_W.items() if st.rw(off) != m.rw(ds, off)]
            diffs += [n for off, n in CMP_D.items()
                      if (st.rw(off) | (st.rw(off + 2) << 16)) != (m.rw(ds, off) | (m.rw(ds, off + 2) << 16))]
            if diffs:
                runs.append((ctx["streak"], diffs))
                ctx["nst"] = None
                return
            ctx["streak"] += 1

        st = ctx["nst"]
        for off in INPUT_OFFS:
            st.ww(off, m.rw(ds, off))
        try:
            from skyroads.native.loop import native_gameplay_substep
            ctx["nsc"] = native_gameplay_substep(GameView(st), ctx["nsc"], allow_unmodelled_effect=True)
        except SkyroadsGap as exc:
            runs.append((ctx["streak"], [f"GAP: {exc}"]))
            ctx["nst"] = None

    orig = CPU8086.step

    def patched(self):
        if self.s.cs == 0x1010 and self.s.ip == LOOP_TOP_IP:
            step(self)
        return orig(self)

    CPU8086.step = patched
    try:
        frame = 0
        while not pb.finished(frame):
            pb.apply_to_runtime(frame, rt, deliver=lambda r, sc: frontend.deliver_input(r, sc))
            try:
                frontend.advance_frame(rt, args, frame)
            except ConsoleInputWouldBlock:
                pass
            except HaltExecution:
                break
            frame += 1
    finally:
        CPU8086.step = orig

    total = sum(s for s, _ in runs)
    field_breaks = [r for r in runs if r[1] and not str(r[1][0]).startswith("GAP")]
    print(f"[verify] {len(runs)} lockstep runs, {total} total in-sync steps, "
          f"longest={max((s for s, _ in runs), default=0)}")
    for streak, cause in runs:
        print(f"  {streak:5d} steps in sync -> {cause}")
    if field_breaks:
        print(f"\n*** {len(field_breaks)} run(s) ended on a real field divergence, not a clean gap ***")
    else:
        print("\nAll runs ended on a detected boundary (gap) -- zero silent drift.")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--demo", required=True, help="demo dir to boot from and seed level data with")
    p.add_argument("--extra-ticks", type=int, default=0,
                   help="keep ticking the native driver this many times past the demo's recorded input")
    p.add_argument("--verify", action="store_true",
                   help="run the VM alongside and report native/VM divergence instead of a plain offline replay")
    args = p.parse_args()

    demo_path = ROOT / args.demo if not Path(args.demo).is_absolute() else Path(args.demo)

    if args.verify:
        run_verify(ROOT, demo_path)
        return

    print(f"[boot] driving the original game to real gameplay via the VM ({demo_path.name})...")
    seed, inputs, _live = boot_and_seed(ROOT, demo_path)
    print(f"[boot] seeded at frame {seed['frame']}, jump_level_gate={seed['jump_level_gate']} "
          f"-- switching to 100% native from here ({len(inputs)} recorded input frames)")
    run_offline(seed["state"], seed["scratch"], seed["jump_level_gate"], inputs, args.extra_ticks)


if __name__ == "__main__":
    main()
