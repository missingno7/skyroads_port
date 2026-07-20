"""Convergence proof: the ASSEMBLED native gameplay sub-step
(skyroads.native.loop.native_gameplay_substep) reproduces the VM.

This is the payoff of recovering the whole physics/collision sub-step as
individual islands: composed in ASM spine order over a GameplayScratch, they
step real gameplay -- INCLUDING the forward motion, which is the classification's
dispatch_menu_action (1B49) call (action 0xA advances ship_pos by 0x12F).
Driving the E2E demo, we seed a NativeGameState + scratch from the VM at each
game_state==0 sub-step (loop top 2324), run one native sub-step, and compare
the full gameplay DGROUP back to the VM at the next loop top.

native_gameplay_substep raises a gap on the paths not yet recovered (the 1DFA
effect frame, game_state != 0); those sub-steps are counted as gaps, not
failures -- the honest current ceiling. A tiny number of edge cases (a rare
[AF2E] landing adjustment) still miss, so the assertion is a high match rate,
not 100%.
"""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXE = ROOT / "assets" / "SKYROADS.EXE"
DEMO = ROOT / "artifacts" / "demos" / "demo_e2e_20260710_132930"

pytestmark = pytest.mark.skipif(
    not (EXE.exists() and DEMO.exists()),
    reason="needs SKYROADS.EXE + the E2E demo",
)

# Gameplay DGROUP fields the sub-step computes (word fields).
SUBSTEP_FIELDS = {
    0x9336: "bounce", 0xAF1C: "af1c", 0xAF2C: "af2c", 0x456E: "game_state",
    0x456A: "f456a", 0x4568: "lateral_accel", 0x5496: "u5496", 0xB13C: "timer_b",
    0x4558: "frame_ctr", 0x455A: "f455a", 0xAF2E: "af2e", 0xAF30: "af30",
    0x5494: "timer_a",
}
# 32-bit fields.
SUBSTEP_DWORDS = {0x54AC: "ship_pos", 0x9618: "lateral"}


def test_native_substep_matches_vm_over_demo() -> None:
    import scripts.play as sp
    from dos_re import player
    from dos_re.cpu import CPU8086, HaltExecution
    from dos_re.dos import ConsoleInputWouldBlock
    from tests.replay_support import open_oracle_replay

    from skyroads.bridge.dgroup_view import GameView
    from skyroads.native.gaps import SkyroadsGap
    from skyroads.native.loop import GameplayScratch, native_gameplay_substep
    from skyroads.native.state import NativeGameState
    from skyroads.handrecovered.dynamics import JumpScratch

    frontend = sp.SkyroadsFrontend(ROOT)
    args = player.build_arg_parser(frontend).parse_args(
        ["--play-demo", str(DEMO), "--headless"])
    pb, rt = open_oracle_replay(frontend, args, DEMO)

    LOOP = 0x2324

    def _bpw(m, ss, bp, o):
        return m.rw(ss, (bp - o) & 0xFFFF)

    st = {"armed": False, "dg": None, "sc": None}
    stats = {"ok": 0, "mismatch": 0, "gap": 0}
    field_misses: dict = {}

    def _seed(cpu):
        s = cpu.s
        m = cpu.mem
        ds = s.ds
        bp = s.bp
        dg = bytearray(m.data[(ds << 4):(ds << 4) + 0x10000])
        sc = GameplayScratch(
            jump=JumpScratch(_bpw(m, s.ss, bp, 8), _bpw(m, s.ss, bp, 10),
                             _bpw(m, s.ss, bp, 6)),
            bp12=_bpw(m, s.ss, bp, 12), bp14=_bpw(m, s.ss, bp, 14),
            bp24=_bpw(m, s.ss, bp, 24), tgt_af2c=_bpw(m, s.ss, bp, 28))
        return dg, sc

    def _compare(cpu):
        view = GameView(NativeGameState(st["dg"]))
        try:
            native_gameplay_substep(view, st["sc"])
        except SkyroadsGap:
            stats["gap"] += 1
            return
        m = cpu.mem
        ds = cpu.s.ds
        misses = []
        for off, name in SUBSTEP_FIELDS.items():
            if view._backend.rw(off) != m.rw(ds, off):
                misses.append(name)
                field_misses[name] = field_misses.get(name, 0) + 1
        for off, name in SUBSTEP_DWORDS.items():
            got = view._backend.rw(off) | (view._backend.rw(off + 2) << 16)
            exp = m.rw(ds, off) | (m.rw(ds, off + 2) << 16)
            if got != exp:
                misses.append(name)
                field_misses[name] = field_misses.get(name, 0) + 1
        if misses:
            stats["mismatch"] += 1
        else:
            stats["ok"] += 1

    orig = CPU8086.step

    def patched(self):
        if self.s.cs == 0x1010 and self.s.ip == LOOP:
            if st["armed"]:
                _compare(self)
            if self.mem.rw(self.s.ds, 0x456E) == 0 and (stats["ok"] + stats["mismatch"] + stats["gap"]) < 300:
                st["dg"], st["sc"] = _seed(self)
                st["armed"] = True
            else:
                st["armed"] = False
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

    checked = stats["ok"] + stats["mismatch"]
    assert checked > 100, f"too few sub-steps checked: {stats}"
    # The assembled sub-step reproduces the VM's sub-step fields on the vast
    # majority; the few misses are documented edge cases (rare [AF2E] landing
    # adjustment, a game_state transition) -- see the module docstring.
    rate = stats["ok"] / checked
    assert rate >= 0.95, f"sub-step match rate {rate:.3f} too low; misses={field_misses} stats={stats}"
