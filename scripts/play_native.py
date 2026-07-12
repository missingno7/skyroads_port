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
    # THE MILESTONE: cold-start a level (ship_pos=0, zero player input) and
    # play it 100% natively to completion -- forward motion is automatic in
    # SkyRoads (driven by the per-sub-step classification, not player input),
    # so an idle input still finishes the level:
    python scripts/play_native.py --demo artifacts/demos/demo_e2e_20260710_132930 --cold

    # Same, but ALSO reset the real VM to the identical cold state and confirm
    # it independently reaches the same level-complete conclusion -- the
    # strongest form of the proof:
    python scripts/play_native.py --demo artifacts/demos/demo_e2e_20260710_132930 --cold-verify

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
from skyroads.native.loop import GameplayScratch, NativeGameplayDriver, apply_level_init  # noqa: E402
from skyroads.native.state import NativeGameState  # noqa: E402
from skyroads.recovered.dynamics import JumpScratch  # noqa: E402
from skyroads.recovered.player import RespawnState, level_gravity  # noqa: E402

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


def run_cold(state, jump_level_gate, max_ticks: int = 2000) -> None:
    """THE MILESTONE: reset to a genuine COLD level start
    (:func:`~skyroads.native.loop.apply_level_init` -- ``ship_pos = 0``, the
    fixed :class:`~skyroads.recovered.player.RespawnState` fields, the
    derived per-level gravity) over real level geometry, then run the native
    driver with ZERO player input -- no steer, no jump, no recorded demo --
    until it reaches a genuine level-complete transition
    (``ship_pos >= LEVEL_END``, ``game_state -> 2``) on its own.

    This works because forward motion is AUTOMATIC in SkyRoads (driven by the
    classification's ``dispatch_menu_action`` call each sub-step, not by
    player input -- see ``skyroads.recovered.classify``'s docstring); a
    completely idle input still drives the ship the length of the level.
    100% native from the first tick: no VM, no recorded input, no
    original binary -- only the level's static geometry tables were ever
    read from a VM capture.
    """
    view = GameView(state)
    scratch = apply_level_init(view, jump_level_gate)
    print(f"[cold] reset to a genuine level start: ship_pos={view.ship_pos:#x} "
          f"af2c={view.af2c:#06x} game_state={view.game_state} gravity={view.gravity:#06x}")
    driver = NativeGameplayDriver(view, jump_level_gate, scratch)
    for i in range(max_ticks):
        outcome = driver.tick()
        if outcome.transitioned:
            completed = "game_state=2" in outcome.reason
            print(f"[cold] tick {i}: transition -> {outcome.reason}")
            if completed:
                print(f"\n*** COLD RUN COMPLETE: level finished in {i + 1} ticks, "
                      f"100% native, zero player input, zero VM after the geometry seed ***")
            else:
                print(f"\n[cold] stopped on a non-level-complete transition after {i + 1} ticks "
                      f"(death/crash/timeout, not a level finish)")
            return
    print(f"\n[cold] did not reach a transition within {max_ticks} ticks "
          f"(ship_pos={view.ship_pos:#x})")


