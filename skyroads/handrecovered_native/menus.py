"""Cold-boot screen/menu state machine -- transitions VERIFIED against the
oracle demo ``demo_colde2e_full_20260713_144604`` (a full interactive cold
session: intro -> main menu -> level select -> play -> finish -> menus -> exit),
read off rendered frames + recorded keydowns (see docs/skyroads/run_status.md
and demo_manifest.md, 2026-07-13).

This is deliberately pure/UI-logic only (no pygame, no rendering) so the whole
screen graph is unit-testable; ``scripts/play_native.run_cold_boot`` drives it
and owns the drawing.

Screen graph (verified transitions cite the demo frame that proves them):

    main  --Start (ENTER)-->   select      (f571 -> grid -> gameplay f840)
    main  --Help  (ENTER)-->   help        (f2622 -> help f2650)
    main  --Controls (ENTER)-> controls    (asset SETMENU.LZS; not directly
                                             exercised by the demo)
    main  --ESC-->             exit         (f2838 quit)
    select --ENTER-->          gameplay     (verified, multiple)
    select --ESC-->            main         (f2492 grid -> main menu f2560)
    gameplay --finish/esc-->   select       (finish f2300-2475; esc f1003 -> grid)
    help  --SPACE-->           help (next page, wraps to main after last)
    help  --ESC-->             main
    controls --ESC-->          main

NOT verifiable from the demo, chosen conservatively and flagged:
  * main-menu UP/DOWN clamp vs wrap (the demo's key counts are consistent with
    both) -- clamped here, matching the verified level-select behaviour.
  * whether SPACE past the last help page returns to main or sticks -- returns
    to main here (the screen's own "Press SPACE to view next page" + the demo
    ending back on the main menu after the last SPACE are consistent with a
    wrap-to-exit).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from skyroads.handrecovered_native.level_select import move_selection

MAIN = "main"
SELECT = "select"
HELP = "help"
CONTROLS = "controls"
GAMEPLAY = "gameplay"
EXIT = "exit"

MAIN_ITEMS = ("Start", "Controls", "Help")
START, ITEM_CONTROLS, ITEM_HELP = 0, 1, 2

# HELPMENU.LZS carries multiple pages cycled by SPACE (page 1 = controls
# reference, page 2 = the road-tile legend). Verified >= 2; exact count is a
# render-time detail owned by the caller.
DEFAULT_HELP_PAGES = 2


@dataclass
class MenuModel:
    """Mutable cold-boot screen state. Feed it edge-triggered actions."""

    screen: str = MAIN
    main_index: int = START
    selected_level: int = 0
    help_page: int = 0
    help_pages: int = DEFAULT_HELP_PAGES
    # set when a gameplay session should begin; the caller consumes it
    launch_level: "int | None" = field(default=None)

    # ---- main menu ----
    def main_up(self) -> None:
        if self.screen == MAIN:
            self.main_index = max(0, self.main_index - 1)

    def main_down(self) -> None:
        if self.screen == MAIN:
            self.main_index = min(len(MAIN_ITEMS) - 1, self.main_index + 1)

    # ---- generic actions ----
    def confirm(self) -> None:
        """ENTER."""
        if self.screen == MAIN:
            if self.main_index == START:
                self.screen = SELECT
            elif self.main_index == ITEM_CONTROLS:
                self.screen = CONTROLS
            elif self.main_index == ITEM_HELP:
                self.screen = HELP
                self.help_page = 0
        elif self.screen == SELECT:
            self.launch_level = self.selected_level
            self.screen = GAMEPLAY

    def back(self) -> None:
        """ESC -- one step up the screen hierarchy (verified)."""
        if self.screen == MAIN:
            self.screen = EXIT
        elif self.screen in (SELECT, HELP, CONTROLS):
            self.screen = MAIN
        elif self.screen == GAMEPLAY:
            self.screen = SELECT

    def next_page(self) -> None:
        """SPACE on the help screen: advance page, wrapping back to main."""
        if self.screen == HELP:
            self.help_page += 1
            if self.help_page >= self.help_pages:
                self.screen = MAIN
                self.help_page = 0

    # ---- level-select grid (delegates to the verified nav model) ----
    def grid_move(self, *, up=False, down=False, left=False, right=False) -> None:
        if self.screen == SELECT:
            self.selected_level = move_selection(
                self.selected_level, up=up, down=down, left=left, right=right)

    # ---- gameplay hand-back ----
    def level_ended(self, kind: str) -> None:
        """Called when a level ends. Routing is VERIFIED and depends on *why*:

        * ``"finish"`` -- the ship reached the end ramp and flew off: return to
          the level-select grid (demo_colde2e_full f2300-2475).
        * a DEATH (``"crash"``/``"fall"``/red "Burning" tile/etc.) -- the ship
          explodes (SFX id 0, the crash thud) and the SAME level **respawns**
          from the start; control stays in gameplay (demo_skyroads_20260713_154259,
          f39 death -> f115-134 same-level restart). NOT a return to the menu.

        ``kind`` is the :class:`~skyroads.handrecovered_native.loop.TickOutcome` kind.
        """
        if self.screen != GAMEPLAY:
            return
        if kind == "finish":
            self.screen = SELECT
            self.launch_level = None
        # any death -> respawn the same level: stay in GAMEPLAY, keep
        # launch_level (the driver re-inits the level in place).
