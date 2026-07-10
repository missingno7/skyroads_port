# SkyRoads run status

> Dated progress log — sections state what was true at their date. For the
> ledger of per-routine evidence see [`symbol_ledger.md`](symbol_ledger.md);
> open issues are in [`blockers.md`](blockers.md).

## 2026-07-10 — first AUTO-LIFTED island: the master timer ISR (1010:3B17)

The game's INT 08h handler (master clock + music tempo) is the port's first
island recovered with the **automatic lifter** (`dos_re.lift`) rather than by
hand. Workflow, end to end:

1. `dos_re/tools/liftverify.py --entry 1010:3B17 --timer-irqs 6` emitted a
   literal, per-instruction Python hook and verified it in situ — **199 calls,
   byte-exact** against the interpreted original (this also drove the new
   `--timer-irqs` option: a plain forward run never fires the ISR).
2. The mechanical lift was refactored into the port's pure-rule + thin-adapter
   shape: `skyroads/recovered/timer_isr.py::advance_music_timer` (VM-free, the
   prescaler/song/PIT-divisor decision, `@oracle_link ASM_MATCHED`) plus
   `skyroads/hooks.py::master_timer_isr` (the pusha/popa/iret frame, the
   sound-engine call, the PIT/PIC port writes).
3. A unit oracle (`tests/test_master_timer_isr.py`) drives **every prescaler
   value 0..9 x song-continue/end** and diffs full machine state against the
   interpreted `1010:3B17` — full basic-block coverage, incl. the wrap →
   reset-to-9 → chain-to-BIOS path whose `dec [3192]` flags survive to the far
   exit (the IRET path pops them away). 22/22 byte-exact.

Notable: the lift was correct on the flag detail above out of the box — the
kind of thing hand translation gets wrong. Suite green (154). This is the M3
proof of the lifter thesis on a real game: ASM → auto-lift → verify → refactor
to clean recovered source, same oracle throughout.

## 2026-07-10 — whole-game E2E validation of the recovered island

Replayed a full cold-start end-to-end demo (`artifacts/demos/
demo_e2e_20260710_132930`: intro-skip → main menu → level select → play a level
→ die → exit → play another level → exit to menu → quit) through the
fully-hooked runtime. **All 1,719 frames ran to the game's own `exit(0)`**
(HaltExecution at `1010:630F`, the `mov ah,4Ch; int 21h` terminate — the demo's
intended final action), with every recovered hook firing across the whole
lifecycle: `palette_fade` 408K, `fade_gate` 858K, `road_column_strip` 26.9K,
`road_object_visible` 17.3K, RLE sprites 32.5K, `tile_rasterizer` 615, the
three long-arith helpers, `lzs` 266 (multiple level/asset loads), etc. No hook
raised, no divergence, no hang — a strong whole-game integration pass across
menu, two gameplay levels, death, and exit.

Byte-exact spot-check on the E2E's (different) level data: `road_object_visible`
(`1732`) re-verified against the ASM oracle for 439 calls, zero divergence —
the recovery holds on levels beyond the ones it was developed against.

Also confirmed via the busier `world7` gameplay + level-load demos: the
projection LUT (`ds:0x162C`) is static across 956 active-gameplay frames and is
**loaded as data from the level file** (not computed); the "3D" is table-driven
throughout. See [`rendering_architecture.md`](rendering_architecture.md) and
[`level_format.md`](level_format.md).

## 2026-07-09 (cont'd) — in-game profiling + the renderer-island plan

