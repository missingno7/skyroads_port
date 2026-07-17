"""Verified level-select grid navigation (skyroads.handrecovered_native.level_select).

The transitions asserted here are transcribed directly from the oracle
derivation over ``demo_menu_3levels_20260713_144256`` -- the on-screen
selection highlight was tracked frame-by-frame against the recorded keydowns
(see docs/skyroads/run_status.md, 2026-07-13). Each case is a clean, mid-list
sample from that trace (the extremes were excluded as detection-biased and are
covered by the explicit clamp tests below).
"""
from __future__ import annotations

from skyroads.handrecovered_native.level_select import (
    ENTRIES_PER_COLUMN, LEVEL_COUNT, WORLD_NAMES, move_selection, road_of,
    split, world_of)


def _lvl(world: int, road: int) -> int:
    return world * 3 + road


def test_grid_encoding_matches_the_rendered_layout() -> None:
    assert LEVEL_COUNT == 30
    assert WORLD_NAMES[0] == "Red Heat"
    assert WORLD_NAMES[2] == "Blue Planet"      # col0 row2
    assert WORLD_NAMES[6] == "Crab Nebula"      # col1 row1
    assert WORLD_NAMES[8] == "The Earth"        # col1 row3
    # column split: worlds 0..4 -> col0, 5..9 -> col1
    assert split(_lvl(2, 1)) == (0, 2 * 3 + 1)  # Blue Planet Road2 -> col0 entry7
    assert split(_lvl(8, 0)) == (1, 3 * 3 + 0)  # The Earth  Road1  -> col1 entry9


def test_down_crosses_planet_boundaries_within_a_column() -> None:
    # f356->f360: Over-the-Base Road3 (col1) -> The-Earth Road1 (col1)
    over_base_r3 = _lvl(7, 2)
    the_earth_r1 = _lvl(8, 0)
    nxt = move_selection(over_base_r3, down=True)
    assert (world_of(nxt), road_of(nxt)) == (8, 0) == (world_of(the_earth_r1), road_of(the_earth_r1))
    # f326: Red Heat Road3 -> DOWN -> Into the Sun Road1
    assert move_selection(_lvl(0, 2), down=True) == _lvl(1, 0)


def test_up_crosses_planet_boundaries_within_a_column() -> None:
    # f381: Satellite Road1 -> UP -> Blue Planet Road3
    assert move_selection(_lvl(3, 0), up=True) == _lvl(2, 2)
    # f284: Druidia Road1 -> UP -> The Earth Road3
    assert move_selection(_lvl(9, 0), up=True) == _lvl(8, 2)


def test_left_right_switch_column_preserving_vertical_position() -> None:
    # f152->f156: Over-the-Base Road3 (col1 entry8) -LEFT-> Blue Planet Road3 (col0 entry8)
    assert move_selection(_lvl(7, 2), left=True) == _lvl(2, 2)
    # f341: Into-the-Sun Road3 (col0) -RIGHT-> Crab Nebula Road3 (col1), same entry
    assert move_selection(_lvl(1, 2), right=True) == _lvl(6, 2)
    # f368: The Earth Road2 (col1) -LEFT-> Satellite Road2 (col0)
    assert move_selection(_lvl(8, 1), left=True) == _lvl(3, 1)
    # f294: The Earth Road1 -LEFT-> Satellite Road1
    assert move_selection(_lvl(8, 0), left=True) == _lvl(3, 0)


def test_left_from_left_column_and_right_from_right_column_are_noops() -> None:
    blue_r3 = _lvl(2, 2)                 # col0
    assert move_selection(blue_r3, left=True) == blue_r3
    over_r3 = _lvl(7, 2)                 # col1
    assert move_selection(over_r3, right=True) == over_r3


def test_vertical_clamps_at_both_ends_no_wrap() -> None:
    top = _lvl(0, 0)                     # Red Heat Road1, entry 0
    assert move_selection(top, up=True) == top
    bottom_col0 = _lvl(4, 2)             # Misty Road3, col0 entry14
    assert split(bottom_col0)[1] == ENTRIES_PER_COLUMN - 1
    assert move_selection(bottom_col0, down=True) == bottom_col0
    bottom_col1 = _lvl(9, 2)            # Druidia Road3, col1 entry14
    assert move_selection(bottom_col1, down=True) == bottom_col1


def test_column_switch_keeps_entry_even_at_the_extremes() -> None:
    # entry is preserved across a column switch regardless of vertical clamp
    misty_r3 = _lvl(4, 2)              # col0 entry14
    druidia_r3 = _lvl(9, 2)           # col1 entry14
    assert move_selection(misty_r3, right=True) == druidia_r3
    assert move_selection(druidia_r3, left=True) == misty_r3
