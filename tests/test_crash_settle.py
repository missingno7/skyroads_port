"""The crash/finish SETTLE WINDOW runs natively (the explosion animates),
verified against the VM.

Regression for the 2026-07-13 "crash explosion frozen" bug: an over-strict
`game_state in {0,3}` gate in `native_gameplay_substep` exited on the very
first crash frame (`game_state := 1`), so the ~34-frame settle window -- during
which the ship sprite index `si = [456A]//3` cycles through the explosion
frames 0..13 as the `[456A]` counter ramps 2..42 -- never ran, and the viewer
froze. The VM's real gate is `should_run_gameplay` alone, whose settle-window
clause keeps the frame in the handler for game_state 1/2 while `[456A]` ramps.

This seeds the native stepper from the VM at a real lateral wall crash and
checks it reproduces the settle window step-for-step instead of bailing out.
"""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXE = ROOT / "assets" / "SKYROADS.EXE"
DEMO = ROOT / "artifacts" / "demos" / "demo_skyroads_20260710_213019"

pytestmark = pytest.mark.skipif(
    not (EXE.exists() and DEMO.exists()),
    reason="needs SKYROADS.EXE + the collision demo",
)

INPUT_OFFS = [0x95F4, 0x547A, 0x9330, 0x1600, 0x95F6] + list(range(0x0BD0, 0x0BE0))


def test_native_runs_the_crash_settle_window_like_the_vm() -> None:
    import scripts.play as sp
    from dos_re import player
    from dos_re.cpu import CPU8086, HaltExecution
    from dos_re.dos import ConsoleInputWouldBlock
    from skyroads.replay import SkyroadsReplayPlayback
    from dos_re.player import _use_real_console_input

    from skyroads.bridge.dgroup_view import GameView
    from skyroads.native.gaps import SkyroadsGap
    from skyroads.native.loop import GameplayScratch, native_gameplay_substep
    from skyroads.native.state import NativeGameState
    from skyroads.handrecovered.dynamics import JumpScratch

    frontend = sp.SkyroadsFrontend(ROOT)
    args = player.build_arg_parser(frontend).parse_args(
        ["--play-demo", str(DEMO), "--headless"])
    pb = SkyroadsReplayPlayback.load(str(DEMO))
    frontend.apply_demo_metadata(args, pb.manifest.get("metadata", {}))
    rt = frontend.load_demo_runtime(args, pb)
    args.install_replacements = False
    frontend.apply_hook_mode(rt, args)
    _use_real_console_input(rt)

    def _bpw(m, ss, bp, o):
        return m.rw(ss, (bp - o) & 0xFFFF)

    LOOP = 0x2324
    ctx = {"nst": None, "sc": None}
    #: (native_grounded, vm_grounded, native_af2c, vm_af2c, native_gs) per step
    trace = []
    ended = {"gap_at_grounded": None}

    def step(cpu):
        m = cpu.mem
        ds, ss, bp = cpu.s.ds, cpu.s.ss, cpu.s.bp
        gs = m.rw(ds, 0x456E)
        gr = m.rw(ds, 0x456A)
        if ctx["nst"] is None:
            # seed exactly at the crash onset: game_state just became 1, the
            # settle counter is at the very start of its ramp.
            if gs == 1 and 0 < gr <= 3:
                ctx["nst"] = NativeGameState(bytearray(m.data[(ds << 4):(ds << 4) + 0x10000]))
                ctx["sc"] = GameplayScratch(
                    JumpScratch(_bpw(m, ss, bp, 8), _bpw(m, ss, bp, 10), _bpw(m, ss, bp, 6)),
                    _bpw(m, ss, bp, 12), _bpw(m, ss, bp, 14), _bpw(m, ss, bp, 24),
                    _bpw(m, ss, bp, 28))
            return
        nst = ctx["nst"]
        if ended["gap_at_grounded"] is not None:
            return
        trace.append((nst.rw(0x456A), gr, nst.rw(0xAF2C), m.rw(ds, 0xAF2C), nst.rw(0x456E)))
        for off in INPUT_OFFS:
            nst.ww(off, m.rw(ds, off))
        try:
            ctx["sc"] = native_gameplay_substep(GameView(nst), ctx["sc"],
                                                allow_unmodelled_effect=True)
        except SkyroadsGap:
            ended["gap_at_grounded"] = nst.rw(0x456A)

    orig = CPU8086.step

    def patched(self):
        if self.s.cs == 0x1010 and self.s.ip == LOOP:
            step(self)
        return orig(self)

    CPU8086.step = patched
    try:
        frame = 0
        while not pb.finished(frame) and frame <= 360 and ended["gap_at_grounded"] is None:
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

    assert ctx["nst"] is not None, "never seeded at a crash onset"
    # (1) The native stepper did NOT bail out immediately: it ran the whole
    #     settle window (many steps at game_state==1), so the explosion animates.
    assert len(trace) >= 25, f"settle window too short -- native bailed early? ({len(trace)} steps)"
    assert all(gs == 1 for *_rest, gs in trace), \
        "native left game_state 1 mid-settle (should stay frozen through the window)"

    # (2) It reproduces the VM's settle trajectory step-for-step. The harness
    #     compares native's PRE-step state to the VM's state one loop-top later,
    #     so native at step i equals the VM at step i-1: native_grounded[i] ==
    #     vm_grounded[i-1] and same for af2c.
    for i in range(1, len(trace)):
        n_gr, _v_gr, n_af2c, _v_af2c, _gs = trace[i]
        _pn_gr, pv_gr, _pn_af2c, pv_af2c, _pgs = trace[i - 1]
        assert n_gr == pv_gr, f"step {i}: native grounded {n_gr} != VM {pv_gr}"
        assert n_af2c == pv_af2c, f"step {i}: native af2c {n_af2c} != VM {pv_af2c}"

    # (3) The window ends where the VM's does: the settle counter ran past 0x2A.
    assert ended["gap_at_grounded"] is not None and ended["gap_at_grounded"] > 0x2A, \
        f"settle window ended at grounded={ended['gap_at_grounded']} (expected > 0x2A)"
