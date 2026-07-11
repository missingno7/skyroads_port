# What's missing for a complete VM-less SkyRoads port (2026-07-10)

## Where we are: a hybrid, not a port

Today SkyRoads runs as **interpreted 8086 ASM inside the dos_re VM**, with ~17
recovered routines installed as hooks that replace the hottest code. It is a
*hybrid*: the recovered "islands" are correct and verified, but 99%+ of the game
still executes as the original binary under the interpreter, and dos_re still
provides all the DOS/BIOS/hardware services.

A **complete VM-less port** means the opposite end state: every routine the game
executes runs as recovered native code, calling a thin host layer for I/O, with
**no interpreter and no original binary** — and the ASM oracle retired.

## The coverage gap, measured

Replaying the full cold-start E2E demo (menu → level select → play → die → exit
→ another level → quit) and counting what actually runs:

| Metric | Value |
|---|---|
| Distinct functions executed (near-call targets) | **131** |
| …of those currently recovered | ~**17** (~13%) |
| Code-segment 256-byte pages touched | **98 / ~176** (~55%) |
| DOS `INT 21h` calls made | 929,386 (input poll, getch, file load, exit) |
| BIOS `INT 16h` calls | 6 |

The ~17 recovered routines are the **render + math hot path** — the hardest and
most performance-critical part. The other ~114 functions (the majority by count)
are still interpreted.

## Progress: the first native (VM-less) frame steppers (2026-07-11)

`skyroads/native/` now composes the currently-recovered game-logic islands
into real, VM-free per-frame steppers over a `NativeGameState` (a plain
`bytearray`, no VM) via a named `GameView` (`skyroads/bridge/dgroup_view.py`,
using the shared `dos_re.state_view` machinery promoted from pre2_port). See
run_status.md's 2026-07-11 "first native (VM-less) frame steppers" entry for
the full account, including a real vertical-velocity divergence this work
found and fixed. Honest state:

- `native_menu_frame` — **complete and gap-free.** Every level-select
  transition (`dispatch_menu_action`) is recovered.
- `native_gameplay_frame` — commits forward motion (real-demo-proven), then
  raises one of three typed gaps (`skyroads/native/gaps.py`) on every real
  gameplay frame tested so far: the jump-impulse latch, the vertical-velocity
  gate outside one narrow verified envelope, or (always, since it's reached
  last) the movement-target block. No full native gameplay frame has
  completed yet — this narrows exactly what item 1 below still needs, rather
  than replacing it.
- Later the same day: the movement-target FORMULA (`1010:2635-26E6`,
  `compute_movement_targets`) got recovered, and then the WHOLE movement
  pipeline (targets → `resolve_move` → `collision.make_visible`) was proven to
  reproduce the VM's post-move axes 300/300
  (`tests/test_native_movement_pipeline.py`). The movement math has no
  remaining gap. `native_gameplay_frame` still can't call it because of one
  input — `lateral_accel`, stateful steering momentum (see `MovementPhysicsGap`
  and run_status.md) — so the "always gapped" state above is unchanged
  operationally, but the gap is now a single, precisely-named next island.

## What's missing (in rough dependency order)

