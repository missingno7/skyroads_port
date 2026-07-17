"""Native SFX: the SFX.SND bank parser + the sim's 03C2 trigger emission."""
from pathlib import Path

import pytest

from skyroads.native.sfx import EFFECT_COUNT, load_sfx_bank

ROOT = Path(__file__).resolve().parents[1]
SFX_SND = ROOT / "assets" / "SFX.SND"
EXE = ROOT / "assets" / "SKYROADS.EXE"
SLOW_CRASH_DEMO = ROOT / "artifacts" / "demos" / "demo_skyroads_20260713_095814"


@pytest.mark.skipif(not SFX_SND.exists(), reason="game assets not present")
def test_sfx_bank_parses_to_five_effects():
    effects = load_sfx_bank(SFX_SND)
    assert len(effects) == EFFECT_COUNT
    # The known directory: [12, 3996, 9150, 17235, 18036, 25807] -- each
    # effect is (end - start - 1) PCM bytes after its time-constant byte.
    dir_ = [12, 3996, 9150, 17235, 18036, 25807]
    for i, eff in enumerate(effects):
        assert len(eff.pcm) == dir_[i + 1] - dir_[i] - 1
        assert 0 < eff.tc < 256
        assert eff.rate == 1_000_000 // (256 - eff.tc)
    # The recurring gameplay effect observed in the SB-DMA capture (tc=131 ->
    # 8000 Hz, 5153 bytes) is id 1 -- the bounce-landing sound, the most
    # frequent trigger. Both other captured rates appear too: id 0 tc=6
    # (4000 Hz) and id 2 tc=236 (50000 Hz, the bump/crash thump).
    assert effects[1].tc == 131
    assert effects[1].rate == 8000
    assert len(effects[1].pcm) == 5153
    assert effects[2].tc == 236 and effects[2].rate == 50000


def test_jump_landing_emits_sfx_1_with_debounce():
    """Jump on the straight level-14 opening, then land: the decay branch must
    emit the bounce-landing SFX (id 1, VM call-site ret `249E`), and the
    `0476` 8-tick debounce must space repeated landings by >= 8 ticks of
    `[1600]` (which this loop advances +2/tick)."""
    snap = ROOT / "artifacts" / "frame_2d1f" / "snap92" / "memory_1mb.bin"
    if not (snap.exists() and EXE.exists()):
        pytest.skip("baseline snapshot or game assets not present")
    from skyroads.bridge.dgroup_view import GameView
    from skyroads.native.level_load import native_level_load
    from skyroads.native.loop import NativeGameplayDriver, apply_level_init
    from skyroads.native.state import DATA_SEG, NativeGameState

    data = bytearray(snap.read_bytes())
    dgb = DATA_SEG << 4
    st = NativeGameState(bytearray(data[dgb:dgb + 0x10000]))
    native_level_load(st, 14, game_root=str(ROOT / "assets"))
    view = GameView(st)
    gate = view.jump_level_gate
    events = []
    driver = NativeGameplayDriver(
        view, gate, apply_level_init(view, gate),
        on_sfx=lambda i: events.append((driver.ticks, i)))
    for t in range(200):
        view.speed = 1
        view.jump = 1 if 20 <= t < 28 else 0
        view.elapsed_ticks = (view.elapsed_ticks + 2) & 0xFFFF
        if driver.tick().transitioned:
            break
    landings = [tick for tick, i in events if i == 1]
    assert landings, f"jump should emit landing sfx id 1 (got {events})"
    assert all(b - a >= 4 for a, b in zip(landings, landings[1:])), \
        f"debounce violated: {landings}"    # 8 [1600]-ticks / +2 per loop tick
    assert all(i == 1 for _, i in events), f"unexpected ids: {events}"


@pytest.mark.skipif(not (EXE.exists() and SLOW_CRASH_DEMO.exists()),
                    reason="needs SKYROADS.EXE + demo_skyroads_20260713_095814")
