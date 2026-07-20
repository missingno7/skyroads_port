"""The native level-select menu's grid geometry (the recovered presentation
run_cold_boot): row/column bands measured off GOMENU.LZS's own decoded green
text pixels. Not ROM-recovered logic -- a UI affordance -- but its mapping
from level index to screen position should stay correct and in sync with
skyroads.native.world_load's level -> world/road convention."""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"

WORLD_ROW_Y0 = (12, 51, 90, 129, 168)
ROAD_SUB_Y = ((0, 8), (10, 17), (19, 27))
COL_X = ((6, 112), (166, 272))


def level_box(level: int):
    world, road = level // 3, level % 3
    col, row = (0, world) if world < 5 else (1, world - 5)
    y0, y1 = WORLD_ROW_Y0[row] + ROAD_SUB_Y[road][0], WORLD_ROW_Y0[row] + ROAD_SUB_Y[road][1]
    x0, x1 = COL_X[col]
    return x0, y0, x1, y1


def test_all_30_levels_map_to_distinct_in_bounds_boxes():
    boxes = [level_box(i) for i in range(30)]
    assert len(set(boxes)) == 30
    for x0, y0, x1, y1 in boxes:
        assert 0 <= x0 < x1 <= 320
        assert 0 <= y0 < y1 <= 200


def test_world_road_matches_world_load_convention():
    from skyroads.native.world_load import world_for_level
    for level in range(30):
        assert world_for_level(level) == level // 3


@pytest.mark.skipif(not ASSETS.exists(), reason="game assets absent")
def test_highlight_boxes_land_on_green_text():
    """Each level's box must overlap real green 'Road N' pixels in the
    decoded GOMENU background (catches a geometry regression, not just a
    self-consistency check)."""
    from skyroads.native.level_load import read_game_file
    from skyroads.native.boot import load_pict, parse_lzs_container

    gm = read_game_file(ASSETS, "GOMENU.LZS")
    cmap, _, at, _, _, _ = parse_lzs_container(gm)
    _, pix = load_pict(gm, at)
    green = 2   # the palette index sampled as (0,158,0) in run_status.md
    for level in range(30):
        x0, y0, x1, y1 = level_box(level)
        hits = sum(1 for y in range(y0, y1) for x in range(x0, x1)
                  if pix[y * 320 + x] == green)
        assert hits > 5, f"level {level} box has no green text ({hits} px)"
