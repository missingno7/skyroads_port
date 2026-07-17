"""Proof that the native movement PIPELINE is mathematically complete:

    compute_movement_targets  ->  resolve_move(make_visible)

reproduces the real VM's post-move (lateral, af1c, af2c) for real gameplay
frames, end to end. The two halves are each already ASM_MATCHED on their own
(skyroads/recovered/physics.py 682/682, movement.py 1760/1760); this proves
their COMPOSITION -- compute's output feeding resolve_move's target inputs,
with the collision predicate (skyroads/recovered_native/collision.make_visible) bound to
a NativeGameState's DGROUP tables -- against the live oracle.

Why this matters for the native port: it establishes that the lateral/vertical
movement MATH has no remaining gap. The only reason native_gameplay_frame does
not yet call this pipeline is one INPUT to it -- lateral_accel (ds:[4568]) --
which is stateful steering momentum updated mid-frame at 1010:2568 under
jump-latch-gated conditions (60/682 real frames have lateral_accel != steer*29,
e.g. -29 persisting a frame after the steer key released), so it cannot be
derived from frame-start state without recovering that block. See
skyroads.recovered_native.gaps.MovementPhysicsGap.

Captures at IP=2635 (pre-move state + the real target-formula inputs + a DGROUP
snapshot for the collision tables) and IP=26E9 (post-resolve_move axes), then
composes the recovered functions in-process. Live-oracle test -- gated on the
game files, like tests/test_sb_pcm_audio.py's capture test.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import scripts.play as sp
from dos_re import player
from dos_re.cpu import CPU8086, HaltExecution
from dos_re.dos import ConsoleInputWouldBlock
from dos_re.input_demo import InputDemoPlayback
from dos_re.player import _use_real_console_input

from skyroads.recovered_native.collision import make_visible
from skyroads.recovered_native.state import NativeGameState
from skyroads.recovered.movement import resolve_move
from skyroads.recovered.physics import compute_movement_targets

ROOT = Path(__file__).resolve().parents[1]
EXE = ROOT / "assets" / "SKYROADS.EXE"
DEMO = ROOT / "artifacts" / "demos" / "demo_e2e_20260710_132930"

pytestmark = pytest.mark.skipif(
    not (EXE.exists() and DEMO.exists()),
    reason="needs SKYROADS.EXE + the E2E demo",
)

CODE_SEG = 0x1010
IP_PRE = 0x2635    # start of the movement-target computation (post advance_ship/vvel)
IP_POST = 0x26E9   # right after resolve_move (186B) returns
MAX_FRAMES = 1200  # steering starts partway through the demo; go far enough to catch it


def _collect(max_cases: int = 160):
    frontend = sp.SkyroadsFrontend(ROOT)
    args = player.build_arg_parser(frontend).parse_args(
        ["--play-demo", str(DEMO), "--headless"])
    pb = InputDemoPlayback.load(str(DEMO))
    frontend.apply_demo_metadata(args, pb.manifest.get("metadata", {}))
    rt = (frontend.create_runtime(args) if pb.is_cold_start
          else frontend.load_snapshot_runtime(args, pb.snapshot_path()))
    args.install_replacements = False  # pure ASM oracle
    frontend.apply_hook_mode(rt, args)
    _use_real_console_input(rt)

    pending: dict = {}
    cases: list[dict] = []

    def _probe(cpu):
        s = cpu.s
        m = cpu.mem
        ds = s.ds
        if s.ip == IP_PRE:
            pending.clear()
            pending.update(
                dgroup=bytes(m.data[(ds << 4):(ds << 4) + 0x10000]),
                lateral=m.rw(ds, 0x9618) | (m.rw(ds, 0x961A) << 16),
                af1c=m.rw(ds, 0xAF1C),
                af2c=m.rw(ds, 0xAF2C),
                ship_pos=m.rw(ds, 0x54AC) | (m.rw(ds, 0x54AE) << 16),
                vvel=m.rw(ds, 0x9336),
                lateral_accel=m.rw(ds, 0x4568),
                unknown_5496=m.rw(ds, 0x5496),
            )
        elif s.ip == IP_POST and pending:
            pending["post"] = (
                m.rw(ds, 0x9618) | (m.rw(ds, 0x961A) << 16),
                m.rw(ds, 0xAF1C),
                m.rw(ds, 0xAF2C),
            )
            cases.append(dict(pending))
            pending.clear()

    orig = CPU8086.step

    def patched(self):
        if self.s.cs == CODE_SEG and self.s.ip in (IP_PRE, IP_POST):
            _probe(self)
        return orig(self)

    CPU8086.step = patched
    try:
        frame = 0
        while not pb.finished(frame) and frame < MAX_FRAMES and len(cases) < max_cases:
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
    return cases


@pytest.fixture(scope="module")
def cases():
    got = _collect()
    assert got, "collected no movement-pipeline samples -- demo/oracle setup broken"
    return got


def test_pipeline_reproduces_vm_post_move(cases) -> None:
    for c in cases:
        state = NativeGameState(bytearray(c["dgroup"]))
        tgt = compute_movement_targets(
            c["ship_pos"], c["lateral"], c["af1c"], c["af2c"], c["vvel"],
            c["lateral_accel"], c["unknown_5496"],
        )
        got = resolve_move(
            c["lateral"], c["af1c"], c["af2c"],
            tgt.tgt_lateral, tgt.tgt_af1c, tgt.tgt_af2c, make_visible(state.rw),
        )
        assert got == c["post"], (
            f"pipeline diverged: accel={c['lateral_accel']:#06x} "
            f"got={got} expected={c['post']}"
        )


def test_sample_includes_real_steering(cases) -> None:
    # The proof is only meaningful if some captured frames actually steered
    # (lateral_accel != 0 exercises the tgt_af1c multiply, not just a carry).
    steering = [c for c in cases if c["lateral_accel"] != 0]
    assert steering, "no real-steering frames captured -- pipeline proof is weak"
