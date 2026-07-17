"""Verify the recovered post-move collision response
(skyroads.handrecovered.collision_response) -- so far the vertical centering scan
(1010:2963-2A24) -- with pure-logic unit tests plus a live-oracle test that
computes every 1732 probe through the real DGROUP tables (314/314 vs the ASM).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from skyroads.handrecovered.collision_response import (
    CENTER_NUDGE,
    CONTACT_BRAKE,
    LATERAL_BUMP_STEP,
    SCAN_MAX_CELLS,
    af1c_contact_fixup,
    lateral_wall_bump,
    resolve_landing,
    resolve_lateral_crash,
    vertical_center_nudge,
)
from skyroads.handrecovered.collision_response import fell_off_segment, ship_fell_off
from skyroads.handrecovered.dynamics import JumpScratch


# ---- ship_fell_off (fall predicate) unit tests -----------------------------

def test_fell_off_invalid_segment_never_falls() -> None:
    # persp_word nibble not in {0x100,0x300,0x500} -> 0 (no valid road segment).
    assert ship_fell_off(0x0000, af1c=0x8000, af2c=0x3000, seg_low=0, seg_high=0) == 0
    assert ship_fell_off(0x0200, af1c=0x8000, af2c=0x3000, seg_low=0, seg_high=0) == 0


def test_fell_off_row_below_midpoint_is_a_fall() -> None:
    # valid segment (nibble 0x100); row = (af2c-0x2200)/128 < mid -> fell (1).
    # af2c=0x2300 -> row=(0x100)/128=2; seg_low+seg_high large -> mid big -> 2<mid.
    assert ship_fell_off(0x0100, af1c=0x8000, af2c=0x2300, seg_low=0x40, seg_high=0x40) == 1


def test_fell_off_row_at_or_above_midpoint_is_safe() -> None:
    # row >= mid -> on road (0). mid = (0+0)/2 = 0; any row >= 0 -> 0.
    assert ship_fell_off(0x0100, af1c=0x8000, af2c=0x3000, seg_low=0, seg_high=0) == 0


def test_fell_off_segment_is_minus_one_or_in_range() -> None:
    # fell_off_segment returns -1 (out of range) or a valid mirrored index 0..0x25.
    segs = {fell_off_segment(a) for a in range(0, 0x10000, 0x40)}
    assert all(s == -1 or 0 <= s <= 0x25 for s in segs), sorted(segs)[:5]


# ---- resolve_lateral_crash unit tests --------------------------------------

def test_no_crash_when_lateral_reached_target() -> None:
    r = resolve_lateral_crash(cur_lateral=0x1234, tgt_lateral=0x1234,
                              ship_pos=0x2000, f456a=0, game_state=3)
    assert r.crashed is False
    assert (r.ship_pos, r.f456a, r.game_state) == (0x2000, 0, 3)


def test_crash_resets_ship_pos_to_zero() -> None:
    r = resolve_lateral_crash(cur_lateral=1, tgt_lateral=5, ship_pos=0x2000,
                              f456a=0, game_state=3)
    assert r.crashed is True
    assert r.ship_pos == 0


def test_crash_past_gate_flags_state_when_transitional() -> None:
    r = resolve_lateral_crash(cur_lateral=1, tgt_lateral=5, ship_pos=0x1C5C,
                              f456a=0, game_state=0)
    assert r.f456a == 1
    assert r.game_state == 1     # 0 -> 1


def test_crash_past_gate_keeps_nonzero_state() -> None:
    r = resolve_lateral_crash(cur_lateral=1, tgt_lateral=5, ship_pos=0x1D48,
                              f456a=0, game_state=3)
    assert r.f456a == 1
    assert r.game_state == 3     # not overwritten


def test_crash_before_gate_does_not_flag() -> None:
    r = resolve_lateral_crash(cur_lateral=1, tgt_lateral=5, ship_pos=0x0500,
                              f456a=0, game_state=0)
    assert r.ship_pos == 0       # still restarts
    assert r.f456a == 0          # but no flag before the gate
    assert r.game_state == 0


# ---- resolve_landing unit tests --------------------------------------------

def test_landing_when_descending_off_target_clears_latches() -> None:
    r = resolve_landing(JumpScratch(jumping=1, jump_start_y=0x2800, effect_latch=1),
                        tgt_af2c=0x2000, af2c=0x2800, bounce=0xFF00,  # bounce<0
                        af2e=0, af30=0, f455a=5, ship_pos=0x1000)
    assert r.landed is True
    assert r.scratch.jumping == 0 and r.scratch.effect_latch == 0
    assert r.scratch.jump_start_y == 0x2800   # preserved
    assert r.gameplay_active == 1
    assert r.f455a == 0
    assert r.ship_pos == 0x1000                # af2e/af30 zero -> no back-off


def test_no_landing_when_at_vertical_target() -> None:
    r = resolve_landing(JumpScratch(1, 0x2800, 1), tgt_af2c=0x2800, af2c=0x2800,
                        bounce=0xFF00, af2e=0, af30=0, f455a=5, ship_pos=0x1000)
    assert r.landed is False
    assert r.gameplay_active == 0
    assert r.scratch.jumping == 1              # unchanged
    assert r.f455a == 5 and r.ship_pos == 0x1000


def test_no_landing_when_ascending() -> None:
    r = resolve_landing(JumpScratch(1, 0x2800, 1), tgt_af2c=0x2000, af2c=0x2800,
                        bounce=0x0100, af2e=0, af30=0, f455a=5, ship_pos=0x1000)
    assert r.landed is False
    assert r.gameplay_active == 0


def test_landing_backs_off_and_clamps_ship_pos() -> None:
    r = resolve_landing(JumpScratch(1, 0, 0), tgt_af2c=0x2000, af2c=0x2800,
                        bounce=0xFF00, af2e=0x0050, af30=0, f455a=0, ship_pos=0x0030)
    assert r.ship_pos == 0  # 0x30 - 0x50 < 0 -> clamped


# ---- lateral_wall_bump unit tests ------------------------------------------

def test_wall_bump_noop_when_lateral_reached_target() -> None:
    # cur_lateral == tgt_lateral -> no bump regardless of blocking.
    af1c, tgt = lateral_wall_bump(lambda *a: 1, cur_lateral=5, tgt_lateral=5,
                                  af1c=0x8000, tgt_af1c=0x8000, af2c=0x3000)
    assert (af1c, tgt) == (0x8000, 5)


def test_wall_bump_noop_when_af1c_not_at_target() -> None:
    af1c, tgt = lateral_wall_bump(lambda *a: 1, cur_lateral=1, tgt_lateral=5,
                                  af1c=0x8000, tgt_af1c=0x7000, af2c=0x3000)
    assert (af1c, tgt) == (0x8000, 5)


def test_wall_bump_noop_when_target_cell_clear() -> None:
    af1c, tgt = lateral_wall_bump(lambda *a: 0, cur_lateral=1, tgt_lateral=5,
                                  af1c=0x8000, tgt_af1c=0x8000, af2c=0x3000)
    assert (af1c, tgt) == (0x8000, 5)


def test_wall_bump_slips_down_first() -> None:
    # target blocked; the cell below (af1c-0x3A0) is clear -> move there, snap tgt.
    def visible(lat, depth, sy):
        return 0 if depth == (0x8000 - LATERAL_BUMP_STEP) else 1
    af1c, tgt = lateral_wall_bump(visible, cur_lateral=1, tgt_lateral=5,
                                  af1c=0x8000, tgt_af1c=0x8000, af2c=0x3000)
    assert af1c == 0x8000 - LATERAL_BUMP_STEP
    assert tgt == 1  # tgt_lateral snapped to cur_lateral


def test_wall_bump_slips_up_when_below_blocked() -> None:
    def visible(lat, depth, sy):
        return 0 if depth == (0x8000 + LATERAL_BUMP_STEP) else 1
    af1c, tgt = lateral_wall_bump(visible, cur_lateral=1, tgt_lateral=5,
                                  af1c=0x8000, tgt_af1c=0x8000, af2c=0x3000)
    assert af1c == 0x8000 + LATERAL_BUMP_STEP
    assert tgt == 1


# ---- af1c_contact_fixup unit tests -----------------------------------------

def test_contact_fixup_noop_when_af1c_reached() -> None:
    assert af1c_contact_fixup(0x8000, 0x8000, cur_5496=50, lateral_accel=7,
                              ship_pos=0x1000) == (7, 50, 0x1000)


def test_contact_fixup_brakes_and_clears_accel() -> None:
    accel, c5496, pos = af1c_contact_fixup(0x8000, 0x7000, cur_5496=0,
                                           lateral_accel=99, ship_pos=0x1000)
    assert accel == 0
    assert pos == 0x1000 - CONTACT_BRAKE


def test_contact_fixup_zeroes_5496_when_sign_agrees_upward() -> None:
    # cur_5496 > 0 and tgt_af1c > af1c -> zero it.
    _, c5496, _ = af1c_contact_fixup(0x7000, 0x8000, cur_5496=40,
                                     lateral_accel=0, ship_pos=0x1000)
    assert c5496 == 0


def test_contact_fixup_keeps_5496_when_sign_disagrees() -> None:
    # cur_5496 > 0 but tgt_af1c < af1c -> keep it.
    _, c5496, _ = af1c_contact_fixup(0x8000, 0x7000, cur_5496=40,
                                     lateral_accel=0, ship_pos=0x1000)
    assert c5496 == 40


def test_contact_fixup_clamps_ship_pos_at_zero() -> None:
    _, _, pos = af1c_contact_fixup(0x8000, 0x7000, cur_5496=0, lateral_accel=0,
                                   ship_pos=0x50)  # 0x50 - 0x97 < 0 -> clamp
    assert pos == 0


# ---- pure-logic unit tests -------------------------------------------------

def test_clear_above_only_nudges_positive() -> None:
    # first cell above clear, everything below blocked -> net +1
    def visible(lat, depth, sy):
        return 0 if depth > 0x8000 else 1  # above ships's af1c=0x8000 is "clear"
    got = vertical_center_nudge(visible, lateral=0, af1c=0x8000, af2c=0x3000, cur_5496=100)
    assert got == 100 + CENTER_NUDGE


def test_clear_below_only_nudges_negative() -> None:
    def visible(lat, depth, sy):
        return 0 if depth < 0x8000 else 1  # below is "clear"
    got = vertical_center_nudge(visible, lateral=0, af1c=0x8000, af2c=0x3000, cur_5496=100)
    assert got == 100 - CENTER_NUDGE


def test_clear_both_sides_nets_zero_and_zeroes_5496() -> None:
    got = vertical_center_nudge(lambda *a: 0, lateral=0, af1c=0x8000, af2c=0x3000, cur_5496=999)
    assert got == 0  # net 0 -> the ASM zeroes [5496]


def test_blocked_both_sides_nets_zero_and_zeroes_5496() -> None:
    got = vertical_center_nudge(lambda *a: 1, lateral=0, af1c=0x8000, af2c=0x3000, cur_5496=999)
    assert got == 0


def test_probes_screen_y_is_af2c_minus_one() -> None:
    seen = []

    def visible(lat, depth, sy):
        seen.append(sy)
        return 1

    vertical_center_nudge(visible, lateral=0, af1c=0x8000, af2c=0x3000, cur_5496=0)
    assert seen and all(sy == 0x2FFF for sy in seen)
    assert len(seen) == 2 * SCAN_MAX_CELLS  # all blocked -> full scan both ways


# ---- live-oracle test: full path vs the real ASM ---------------------------

ROOT = Path(__file__).resolve().parents[1]
EXE = ROOT / "assets" / "SKYROADS.EXE"
DEMO = ROOT / "artifacts" / "demos" / "demo_menu_3levels_20260713_144256"


@pytest.mark.skipif(not (EXE.exists() and DEMO.exists()),
                    reason="needs SKYROADS.EXE + the E2E demo")
def test_native_vertical_scan_matches_asm_over_demo() -> None:
    import scripts.play as sp
    from dos_re import player
    from dos_re.cpu import CPU8086, HaltExecution
    from dos_re.dos import ConsoleInputWouldBlock
    from dos_re.input_demo import InputDemoPlayback
    from dos_re.player import _use_real_console_input

    from skyroads.native.collision import make_visible
    from skyroads.native.state import NativeGameState

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

    IP_IN, IP_OUT = 0x2963, 0x2A24
    pending: dict = {}
    checked = [0]

    def _probe(cpu):
        s = cpu.s
        m = cpu.mem
        ds = s.ds
        if s.ip == IP_IN:
            pending.clear()
            pending.update(
                state=NativeGameState(bytearray(m.data[(ds << 4):(ds << 4) + 0x10000])),
                cur=m.rw(ds, 0x5496),
                lateral=m.rw(ds, 0x9618) | (m.rw(ds, 0x961A) << 16),
                af1c=m.rw(ds, 0xAF1C), af2c=m.rw(ds, 0xAF2C),
            )
        elif s.ip == IP_OUT and pending:
            got = vertical_center_nudge(
                make_visible(pending["state"].rw), pending["lateral"],
                pending["af1c"], pending["af2c"], pending["cur"])
            assert got == m.rw(ds, 0x5496)
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
        while not pb.finished(frame) and frame < 1906:
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

    assert checked[0] > 50, f"only {checked[0]} scan frames checked"


# A demo that actually exercises the wall-bump (274B) and the af1c-contact
# fix-up (283C with af1c != tgt) -- the E2E demo is a clean run that never does.
COLLISION_DEMO = ROOT / "artifacts" / "demos" / "demo_skyroads_20260710_213019"


@pytest.mark.skipif(not (EXE.exists() and COLLISION_DEMO.exists()),
                    reason="needs SKYROADS.EXE + a collision demo")
def test_wall_bump_and_contact_fixup_match_asm_over_collision_demo() -> None:
    import scripts.play as sp
    from dos_re import player
    from dos_re.cpu import CPU8086, HaltExecution
    from dos_re.dos import ConsoleInputWouldBlock
    from dos_re.input_demo import InputDemoPlayback
    from dos_re.player import _use_real_console_input

    from skyroads.native.collision import make_visible
    from skyroads.native.state import NativeGameState

    frontend = sp.SkyroadsFrontend(ROOT)
    args = player.build_arg_parser(frontend).parse_args(
        ["--play-demo", str(COLLISION_DEMO), "--headless"])
    pb = InputDemoPlayback.load(str(COLLISION_DEMO))
    frontend.apply_demo_metadata(args, pb.manifest.get("metadata", {}))
    rt = (frontend.create_runtime(args) if pb.is_cold_start
          else frontend.load_snapshot_runtime(args, pb.snapshot_path()))
    args.install_replacements = False
    frontend.apply_hook_mode(rt, args)
    _use_real_console_input(rt)

    def _bpw(m, ss, bp, o):
        return m.rw(ss, (bp - o) & 0xFFFF)

    bump = {}       # captured at 26EC
    fixup = {}      # captured at 283C
    stats = {"bump": 0, "bump_active": 0, "fixup": 0, "fixup_active": 0}

    def _probe(cpu):
        s = cpu.s
        m = cpu.mem
        ds = s.ds
        bp = s.bp
        if s.ip == 0x26EC:
            bump.clear()
            bump.update(
                state=NativeGameState(bytearray(m.data[(ds << 4):(ds << 4) + 0x10000])),
                cur_lat=m.rw(ds, 0x9618) | (m.rw(ds, 0x961A) << 16),
                tgt_lat=_bpw(m, s.ss, bp, 32) | (_bpw(m, s.ss, bp, 30) << 16),
                af1c=m.rw(ds, 0xAF1C), tgt_af1c=_bpw(m, s.ss, bp, 26),
                af2c=m.rw(ds, 0xAF2C),
            )
        elif s.ip == 0x27A3 and bump:
            got_af1c, got_tgt = lateral_wall_bump(
                make_visible(bump["state"].rw), bump["cur_lat"], bump["tgt_lat"],
                bump["af1c"], bump["tgt_af1c"], bump["af2c"])
            exp_tgt = _bpw(m, s.ss, bp, 32) | (_bpw(m, s.ss, bp, 30) << 16)
            assert got_af1c == m.rw(ds, 0xAF1C), "wall_bump af1c diverged"
            assert got_tgt == exp_tgt, "wall_bump tgt_lateral diverged"
            stats["bump"] += 1
            if got_af1c != bump["af1c"]:
                stats["bump_active"] += 1
            bump.clear()
        elif s.ip == 0x283C:
            af1c = m.rw(ds, 0xAF1C)
            tgt = _bpw(m, s.ss, bp, 26)
            fixup.clear()
            fixup.update(
                af1c=af1c, tgt=tgt, cur_5496=m.rw(ds, 0x5496),
                accel=m.rw(ds, 0x4568),
                ship_pos=m.rw(ds, 0x54AC) | (m.rw(ds, 0x54AE) << 16),
                active=(af1c != tgt),
            )
        elif s.ip == 0x28D7 and fixup:
            accel, c5496, pos = af1c_contact_fixup(
                fixup["af1c"], fixup["tgt"], fixup["cur_5496"], fixup["accel"],
                fixup["ship_pos"])
            assert accel == m.rw(ds, 0x4568)
            assert c5496 == m.rw(ds, 0x5496)
            assert pos == (m.rw(ds, 0x54AC) | (m.rw(ds, 0x54AE) << 16))
            stats["fixup"] += 1
            if fixup["active"]:
                stats["fixup_active"] += 1
            fixup.clear()

    orig = CPU8086.step

    def patched(self):
        if self.s.cs == 0x1010 and self.s.ip in (0x26EC, 0x27A3, 0x283C, 0x28D7):
            _probe(self)
        return orig(self)

    CPU8086.step = patched
    try:
        frame = 0
        while not pb.finished(frame) and frame < 1906:
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

    # The whole point of this demo is that the ACTIVE branches actually fire.
    assert stats["bump_active"] >= 1, f"no real wall-bump exercised ({stats})"
    assert stats["fixup_active"] >= 1, f"no real af1c contact exercised ({stats})"


@pytest.mark.skipif(not (EXE.exists() and COLLISION_DEMO.exists()),
                    reason="needs SKYROADS.EXE + a collision demo")
def test_resolve_landing_matches_asm_over_demo() -> None:
    import scripts.play as sp
    from dos_re import player
    from dos_re.cpu import CPU8086, HaltExecution
    from dos_re.dos import ConsoleInputWouldBlock
    from dos_re.input_demo import InputDemoPlayback
    from dos_re.player import _use_real_console_input

    frontend = sp.SkyroadsFrontend(ROOT)
    args = player.build_arg_parser(frontend).parse_args(
        ["--play-demo", str(COLLISION_DEMO), "--headless"])
    pb = InputDemoPlayback.load(str(COLLISION_DEMO))
    frontend.apply_demo_metadata(args, pb.manifest.get("metadata", {}))
    rt = (frontend.create_runtime(args) if pb.is_cold_start
          else frontend.load_snapshot_runtime(args, pb.snapshot_path()))
    args.install_replacements = False
    frontend.apply_hook_mode(rt, args)
    _use_real_console_input(rt)

    def _bpw(m, ss, bp, o):
        return m.rw(ss, (bp - o) & 0xFFFF)

    pending: dict = {}
    checked = [0]

    def _probe(cpu):
        s = cpu.s
        m = cpu.mem
        ds = s.ds
        bp = s.bp
        if s.ip == 0x28D7:
            pending.clear()
            pending.update(
                scratch=JumpScratch(_bpw(m, s.ss, bp, 8), _bpw(m, s.ss, bp, 10),
                                    _bpw(m, s.ss, bp, 6)),
                tgt_af2c=_bpw(m, s.ss, bp, 28), af2c=m.rw(ds, 0xAF2C),
                bounce=m.rw(ds, 0x9336), af2e=m.rw(ds, 0xAF2E), af30=m.rw(ds, 0xAF30),
                f455a=m.rw(ds, 0x455A),
                ship_pos=m.rw(ds, 0x54AC) | (m.rw(ds, 0x54AE) << 16),
            )
        elif s.ip == 0x2963 and pending:  # 2963 is reached ONLY on a landing
            r = resolve_landing(
                pending["scratch"], pending["tgt_af2c"], pending["af2c"],
                pending["bounce"], pending["af2e"], pending["af30"],
                pending["f455a"], pending["ship_pos"])
            assert r.landed is True
            assert r.scratch.jumping == _bpw(m, s.ss, bp, 8)
            assert r.scratch.effect_latch == _bpw(m, s.ss, bp, 6)
            assert r.gameplay_active == _bpw(m, s.ss, bp, 12)
            assert r.f455a == m.rw(ds, 0x455A)
            assert r.ship_pos == (m.rw(ds, 0x54AC) | (m.rw(ds, 0x54AE) << 16))
            checked[0] += 1
            pending.clear()

    orig = CPU8086.step

    def patched(self):
        if self.s.cs == 0x1010 and self.s.ip in (0x28D7, 0x2963):
            _probe(self)
        return orig(self)

    CPU8086.step = patched
    try:
        frame = 0
        while not pb.finished(frame) and frame < 1906:
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

    assert checked[0] > 20, f"only {checked[0]} landing frames checked"


@pytest.mark.skipif(not (EXE.exists() and COLLISION_DEMO.exists()),
                    reason="needs SKYROADS.EXE + a collision demo")
def test_resolve_lateral_crash_matches_asm_over_demo() -> None:
    import scripts.play as sp
    from dos_re import player
    from dos_re.cpu import CPU8086, HaltExecution
    from dos_re.dos import ConsoleInputWouldBlock
    from dos_re.input_demo import InputDemoPlayback
    from dos_re.player import _use_real_console_input

    frontend = sp.SkyroadsFrontend(ROOT)
    args = player.build_arg_parser(frontend).parse_args(
        ["--play-demo", str(COLLISION_DEMO), "--headless"])
    pb = InputDemoPlayback.load(str(COLLISION_DEMO))
    frontend.apply_demo_metadata(args, pb.manifest.get("metadata", {}))
    rt = (frontend.create_runtime(args) if pb.is_cold_start
          else frontend.load_snapshot_runtime(args, pb.snapshot_path()))
    args.install_replacements = False
    frontend.apply_hook_mode(rt, args)
    _use_real_console_input(rt)

    def _bpw(m, ss, bp, o):
        return m.rw(ss, (bp - o) & 0xFFFF)

    pending: dict = {}
    stats = {"total": 0, "crashed": 0}

    def _probe(cpu):
        s = cpu.s
        m = cpu.mem
        ds = s.ds
        bp = s.bp
        if s.ip == 0x27A3:
            pending.clear()
            pending.update(
                cur_lateral=m.rw(ds, 0x9618) | (m.rw(ds, 0x961A) << 16),
                tgt_lateral=_bpw(m, s.ss, bp, 32) | (_bpw(m, s.ss, bp, 30) << 16),
                ship_pos=m.rw(ds, 0x54AC) | (m.rw(ds, 0x54AE) << 16),
                f456a=m.rw(ds, 0x456A), game_state=m.rw(ds, 0x456E),
            )
        elif s.ip == 0x283C and pending:  # end of the crash region
            r = resolve_lateral_crash(
                pending["cur_lateral"], pending["tgt_lateral"],
                pending["ship_pos"], pending["f456a"], pending["game_state"])
            assert r.ship_pos == (m.rw(ds, 0x54AC) | (m.rw(ds, 0x54AE) << 16))
            assert r.f456a == m.rw(ds, 0x456A)
            assert r.game_state == m.rw(ds, 0x456E)
            stats["total"] += 1
            if r.crashed:
                stats["crashed"] += 1
            pending.clear()

    orig = CPU8086.step

    def patched(self):
        if self.s.cs == 0x1010 and self.s.ip in (0x27A3, 0x283C):
            _probe(self)
        return orig(self)

    CPU8086.step = patched
    try:
        frame = 0
        while not pb.finished(frame) and frame < 1906:
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

    assert stats["total"] > 100, f"only {stats['total']} crash-region frames checked"
    assert stats["crashed"] >= 1, f"no real lateral crash exercised ({stats})"
