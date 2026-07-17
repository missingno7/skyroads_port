"""Cold-boot screen state machine (skyroads.handrecovered_native.menus).

Transitions assert the flow VERIFIED from demo_colde2e_full_20260713_144604
(rendered-frame ground truth + recorded keydowns; see demo_manifest.md). Each
verified case notes the demo frame that proves it.
"""
from __future__ import annotations

from skyroads.handrecovered_native import menus
from skyroads.handrecovered_native.menus import (CONTROLS, EXIT, GAMEPLAY, HELP, MAIN,
                                    SELECT, MenuModel)


def test_start_from_main_menu_goes_to_level_select() -> None:
    # f571: ENTER on "Start!" -> level-select grid
    m = MenuModel()
    assert m.screen == MAIN and m.main_index == menus.START
    m.confirm()
    assert m.screen == SELECT


def test_main_menu_navigation_reaches_help_and_confirms() -> None:
    # f2571..2622: several DOWNs then ENTER -> Help screen (f2650)
    m = MenuModel()
    m.main_down(); m.main_down()          # Start -> Controls -> Help
    assert m.main_index == menus.ITEM_HELP
    m.confirm()
    assert m.screen == HELP and m.help_page == 0


def test_main_menu_controls_item_opens_controls() -> None:
    m = MenuModel()
    m.main_down()                          # -> Controls
    assert m.main_index == menus.ITEM_CONTROLS
    m.confirm()
    assert m.screen == CONTROLS


def test_main_menu_up_down_clamp() -> None:
    m = MenuModel()
    m.main_up()                            # already at top
    assert m.main_index == 0
    m.main_down(); m.main_down(); m.main_down()   # clamp at last
    assert m.main_index == len(menus.MAIN_ITEMS) - 1


def test_esc_from_main_menu_exits_game() -> None:
    # f2838: ESC at the main menu -> quit
    m = MenuModel()
    m.back()
    assert m.screen == EXIT


def test_esc_from_level_select_returns_to_main_menu() -> None:
    # f2492: ESC at the grid -> main menu (f2560)
    m = MenuModel(screen=SELECT)
    m.back()
    assert m.screen == MAIN


def test_confirm_in_select_launches_selected_level() -> None:
    m = MenuModel(screen=SELECT, selected_level=7)
    m.confirm()
    assert m.screen == GAMEPLAY
    assert m.launch_level == 7


def test_finish_returns_to_grid() -> None:
    # demo_colde2e_full f2300-2475: natural finish -> level-select grid
    m = MenuModel(screen=GAMEPLAY, selected_level=7, launch_level=7)
    m.level_ended("finish")
    assert m.screen == SELECT
    assert m.launch_level is None


def test_death_respawns_same_level_not_menu() -> None:
    # demo_skyroads_20260713_154259: red-tile death (SFX id 0) -> explosion ->
    # SAME level restarts (f115-134), NOT the level-select menu.
    for kind in ("crash", "fall", ""):
        m = MenuModel(screen=GAMEPLAY, selected_level=7, launch_level=7)
        m.level_ended(kind)
        assert m.screen == GAMEPLAY, f"death kind {kind!r} must respawn, not menu"
        assert m.launch_level == 7


def test_esc_during_gameplay_returns_to_grid() -> None:
    # f1003: ESC mid-level -> level-select grid
    m = MenuModel(screen=GAMEPLAY, launch_level=3)
    m.back()
    assert m.screen == SELECT


def test_help_pages_cycle_with_space_then_return_to_main() -> None:
    # f2700/f2765: SPACE cycles help pages; past the last -> back to main menu
    m = MenuModel(screen=HELP, help_pages=2)
    m.next_page()
    assert m.screen == HELP and m.help_page == 1
    m.next_page()                          # past last page
    assert m.screen == MAIN and m.help_page == 0


def test_esc_from_help_and_controls_returns_to_main() -> None:
    for scr in (HELP, CONTROLS):
        m = MenuModel(screen=scr)
        m.back()
        assert m.screen == MAIN


def test_grid_navigation_delegates_to_verified_model() -> None:
    # Over-the-Base Road3 (level 23) -LEFT-> Blue Planet Road3 (level 8)
    m = MenuModel(screen=SELECT, selected_level=7 * 3 + 2)
    m.grid_move(left=True)
    assert m.selected_level == 2 * 3 + 2
    # grid actions are ignored off the select screen
    m2 = MenuModel(screen=MAIN, selected_level=5)
    m2.grid_move(down=True)
    assert m2.selected_level == 5