1. **Game logic — mostly mapped, partially recovered, not yet fully wired.**
   Forward motion (`advance_ship`), the menu/level-select dispatcher, and the
   entire lateral/vertical movement PIPELINE are recovered:
   `compute_movement_targets` (`1010:2635-26E6`) → `resolve_move` (`186B`) →
   `collision.make_visible` reproduces the VM's post-move axes **300/300**
   (`tests/test_native_movement_pipeline.py`) — the movement MATH has no
   remaining gap (`af1c_base_offset` is the constant `0x0618`; the earlier
   "unrecovered selector" reading was corrected — see run_status.md). Still
   pipeline's `lateral_accel` input, the jump latch, and gravity are now ALSO
   recovered — `skyroads.recovered.dynamics.step_jump_steer_gravity`
   (`1010:252B-2635`, 415/416 vs VM), which carries a session-persistent
   `JumpScratch` (`bp-8`/`bp-10`/`bp-6`). The perspective **classification**
   (`1010:2324-23BF`) that produces the `bp-14`/`bp-18` flags
   `step_jump_steer_gravity` needs is now ALSO recovered —
   `skyroads.recovered.classify` / `skyroads.native.classify` (682/682 vs VM).
   `skyroads.recovered.classify` / `skyroads.native.classify` (682/682 vs VM).
   And the post-move tail's **level-progression state machine**
   (`1010:2A35-2AE2`) is recovered too — `skyroads.recovered.progression`
   (682/682 vs VM): the level timers (`[5494]` distance/"fuel", `[B13C]`
   time/"oxygen") and the `game_state` transitions (`0→3` resume when
   `af2c<0x2800`, `0→4`/`0→5` timer-expired) — i.e. the level-complete /
   out-of-time death logic. (Also fixed an inverted resume-gate bug in
   `player.is_landed_for_resume` found in the process.)
   So classification + dynamics + movement pipeline + level progression are all
   recovered and proven. What's still open before `native_gameplay_frame` can
   run the whole chain end to end: (a) the **collision-response** middle of the
   tail (`26EC-2A24`) — mostly recovered now in
   `skyroads.recovered.collision_response`: the vertical `1732`-probe scan
   (`vertical_center_nudge`, 314/314), the lateral wall-bump (`lateral_wall_bump`,
   511/511 on a collision demo incl. a real bump), and the af1c contact fix-up
   (`af1c_contact_fixup`, 511/511 incl. real collisions). Still open in this
   region: the position milestones (`27A3-2800`, `[54AC]>=0xE38` →
   `[456A]/[456E]:=1`) and the `bp-8`-clear landing check (`28DC-2901`, mapped);
   (b) the upstream
   `decay_bounce` region (`2421-24BA`) and early visibility check
   (`23CA-2421`); (c) the `1B49` gameplay side effect (`classify` flags it,
   doesn't model it); (d) the `1DFA` special effect (`25AC-25D6`); (e) `bp-12`'s
   full drive (set at `206C`/`2901`, cleared at `28D7`). Then: scoring, and the
   session-scratch plumbing to carry `bp-6/8/10/12/14` across frames.

2. **The orchestration / state machine.** The main loop and the game-state
   dispatch (`ds:[456A]/[456E]/[4558]`) that sequences intro → menu → level
   select → gameplay → death → exit. The top-level `0C98`/`22xx` frame driver.
   (`native_menu_frame`/`native_gameplay_frame` are per-mode steppers a future
   dispatcher would call — they don't decide which mode is active themselves.)

3. **The menu/UI subsystem.** Level-select's ACTION DISPATCH is recovered and
   wired natively (`native_menu_frame`, see "Progress" above). Still open:
   the rest of the menu/UI subsystem — main menu, settings, help/credits, the
   "go" screens — several screens' worth of layout, input handling, and
   rendering (some of which reuse the recovered sprite/blit leaves).

4. **Input.** Keyboard handling: today the game polls `INT 21h AH=0Bh`/`07h` and
   reads BIOS scancodes; a port maps host key events to the game's key-state
   model directly (no INT).

5. **Sound.** The AdLib/OPL + Sound Blaster driver (music + SFX). Either recover
   the driver and feed a software OPL/PCM, or reimplement playback from the
   MUZAX/asset data.

6. **The level cell-format — finish the decode.** The container, blocks, and
   roles are decoded (see `level_format.md`), but the exact per-cell/record bit
   layout of the level grid is still open (it uses far-pointer indirection). A
   port must parse `WORLD*.LZS` into its own level structures, so this has to be
   completed.

7. **A host abstraction to replace dos_re.** Currently dos_re emulates: the
   mode-13h VGA framebuffer (→ a window/texture), the DAC palette, the PIT timer
   / frame pacing, keyboard, DOS file I/O (for `WORLD*.LZS`, `MUZAX`, etc.), and
   program exit. A standalone port needs a small platform layer providing these
   — but as plain host calls, not emulated hardware.

8. **Retiring the oracle.** The endgame flip: once recovered code covers the
   whole executed path, run the game entirely on recovered code and
   **frame-verify it against the ASM** over long playthroughs (the
   `frame_verify` infrastructure already exists), then delete the interpreter
   and the binary. The "islands merge into a continent," and the VM becomes just
   the host layer.

## What's already done / de-risked

- **The hardest part is recovered and verified.** The table-driven pseudo-3D
  renderer (perspective transform, per-segment cull, road-column/tile/sprite
  rasterizers) and the 32-bit long-arithmetic primitives are byte-exact vs the
  ASM over full gameplay and the whole E2E lifecycle.
- **The data side is largely solved.** The LZS codec is recovered; the
  `WORLD*.LZS` container + block roles are understood. Assets (palette, tile/
  sprite graphics, the projection LUT) are *data the port loads*, not code to
  rewrite.
- **The architecture holds no surprises.** It is a static-camera, data-driven,
  pre-scaled-sprite pseudo-3D engine — simple and well-structured. No 3D math,
  no dynamic codegen, no self-modifying hot paths beyond the known LZS width
  patch.
- **The methodology is proven and scales.** Every recovery is a thin adapter
  over clean code, differential-verified byte-exact against the ASM. The same
  loop applies to the remaining ~114 functions, and the "collapse" pattern
  (e.g. `325B` subsuming `32C1`+`33FD`, `1732` subsuming `04C0`+`1631`) folds
  islands into higher-level modules on the way up.

## Honest scale

The recovered ~17 routines are ~13% by function count but represent the hardest,
hottest slice. A complete VM-less port is a **substantially larger** body of
work — most of the remaining ~114 functions (game logic, menus, orchestration,
sound) plus the host layer and the finished level-format decode. The good news:
almost all of it is *simpler* than what is already done, the risky unknowns are
retired, and the verification methodology guarantees correctness at each step.
It is very achievable — it is now a matter of breadth, not of open questions.
