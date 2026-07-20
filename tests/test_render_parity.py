"""Frame-accurate render parity: the native full-frame road render matches the
VM's own VGA output frame-by-frame over a replay.

The 2026-07-13 user report ("edge terrain ghosting", "gauges not filled") flew
past the existing per-piece tests because nothing checked the INTEGRATED
rendered frame against the VM. This test closes that: it replays a replay and, at
several frames, renders the native pipeline (``render_native_frame(rebuild=
True)`` -- exactly what the native renderer does) from the VM's own live DGROUP
and diffs the resulting VGA road band against the VM's.

The native full-frame render (`rebuild=True`) must be
BYTE-EXACT to the VM's own road band. This held after the 2026-07-13
screen_row fix (the ship was drawing one row too low in offscreen mode --
`screen_row` used `af2c_eff` instead of raw `af2c`; see render_params.py):
the per-frame diff went from ~200-350 px around the ship to ZERO. The edge
TERRAIN ghosting the user first reported lived only in the delta/skip render
path, which the native renderer avoids by rendering full every frame -- so a full
render is ghosting-free AND (now) pixel-exact.
"""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXE = ROOT / "assets" / "SKYROADS.EXE"
REPLAY = ROOT / "artifacts" / "replays" / "replay_skyroads_20260713_103107"

pytestmark = pytest.mark.skipif(
    not (EXE.exists() and REPLAY.exists()),
    reason="needs SKYROADS.EXE + the level-5 render replay",
)

ROAD_BAND_ROWS = 131          # rows 0..130 (the sky + road band, above the dashboard)
CHECK_FRAMES = {90, 140, 200}
#: BYTE-EXACT: the native full render reproduces the VM's road band exactly
#: (0 diffs on every checked frame after the screen_row fix). A tiny non-zero
#: tolerance would only hide a regression, so this is 0.
MAX_ROAD_BAND_DIFFS = 0


def test_native_full_render_matches_vm_no_edge_ghosting() -> None:
    import numpy as np

    import scripts.play as sp
    from dos_re import player
    from dos_re.dos import ConsoleInputWouldBlock
    from dos_re.cpu import HaltExecution
    from tests.replay_support import open_oracle_replay

    from skyroads.native.frame import render_native_frame
    from skyroads.native.image import NativeGameImage
    from skyroads.native.state import DATA_SEG

    frontend = sp.SkyroadsFrontend(ROOT)
    args = player.build_arg_parser(frontend).parse_args(
        ["--play-replay", str(REPLAY), "--headless"])
    pb, rt = open_oracle_replay(frontend, args, REPLAY)
    mem = rt.mem if hasattr(rt, "mem") else rt.cpu.mem

    checked = 0
    frame = 0
    end = pb.manifest.get("end_boundary", 271)
    while not pb.finished(frame) and frame <= end:
        pb.apply_to_runtime(frame, rt, deliver=lambda r, sc: frontend.deliver_input(r, sc))
        try:
            frontend.advance_frame(rt, args, frame)
        except ConsoleInputWouldBlock:
            pass
        except HaltExecution:
            break
        if frame in CHECK_FRAMES:
            vm = np.frombuffer(bytes(mem.data[0xA0000:0xA0000 + 64000]),
                               dtype=np.uint8).reshape(200, 320)
            nimg = NativeGameImage(bytearray(mem.data[:0x100000]))
            render_native_frame(nimg, DATA_SEG, offscreen=1, rebuild=True)
            nat = np.frombuffer(bytes(nimg.data[0xA0000:0xA0000 + 64000]),
                                dtype=np.uint8).reshape(200, 320)
            diff = (vm[:ROAD_BAND_ROWS] != nat[:ROAD_BAND_ROWS])
            n = int(diff.sum())
            assert n <= MAX_ROAD_BAND_DIFFS, \
                f"frame {frame}: {n} road-band pixel diffs (> {MAX_ROAD_BAND_DIFFS})"
            checked += 1
        frame += 1

    assert checked == len(CHECK_FRAMES), f"only checked {checked}/{len(CHECK_FRAMES)} frames"