**Why gameplay is ~2-3 FPS (measured, not guessed).** On this machine the
interpreter sustains ~626K 8086-steps/second. Frame decode (VGA→RGB, 0.4ms),
pygame present (0.5ms) and AdLib OPL3 pump (0.3ms) are all negligible — 98% of
a frame is interpreting instructions. The catch: a *viewer frame* (30,000
steps) is not a *visual frame*. Measuring steps between actual screen updates
gives ~57,000 steps/visual-frame average (heavy frames >130,000). 626K ÷ 57K ≈
low-single-digit FPS. So the bottleneck is purely "8086 instructions executed
per rendered frame", and the only lever is removing them (hooks) — presentation
is already free. A 10× speedup to smooth play cannot come from per-loop hooks
shaving percentages; it needs the whole render path lifted out of the
interpreter.

**Hooks installed this session (all differential-verified against the ASM
oracle, zero divergence):** `palette_upload` (6168), `sprite_blit` (3A22),
`occluded_column_blit` (3283), `ulong_div` (5D8C), `ulong_mul` (5D4C),
`rle_sprite_forward` (3153), `rle_sprite_backward` (3190), plus the behavioral
`fade_loop_tick_gate` (4344/434A). See `symbol_ledger.md`.

**Render call tree (mapped via caller-chain tracing on the in-game demo
`demo_skyroads_20260709_225824`).** Shallow → deep:

```
main loop (~22xx)
  render dispatch (~0C26/0C32/0C98/0CA2)
    per-object / road-segment render (~1732/1747/175C/17CD/1821/1846)   [NOT YET RECOVERED]
      fixed-point perspective transform  04C0                          [NOT YET RECOVERED - keystone]
        ulong_mul 5D4C / ulong_div 5D8C                                 [HOOKED]
      leaf rasterizers 3153 / 3190 / 3283 / 3A22                        [HOOKED]
```

**The renderer-island plan.** Goal: a clean, VM-agnostic recovered renderer
(a `skyroads/recovered/renderer.py` module) that, given game state, produces
the exact framebuffer the ASM does — wired in behind ONE thin hook at the
render root, verified whole against the oracle. Bottom-up, the leaf + math
layers are DONE. Remaining, in dependency order:
1. **`04C0` fixed-point perspective transform** — the keystone; every render
   path calls it, and it now depends only on the already-hooked long-arithmetic.
   DONE (2026-07-09): recovered as `skyroads/recovered/renderer.py::
   perspective_row_offset`, wired via a thin `perspective_transform` hook,
   VERIFIED byte-exact over all 34,786 in-game calls. First recovered-code
   layer of the island. (The recovery corrected a decode error — the third
   stage is a ×14 multiply via ulong_mul, not a divide.)
