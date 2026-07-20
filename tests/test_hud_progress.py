"""The level PROGRESS BAR -- `1010:159C` (target column) + `1010:1218` (per-column
fill) -- recovered and verified byte-exact against the VM over
demo_skyroads_L1FULL_20260713_212417 (run_status.md 2026-07-13). The target-
column formula matched 321/321 sub-steps; the per-column fill matched the VM's
plane 0/290 frames over the whole bar; and the level length [41C0] the formula
divides by is len(road)//14 (the VM's `5614` return).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from skyroads.native.hud import progress_target_col

ROOT = Path(__file__).resolve().parents[1]
EXE = ROOT / "assets" / "SKYROADS.EXE"
DEMO = ROOT / "artifacts" / "demos" / "demo_skyroads_L1FULL_20260713_212417"


def test_progress_target_col_matches_the_vm_formula() -> None:
    """Points captured live from the VM at `1010:15CB` (prog32, level_len ->
    target column). L=55 for this level (a 770-byte road, 770//14)."""
    L = 55
    # (prog32, expected target) -- real VM samples spanning empty..clamped.
    cases = [
        (0x30000, 0),           # exactly at the start offset -> column 0
        (1219845, 9), (1678569, 13), (2137293, 17),
        (2585095, 21), (3043819, 25), (3502543, 29),   # last -> the 29 clamp
        (99_000_000, 29),       # far past the end stays clamped at 29
        (0, 0),                 # before the start offset -> clamped to 0
    ]
    for prog32, want in cases:
        assert progress_target_col(prog32, L) == want, hex(prog32)


def test_level_length_is_road_rows() -> None:
    """[41C0] = decompressed road length // 14 (7-UINT16 rows) -- the VM's 5614
    return; the demo level's 770-byte road gives 55."""
    from skyroads.native.level_load import _ROAD_ROW_BYTES
    assert 770 // _ROAD_ROW_BYTES == 55


@pytest.mark.skipif(not (EXE.exists() and DEMO.exists()),
                    reason="needs SKYROADS.EXE + the L1 full demo")
def test_progress_bar_draw_is_byte_exact_vs_vm() -> None:
    """Drive the native `update_progress_bar` alongside the VM over the level and
    assert the bar's pixels match the VM's VGA plane every frame."""
    import scripts.play as sp
    from dos_re import player
    from dos_re.cpu import HaltExecution
    from dos_re.dos import ConsoleInputWouldBlock
    from tests.replay_support import open_oracle_replay
    from skyroads.native.image import NativeGameImage
    from skyroads.native.state import DATA_SEG
    from skyroads.native.hud import update_progress_bar, PROGRESS_SRC, PROGRESS_LEN

    frontend = sp.SkyroadsFrontend(ROOT)
    args = player.build_arg_parser(frontend).parse_args(["--play-demo", str(DEMO), "--headless"])
    pb, rt = open_oracle_replay(frontend, args, DEMO)
    rt.dos.mouse_present = pb.mouse_present_hint

    def vmw(off):
        return rt.cpu.mem.rw(rt.cpu.s.ds, off)

    def adv(n):
        f = 0
        while f < n and not pb.finished(f):
            pb.apply_to_runtime(f, rt, deliver=lambda r, sc: frontend.deliver_input(r, sc))
            try:
                frontend.advance_frame(rt, args, f)
            except ConsoleInputWouldBlock:
                pass
            except HaltExecution:
                break
            f += 1
        return f

    f = adv(110)
    img = NativeGameImage(bytearray(rt.cpu.mem.data))   # seed = VM memory
    bar = [(r, c) for r in range(135, 150) for c in range(42, 72)]
    mism = 0
    while f < 400 and not pb.finished(f):
        for o in (PROGRESS_SRC, PROGRESS_SRC + 2, PROGRESS_LEN):
            img.ww(DATA_SEG, o, vmw(o))
        update_progress_bar(img, DATA_SEG)
        mism += sum(1 for r, c in bar
                    if img.data[0xA0000 + r * 320 + c] != rt.cpu.mem.data[0xA0000 + r * 320 + c])
        pb.apply_to_runtime(f, rt, deliver=lambda r, sc: frontend.deliver_input(r, sc))
        try:
            frontend.advance_frame(rt, args, f)
        except ConsoleInputWouldBlock:
            pass
        except HaltExecution:
            break
        f += 1
    assert mism == 0, f"{mism} progress-bar pixels diverged from the VM"