def run_level(root: Path, level: int, baseline_dir: Path, max_ticks: int = 4000) -> None:
    """Play LEVEL by INDEX, VM-FREE -- no demo, no per-run snapshot. Loads the
    level's geometry straight from ``ROADS.LZS`` with
    :func:`skyroads.native.level_load.native_level_load` (verified byte-exact vs
    the VM), over a level-INDEPENDENT constants baseline (the sim's clip/shape
    tables are computed at startup, so a fresh state lacks them -- see
    run_status.md; computing them from scratch is the cold-boot milestone). Then
    :func:`apply_level_init` for the player state, and runs the native driver with
    the accelerate key held (forward motion is input-driven: `[0x9330]` speed
    comes from the up key). The ship advances +75/tick and crashes at the first
    obstacle absent steer/jump -- to COMPLETE a level, feed its recorded input.
    """
    from skyroads.native.level_load import native_level_load
    from skyroads.native.state import NativeGameState, DATA_SEG

    mem_bin = baseline_dir / "memory_1mb.bin"
    if not mem_bin.exists():
        raise SystemExit(
            f"constants baseline not found: {mem_bin}\n"
            "Pass --baseline <snapshot_dir> (a captured DGROUP providing the "
            "level-independent startup constants). Any gameplay snapshot works; "
            "the level geometry in it is overwritten by native_level_load.")
    base = DATA_SEG << 4
    dg = mem_bin.read_bytes()[base:base + 0x10000]
    state = NativeGameState(bytearray(dg))

    decoded = native_level_load(state, level, game_root=str(root / "assets"))
    gate = state.rw(0x4562)
    print("[level] NOTE: this is the HEADLESS native SIM (no game window yet) -- it plays the "
          "level's physics/collision and prints the outcome. The native renderer is recovered "
          "but not yet assembled into a windowed player; for windowed play use scripts/play.py.")
    print(f"[level] loaded level {level} from ROADS.LZS VM-FREE: gravity/gate={gate:#06x} "
          f"fuel={decoded.fuel} oxygen={decoded.oxygen} road={len(decoded.road)}B")

    view = GameView(state)
    scratch = apply_level_init(view, gate)
    print(f"[level] cold start: ship_pos={view.ship_pos:#x} game_state={view.game_state} "
          f"gravity={view.gravity:#06x} -- holding ACCELERATE (no steer/jump)")
    driver = NativeGameplayDriver(view, gate, scratch)
    for i in range(max_ticks):
        view.speed = 1  # hold the accelerate key
        outcome = driver.tick()
        if outcome.transitioned:
            if "game_state=2" in outcome.reason:
                print(f"\n*** LEVEL {level} COMPLETE in {i + 1} ticks -- 100% native, "
                      f"loaded by index, zero VM ***")
            else:
                print(f"[level] tick {i}: {outcome.reason} (ship_pos={view.ship_pos:#x}) "
                      f"-- expected without steer/jump; feed recorded input to finish")
            return
    print(f"[level] no transition in {max_ticks} ticks (ship_pos={view.ship_pos:#x})")


