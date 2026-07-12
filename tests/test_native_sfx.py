"""Native SFX: the SFX.SND bank parser + the sim's 03C2 trigger emission."""
from pathlib import Path

import pytest

from skyroads.native.sfx import EFFECT_COUNT, load_sfx_bank

ROOT = Path(__file__).resolve().parents[1]
SFX_SND = ROOT / "assets" / "SFX.SND"


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
    if not snap.exists():
        pytest.skip("baseline snapshot not present")
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


def test_sfx_callback_absence_is_pure():
    """Without a callback the emission layer must be a strict no-op: two runs
    (with sfx=None vs with a swallowing callback) reach the same crash tick."""
    snap = ROOT / "artifacts" / "frame_2d1f" / "snap92" / "memory_1mb.bin"
    if not snap.exists():
        pytest.skip("baseline snapshot not present")
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