2. **`17xx` per-object/road-segment render** — the layer that projects a
   road segment / object via `04C0` and dispatches to the rasterizers. The
   root is `1732` (`enter 0xA`), which calls `04C0` four times AND the leaf
   `1631` twice. `1631` (a self-contained per-segment visibility/clip test,
   NO calls) is DONE (2026-07-10): recovered as `renderer.py::
   road_segment_clip`, ASM_MATCHED over all 9,238 in-game calls (selectors
   0x100/0x200/default exercised; 0x300/0x400/0x500 decoded but not hit in
   this demo). Per the island strategy, leaves are recovered as clean
   functions WITHOUT their own hook; the single hook goes at the island root
   (`1732`), where the whole subtree — `04C0` + `1631` + the clamp/dispatch
   glue — collapses into one verified Python call. The `1732` ROOT itself is
   now DONE as a clean function (2026-07-10): `renderer.py::
   road_object_visible`, ASM_MATCHED over all 12,152 in-game calls (both
   return values exercised). It projects the segment's near/far edges via
   `04C0`, runs the nibble + screen-band cull, and on survivors does a
   mirrored two-sided `1631` clip — pure, no memory writes, returns 0/1.
   DONE (2026-07-10): the `1732` hook is wired + VERIFIED byte-exact over all
   12,152 in-game calls (collapsing its four nested `04C0` calls plus the cull
   glue into one Python call). Exit BX/CX/DX are reproduced by threading the
   nested `04C0`/`1631` calls' exit registers through the taken path. With
   this, layers 1+2 of the island are fully hooked; `04C0` dropped out of the
   top hooks (most of its 34K calls came from `1732`). Layer 3 (the `0Cxx`
   render dispatch that would become the island's single top-level boundary)
   is the remaining upward step.
   Separately, the biggest single in-game render cost, the `38BF` road-column
   strip compositor, is now hooked + VERIFIED (14,896 calls, ~1.4x demo
   wall-clock); the RLE leaf rasterizers (`3153`/`3190`) were already hooked.
   Profiling note: excluding the `22F8` pacing spin (28% of interpreted
   steps, an idle timer-tick wait — the game finishes a tick's work then
   spins for the rest of the fixed step budget), the real render work is
   ~24% in the `17xx`/`18xx` glue and ~26% in the `35xx`/`39xx` stride-3
   display-list rasterizer scans (the biggest single un-hooked leaves).
3. **`0Cxx` render dispatch** — the per-frame "draw the whole scene" entry;
   this becomes the island's single hook boundary once 1–2 are recovered.
Each layer is recovered + verified before the next, so the island grows upward
with the differential verifier guarding every step — the same methodology used
for LZS and the leaves.

## 2026-07-09 (cont'd) — the menu "halt" was a VM bug (phantom Esc); + AdLib audio

**Root cause of the spurious main-menu exit: a framework input bug, not a game
decision.** The game reads menu keys with `INT 21h AH=07h` (blocking `getch`,
recovered at `1010:5FEB`) and treats Esc as "quit". `DOSMachine` defaulted
`console_input_fallback` to `0x011B` (**Esc**) so a bare headless `cpu.run()`
wouldn't hang on a blocking read — but nothing in the player NEEDS that: every
driver path routes blocking reads through `_step_frame`, which already catches
`ConsoleInputWouldBlock` and reports "waiting for DOS key" without hanging. So
the Esc synthesis was pure downside: with no real key queued, `getch` returned
Esc, the game read "quit", and called `exit(0)` — surfacing as "program
halted" at the menu a few seconds in, with no keypress. Traced by walking the
exit path from the owner's pre-halt snapshot: `58C3` AdLib-register-clear loop
→ `005A` (`call 5BC0` SB DMA/DSP cleanup) → `005D` (`push 0`) → `6001`
(`pop/pop; jmp 630B`) → `630B` (`mov ah,4Ch; int 21h`) — the textbook C-runtime
`exit(0)` epilogue (silence AdLib, shut down SB, restore text mode, exit),
reached because `5FEB`'s `getch` returned `0x1B`.

Fix (`dos_re/dos_re/player.py::_use_real_console_input`): the player clears
`console_input_fallback` to `None` for all modes right after runtime
creation, so blocking console reads wait for a real key (interactive) or the
demo/queue (headless/replay). Verified: from the main menu the game now blocks
at `5FEB` waiting for input instead of exiting; delivering Enter advances it
through the fade into the road-select / level-intro screens it never reached
before. Both suites green (154 dos_re + 123 skyroads_port).

**AdLib audio (`--audio adlib` was silent):** the OPL register-write plumbing
(`0x388`/`0x389` → `_notify_adlib` → `AdlibSpeakerSink._on_adlib` →
`OPL3.write`) was fine — the `pynuked_opl3` C extension simply wasn't built,
so `AdlibSpeakerSink._opl` was `None` and rendered nothing. Built it once
(`python -m pynuked_opl3._ffi_build`, needs MSVC Build Tools, which are
present); it lands in the shared `ancient_port/dos_re/pynuked_opl3/` copy
(that's where the editable install resolves `pynuked_opl3` for every sibling
port). Confirmed the game's own 117 boot/menu OPL writes now synthesize
audible PCM (peak 2721 / rms 1228, was total silence). Re-run `play.py
--audio adlib` to hear it.

## 2026-07-09 (cont'd) — LZS decode-loop hook finished: installed and verified

Finished the LZS decoder performance island. The codec fix (`1<<
WIDTH_DIST_LONG` short-distance base, see `blockers.md`) only surfaced fully
once a *third* file (`INTRO.LZS`, `WIDTH_DIST_LONG=9`) was tested — `TREKDAT
.LZS` and `MUZAX.LZS` both use `WIDTH_DIST_LONG=10`, so two files' worth of
testing had coincidentally never distinguished "fixed 0x400 constant" from
"computed per file." Lesson: a fix that passes on N files sharing a parameter
value is not verified against that parameter — the discriminating test needed
a file where it actually varies.

The hook itself (`skyroads/hooks.py::lzs_decode_loop_hook`) needed six
additional real bugs fixed in its own state bookkeeping (see `blockers.md`
for the full list) before `dos_re.verification`'s strict full-memory
differential verifier came back clean — 15 hook calls, zero divergence,
across `TREKDAT.LZS` (all 9 records), `MUZAX.LZS`, and `INTRO.LZS`. Now
installed by default (`@registry.replace` active).

Measured impact: pure-ASM interpretation needs 144,515 to 1,176,774
instructions per LZS block (11+ blocks during boot) — in a fixed
3,000,000-instruction budget, pure-ASM is still stuck decoding the *first*
file (`CS:IP 1010:6508`) while the hook gets completely through *all*
boot-time LZS decompression and into subsequent loading logic (`CS:IP
1010:6197`). Full test suite: 123 passed.

## 2026-07-09 (cont'd) — menu "halt" investigated: not a bug, an idle timeout

A user-reported halt (`gap_snapshot_skyroads_20260709_163042`, CS:IP
`1010:630F`, `AX=4C00` right after `INT 21h AH=4Ch`) turned out to be the
game's own **normal** exit-to-DOS sequence (palette fade-out, then a Sound
Blaster DMA-halt/DSP-command cleanup at `1010:5BC0`-`5BDA`, then a clean
`exit(0)`) — not a crash. Reproduced deterministically from the "right before
halt" snapshot (`snapshot_skyroads_20260709_160101`, confirmed via
`tools/render_frame.py` to be sitting at the main menu) two ways: pressing
Enter alone, or providing **no input at all**, both lead to the exact same
exit within ~73,000 steps. This means the main menu has an idle timeout
(likely tied to demo/attract-mode playback finishing) that exits to DOS if no
navigation key registers quickly enough after the menu appears.

