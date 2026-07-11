"""Unit tests for skyroads.native.state.NativeGameState and
skyroads.bridge.dgroup_view.GameView -- the plumbing every native frame
stepper runs through (no real demo needed; that proof is
test_native_loop.py's real-demo integration test)."""
from __future__ import annotations

from skyroads.bridge.dgroup_view import GameView
from skyroads.native.state import DATA_SEG, SEGMENT_SIZE, NativeGameState


class _FakeMem:
    """Minimal stand-in for a dos_re.memory.Memory: a flat 1 MB image."""

    def __init__(self):
        self.data = bytearray(0x100000)


def test_native_game_state_defaults_to_zeroed_segment() -> None:
    st = NativeGameState()
    assert len(st.data) == SEGMENT_SIZE
    assert st.rb(0x54AC) == 0
    assert st.rw(0x54AC) == 0


def test_native_game_state_rb_rw_wb_ww_round_trip() -> None:
    st = NativeGameState()
    st.ww(0x9330, 0xFFFF)
    assert st.rb(0x9330) == 0xFF
    assert st.rb(0x9331) == 0xFF
    assert st.rw(0x9330) == 0xFFFF
    st.wb(0x9330, 0x12)
    assert st.rw(0x9330) == 0xFF12


def test_native_game_state_offsets_wrap_at_64k() -> None:
    st = NativeGameState()
    st.wb(0xFFFF, 0xAB)
    st.wb(0x0000, 0xCD)
    assert st.rw(0xFFFF) == 0xCDAB  # low byte at 0xFFFF, high byte wraps to 0x0000


def test_native_game_state_from_vm_seeds_from_the_right_segment() -> None:
    mem = _FakeMem()
    base = (DATA_SEG << 4)
    mem.data[base + 0x54AC] = 0x77
    mem.data[base + SEGMENT_SIZE - 1] = 0x99  # last byte of the segment

    class _RT:
        class cpu:  # noqa: N801 -- matches rt.cpu.mem.data's real shape
            pass
    _RT.cpu.mem = mem

    st = NativeGameState.from_vm(_RT)
    assert st.rb(0x54AC) == 0x77
    assert st.data[-1] == 0x99


def test_game_view_named_fields_address_documented_offsets() -> None:
    st = NativeGameState()
    view = GameView(st)
    view.speed = 1
    view.game_state = 3
    view.af1c = 0x1234
    assert st.rw(0x9330) == 1
    assert st.rw(0x456E) == 3
    assert st.rw(0xAF1C) == 0x1234


def test_game_view_dword_fields_compose_lo_hi_words() -> None:
    st = NativeGameState()
    view = GameView(st)
    view.ship_pos = 0x0002AAAA
    assert st.rw(0x54AC) == 0xAAAA
    assert st.rw(0x54AE) == 0x0002
    assert view.ship_pos == 0x0002AAAA

    view.lateral = 0x00030000
    assert st.rw(0x9618) == 0x0000
    assert st.rw(0x961A) == 0x0003
    assert view.lateral == 0x00030000


def test_game_view_entered_and_grounded_alias_the_same_offset() -> None:
    st = NativeGameState()
    view = GameView(st)
    view.entered = 1
    assert view.grounded == 1
    view.grounded = 0
    assert view.entered == 0


def test_game_view_key_row_indexes_by_absolute_offset() -> None:
    st = NativeGameState()
    view = GameView(st)
    st.wb(0x0BD2, 0x80)  # the "up" key's DGROUP offset, per controls.py
    assert view.key_row[0x0BD2] == 0x80
    assert view.key_row[0x0BD3] == 0x00


def test_game_view_runs_over_vm_mem_with_an_explicit_base() -> None:
    mem = _FakeMem()
    ds = 0x1686
    view = GameView(mem, base=ds << 4)
    view.speed = 5
    assert mem.data[(ds << 4) + 0x9330] == 5
