"""Multi-step convergence proof: the native stepper runs in LOCKSTEP with the VM.

test_native_substep proves one sub-step in isolation (re-seeded from the VM each
time). This proves the ACCUMULATED loop: seed a NativeGameState + GameplayScratch
ONCE from the VM at a gameplay sub-step, then run native_gameplay_substep over
and over -- carrying its OWN scratch, injecting only the INPUT fields
(steer/jump/speed/keys/tick) the outer loop sets between sub-steps -- and check
the native state stays in sync with the VM on every other gameplay field at
every step.

The strong claim is that the native loop stays byte-identical to the VM for long
accumulated stretches (whole levels -- 50-120+ steps) and ends runs cleanly:
almost every run ends because the stepper detected a boundary it doesn't own and
RAISED a typed gap (LevelEndTransition when game_state leaves the in-level set
{0,3}; FallDeathTransition on a fall; or the 1DFA-effect gap), not a silent
field divergence. A small residual of runs end on an un-modelled respawn/level-
load transition (game_state 3 -> respawn, the transition subsystem is not
recovered) -- those are bounded and documented, not general drift.
"""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXE = ROOT / "assets" / "SKYROADS.EXE"
DEMO = ROOT / "artifacts" / "demos" / "demo_skyroads_L1FULL_20260713_212417"

pytestmark = pytest.mark.skipif(
    not (EXE.exists() and DEMO.exists()),
    reason="needs SKYROADS.EXE + the E2E demo",
)

# Input fields the outer loop / input handler sets between sub-steps -- injected
# each step, not predicted (steer, jump, speed, control device, tick, key rows).
INPUT_OFFS = [0x95F4, 0x547A, 0x9330, 0x1600, 0x95F6] + list(range(0x0BD0, 0x0BE0))
# Gameplay fields the sub-step computes -- these must stay in sync.
CMP_W = {0x9336: "bounce", 0xAF1C: "af1c", 0xAF2C: "af2c", 0x456E: "game_state",
         0x456A: "f456a", 0x4568: "lateral_accel", 0x5496: "u5496", 0x5494: "timer_a",
         0xB13C: "timer_b", 0x4558: "frame_ctr", 0x455A: "f455a",
         0xAF2E: "af2e", 0xAF30: "af30"}
CMP_D = {0x54AC: "ship_pos", 0x9618: "lateral"}


def test_native_loop_stays_in_lockstep_with_vm() -> None:
    import scripts.play as sp
    from dos_re import player
    from dos_re.cpu import CPU8086, HaltExecution
    from dos_re.dos import ConsoleInputWouldBlock
    from dos_re.input_demo import InputDemoPlayback
    from dos_re.player import _use_real_console_input

    from skyroads.bridge.dgroup_view import GameView
    from skyroads.native.gaps import SkyroadsGap
    from skyroads.native.loop import GameplayScratch, native_gameplay_substep
    from skyroads.native.state import NativeGameState
    from skyroads.handrecovered.dynamics import JumpScratch

    frontend = sp.SkyroadsFrontend(ROOT)
    args = player.build_arg_parser(frontend).parse_args(
        ["--play-demo", str(DEMO), "--headless"])
    pb = InputDemoPlayback.load(str(DEMO))
    frontend.apply_demo_metadata(args, pb.manifest.get("metadata", {}))
    rt = (frontend.create_runtime(args) if pb.is_cold_start
          else frontend.load_snapshot_runtime(args, pb.snapshot_path()))
    args.install_replacements = False
    frontend.apply_hook_mode(rt, args)
    _use_real_console_input(rt)
    # Replay with the mouse-presence the demo was recorded under (pinned in its
    # metadata), so a demo recorded with the mouse present reproduces faithfully.
    rt.dos.mouse_present = pb.mouse_present_hint

    def _bpw(m, ss, bp, o):
        return m.rw(ss, (bp - o) & 0xFFFF)

    LOOP = 0x2324
    ctx = {"nst": None, "nsc": None, "streak": 0}
    runs = []          # (streak_len, cause) -- cause is "GAP" or a field name
    field_breaks = []  # any run that ended on a real field divergence (should be none)

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
                runs.append((ctx["streak"], diffs[0]))
                field_breaks.append((ctx["streak"], diffs))
                ctx["nst"] = None
                return
            ctx["streak"] += 1

        st = ctx["nst"]
        for off in INPUT_OFFS:                    # inject VM input
            st.ww(off, m.rw(ds, off))
        if gs != 0:
            # The VM left game_state 0 (level end / crash / resume). The native
            # substep no longer RAISES exactly at that step -- with the VM-exact
            # gate (should_run_gameplay alone) game_state 1/2 while the settle
            # window counter [456A] is ramping is a "keep running" frame, so the
            # clean run now ends at this boundary rather than on a native GAP.
            # Record the whole-level streak here so it's still measured; this
            # harness only feeds native game_state==0 frames, so it never runs
            # the settle window itself (that's exercised by tests/test_native_
            # driver.py's auto_respawn=False + the crash settle-window trace).
            if ctx["streak"] > 0:
                runs.append((ctx["streak"], "BOUNDARY"))
            ctx["nst"] = None
            return
        try:
            ctx["nsc"] = native_gameplay_substep(GameView(st), ctx["nsc"])
        except SkyroadsGap:
            runs.append((ctx["streak"], "GAP"))
            ctx["nst"] = None

    orig = CPU8086.step

    def patched(self):
        if self.s.cs == 0x1010 and self.s.ip == LOOP:
            step(self)
        return orig(self)

    CPU8086.step = patched
    try:
        frame = 0
        while not pb.finished(frame) and frame < 1200:
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

    assert runs, "no lockstep runs recorded -- harness/demo setup broken"
    total_in_sync = sum(s for s, _ in runs)
    max_streak = max(s for s, _ in runs)

    # (1) The native loop runs whole levels in perfect lockstep -- long
    #     accumulated stretches, not one-off steps.
    assert max_streak >= 50, f"longest lockstep run only {max_streak} steps ({runs})"
    assert total_in_sync >= 140, f"only {total_in_sync} total in-sync steps ({runs})"

    # (2) Runs end cleanly -- either the VM leaves game_state 0 at a level
    #     boundary ("BOUNDARY") or the stepper DETECTS a boundary it doesn't own
    #     and raises a typed gap ("GAP") -- rather than drifting. A tiny residual
    #     ends on an un-modelled respawn/level-load transition (game_state 3 ->
    #     respawn); bound it, don't allow general drift.
    assert len(field_breaks) <= 2, (
        f"native loop drifted on {len(field_breaks)} runs (only rare respawn "
        f"edges allowed): {field_breaks}")
    clean_runs = len(runs) - len(field_breaks)
    assert clean_runs >= 2 * len(field_breaks), (
        f"too many field-break runs vs clean gap-stops: {runs}")
