"""Level-select grid navigation -- semantics VERIFIED against the oracle.

Derived from the interactive demo ``demo_menu_3levels_20260713_144256`` (the
user's own play session: main menu -> level-select grid -> three levels) by
tracking the ON-SCREEN selection highlight frame-by-frame against the recorded
keydowns (``scratchpad`` derivation; see docs/skyroads/run_status.md's
2026-07-13 level-select entry). This is measured screen behaviour, NOT the
earlier guessed model.

The GOMENU screen is a 2-column x 5-row grid of worlds, each world exposing
three "Road N" lines -- 30 levels total. Layout (verified off the rendered
grid)::

    col 0 (left)            col 1 (right)
    row0  Red Heat          Asteroid Belt
    row1  Into the Sun      Crab Nebula
    row2  Blue Planet       Over the Base
    row3  Satellite         The Earth
    row4  Misty             Druidia

Encoding: ``level = planet*3 + road`` with ``planet = col*5 + row`` and
``road in 0..2`` (Road 1..3).  Equivalently ``level = col*15 + entry`` where
``entry = row*3 + road`` is the position in that column's flat 15-entry list.

Verified navigation rules (each confirmed by multiple clean mid-list samples
in the demo -- e.g. DOWN from Over-the-Base/Road3 -> The-Earth/Road1 crossing a
planet boundary; LEFT from The-Earth/Road2 -> Satellite/Road2 preserving the
vertical position):

* **UP / DOWN** move one step through the current column's 15-entry list,
  crossing planet boundaries (road-major), **clamped** at the ends (no wrap).
* **LEFT / RIGHT** switch column (LEFT -> col 0, RIGHT -> col 1) while
  **preserving the vertical position** (row + road).

The earlier shipped model (UP/DOWN cycling ``road % 3``; LEFT/RIGHT cycling
``world % 10``) is refuted by the demo and has been replaced.
"""
from __future__ import annotations

WORLDS_PER_COLUMN = 5
ROADS_PER_WORLD = 3
ENTRIES_PER_COLUMN = WORLDS_PER_COLUMN * ROADS_PER_WORLD   # 15
COLUMNS = 2
LEVEL_COUNT = COLUMNS * ENTRIES_PER_COLUMN                 # 30

WORLD_NAMES = (
    # column 0 (left), top -> bottom
    "Red Heat", "Into the Sun", "Blue Planet", "Satellite", "Misty",
    # column 1 (right), top -> bottom
    "Asteroid Belt", "Crab Nebula", "Over the Base", "The Earth", "Druidia",
)


def split(level: int) -> "tuple[int, int]":
    """``level`` -> ``(column, entry)`` where entry is 0..14 within the column."""
    return level // ENTRIES_PER_COLUMN, level % ENTRIES_PER_COLUMN


def join(column: int, entry: int) -> int:
    """``(column, entry)`` -> ``level`` (inverse of :func:`split`)."""
    return column * ENTRIES_PER_COLUMN + entry


def world_of(level: int) -> int:
    """Planet index 0..9 (col 0 -> 0..4, col 1 -> 5..9)."""
    col, entry = split(level)
    return col * WORLDS_PER_COLUMN + entry // ROADS_PER_WORLD


def road_of(level: int) -> int:
    """Road index 0..2 (Road 1..3)."""
    return level % ROADS_PER_WORLD


def move_selection(level: int, *, up: bool = False, down: bool = False,
                   left: bool = False, right: bool = False) -> int:
    """Apply one navigation step to ``level`` and return the new level.

    Verified semantics (see module docstring). Only edge-triggered presses
    should be passed (one call per keydown); multiple flags may be combined but
    vertical is resolved before horizontal, matching the demo where a diagonal
    was never observed to skip a cell.
    """
    col, entry = split(level)
    if up:
        entry = max(0, entry - 1)
    if down:
        entry = min(ENTRIES_PER_COLUMN - 1, entry + 1)
    if left:
        col = 0
    if right:
        col = 1
    return join(col, entry)