Pressing an arrow key (e.g. Down) before Enter avoids it entirely — traced
200 frames with `Down` then `Enter` with zero halts, ending on the level
-select screen (`Red Heat` / `Asteroid Belt` / ... each with `Road 1/2/3`),
confirming asset loading and menu-to-gameplay progression both work cleanly.
**Practical takeaway for interactive play: press a navigation key (arrow)
promptly after the menu appears, before Enter/Start.** No framework or hook
fix needed here.

## 2026-07-09 (cont'd) — real halts fixed: memory allocator + sound detection

Two real bugs found via user-reported halts (both now fixed and confirmed
clean over 90M+ instructions each):

**Memory allocator never reclaimed freed blocks.** `dos_re`'s AH=49h (free
memory) handler dropped the tracking record but never made that address
range reusable — a bump-pointer allocator with no reuse. SKYROADS cycles
scratch buffers heavily (269 allocs vs 255 frees in one session), so this
silently exhausted the ~576KB conventional-memory budget well before a real
DOS machine would, producing a genuine "Not enough memory" exit mid
`intro.lzs` decode. Fixed in the canonical `dos_re` repo
(`D:\Games\DOS\dos_recosystem\dos_re`, then synced into this submodule
checkout): `DOSMachine._free_gaps()`/`_find_free_gap()`/`_largest_free_gap()`
implement deterministic first-fit allocation over the current live
allocations, so a freed block's address range becomes reusable immediately
— matching how a real DOS MCB chain behaves by default. Confirmed reclaiming
a real 188KB gap that was previously wasted; both `dos_re`'s own 153 tests
and this repo's 121 pass.

