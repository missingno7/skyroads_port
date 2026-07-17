"""Real-demo integration proof for skyroads.native.* against the pure ASM
oracle -- NOT re-proving individual islands (advance_ship/decay_bounce/
update_vertical_velocity/dispatch_menu_action are already ASM_MATCHED, see
their own @oracle_link status), but proving the NEW plumbing around them:
NativeGameState.from_vm seeding, GameView field decode/encode, and
native/loop.py's composition order, end to end, against real captured
gameplay.

This is also where a real divergence was FOUND (2026-07-11): composing
decay_bounce + update_vertical_velocity unconditionally every frame predicts
values the real ASM does not produce outside one narrow, directly-verified
envelope (see skyroads.native.gaps.VerticalVelocityGap) -- so this test
checks the field(s) native_gameplay_frame actually COMMITS before each gap,
not a full post-condition, and treats "which gap fired" as part of the
assertion.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import scripts.play as sp
from dos_re import player
from dos_re.cpu import HaltExecution
from dos_re.dos import ConsoleInputWouldBlock
from dos_re.input_demo import InputDemoPlayback
from dos_re.player import _use_real_console_input

from skyroads.bridge.dgroup_view import GameView
from skyroads.native.gaps import JumpGateGap, MovementPhysicsGap, VerticalVelocityGap
from skyroads.native.loop import native_gameplay_frame, native_menu_frame
from skyroads.native.state import NativeGameState
from skyroads.recovered.player import GRAVITY_HEIGHT_GATE

ROOT = Path(__file__).resolve().parents[1]
EXE = ROOT / "assets" / "SKYROADS.EXE"
DEMO = ROOT / "artifacts" / "demos" / "demo_e2e_20260710_132930"
MAX_FRAMES = 900  # enough to collect several samples of each kind; keeps the test fast

pytestmark = pytest.mark.skipif(
    not (EXE.exists() and DEMO.exists()),
    reason="needs SKYROADS.EXE + the E2E demo",
)

SHIP_POS = 0x54AC


def _dword(buf: bytes, off: int) -> int:
    return buf[off] | (buf[off + 1] << 8) | (buf[off + 2] << 16) | (buf[off + 3] << 24)


def _collect_samples():
    """Replay the E2E demo on the pure ASM oracle (no recovered hooks), and
    for each frame capture (before-DGROUP, after-DGROUP, kind) where kind is
    "envelope" / "outside" (gameplay, split on the vertical-velocity gap's
    condition) or "menu-noop" (a level-select frame whose real dispatch left
    game_state/scroll_pos/entered/timers unchanged, so any no-op action code
    reproduces it)."""
    frontend = sp.SkyroadsFrontend(ROOT)
    args = player.build_arg_parser(frontend).parse_args(
        ["--play-demo", str(DEMO), "--headless"])
    pb = InputDemoPlayback.load(str(DEMO))
    frontend.apply_demo_metadata(args, pb.manifest.get("metadata", {}))
    rt = (frontend.create_runtime(args) if pb.is_cold_start
          else frontend.load_snapshot_runtime(args, pb.snapshot_path()))
    args.install_replacements = False  # pure ASM oracle -- nothing here can mask a divergence
    frontend.apply_hook_mode(rt, args)
    _use_real_console_input(rt)

    gameplay, menu = [], []
    envelope_seen = outside_seen = 0
    frame = 0
    while not pb.finished(frame) and frame < MAX_FRAMES:
        pb.apply_to_runtime(frame, rt, deliver=lambda r, sc: frontend.deliver_input(r, sc))
        cpu = rt.cpu
        ds = cpu.s.ds
        before = kind = None
        if ds != 0:
            game_state = cpu.mem.rw(ds, 0x456E)
            ctrl_device = cpu.mem.rb(ds, 0x95F6)
            jump_held = cpu.mem.rb(ds, 0x0BDB) & 0x80
            af2c = cpu.mem.rw(ds, 0xAF2C)
            grounded = cpu.mem.rw(ds, 0x456A)
            in_envelope = grounded == 0 and af2c >= GRAVITY_HEIGHT_GATE
            # ctrl_device selects only WHERE steering input comes from (0=kbd,
            # 2=mouse-mode); the native gameplay sub-step consumes the already-
            # computed input and is device-independent (the movement-pipeline
            # oracle proves it for this same demo, which runs at device 2). Accept
            # either so a faithful mouse-absent replay still yields samples.
            if (game_state == 3 and ctrl_device in (0, 2) and not jump_held
                    and ((in_envelope and envelope_seen < 4) or (not in_envelope and outside_seen < 4))):
                before = bytes(cpu.mem.data[(ds << 4):(ds << 4) + 0x10000])
                kind = "envelope" if in_envelope else "outside"
            elif game_state in (0, 2) and len(menu) < 4:
                before = bytes(cpu.mem.data[(ds << 4):(ds << 4) + 0x10000])
                kind = "menu"

        try:
            frontend.advance_frame(rt, args, frame)
        except ConsoleInputWouldBlock:
            pass
        except HaltExecution:
            break

        if before is not None:
            ds2 = rt.cpu.s.ds
            after = bytes(rt.cpu.mem.data[(ds2 << 4):(ds2 << 4) + 0x10000])
            if kind == "menu":
                unchanged = (before[0x456A:0x4570] == after[0x456A:0x4570]
                             and before[SHIP_POS:SHIP_POS + 4] == after[SHIP_POS:SHIP_POS + 4]
                             and before[0x5494:0x5496] == after[0x5494:0x5496]
                             and before[0xB13C:0xB13E] == after[0xB13C:0xB13E])
                if unchanged:  # only a confirmed no-op frame is a valid sample -- see module docstring
                    menu.append((before, after))
            else:
                gameplay.append((before, after, kind))
                if kind == "envelope":
                    envelope_seen += 1
                else:
                    outside_seen += 1
        frame += 1
        if envelope_seen >= 4 and outside_seen >= 4 and len(menu) >= 4:
            break
    return gameplay, menu


@pytest.fixture(scope="module")
def samples():
    gameplay, menu = _collect_samples()
    assert gameplay, "collected no gameplay samples -- demo/oracle setup broken"
    assert menu, "collected no menu no-op samples -- demo/oracle setup broken"
    return gameplay, menu


def test_native_gameplay_frame_matches_asm_up_to_its_first_gap(samples) -> None:
    gameplay, _ = samples
    for before, after, kind in gameplay:
        view = GameView(NativeGameState(bytearray(before)))
        try:
            native_gameplay_frame(view)
            pytest.fail(f"no gap raised for a real gameplay frame ({kind})")
        except JumpGateGap:
            pytest.fail("JumpGateGap on a frame the collector confirmed had no jump held")
        except VerticalVelocityGap:
            assert kind == "outside", "VerticalVelocityGap raised inside the verified envelope"
        except MovementPhysicsGap:
            assert kind == "envelope", "MovementPhysicsGap should only be reached inside the envelope"

        # ship_pos is committed before every gap this function can raise.
        assert view.ship_pos == _dword(after, SHIP_POS)
        if kind == "envelope":
            # bounce is ALSO committed inside the verified envelope.
            real_bounce = after[0x9336] | (after[0x9337] << 8)
            assert view.bounce == real_bounce


def test_native_menu_frame_noop_matches_asm(samples) -> None:
    _, menu = samples
    for before, after in menu:
        view = GameView(NativeGameState(bytearray(before)))
        native_menu_frame(view, 0)  # any no-op action code (0/1/3) reproduces a confirmed no-op frame
        assert view.game_state == (after[0x456E] | (after[0x456F] << 8))
        assert view.ship_pos == _dword(after, SHIP_POS)
