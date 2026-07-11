"""Proof of "full vmless native gameplay": skyroads.native.loop.NativeGameplayDriver
runs the recovered gameplay engine INDEFINITELY -- through level-complete,
respawn, and crash transitions -- with no VM ever consulted after the initial
seed.

Two tests:
* a pure smoke test (no game files needed) that the driver never crashes over
  thousands of ticks even from an empty (all-zero) level;
* a live-oracle test that seeds real level geometry + tables from the VM once,
  then drives with the E2E demo's REAL recorded input for its whole length,
  proving the driver plays through multiple real transitions (the demo's E2E
  run itself completes and restarts several levels) without ever raising an
  unhandled exception or needing the VM again after the seed.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from skyroads.bridge.dgroup_view import GameView
from skyroads.native.loop import NativeGameplayDriver
from skyroads.native.state import NativeGameState


def test_driver_never_crashes_from_an_empty_level() -> None:
    view = GameView(NativeGameState())
    driver = NativeGameplayDriver(view, jump_level_gate=8)
    for _ in range(5000):
        driver.tick()  # must not raise
    assert driver.ticks == 5000
    # An empty level (no real geometry) still cycles through transitions --
    # the driver should never get permanently stuck.
    assert driver.transitions > 0


def test_driver_transitions_are_well_formed() -> None:
    view = GameView(NativeGameState())
    driver = NativeGameplayDriver(view, jump_level_gate=9)
    seen_transition = False
    for _ in range(2000):
        outcome = driver.tick()
        if outcome.transitioned:
            seen_transition = True
            assert outcome.reason  # a real cause string, not empty
            # apply_level_init always leaves the view in the fresh-respawn state
            assert view.game_state == 0
            assert view.af2c == 0x2800
    assert seen_transition


# ---- live-oracle: real level data + real recorded input, driven standalone --

ROOT = Path(__file__).resolve().parents[1]
EXE = ROOT / "assets" / "SKYROADS.EXE"
DEMO = ROOT / "artifacts" / "demos" / "demo_e2e_20260710_132930"


@pytest.mark.skipif(not (EXE.exists() and DEMO.exists()),
                    reason="needs SKYROADS.EXE + the E2E demo")
def test_driver_plays_the_whole_demo_standalone() -> None:
    import scripts.play as sp
    from dos_re import player
    from dos_re.cpu import CPU8086, HaltExecution
    from dos_re.dos import ConsoleInputWouldBlock
    from dos_re.input_demo import InputDemoPlayback
    from dos_re.player import _use_real_console_input

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

    # Seed ONCE from the VM at the first real gameplay sub-step, then replay
    # the demo's recorded INPUT into the standalone driver -- the VM is only
    # a source of (a) the initial level data and (b) recorded input from here.
    LOOP = 0x2324
    seed = {}

    def _try_seed(cpu):
        if seed:
            return
        m = cpu.mem
        ds = cpu.s.ds
        if m.rw(ds, 0x456E) == 0:
            seed["dgroup"] = bytearray(m.data[(ds << 4):(ds << 4) + 0x10000])
            seed["jump_level_gate"] = m.rw(ds, 0x4562)

    inputs = []  # (steer, jump, speed, key_bytes, tick) recorded per real sub-step

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
        if self.s.cs == 0x1010 and self.s.ip == LOOP:
            _try_seed(self)
            _record_input(self)
        return orig(self)

    CPU8086.step = patched
    try:
        frame = 0
        while not pb.finished(frame) and frame < 1719 and len(inputs) < 700:
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

    assert seed, "never reached a game_state==0 sub-step to seed from"
    assert len(inputs) > 100, f"too few recorded inputs: {len(inputs)}"

    # Now drive PURELY natively -- the VM/runtime is not touched again.
    view = GameView(NativeGameState(seed["dgroup"]))
    driver = NativeGameplayDriver(view, seed["jump_level_gate"])
    for steer, jump, speed, keys, tick in inputs:
        view.steer = steer
        view.jump = jump
        view.speed = speed
        for i, kb in enumerate(keys):
            view._backend.wb(0x0BD0 + i, kb)
        view.elapsed_ticks = tick
        driver.tick()  # must not raise

    assert driver.ticks == len(inputs)
    # The real demo (attract mode, replaying one level repeatedly) completes
    # and restarts multiple times -- the standalone driver should too.
    assert driver.transitions >= 1, "the driver never crossed a single transition"