**Sound Blaster never attached, so detection legitimately found nothing.**
SKYROADS probes SB ports 0x220-0x270 (standard DSP reset handshake) at boot
and, once one responds, assumes its onboard OPL is present too and starts
loading FM instrument patches — there's no separate AdLib-only probe. With
no SB attached in our runtime, all six candidates fail and the game
hard-exits (`mov ah,4Ch`) with no printed message, sometimes well past the
intro (reached the menu before hitting it in the reported case). Traced the
*entire* SB+OPL sequence live (DSP reset -> `0xAA` ack -> `Speaker On` -> OPL
instrument register writes) to confirm this reads as completely normal,
successful hardware init once a Sound Blaster is actually present — not a
detection-handshake mismatch as first suspected. Fixed by wiring
`dos_re.runtime.enable_sound_blaster(detection_only=True)` into
`skyroads/runtime.py`'s `create_game_runtime`/`load_game_snapshot` (on by
default, `enable_sound=False` to reproduce the original exit for study).
**Must run on a fresh boot** — attaching it to an already-halted snapshot
does nothing, since "no sound" is already recorded in the game's own memory
by the time detection ran. Confirmed clean over 90M instructions from a
fresh boot with no further halt.

**Halt diagnostics** (also `dos_re.player`, canonical + synced): any
`HaltExecution`/`UnsupportedInstruction`/exception now prints DOS console
stdout (many DOS programs print a plain-text reason before exiting — this is
exactly what revealed "Not enough memory"), a compact memory-allocator
summary, and open file handles, and always auto-saves a resumable gap
snapshot — previously only generic exceptions got a snapshot, and the
message was just "program halted" with zero context.

## 2026-07-09 (cont'd) — the real bottleneck: a 6:1 software timer prescaler

After installing the palette-fade hook, re-profiling the same snapshot
surfaced a much bigger, structurally different cost: a generic "wait until
ds:[1600] (elapsed ticks) reaches a threshold OR a key is pressed" poll loop
(`1010:4465`-`417D`, called between palette-fade passes and presumably
elsewhere). Live-tracing SKYROADS' own INT 08h ISR (`1010:3B17`) found the
real mechanism: a software prescaler at `ds:[3192]` that only increments
`ds:[1600]` once every **6** real timer interrupts — an intentional ~3 Hz
game-tick rate divided down from the 18.2 Hz BIOS timer. This is *correct,
original pacing*, not a bug — a real DOS machine would also only see this
counter advance ~3 times/second. The bug is in how a driver delivers INT 08h:
`scripts/play.py` (and every benchmark/probe script in this session) had
been delivering exactly 1 IRQ before a large step budget, so 5 out of every
6 driver frames advanced this counter not at all while still burning a full
interpreted step budget spinning uselessly in the wait loop.

**Fix (driver-level, no CPU hook, no verification risk):** `scripts/play.py`'s
`SkyroadsFrontend` now delivers 6 IRQs per frame (matching the real
prescaler exactly) with a smaller per-frame step budget (200,000 -> 30,000,
empirically tuned — see `symbol_ledger.md`) so those bursts land far more
often per wall-clock second. Measured head-to-head via `scripts/play.py`
itself, both from the same intro-fade snapshot, 100 frames each:

| | steps | wall time | ending state |
|---|---|---|---|
| old (1 IRQ / 200K steps) | 20,004,362 | 95.3s | still in the same fade phase it started in |
| new (6 IRQ / 30K steps) | 3,018,728 | 12.7s | progressed into an entirely new code region |

