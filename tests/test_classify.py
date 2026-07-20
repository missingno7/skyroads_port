"""Verify the recovered perspective classification (1010:2324-23BF):

* the pure logic (skyroads.handrecovered.classify.classify_perspective) on
  hand-written cases covering each branch, and
* the authored state-view path (skyroads.native.classify.classify_ship, computing the
  perspective word via renderer.perspective_row_offset + a DGROUP read) against
  the live ASM oracle over the real E2E replay -- 682/682 frames byte-exact on
  (class_skip, bp16, class_zero).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from skyroads.handrecovered.classify import CLASS_HEIGHT_GATE, classify_perspective


# ---- pure-logic unit tests (no game files needed) --------------------------

def test_class_zero_is_perspective_word_zero() -> None:
    r = classify_perspective(0, af2c=0, bp12=0, class_skip_prev=0,
                             read_seg_table=lambda i: 0)
    assert r.class_zero == 1
    r = classify_perspective(5, af2c=0, bp12=0, class_skip_prev=0,
                             read_seg_table=lambda i: 0)
    assert r.class_zero == 0


def test_bp12_zero_persists_class_skip_and_skips_1b49() -> None:
    r = classify_perspective(0x0800, af2c=0x3000, bp12=0, class_skip_prev=1,
                             read_seg_table=lambda i: pytest.fail("table read on bp12==0"))
    assert r.class_skip == 1          # persisted from class_skip_prev
    assert r.bp16 == 0
    assert r.calls_1b49 is False


def test_bp12_set_low_gate_uses_raw_word_nibble() -> None:
    # af2c <= gate: the word is used unreduced; low nibble 8 -> class_skip.
    r = classify_perspective(0x0038, af2c=CLASS_HEIGHT_GATE, bp12=1,
                             class_skip_prev=0, read_seg_table=lambda i: 0)
    assert r.class_skip == 1
    assert r.calls_1b49 is True


def test_bp12_set_high_gate_table_match_reduces_word() -> None:
    # af2c > gate and table[word>>8] == af2c -> word >>= 4, then nibble test.
    # word=0x0080 -> >>4 = 0x0008 -> low nibble 8 -> class_skip.
    r = classify_perspective(0x0080, af2c=0x3000, bp12=1, class_skip_prev=0,
                             read_seg_table=lambda i: 0x3000 if i == 0 else 0)
    assert r.reduced_word == 0x0008
    assert r.class_skip == 1


def test_bp12_set_high_gate_table_mismatch_zeros_word() -> None:
    r = classify_perspective(0x0088, af2c=0x3000, bp12=1, class_skip_prev=1,
                             read_seg_table=lambda i: 0x9999)  # != af2c
    assert r.reduced_word == 0
    assert r.class_skip == 0  # recomputed to 0, NOT the prev 1 (bp12 != 0)
    assert r.class_zero == 0


def test_bp16_low_nibble_two() -> None:
    r = classify_perspective(0x0002, af2c=CLASS_HEIGHT_GATE, bp12=1,
                             class_skip_prev=0, read_seg_table=lambda i: 0)
    assert r.bp16 == 1


# ---- live-oracle test: authored state-view path vs the real ASM ------------

ROOT = Path(__file__).resolve().parents[1]
EXE = ROOT / "assets" / "SKYROADS.EXE"
REPLAY = ROOT / "artifacts" / "replays" / "replay_e2e_20260710_132930"

_live = pytest.mark.skipif(
    not (EXE.exists() and REPLAY.exists()),
    reason="needs SKYROADS.EXE + the E2E replay",
)


@_live
def test_native_classify_matches_asm_over_replay() -> None:
    import scripts.play as sp
    from dos_re import player
    from dos_re.cpu import CPU8086, HaltExecution
    from dos_re.dos import ConsoleInputWouldBlock
    from tests.replay_support import open_oracle_replay

    from skyroads.native.classify import classify_ship
    from skyroads.native.state import NativeGameState

    frontend = sp.SkyroadsFrontend(ROOT)
    args = player.build_arg_parser(frontend).parse_args(
        ["--play-replay", str(REPLAY), "--headless"])
    pb, rt = open_oracle_replay(frontend, args, REPLAY)

    IP_IN, IP_OUT = 0x2324, 0x23CA
    pending: dict = {}
    checked = [0]

    def _probe(cpu):
        s = cpu.s
        m = cpu.mem
        ds = s.ds
        bp = s.bp
        if s.ip == IP_IN:
            pending.clear()
            pending.update(
                state=NativeGameState(bytearray(m.data[(ds << 4):(ds << 4) + 0x10000])),
                bp12=m.rw(s.ss, (bp - 12) & 0xFFFF),
                skip_prev=m.rw(s.ss, (bp - 14) & 0xFFFF),
                lateral=m.rw(ds, 0x9618) | (m.rw(ds, 0x961A) << 16),
                af1c=m.rw(ds, 0xAF1C), af2c=m.rw(ds, 0xAF2C),
            )
        elif s.ip == IP_OUT and pending:
            r = classify_ship(pending["state"].rw, pending["lateral"],
                              pending["af1c"], pending["af2c"],
                              pending["bp12"], pending["skip_prev"])
            assert r.class_skip == m.rw(s.ss, (bp - 14) & 0xFFFF)
            assert r.bp16 == m.rw(s.ss, (bp - 16) & 0xFFFF)
            assert r.class_zero == m.rw(s.ss, (bp - 18) & 0xFFFF)
            checked[0] += 1
            pending.clear()

    orig = CPU8086.step

    def patched(self):
        if self.s.cs == 0x1010 and self.s.ip in (IP_IN, IP_OUT):
            _probe(self)
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

    assert checked[0] > 100, f"only {checked[0]} classification frames checked"
