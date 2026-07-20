"""The grav-o-meter LCD number -- `1010:1114` (draw_number) over `1010:1073`
(draw_glyph_at), called from `1010:2BC3`. Recovered and verified byte-exact
against the VM over demo_cold_20260713_213510 (run_status.md 2026-07-13):
value = (gravity-3)*100, drawn right-aligned in a 4-digit field from the DGROUP
digit font at 0x16C, each glyph 4x5 with font byte b -> colour (b==0 ? 0 : 0x60+b).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from skyroads.native.hud import grav_value

ROOT = Path(__file__).resolve().parents[1]
EXE = ROOT / "assets" / "SKYROADS.EXE"
DEMO = ROOT / "artifacts" / "demos" / "demo_cold_20260713_213510"


def test_grav_value_formula() -> None:
    """`1010:2BC5`: (gravity-3)*100 as a 16-bit signed multiply."""
    assert grav_value(8) == 500       # the demo level -> "500"
    assert grav_value(3) == 0
    assert grav_value(13) == 1000
    assert grav_value(4) == 100


@pytest.mark.skipif(not (EXE.exists() and DEMO.exists()),
                    reason="needs SKYROADS.EXE + the cold e2e demo")
def test_grav_meter_draw_is_byte_exact_vs_vm() -> None:
    """Drive native `draw_grav_meter` alongside the VM through a gameplay frame
    and assert the grav-o-meter LCD pixels match the VM's VGA plane exactly."""
    import scripts.play as sp
    from dos_re import player
    from dos_re.cpu import HaltExecution
    from dos_re.dos import ConsoleInputWouldBlock
    from tests.replay_support import open_oracle_replay
    from skyroads.native.image import NativeGameImage
    from skyroads.native.state import DATA_SEG
    from skyroads.native.hud import (draw_grav_meter, GRAV_GRAVITY, GRAV_FONT,
                                     GRAV_ROW, GRAV_COL, GRAV_WIDTH, GRAV_PITCH)

    frontend = sp.SkyroadsFrontend(ROOT)
    args = player.build_arg_parser(frontend).parse_args(["--play-demo", str(DEMO), "--headless"])
    pb, rt = open_oracle_replay(frontend, args, DEMO)
    rt.dos.mouse_present = pb.mouse_present_hint

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

    # advance into the level so the VM has drawn the grav-o-meter at least once
    adv(905)

    img = NativeGameImage(bytearray(rt.cpu.mem.data))     # seed = VM memory
    # seed the DGROUP inputs draw_grav_meter reads from the live VM state
    img.ww(DATA_SEG, GRAV_GRAVITY, rt.cpu.mem.rw(rt.cpu.s.ds, GRAV_GRAVITY))
    for k in range(10 * 20):
        img.data[(DATA_SEG << 4) + GRAV_FONT + k] = rt.cpu.mem.rb(rt.cpu.s.ds, GRAV_FONT + k)

    draw_grav_meter(img, DATA_SEG)

    # the full LCD region: 4 digit cells (width*pitch px wide) x 5 rows
    x_lo = GRAV_COL
    x_hi = GRAV_COL + GRAV_WIDTH * GRAV_PITCH
    mism = sum(1 for r in range(GRAV_ROW, GRAV_ROW + 5) for c in range(x_lo, x_hi)
               if img.data[0xA0000 + r * 320 + c] != rt.cpu.mem.data[0xA0000 + r * 320 + c])
    assert mism == 0, f"{mism} grav-o-meter pixels diverged from the VM"