**~7.5x faster wall-clock, using 6.6x fewer total instructions** — the win
comes from eliminating wasted busy-wait cycles, not from running faster in
any crude sense. This is very likely the dominant cause of the "1 frame
every 3 seconds" symptom originally reported. Not yet re-validated against
real gameplay (still unreached) — the tuning (30,000 steps/frame) was
optimized against this specific intro-fade snapshot and may need revisiting
once gameplay code is reachable, since a non-wait-bound game-logic frame
might need a larger budget to complete meaningful work.

## 2026-07-09 — first verified + installed hook: palette-fade inner loop (6.7x)

The palette-fade inner loop (`1010:43A9`-`442D`, see `symbol_ledger.md`) is
now hooked, verified (34,439 calls, zero divergence), and installed. Fixed
three real bugs along the way (missing register writeback, `idiv` remainder,
`LES` also loading ES) — each caught immediately by the differential
verifier with a precise register/segment diff, never guessed. Measured
**6.7x wall-clock speedup** processing the same amount of fade animation.

**Also found and fixed a real bug in `skyroads/runtime.py`:**
`load_game_snapshot` called `dos_re.snapshot.load_snapshot` directly without
ever calling `registry.install(cpu)` on the restored CPU — so a hook
installed via `@registry.replace` (like this one) silently never ran on any
snapshot-resumed session, only on a fresh `create_game_runtime` boot. This
would have made every future hook look like a no-op whenever tested against
a snapshot (which is most of the time, since fresh cold boots are slow).
`scripts/play.py`'s `--snapshot` resume path uses `load_game_snapshot`, so it
was silently affected too — now fixed, no caller changes needed.

**Process note on verification cost:** the strict differential verifier
(`HookVerifierConfig.strict()`) clones the full 1MB memory image, re-runs the
original ASM a second time, and diffs the whole memory image — *per hook
call*, by design (its own docstring: "for small targeted investigations, not
fast gameplay"). An initial 30M-instruction verification budget was wildly
oversized for what's needed to build confidence; ~250K instructions (34K+
hook calls, ~45 full passes with many pass-boundary transitions) is plenty
and runs in well under two minutes. Scope future verification runs
accordingly rather than defaulting to a huge budget.

## 2026-07-08 — bring-up: boots, first island (asset decompressor) recovered

**Boots and runs stably.** `assets/SKYROADS.EXE` boots and runs in the `dos_re`
VM (confirmed over a 300M-instruction / 1500-simulated-frame soak, rendering
the real title/attract-mode checkerboard road in VGA mode 13h). Framework-level
gaps fixed to get there (all in `dos_re/`, not game-specific):
- INT 10h AH=1Ah (Get/Set Display Combination Code) — `dos_re/dos.py`.
- PIT channel-0 counter read-back (SKYROADS busy-waits on the raw hardware
  counter via a latch command on port 43h + `IN AL,40h`, not through IRQ0) —
  `dos_re/dos.py`. Deterministic default ages the counter from
  `cpu.instruction_count` when no wall-clock `time_source` is set.
- 80186 `PUSHA`/`POPA` (opcodes 0x60/0x61) — `dos_re/cpu.py`, not implemented
  at all before this.
- INT 21h AH=0Bh (check stdin input status) — `dos_re/dos.py`.
- The title-screen idle loop separately needs the INT 08h timer tick
  delivered (real IRQ0, not just the PIT counter model) — a raw
  `create_runtime` + `cpu.run()` probe with no IRQ pump appears to hang here;
  it isn't a bug, see `scripts/play.py --timer-irqs-per-frame`.

**Adapter scaffold.** `skyroads/` package created (`runtime.py`, `hooks.py`,
`verification.py`, `frame_verify.py`, `input_waits.py`, `recovered/`,
`bridge/`, `codecs/`, `probes/`), wired into `tools/lint.py`, covered by
`tests/test_skyroads_boot.py` (skips without `assets/`). No hooks recovered
yet — everything currently runs as pure ASM oracle.

