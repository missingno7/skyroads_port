"""Byte-exact oracle for the recovered master timer ISR (skyroads/handrecovered/timer_isr.py).

The island was produced by the automatic lifter and refactored into named code;
this test is the contract that keeps the refactor honest. For every prescaler
value (and both song states) it sets up the ISR pre-state on a snapshot clone,
runs the recovered hook, then interprets the ORIGINAL 1010:3B17 from the same
pre-state to the hook's own resulting CS:IP and diffs the full machine state.
Driving all prescaler values + song states exercises every basic block.

Skips when assets/ or the fixture snapshot is missing (CI has no game files).
"""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXE = ROOT / "assets" / "SKYROADS.EXE"
SNAP = ROOT / "artifacts" / "snapshot_after_keyqueue_push"

if not EXE.is_file() or not (SNAP / "memory_1mb.bin").is_file():
    pytest.skip("needs assets/SKYROADS.EXE + the fixture snapshot", allow_module_level=True)

from dos_re.repro_artifacts import clone_runtime_state  # noqa: E402
from dos_re.snapshot import load_snapshot  # noqa: E402

from skyroads.hooks import master_timer_isr  # noqa: E402  (the VM hook)
from skyroads.handrecovered.timer_isr import advance_music_timer  # noqa: E402  (the pure rule)

CS = 0x1010
ISR = 0x3B17
RET = (0x2222, 0xBEEF)


@pytest.fixture(scope="module")
def base():
    rt = load_snapshot(EXE, SNAP, game_root=ROOT / "assets")
    rt.cpu.trace_enabled = False
    return rt


def _fresh(base, prescaler, *, song_ends):
    rt = clone_runtime_state(base)
    cpu = rt.cpu
    cpu.trace_enabled = False
    cpu.replacement_hooks.clear()
    cpu.hook_names.clear()
    cpu.hook_verifier = None
    ds = cpu.mem.rw(CS, 0x3ACA)
    cpu.mem.wb(ds, 0x3192, prescaler)
    if song_ends:
        cpu.mem.ww(ds, cpu.mem.rw(ds, 0x0BD0), 0)   # song word 0 -> the end branch
    # INT-entry frame with a sentinel return, on a scratch stack.
    cpu.s.cs, cpu.s.ip = CS, ISR
    cpu.s.ss, cpu.s.sp = 0x0000, 0x0800
    cpu.push(cpu.s.flags)
    cpu.push(RET[0])
    cpu.push(RET[1])
    return rt


@pytest.mark.parametrize("prescaler", range(0, 10))
@pytest.mark.parametrize("song_ends", [False, True])
def test_recovered_isr_matches_asm(base, prescaler, song_ends):
    hook_rt = _fresh(base, prescaler, song_ends=song_ends)
    master_timer_isr(hook_rt.cpu)
    target = (hook_rt.cpu.s.cs & 0xFFFF, hook_rt.cpu.s.ip & 0xFFFF)

    asm_rt = _fresh(base, prescaler, song_ends=song_ends)
    asm = asm_rt.cpu
    for _ in range(200_000):
        if (asm.s.cs & 0xFFFF, asm.s.ip & 0xFFFF) == target:
            break
        asm.step()
    else:
        pytest.fail(f"ASM never reached the hook's continuation {target}")

    assert asm.s.snapshot() == hook_rt.cpu.s.snapshot(), \
        f"registers/flags differ (prescaler={prescaler}, song_ends={song_ends})"
    assert asm.mem.data == hook_rt.cpu.mem.data, \
        f"memory differs (prescaler={prescaler}, song_ends={song_ends})"


def test_pure_rule_contract():
    """The recovered pure rule, independent of the VM (no assets needed for this
    one — it runs even in CI, unlike the byte-exact hook tests above)."""
    # music emitted only on prescaler 5 and 0
    assert advance_music_timer(5, 0x100).emit_note
    assert advance_music_timer(0, 0x100).emit_note
    assert not advance_music_timer(3, 0x100).emit_note
    # song word 0 = stream ended: don't advance the cursor
    assert advance_music_timer(5, 0x40).advance_cursor
    assert not advance_music_timer(5, 0x00).advance_cursor
    # divisor is note_word + 2
    assert advance_music_timer(5, 0x40).pit_divisor == 0x42
    # prescaler 0 wraps -> reset to 9 + chain BIOS; others just decrement
    zero = advance_music_timer(0, 0)
    assert zero.chain_to_bios and zero.next_prescaler == 0x09
    assert not advance_music_timer(4, 0).chain_to_bios
    assert advance_music_timer(4, 0).next_prescaler == 3


def test_prescaler_zero_chains_to_bios_and_others_iret(base):
    # prescaler 0 wraps -> resets to 9 and chains to the BIOS timer ISR;
    # 1..9 send EOI and IRET to the sentinel return.
    z = _fresh(base, 0, song_ends=False)
    master_timer_isr(z.cpu)
    assert (z.cpu.s.cs, z.cpu.s.ip) == (0xF000, 0xFF53)
    ds = z.cpu.mem.rw(CS, 0x3ACA)
    assert z.cpu.mem.rb(ds, 0x3192) == 0x09       # prescaler reset

    one = _fresh(base, 1, song_ends=False)
    master_timer_isr(one.cpu)
    assert (one.cpu.s.cs, one.cpu.s.ip) == RET     # IRET to sentinel