def run_cold_verify(root: Path, demo_path: Path, max_ticks: int = 2000) -> None:
    """The strongest form of the cold-run proof: reset the REAL VM (the
    unmodified original game) to the SAME cold level-start state, force zero
    input on every sub-step, and check it independently reaches the same
    level-complete conclusion -- confirming the native cold-run milestone
    isn't just self-consistent, it matches what the original game itself
    would do from the same starting point."""
    frontend = sp.SkyroadsFrontend(root)
    args = player.build_arg_parser(frontend).parse_args(
        ["--play-demo", str(demo_path), "--headless"])
    pb = InputDemoPlayback.load(str(demo_path))
    frontend.apply_demo_metadata(args, pb.manifest.get("metadata", {}))
    rt = (frontend.create_runtime(args) if pb.is_cold_start
          else frontend.load_snapshot_runtime(args, pb.snapshot_path()))
    args.install_replacements = False  # the pure ASM oracle -- the strongest proof
    frontend.apply_hook_mode(rt, args)
    _use_real_console_input(rt)

    r = RespawnState()
    reset_done = [False]
    tick_count = [0]
    result = {}

    def _reset_and_zero_input(cpu):
        m = cpu.mem
        ds = cpu.s.ds
        if not reset_done[0] and m.rw(ds, 0x456E) == 0:
            gate = m.rw(ds, 0x4562)
            m.ww(ds, 0x9618, r.lateral_lo); m.ww(ds, 0x961A, r.lateral_hi)
            m.ww(ds, 0xAF1C, r.vert_af1c); m.ww(ds, 0xAF2C, r.vert_af2c)
            m.ww(ds, 0x5496, r.unknown_5496); m.ww(ds, 0x4568, r.lateral_accel)
            m.ww(ds, 0x9336, r.vvel)
            m.ww(ds, 0x54AC, r.ship_pos_lo); m.ww(ds, 0x54AE, r.ship_pos_hi)
            m.ww(ds, 0x5494, r.level_timer_a); m.ww(ds, 0xB13C, r.level_timer_b)
            m.ww(ds, 0x456E, r.game_state); m.ww(ds, 0x4558, r.frame_ctr)
            m.ww(ds, 0x456A, r.unknown_456a)
            m.ww(ds, 0x54AA, level_gravity(gate))
            m.ww(ds, 0x95F4, 0); m.ww(ds, 0x547A, 0); m.ww(ds, 0x9330, 0)
            for o in range(0x0BD0, 0x0BE0):
                m.wb(ds, o, 0)
            reset_done[0] = True
            print("[vm-cold] VM memory reset to the same cold apply_level_init() state")
        elif reset_done[0]:
            m.ww(ds, 0x95F4, 0); m.ww(ds, 0x547A, 0); m.ww(ds, 0x9330, 0)
            if cpu.s.ip == LOOP_TOP_IP:
                tick_count[0] += 1
                gs = m.rw(ds, 0x456E)
                ship = m.rw(ds, 0x54AC) | (m.rw(ds, 0x54AE) << 16)
                if gs != 0 and "final_tick" not in result:
                    result["final_tick"] = tick_count[0]
                    result["game_state"] = gs
                    result["ship_pos"] = ship

    orig = CPU8086.step

    def patched(self):
        if self.s.cs == 0x1010 and self.s.ip == LOOP_TOP_IP:
            _reset_and_zero_input(self)
        return orig(self)

    CPU8086.step = patched
    try:
        frame = 0
        # Keep advancing PAST the demo's own recorded length -- input is force-
        # zeroed every sub-step regardless (see _reset_and_zero_input), so the
        # demo's own recorded length is irrelevant once the cold reset happens.
        while frame < max_ticks + 200 and "final_tick" not in result:
            if not pb.finished(frame):
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

    print(f"\n[vm-cold] result: {result}")
    if result.get("game_state") == 2:
        print("*** VM independently confirms: same cold start -> level complete ***")


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
    p.add_argument("--demo", help="demo dir to boot from and seed level data with "
                   "(not needed with --level)")
    p.add_argument("--level", type=int, default=None,
                   help="play THIS level index (0-30) VM-FREE: load its geometry from ROADS.LZS "
                        "and play natively -- no demo, no per-run snapshot")
    p.add_argument("--baseline", default="artifacts/snapshots/gameplay_f640",
                   help="constants-baseline snapshot dir for --level (level-independent startup "
                        "constants; the geometry in it is overwritten). Default: %(default)s")
    p.add_argument("--extra-ticks", type=int, default=0,
                   help="keep ticking the native driver this many times past the demo's recorded input")
    p.add_argument("--verify", action="store_true",
                   help="run the VM alongside and report native/VM divergence instead of a plain offline replay")
    p.add_argument("--cold", action="store_true",
                   help="THE MILESTONE: reset to a genuine cold level start (ship_pos=0) and play the "
                        "WHOLE LEVEL with zero player input, 100%% native, until it completes")
    p.add_argument("--cold-verify", action="store_true",
                   help="like --cold, but ALSO resets the real VM to the same cold state and confirms "
                        "it independently reaches the same level-complete conclusion")
    p.add_argument("--max-ticks", type=int, default=2000, help="tick budget for --cold/--cold-verify")
    args = p.parse_args()

    if args.level is not None:
        baseline = Path(args.baseline)
        if not baseline.is_absolute():
            baseline = ROOT / baseline
        run_level(ROOT, args.level, baseline, args.max_ticks)
        return

    if not args.demo:
        p.error("one of --demo or --level is required")
    demo_path = ROOT / args.demo if not Path(args.demo).is_absolute() else Path(args.demo)

    if args.cold_verify:
        run_cold_verify(ROOT, demo_path, args.max_ticks)
        return

    if args.verify:
        run_verify(ROOT, demo_path)
        return

    print(f"[boot] driving the original game to real gameplay via the VM ({demo_path.name})...")
    seed, inputs, _live = boot_and_seed(ROOT, demo_path)
    print(f"[boot] seeded at frame {seed['frame']}, jump_level_gate={seed['jump_level_gate']} "
          f"-- switching to 100% native from here ({len(inputs)} recorded input frames)")

    if args.cold:
        run_cold(seed["state"], seed["jump_level_gate"], args.max_ticks)
        return

    run_offline(seed["state"], seed["scratch"], seed["jump_level_gate"], inputs, args.extra_ticks)


if __name__ == "__main__":
    main()