**Interactive runner.** `scripts/play.py` — a thin `GameFrontend` over the
unified `dos_re.player` runner (standard CLI: viewer by default, `--headless`
to disable; F11 demo record/stop, F12 snapshot save, F10 screenshot;
`--snapshot` resume, `--play-demo` replay). Verified end-to-end:
record → replay reaches byte-identical CS:IP/registers/instruction-count;
snapshot-save → resume continues correctly (matches a fresh continuous run).
Deterministic by construction: both live play and replay use the same fixed
(steps-per-frame, timer-irqs-per-frame) budget per frame with no wall clock,
so — unlike a fully-tuned adapter — record/replay determinism needed no
extra clock-model work.

**First island: the `.LZS`/`.DAT` asset decompressor.** Traced live via
`tools/profile_hotspots.py` (hottest region CS:IP `1010:64A0`-`1010:675E`
while loading `TREKDAT.LZS`) + a forced linear disassembly + register-level
single-step trace of the *live* code (this routine is copied/patched into the
code segment at runtime — it reads as all zero bytes in the static EXE
image). Recovered into `skyroads/codecs/lzs.py`, status **OBSERVED** (traced
from the oracle, not yet round-trip verified — see `symbol_ledger.md` and
task tracking for the open ends).

Algorithm: an MSB-first bit reader refilled from a 4KB file-backed staging
buffer, feeding a 3-way LZ77-style loop — one flag bit selects a
long-distance match, else a second flag bit selects a short-distance match or
a raw 8-bit literal; match length and both distance variants use bit-widths
read as 3 raw header bytes and patched into the decoder as self-modifying
immediates before the loop starts.

**External cross-reference (2026-07-08):** the independent RE project
[ammaarreshi/SkyRoads-Codex](https://github.com/ammaarreshi/SkyRoads-Codex)
(a from-scratch native Rust port, DOSBox-X + static-analysis based, not
affiliated with this project) published structurally matching findings —
notably "3 bytes: SkyRoads compression widths" per compressed block, and
concrete widths `(4, 10, 13)` for `TREKDAT.LZS` / `(6, 10, 12)` for
`MUZAX.LZS`. This corroborates but does **not** replace our own oracle
verification (pitfalls.md #21 — an external write-up is a hypothesis to
check against our VM, never a source to copy blind); it is however a very
useful map of the surrounding file formats we have not yet traced ourselves:
`CMAP`/`PICT` image chunks, `ROADS.LZS`'s 31-entry offset table, `MUZAX.LZS`'s
song table, `DEMO.REC`'s control-byte decode, and the dashboard `*.DAT`
fragment format. Treat every one of its claims as a lead to verify against
our own trace, not a ground truth — see `blockers.md`/task tracking for what
that verification pass should check first (their published `TREKDAT` record
header layout: `load_buff_end:u16, bytes_to_read:u16, widths:3 bytes,
payload` lines up with what we traced independently and is the natural next
thing to confirm byte-for-byte).

## 2026-07-08 (cont'd) — LZS bug fix + performance hot-spot found

**LZS decoder: found and fixed a real bug via oracle byte-diff.** Round-trip
verifying `skyroads/codecs/lzs.py` against `TREKDAT.LZS` record 0's actual
decompressed memory (dumped from a fresh boot, segment `2B12`) found the
match-length formula was wrong: `get_bits(WIDTH_LEN)+1` should be `+2` (the
ASM's `LOOP` body does `get_bits(WIDTH_LEN)+1` copies, plus one more
unconditional `movsb` afterward). Fixing it took the exact-byte match from
933/18072 to 8964/18072 (~50%). A further, precisely localized divergence at
output-relative byte 2938 (in a short-distance match) remains open — logged
in `blockers.md` with the full symbol-trace evidence rather than guessed at
further, per the project's own "two focused attempts, then log it" rule.

**Performance: found the dominant hot loop, it's a palette fade, not (yet
confirmed) pixel drawing.** From an owner-captured snapshot at the intro
fade-in (`artifacts/snapshot_skyroads_20260708_165846`),
`tools/profile_hotspots.py` found a ~40-instruction loop at CS:IP
`1010:43A9`-`442D` dominating a 3M-instruction profiling window (~57K hits).
Disassembly + a snapshot-based trace identified it precisely: a per-byte
linear interpolation between two palette arrays for a fade transition (see
`symbol_ledger.md`). The intro does not appear to auto-advance past this
fade on a timer (confirmed independently by SkyRoads-Codex's own DOSBox-X
trace notes) and repeated keypress injection didn't unstick it either within
our probing budget, so we have not yet reached the actual gameplay
road/pixel renderer to confirm whether IT is also a big win. This fade loop
is nonetheless a real, well-evidenced, high-value hook target on its own —
not yet hooked, because its stack-frame indexing has the same kind of subtle
off-by-one risk the LZS bug just demonstrated is real on this codebase; the
right next step is writing the hook AND running it under
`dos_re.verification.install_hook_verifier` (strict/auto-continuation mode)
before trusting it, not hand-verifying by inspection.

