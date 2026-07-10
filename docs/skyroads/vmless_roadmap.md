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

## What's missing (in rough dependency order)

1. **Game logic — none recovered yet.** Ship physics (movement, thrust, jump,
   gravity, speed), **collision detection** (ship vs blocks — the core rule of
   the game), fuel/oxygen, scoring, death/level-complete conditions. These were
   never hooked because they are not performance-hot, but a port cannot run
   without them. Most of this is straightforward integer logic — far simpler
   than the renderer already done.

2. **The orchestration / state machine.** The main loop and the game-state
   dispatch (`ds:[456A]/[456E]/[4558]`) that sequences intro → menu → level
   select → gameplay → death → exit. The top-level `0C98`/`22xx` frame driver.

3. **The menu/UI subsystem.** Main menu, level select, settings, help/credits,
   the "go" screens — several screens' worth of layout, input handling, and
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
