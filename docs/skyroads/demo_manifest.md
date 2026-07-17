# SkyRoads — demo manifest

<!-- The corpus is a measured artifact: track what it covers AND what it
     doesn't (pitfall #22). Demos live under artifacts/demos/ (gitignored:
     each demo's start snapshot embeds the game's 1 MB memory image, which the
     corpus convention says must not be committed). This manifest — which IS
     tracked — is how corpus coverage stays a measured number. Provenance
     matters: human-played (scripts/play.py --record-demo) vs agent-scripted. -->

| Demo | Frames | Source | Covers | Notes |
|---|---|---|---|---|
| demo_death_redtile_20260713_154259 | 135 | human-played | **the red "Burning"-tile DEATH** (starts from a mid-level snapshot, one jump, dies on a red pyramid tile). | Closes #38: death plays **SFX id 0** (the crash thud, same as a frontal wall crash) at f39 as game_state 0→2 (writer 1b68); the ship **explodes** (white flash → debris) and the **SAME level respawns** from the start (f115-134) — death is NOT a return to the menu (that's finish only). |
| demo_colde2e_full_20260713_144604 | 2839 | human-played | **the whole game end-to-end:** intro (anim→title) → **main menu** (`Start!`/`Controls`/`Help`) → level-select grid → several levels (Crab Nebula, Into the Sun) with special tiles → **natural level FINISH → grid** → main menu → **Help screen** (tile legend) → **exit game**. | The comprehensive oracle. Verifies finish→menu (#39), the main-menu + help screens (#41), the tile types, and the grav-o-meter shows a per-level number. **Caveat: the player never DIES** (finishes/ESCs every level) — the red "Burning" tile is passed, not hit, so the red-death SFX (#38) is still uncaptured. `[456E]` reads 0 during later gameplay, so it is NOT a reliable gameplay flag — use rendered frames. |
| demo_menu_3levels_20260713_144256 | 849 | human-played | **main menu → level-select grid → 3 level attempts.** Real GOMENU 2×5 grid navigation (UP/DOWN/LEFT/RIGHT), ENTER confirm, ESC abort-back-to-grid. game_state 0→3→0 once (Crab Nebula). | The demo that pinned the **verified level-select navigation model** (skyroads/recovered_native/level_select.py). All three plays are ESC-exited — **no natural finish, no death** in it. |
| demo_e2e_20260710_132930 | ~1719 | human-played | intro-skip → main menu → level select → play a level; drives the `test_native_driver` standalone-play proof. | attract/auto-cycle heavy; used as the lockstep + driver oracle. |
| demo_cold_20260711_201855 | — | human-played | full cold session, multiple levels, a death, finishes the last level. | first demo to exercise the real menu/level-start code chain (2B0B → apply_level_init). |
| demo_skyroads_20260711_202740 | 156 | human-played | starts sitting at the level-select screen, confirms one level. | tight clip; near-call trace surfaced the 2B53→…→1FD9 level-start chain. |
| demo_skyroads_20260713_131407 | — | human-played | single-level gameplay; verified at 99.1% sub-step parity (one documented 1E48–1FD8 back-off gap). | render/sim parity fixture. |

## Verified whole-game flow (rendered-frame ground truth, both demos)

1. **Intro** — ANIM.LZS ship/tunnel reveal → "SkyRoads" title slides in.
2. **Title / main menu** — "SkyRoads" logo + `Start!` / `Controls` / `Help`
   (UP/DOWN select, ENTER confirms). *(Not yet reproduced in `--boot`.)*
3. `Start!` (ENTER) → fade → **level-select grid**: 2 columns × 5 worlds, each
   world with `Road 1/2/3` (30 levels). Blinking cursor + selected-road highlight.
4. **Navigation (VERIFIED, skyroads/recovered_native/level_select.py):** UP/DOWN step the
   column's flat 15-entry (world×road) list, crossing world boundaries, clamped
   at the ends; LEFT/RIGHT switch column preserving the vertical position.
5. **ENTER** on a cell → that level loads (fade-in, road slides in, dashboard
   with a real GRAV-O-METER number = the level's gravity, e.g. Crab Nebula 100,
   Into the Sun 500).
6. **Level end — finish and death differ:**
   - **Natural FINISH** — the ship rides up the end ramp and flies off the top;
     the road recedes below, ship disappears, **no crash explosion, no SFX** →
     fade → **level-select grid** (verified f2300–2475 of the e2e demo). #39's
     correct behaviour: finish → menu, NOT respawn / fall off.
   - **DEATH** (red "Burning" tile, wall crash, fall) — the ship **explodes**
     (SFX **id 0**, the crash thud) and the **SAME level respawns** from the
     start (verified demo_death_redtile f39→f115-134). Death → respawn, NOT menu.
   - **ESC** during a level → fade → back to the level-select grid.
7. From the grid, **ESC** → back to the **main menu**; `Help` → **Help screen**
   (tile legend: **Supplies / Boost / Sticky / Slippery / Burning**, "Press ESC
   to exit help, SPACE for next page"); from the main menu, **ESC** → exit game.

**Special road tiles** (named on the Help screen): Supplies, Boost (green,
plays SFX id=4 when hit), Sticky, Slippery, Burning (red — the kill tile).

## Corpus blind spots (open risks — standing capture requests)

- **Red-tile death is now captured** (demo_death_redtile) — SFX id 0, explosion,
  respawn-same-level. Still uncaptured: a **wall-crash death** and a **fall off
  the road edge** (assumed to behave like the red-tile death — SFX id 0 +
  respawn — but not individually verified), and whether a **lives/game-over**
  path exists (this death simply respawned; no life counter observed).
- **`Controls` screen not captured** (the e2e demo visits `Help`, not
  `Controls`).
- The level-select **selection variable in memory** is still unmapped (the
  navigation model was derived from the on-screen highlight, not a DGROUP
  offset); selection→level-file mapping for native load is verified only via
  the world-asset loader's index, not the ROM's own confirm handler.
- The grav-o-meter **digit-rendering** path (dashboard text system) is not yet
  ported, so the native shows a baked "00" instead of the level's gravity
  number (#35).