## 2026-07-09 (cont'd) — LZS decoder root-caused and fixed; decode-loop hook drafted

The startup-speed investigation ("cold boot takes quite a long time before I
can see anything") led back to the LZS decoder's long-standing residual
divergence (logged in `blockers.md` since 2026-07-08 at "output-relative
position 2938"). Two rounds of bit-level tracing this session initially
produced *contradictory* results against the earlier symbol-level trace —
root cause: every earlier capture attempt (including this session's first
two) aligned to the target record via a blind instruction-count guess or an
"already patched" poll, both of which are fragile since many unrelated
decode calls across many files share the exact same width-patch address
(`1010:671F`) and even the same values. Fixed by anchoring instead to the
actual `INT 21h AH=3Dh` open of `TREKDAT.LZS` (watching `dos.files` for the
real DOS file-open event, not a memory-write heuristic) — this reproduced the
earlier 8964/18072 match figure exactly, confirming that number was real,
just mis-diagnosed as "divergence at 2938" when the true first divergence is
at byte 1111.

With reliable alignment, a live disassembly of the divergent symbol
(`1010:6750`: `05 00 04` = `ADD AX,0x0400`) found the actual bug: the
short-distance match formula is `get_bits(WIDTH_DIST_SHORT) + 0x400 + 2`, not
the previously-assumed `+3` (a guess-by-analogy with the long-distance
branch that was never actually verified). Full-record verification: 18072/
18072 bytes of `TREKDAT.LZS` record 0, and 3000/3000 bytes of record 1, both
100.00% exact against `skyroads/codecs/lzs.py`. Status raised OBSERVED ->
VERIFIED. Regression tests added (`tests/test_lzs_codec.py`).

Drafted the decode-loop hook (`skyroads/hooks.py::lzs_decode_loop_hook`,
`1010:6712`) to decode an entire block in one Python call instead of one
interpreted iteration per symbol (the actual startup-speed payoff). Required
reverse-engineering the staging-buffer refill mechanism in full (`1010:6350`,
`ds:[41AC]`=file handle, `ds:[41B2]/[41B4]/[41B6]`=buffer start/end/cursor)
to correctly simulate DOS file-position advancement and buffer-cursor state
across chunk boundaries, plus per-symbol scratch-register reconstruction
(AX/CX/DX/SI/FLAGS) to satisfy the strict full-memory differential verifier —
all of which now verify byte-exact except one register, BX, off by a fixed
delta on the one call tested so far (likely a dead scratch value, not yet
proven). **Not installed** pending that last gap — see `blockers.md`.

## Next up
- Find the frame boundary (present/blit routine) so the frame verifier can be
  stood up (`docs/porting_new_game.md` step 3-4).
- Build the input-wait registry for the title/menu polls (step 5) before
  recording any demo intended as a regression asset.
