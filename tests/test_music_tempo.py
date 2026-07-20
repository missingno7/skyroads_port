"""The music tempo matches the VM, PROVEN from the VM's own timing.

The OPL sequencer's tempo is set by how often `1010:5A55` (the music ISR) runs
per second. That rate is the PIT channel-0 timer frequency: the game programs
the PIT to a reload of 6628 -> 1193182/6628 = 180 Hz, and 5A55 fires once per
timer IRQ. So the song must advance 180 `Engine.run_tick()` calls per second.

The runner presents at ``GAME_FPS`` and delivers ``TIMER_IRQS_PER_FRAME`` timer
IRQs per frame, so its song rate is the product. This measures the VM's real
timer rate + ISR cadence and asserts the runner reproduces it (regression for
the 2026-07-13 "music plays slower than it should" bug, when it was 35 fps x 2
= 70 Hz -- 2.6x too slow).

The generated VMless provider carries the shared two timing constants. The
fact under test is the game's 180 Hz timing, independent of composition.
"""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXE = ROOT / "assets" / "SKYROADS.EXE"
REPLAY = ROOT / "artifacts" / "replays" / "replay_cold_20260711_201855"

pytestmark = pytest.mark.skipif(
    not (EXE.exists() and REPLAY.exists()),
    reason="needs SKYROADS.EXE + the multi-level cold replay",
)

PIT_INPUT_HZ = 1193182.0


def test_music_rate_matches_the_vm_timer() -> None:
    import sys
    sys.path.insert(0, str(ROOT / "scripts"))
    from skyroads import vmless_backend

    import scripts.play as sp
    from dos_re import player
    from dos_re.cpu import CPU8086, HaltExecution
    from dos_re.dos import ConsoleInputWouldBlock
    from dos_re.replay_input import RealModeInputAdapter
    from dos_re.replay import ReplayArtifact
    from dos_re.snapshot import apply_runtime_continuation
    from skyroads.replay import recording_base

    frontend = sp.SkyroadsFrontend(ROOT)
    args = player.build_arg_parser(frontend).parse_args(
        ["--play-replay", str(REPLAY), "--headless", "--composition", "oracle"])
    artifact = ReplayArtifact.open(REPLAY)
    frontend.apply_replay_metadata(args, artifact.metadata)
    args.execution_plan = frontend.resolve_execution_plan(args)
    rt = frontend.create_runtime(args)
    apply_runtime_continuation(rt, recording_base(artifact))
    inputs = RealModeInputAdapter(artifact.events)
    rt.dos.console_input_fallback = None
    mem = rt.mem if hasattr(rt, "mem") else rt.cpu.mem

    isr = {"n": 0}
    orig = CPU8086.step

    def patched(self):
        if self.s.cs == 0x1010 and self.s.ip == 0x5A55:
            isr["n"] += 1
        return orig(self)

    CPU8086.step = patched
    per_frame_isr = []
    reloads = []
    try:
        frame = 0
        while frame < artifact.end_point.ordinal and frame <= 400:
            before = isr["n"]
            inputs.apply_to_runtime(
                frame, rt, deliver=lambda r, sc: frontend.deliver_input(r, sc))
            try:
                frontend.advance_frame(rt, args, frame)
            except ConsoleInputWouldBlock:
                pass
            except HaltExecution:
                break
            per_frame_isr.append(isr["n"] - before)
            reloads.append(rt.dos.pit_channel0_reload or 0x10000)
            frame += 1
    finally:
        CPU8086.step = orig

    # steady-state gameplay (skip the boot/menu warm-up)
    ss_isr = per_frame_isr[250:400]
    ss_reload = reloads[250:400]
    assert ss_isr, "no steady-state frames captured"

    # (1) The timer runs at 180 Hz (PIT reload 6628), and the music ISR fires
    #     6x per displayed frame -> the game is 30 fps, the sequencer 180 Hz.
    reload = max(set(ss_reload), key=ss_reload.count)
    timer_hz = PIT_INPUT_HZ / reload
    isr_per_frame = max(set(ss_isr), key=ss_isr.count)
    assert abs(timer_hz - 180.0) < 1.0, f"VM timer {timer_hz:.1f} Hz (expected 180)"
    assert isr_per_frame == 6, f"VM music ISR {isr_per_frame}x/frame (expected 6)"

    # (2) the runner's song-advance rate reproduces that 180 Hz exactly.
    runner_hz = vmless_backend.GAME_FPS * vmless_backend.TIMER_IRQS_PER_FRAME
    assert runner_hz == pytest.approx(timer_hz, abs=1.0), (
        f"runner music rate {runner_hz} Hz "
        f"(GAME_FPS={vmless_backend.GAME_FPS} x TIMER_IRQS_PER_FRAME="
        f"{vmless_backend.TIMER_IRQS_PER_FRAME}) != VM {timer_hz:.1f} Hz")