def test_slow_wall_crash_emits_no_thud():
    """2026-07-13 user report: a slow-speed wall crash played a sound the
    real game keeps silent. `demo_skyroads_20260713_095814` reproduces it:
    the ship hits a wall at `ship_pos=2325`, well below `CRASH_MILESTONE_POS`
    (0x0E38=3640) -- `resolve_lateral_crash`'s "flagged" branch never fires
    (`grounded` stays 0), so the real ASM only ever calls the id-2 "blocked
    thump", never id-0 "crash thud" (VM-verified: the real `03C2` call log
    over this whole demo is id 2 once, id 1 twice, id 0 NEVER). The bug was
    gating id-0 on `LateralCrashResult.crashed` (true for ANY lateral
    mismatch) instead of the real "grounded 0 -> nonzero" flagging edge --
    see native_gameplay_substep's collision-response comment.

    Re-seeds the native sub-step from the VM at EVERY loop-top visit (the
    test_native_substep.py pattern) rather than accumulating a standalone
    replay -- this level's specific physics drift over ~150 native-only
    steps otherwise makes the native pipeline reach the wall at a DIFFERENT
    (already-past-milestone) ship_pos than the VM did, which would make this
    a pipeline-accuracy test instead of the targeted SFX-gating regression
    it's meant to be.
    """
    import scripts.play as sp
    from dos_re import player
    from dos_re.cpu import CPU8086, HaltExecution
    from dos_re.dos import ConsoleInputWouldBlock
    from dos_re.input_demo import InputDemoPlayback
    from dos_re.player import _use_real_console_input

    from skyroads.bridge.dgroup_view import GameView
    from skyroads.native.gaps import SkyroadsGap
    from skyroads.native.loop import GameplayScratch, native_gameplay_substep
    from skyroads.native.sfx import SFX_TOUCHDOWN
    from skyroads.native.state import NativeGameState
    from skyroads.handrecovered.dynamics import JumpScratch

    frontend = sp.SkyroadsFrontend(ROOT)
    args = player.build_arg_parser(frontend).parse_args(
        ["--play-demo", str(SLOW_CRASH_DEMO), "--headless"])
    pb = InputDemoPlayback.load(str(SLOW_CRASH_DEMO))
    frontend.apply_demo_metadata(args, pb.manifest.get("metadata", {}))
    rt = frontend.load_snapshot_runtime(args, pb.snapshot_path())
    args.install_replacements = False
    frontend.apply_hook_mode(rt, args)
    _use_real_console_input(rt)

    LOOP = 0x2324

    def _bpw(m, ss, bp, o):
        return m.rw(ss, (bp - o) & 0xFFFF)

    st = {"armed": False, "dg": None, "sc": None}
    events = []
    stats = {"ok": 0, "gap": 0}

    def _seed(cpu):
        s = cpu.s
        m = cpu.mem
        ds = s.ds
        bp = s.bp
        dg = bytearray(m.data[(ds << 4):(ds << 4) + 0x10000])
        sc = GameplayScratch(
            jump=JumpScratch(_bpw(m, s.ss, bp, 8), _bpw(m, s.ss, bp, 10),
                             _bpw(m, s.ss, bp, 6)),
            bp12=_bpw(m, s.ss, bp, 12), bp14=_bpw(m, s.ss, bp, 14),
            bp24=_bpw(m, s.ss, bp, 24), tgt_af2c=_bpw(m, s.ss, bp, 28))
        return dg, sc

    def _run_one_step():
        view = GameView(NativeGameState(st["dg"]))
        try:
            native_gameplay_substep(view, st["sc"],
                                    sfx=lambda i: events.append(i))
            stats["ok"] += 1
        except SkyroadsGap:
            stats["gap"] += 1

    orig = CPU8086.step

    def patched(self):
        if self.s.cs == 0x1010 and self.s.ip == LOOP:
            if st["armed"]:
                _run_one_step()
            if self.mem.rw(self.s.ds, 0x456E) == 0:
                st["dg"], st["sc"] = _seed(self)
                st["armed"] = True
            else:
                st["armed"] = False
        return orig(self)

    CPU8086.step = patched
    try:
        frame = 0
        end = pb.manifest.get("end_boundary", 999999)
        while not pb.finished(frame) and frame <= end:
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

    assert stats["ok"] > 20, f"too few re-seeded sub-steps checked: {stats}"
    assert SFX_TOUCHDOWN not in events, (
        f"the slow/pre-milestone wall crash fired the crash thud (events={events})")
    assert events, "expected at least the bump/landing SFX to fire"


def test_sfx_callback_absence_is_pure():
    """Without a callback the emission layer must be a strict no-op: two runs
    (with sfx=None vs with a swallowing callback) reach the same crash tick."""
    snap = ROOT / "artifacts" / "frame_2d1f" / "snap92" / "memory_1mb.bin"
    if not (snap.exists() and EXE.exists()):
        pytest.skip("baseline snapshot or game assets not present")
    from skyroads.bridge.dgroup_view import GameView
    from skyroads.native.level_load import native_level_load
    from skyroads.native.loop import NativeGameplayDriver, apply_level_init
    from skyroads.native.state import DATA_SEG, NativeGameState

    def run(on_sfx):
        data = bytearray(snap.read_bytes())
        dgb = DATA_SEG << 4
        st = NativeGameState(bytearray(data[dgb:dgb + 0x10000]))
        native_level_load(st, 14, game_root=str(ROOT / "assets"))
        view = GameView(st)
        gate = view.jump_level_gate
        driver = NativeGameplayDriver(view, gate, apply_level_init(view, gate),
                                      on_sfx=on_sfx)
        for t in range(400):
            view.speed = 1
            view.elapsed_ticks = (view.elapsed_ticks + 2) & 0xFFFF
            if driver.tick().transitioned:
                return t
        return None

    assert run(None) == run(lambda i: None)
