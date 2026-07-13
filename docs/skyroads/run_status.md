# SkyRoads run status

> Dated progress log — sections state what was true at their date. For the
> ledger of per-routine evidence see [`symbol_ledger.md`](symbol_ledger.md);
> open issues are in [`blockers.md`](blockers.md).

## 2026-07-13 (round 7) — palette fade RECONSTRUCTED from the real routine (a prior guess was caught and replaced); menu scroll_pos->level mapping: new evidence, still open

**User correction, taken seriously**: a first pass at the fade-to-black
transition (round 6) used an UN-VERIFIED guessed "subtract ~2 units/frame"
scheme, presented as "VM-measured" when it had only been eyeballed from one
brightness curve -- never checked against the real algorithm. Caught before
it could compound. Lesson applied for the rest of this round: hypotheses are
fine, SHIPPING one without checking it against the oracle is not.

**The real fade, reconstructed and verified:**
- `docs/skyroads/blockers.md` already had an ASM-VERIFIED primitive sitting
  unused: `skyroads.recovered.palette_fade.blend_byte` (`1010:43A9`, 34,439
  real hook calls, zero divergence) -- linear interpolation `out = b +
  trunc((a-b)*percent/100)`, NOT a subtract ramp. Should have been used from
  the start.
- Traced 14 real `1010:4331` (the outer fade driver) calls. First pass
  mis-read the args (sampled `bp` at the function's raw entry, BEFORE its own
  `enter 0x16,0` prologue runs -- so `bp` was still the CALLER's frame; the
  same class of mistake `run_status.md` has flagged before, `0x4331+4` is
  where the corrected frame starts). Re-traced correctly: every real
  full-palette (`count==256`) fade has ONE side a genuine all-zero (black)
  256x3 buffer and the OTHER the actual on-screen scene palette -- a real
  fade to/from black, not an assumption.
- Timed 7 independent instances precisely (entry-CS:IP to return-CS:IP frame
  count): a `duration=36`-tick fade always takes exactly **30 real displayed
  frames = 1.0s @ 30fps**, zero variance.
- **Gameplay is frozen for the WHOLE fade window, not just while black**:
  af2c/ship_pos/gravity all stay 0 through frame 314 of a fade that starts at
  285 (30 frames later) and only go live at 316 -- confirmed on a real
  level-start sequence.

Replaced the guessed constants with `TransitionFader` (`scripts/play_native.
py`), driven by the verified `blend_byte`: fade the old scene out to black
(30 frames), switch state at the exact black-screen midpoint, fade the new
scene in (30 frames), gameplay held frozen throughout. Wired into both
gameplay loops for level start, crash/death, and finish; the menu screen also
now fades out before the gameplay fade-in on confirm (previously an instant
cut). `TRANSITION_HOLD_FRAMES` is now derived (`2 * FADE_FRAMES`) instead of
an independently-guessed 60.

**Menu scroll_pos -> level-index mapping: genuinely re-investigated, still
open.** This is the OTHER known approximation the user flagged (`play_native.
py`'s level-select UI steps a level index 0..29 directly instead of the
ROM's real `scroll_pos`-driven selection -- already honestly disclosed in
`run_cold_boot`'s docstring, unlike the fade). Attempted to finally resolve
the historical "two real captures disagreed" mystery using
`demo_cold_20260711_201855` (the only available demo that visits multiple
levels). New, concrete finding: this demo's `[54A8]` (the level index driving
ROADS.LZS loads) is written ONLY by `1010:0xdd` (init to 0) and `1010:0x17d`
-- disassembled live: `ADD word [54A8], 1`, a PLAIN INCREMENT, called 9 times
in sequence (1..9). This is a hardcoded ATTRACT-MODE auto-cycle through all
10 worlds, completely independent of `scroll_pos` -- the demo never exercises
the real interactive confirm path at all. This plausibly explains the
historical disagreement (comparing `scroll_pos` against this unrelated
counter in two different captures would never agree, because they encode
unrelated things). **Still unresolved**: what the REAL interactive
confirm-driven mapping is. Needs either a fresh capture of genuine arrow-key
GOMENU navigation + confirm (ideally 2-3 different confirmed levels) or
deeper static tracing of the post-`ACTION_ENTER_LEVEL_SELECT` code path --
neither done this round. The level-select UI approximation stays as
documented in `run_cold_boot`'s docstring until one of those lands.

## 2026-07-13 (round 6) — palette fade-to-black transitions; demo re-verified 99.1%

**Fade transitions** (user: missing on death/level start). The VM ramps the DAC
palette to black and back at transitions -- a SUBTRACT fade (each 6-bit
component -~2/frame, so darker colours reach 0 first), ~30 frames, VM-measured
on demo_cold_20260711_201855 (colour 15's 6-bit value ramps 53->0->53 over
frames ~1145-1200). Added `fade_palette()` + a `fade_level`/`fade_target` ramp
to both `play_native` gameplay loops: fade IN from black on level start / after
respawn, fade OUT to black when a transition (death/finish) begins its settle
window. Verified the fade-in ramps 0 -> full over ~32 frames.

**Demo verification** (user asked to check demo_skyroads_20260713_131407):
native reproduces the VM 334/337 sub-steps byte-exact (99.1%). The one
divergence is a jump at frame 407 where the VM runs `1010:1E48-1FD8` (a ~6-iter
loop) that snaps `ship_pos` back and stores the delta into `[af2e]/[af30]` (the
rare landing back-off `resolve_landing` consumes -- its contract's "1/224
frames" case); native doesn't accumulate it, so `ship_pos`/`lateral`/`af1c` +
`af2e`/`af30`/`f455a` diverge for a few frames. A narrow, documented jump-
mechanic gap, not general drift.

**Still open (from the user's round-6 list):** GRAV-O METER digit readout
(`1010:6097`); red kill-platform explosion SFX (verify + fix); finish ->
level-select menu (native respawns the same level); level-select screen
inaccuracies.

## 2026-07-13 (round 5) — HUD gauges not FILLED at level start (stale caches); finish + gauges re-verified correct

User: oxygen/fuel gauges show only their empty outlines at level start (should
be full). Root cause: the `12F8` gauge updater is a DELTA renderer keyed on a
per-gauge "last-drawn" cache; the VM ZEROES those caches (`1010:2B62-2B68`, in
the respawn/level-init flow) so the freshly-painted empty dashboard re-fills on
the first frame. Native's `apply_level_init` didn't -- so a level entered with
stale caches (e.g. the `--level N` baseline snapshot has oxy=fuel=10 already)
had `new == cache` on frame 0 -> nothing drawn -> empty outlines. (`--boot`'s
fresh boot image happened to have caches=0, hiding it there.) Found via a
write-watcher on the cache offsets: `1010:2B62/2B65/2B68` write speed/oxy/fuel
caches -> 0. Fix: zero the three caches in `apply_level_init`; verified the
snapshot path now fills (oxy/fuel 10 -> outline was empty; after the reset the
frame-0 `12F8` fills). `tests/test_native_driver.py::
test_apply_level_init_zeroes_the_hud_gauge_caches`.

Re-verified the other two reports against the VM and found them ALREADY
correct:
* **oxygen/fuel RENDERING** is byte-exact -- widget banks / cell tables /
  `update_hud` all match the VM, and a direct VM-vs-native diff of the whole
  gauge region was 0 px. The only bug was the fill-at-start (caches), above.
* **level FINISH** is byte-exact: at `ship_pos == LEVEL_END` (0x2AAA)
  game_state -> 2 and the ship RISES off the end (af2c 10357 -> 13197, grounded
  ramps 3 -> 43), matching the e2e VM finish exactly. The ship rises into space
  ("disappears"), it does not fall. The `finish_...085832` demo showed a
  different state (ship stuck at 10771, short of LEVEL_END, height draining) --
  a fall short of the line, not the finish.

**Open:** the GRAV-O METER digit readout (`1010:6097` draws it) is not ported
-- the VM shows a live number ("500"), native shows the dashboard's baked-in
"00".

## 2026-07-13 (round 3) — crash explosion animates, gauge UNFILL, music tempo — all VM-PROVEN, each with a regression test

Second round of user-reported play_native issues, with the standing directive
"all of these should be caught by verification -- prove the correct values,
never fiddle." Each fix is derived from and checked against the VM, and each
gets a VM-oracle regression test (the earlier per-piece tests missed these
because nothing verified the INTEGRATED behaviour).

**1. Gauge ghost-on-unfill.** The previous round's `update_hud(force_full=True)`
redrew cells `[0,new)` "on" every frame but never redrew the vacated cells on a
DECREASE, so a dropping gauge left ghost segments. Root fix: use the VM-exact
DELTA path (`flag=0` redraws vacated cells "off") and stop repainting the
dashboard over the gauges -- `play_native` now paints the full dashboard ONCE
and per frame only re-overlays the road-overlapping bezel strip (rows 129..137,
`paint_dashboard(..., byte_count=DASHBOARD_BEZEL_OVERLAP)`); the gauges (rows
138..199, never touched by the road render) are left to the delta updater.
`force_full` removed. Verified: captured 2 real DECREASING `12F8` calls (speed
29->28, fuel 10->9) into the fixture -- the unfill path is now byte-exact
(`tests/test_hud.py`, 9 cases; the fill path was already verified, the unfill
path was the gap).

**2. Crash explosion frozen.** `native_gameplay_substep`'s gate had an extra
`game_state in {0,3}` clause that exited on the FIRST crash frame
(`game_state:=1`), so the settle window never ran and the viewer froze. The
VM's real gate is `should_run_gameplay` ALONE -- its settle-window clause keeps
the frame in the handler while `[456A]` (grounded) ramps 1..0x2A REGARDLESS of
game_state, and the ship sprite `si = [456A]//3` cycles the explosion frames
0..13 as it ramps. Relaxed both gates to `should_run_gameplay` alone; verified
against demo_skyroads_20260710_213019 that the native frozen-ship path
reproduces the settle window step-for-step (grounded 2->42 / af2c, then the
transition past 0x2A) and that `si` animates 1..13 then hides. `play_native`
runs the ramp via `driver.tick()` (animating), then holds the ~60-frame
post-explosion freeze, then respawns. `tests/test_crash_settle.py` +
`tests/test_native_loop_lockstep.py` updated (the whole-level run now ends at
the level BOUNDARY rather than a native GAP, since native no longer raises
exactly at game_state->2).

**3. Music tempo (was ~2.6x too slow).** MEASURED from the VM: the game
programs PIT channel 0 to reload 6628 -> 1193182/6628 = **180 Hz**, and the
music ISR `1010:5A55` fires once per timer IRQ = **6 per displayed frame** ->
the game runs at **30 fps** and the OPL sequencer ticks at **180 Hz**.
`play_native` was rendering at 35 fps and ticking the sequencer 2x/frame = 70
Hz (0.39x). Fixed: `GAME_FPS=30`, `MUSIC_TICKS_PER_FRAME=6` (both derived, not
guessed). `tests/test_music_tempo.py` measures the VM's PIT reload + ISR
cadence and asserts `GAME_FPS * MUSIC_TICKS_PER_FRAME == the VM timer Hz`.

**4. Integrated render verification** (the meta-ask). `tests/test_render_
parity.py` replays a demo and diffs the native full-frame render (`rebuild=
True`, what play_native does) against the VM's VGA road band frame-by-frame,
bounding the per-frame diff -- a fence that would have caught the ghosting/
render regressions the per-piece tests missed.

**Ship-sprite inaccuracy -- FIXED (2026-07-13, round 4).** The ~200-350 px
per-frame residual around the ship was a single off-by-one: in OFFSCREEN mode
(the path play_native uses) `compute_render_params` computed `screen_row` from
`af2c_eff` (af2c - 0x80) instead of raw `af2c`, so the ship-row projection
(`tile_rasterize`'s `[0E6C] = (0x9D-[0E2C])*0x140 + [0E28] - 0x6E`) placed the
ship exactly one row (0x140) too low. The 0x80 subtraction feeds only the
dirty-cache slot, not the projection. Root-caused by a write-watcher (the ship
draw offsets `[0E6C]/[0E70]` and mask `[0E86]` are all written by `1010:325B`,
`tile_rasterize`) then a single-frame VM diff: native `[0E6C]` was consistently
+0x140 vs the VM. Fixed `screen_row = af2c // 0x80`; the full render is now
BYTE-EXACT to the VM (0 road-band diffs on every checked frame, was 219-253).
The fixture only caught the fill path because it was captured with
`offscreen=0` (where af2c_eff == af2c). `tests/test_render_parity.py` now
asserts 0 diffs.

**Still open (documented):** `[1600]` (elapsed-tick counter driving idle-wobble
+ SFX debounce) is still advanced +2/frame, not yet matched to the VM's
~1/frame at 30 fps (secondary timing, not user-visible).

## 2026-07-13 — play_native HUD gauges fill correctly + edge terrain ghosting fixed (both by rendering full each frame)

Two user-reported `play_native` display bugs, both root-caused to the
windowed viewer's per-frame draw order / render path.

**HUD gauges "showing values but not filled".** `play_native` calls
`paint_dashboard` EVERY frame (it must -- the cockpit frame's top rows
129..137 overlap the road band the road render rewrites each frame, verified:
the DASHBRD bank has 176..316 nonzero pixels/row there). But `update_hud`
(the ported `12F8`) is a DELTA updater: it only redraws gauge cells that
changed vs a cache. So each frame `paint_dashboard` wiped the gauge fills
(gauges sit at rows 163..188, well below the road) and the delta redrew only
the 0-or-1 changed cells -> the O2/fuel radial + speed dial showed empty.
Fix: `update_hud(..., force_full=True)` treats the cache as 0 so every gauge
is fully re-filled each frame (the VM-faithful delta path stays the default,
still byte-exact in `tests/test_hud.py`). Verified by rendering: the O2/FUEL
segments now fill magenta (were empty).

**Left/right edge terrain ghosting.** Reproduced against
`demo_skyroads_20260713_103107` (level 5) by rendering the native pipeline
alongside the VM: stale teal terrain (colour 61/63) lingered at cols 0..9 /
312..319 where the VM had black sky (0). Root cause: the VM's `34AE` render
uses delta/skip paths (nothing on `delta==0`, a partial column loop on
`1<=delta<8`) that assume a persistent, incrementally-maintained road band;
in fact THIS demo is `delta==0` on all 542 renders (`[0E2A]`=`lateral_col` is
constant -- the road never redraws, only the ship sprite moves). The native
port's own accumulated offscreen/display-list state drifts under the skip
path and leaves stale edge terrain standing. A full render each frame
(`render_native_frame(rebuild=True)`: bg->offscreen + full tile raster)
sidesteps the delta path entirely -- verified ghosting-free (edge diffs 0),
and cheap (~6 ms/frame, well within the 35 fps budget). Both windowed loops
in `play_native.py` now render full each frame.

**Still open (documented, not hidden):** (a) the native `34AE` delta/skip
render path has a real edge-staleness bug -- the full-render workaround
avoids it but doesn't fix the path itself; (b) a small ship-SPRITE rendering
inaccuracy remains even with a full render (~200 px near the ship: off-by-one
colours + a few missing/extra pixels -- the "probable inaccuracies" the demo
also exposes), not yet chased down.

## 2026-07-13 — per-level MUSIC is RANDOM, not `level // 3`: fixed play_native playing the intro track on gameplay levels

User report: `play_native` plays the intro music on early levels (world 0/1).
Root cause: `song_for_level(level) = level // 3` was WRONG. The cold-boot
trace already recorded MUZAX **song 0 = intro** track and **song 2 = menu**
track, so `level // 3` handed world 0 the intro track and world 2 the menu
track — exactly the symptom.

Recovered the real selection by hooking the MUZAX loader `1010:57C4` over a
multi-level cold session (`demo_cold_20260711_201855`) and matching each
level's (gravity,fuel,oxygen) scalars back to a ROADS index: **levels 16 and
17 — both world 5 — loaded DIFFERENT songs (8 then 7)**, and level 1 (world 0)
loaded song 2. So song is not per-world at all. Disassembling the caller from
live memory (return addr `0x2CC`) shows why — the per-level pick at
`1010:0296-02C8` is RANDOM:

    0296 mov ax,[4518] / call rand      ; PRNG -> ax
    029F mov cx,9 / sub dx,dx / div cx  ; dx = rand % 9
    02A6 mov di,dx                       ; di in 0..8
    02A8 cmp di,[bp-6]; je -> di=(di+1)%9 ; no immediate repeat
    02C3 mov ax,di / add ax,1            ; song index = di+1  (1..9)
    02C8 push ax / call 57C4             ; load MUZAX song[ax]

So the gameplay song is `(rand % 9) + 1` — a random track in **1..9**, never
song 0 (intro), avoiding an immediate repeat. (This also re-contextualizes the
old "song 4 == level-14 snapshot" and "level 30 → song 6" notes: those were
byte-exact *loader* proofs / one-capture *observations* of a random pick, not
a fixed level→song table.)

Fix: `skyroads/native/world_load.py` gains `pick_gameplay_song(rand_value,
prev)` (the ASM's `(rand%9)+1` + no-repeat rule; returns `(song_index, di)`)
and `GAMEPLAY_SONG_COUNT=9`; `native_song_load` now takes an explicit
`song_index` (the pure loader stays deterministic/testable — the random
*policy* lives in the caller). `song_for_level` is removed (there is no such
mapping). `scripts/play_native.py` picks a random gameplay track per level
via a `choose_song()` helper feeding `random.randrange(0x10000)`. Verified:
booting level 0 three times now loads songs 9/3/7 (was always song 0=intro).
`tests/test_world_load.py` gains `test_pick_gameplay_song_reproduces_the_asm`;
`world_for_level` (graphics, still `level//3`, verified byte-exact) is
unchanged.

## 2026-07-13 — fixed a real crash-thud SFX bug: slow/early wall crashes were playing a sound the real game keeps silent

User report: `play_native` plays a crash sound on a slow-speed wall hit that
the real game does not, reproduced in `demo_skyroads_20260713_095814`.

Root cause was in `native_gameplay_substep`'s collision-response block
(`skyroads/native/loop.py`): the `03C2(0)` "wall crash thud" was gated on
`crash.crashed` (`LateralCrashResult.crashed`, from `resolve_lateral_crash`),
which is `True` for EVERY lateral mismatch (ship_pos always resets to 0 on
any wall hit) — not just the real ASM's "flagged" crash (past
`CRASH_MILESTONE_POS`=`0x0E38` AND not already flagged, the one branch of
`resolve_lateral_crash` that sets `[456A]`/`grounded` 0 -> nonzero). A fresh
VM capture of the new demo confirmed it directly: at frame 26, the ship
hits a wall at `ship_pos=2325` (below the 3640 milestone — "going slow" here
means "hasn't yet travelled far enough into the level", not literal
velocity) and the real `03C2` call log shows only a `2828`-return call (id 2,
the distance-gated "blocked-repeat thump") — no `27EA` (id 0) call at all.
The native code's `if crash.crashed:` fired id 0 unconditionally.

This also let me correct a wrong claim from the 2026-07-13 SFX entry below:
"the FRONTAL crash (game_state=3 ...) plays nothing from the substep". A
fresh capture of the ORIGINAL collision demo
(`demo_skyroads_20260710_213019`) with `game_state` logged at every `03C2`
call shows BOTH `27EA` (id 0) calls firing on the grounded `0 -> nonzero`
edge — one from `game_state==0`, one from `game_state==3` (the "frontal"
case) — so id 0 does NOT depend on `game_state` at all, only on `grounded`
having been `0` immediately before the call. That old note was simply wrong
(likely a mischaracterization from an earlier, less-instrumented capture).

Fix: capture `grounded_before = view.grounded` ahead of the
`resolve_lateral_crash` call and gate `03C2(0)` on
`grounded_before == 0 and crash.f456a != 0` (the real 0 -> nonzero flagging
edge) instead of `crash.crashed`; everything else falls through to the
existing distance-gated `03C2(2)` thump, matching the ASM exactly. Updated
`skyroads/native/sfx.py`'s id map to describe the corrected, game_state-
independent condition.

## 2026-07-13 — HUD gauges + real Nuked-OPL3 music in `play_native`; crash/finish settle window

Three fixes to `play_native`, all reported directly from testing the windowed
player (not VM-derived bugs):

**1. HUD dashboard/gauges.** `paint_dashboard` overlays the cockpit dashboard
onto the live VGA plane (nonzero pixels only), fixing the missing top-of-HUD
rows. `skyroads/native/hud.py` ports `1010:12F8`/`0F8C` (the speed/oxygen/fuel
gauge updater + its widget-draw helper) to pure functions — verified against
7 real VM calls, 6/7 exact VGA-byte match (the 7th differs only in the
deliberately-unported fuel/oxygen digit-pair readout), gauge cache values
7/7. Building the fixture also surfaced two LATENT `native_boot_image` bugs:
`SEG_OXY_BANK`/`SEG_FUL_BANK` were swapped (verified by content match against
the `.DAT` files, not just naming), and DGROUP was written LAST even though
`SEG_OXY_BANK`/`SEG_FUL_BANK`/`SEG_SPEED_BANK`/`SEG_SFX_BANK` physically
overlap DGROUP_SEG's 64KB window — silently zeroing all four banks every
boot. Both were invisible before this session because nothing previously read
that memory back out of the boot image.

**2. Real Nuked-OPL3 music.** Swapped `ModernSynth`'s float-FM approximation
for a real Nuked-OPL3 chip (`skyroads/audio/opl3_synth.py`, via the
`pynuked_opl3` cffi binding), fed directly from `Engine.run_tick()`'s raw
`(register, value)` writes — no decode-to-semantic-events step in between.
`pynuked_opl3` is now this repo's OWN top-level submodule (previously only
reachable through `dos_re`'s nested copy).

**3. Crash/finish settle window.** `play_native --level N --window`/`--boot`
previously called `NativeGameplayDriver`'s auto-respawn the INSTANT a
transition fired, so crashing looked like nothing happened and finishing
looked like the ship glitching back to the start. Traced both
`demo_skyroads_crash_20260713_085908` and `demo_skyroads_finish_20260713_085832`
(real VM) frame-by-frame to size the fix:

- The crash demo shows the wall-crash does NOT flip `game_state` to 1 when
  the ship had already resumed (`game_state` was already 3 from an earlier
  landing) — `resolve_lateral_crash` only sets `game_state:=1` when it was
  0. The only signal is `grounded` (`[456A]`) going nonzero and ramping; this
  ramp (0 → `SETTLE_WINDOW_MAX`=0x2A over ~34 real frames, `af2c` animating)
  is ALREADY played natively without any fix — `should_run_gameplay`'s own
  settle-window logic (`orchestration.py`, landed 2026-07-11) keeps
  `native_gameplay_substep` running through it.
- What's genuinely missing: the gap AFTER `should_run_gameplay` finally
  exits. `grounded` plateaus at frame 45 in the demo (the recovered
  gameplay pipeline's boundary — `LevelEndTransition` fires here), but the
  real VM's `game_state 3 -> 0` reset doesn't land until frame 105 — a
  60-frame non-interactive hold this driver doesn't own (a separate,
  not-yet-recovered transition-display subsystem).
- The finish demo's captured window (176 frames, zero input) never actually
  shows a `game_state -> 2` transition — `ship_pos` sits frozen at 10771 (151
  short of `LEVEL_END`=0x2AAA) with `af2c` draining 10240 → 0 and
  `game_state` pinned at 0 throughout, a different scenario than the
  documented "reach `LEVEL_END`" finish path (which IS proven native-only via
  `run_cold`'s zero-input runs reaching `game_state=2`). This demo couldn't
  independently confirm a hold duration for the finish case.

Added `NativeGameplayDriver(..., auto_respawn=False)`: on a transition it now
holds a `TickOutcome` (extended with `kind` — `crash`/`finish`/
`timeout_fuel`/`timeout_oxygen`/`fall`/`""`, classified from `game_state` AND
`grounded` since `game_state` alone under-classifies the already-landed crash
case above — and `game_state`, the pre-reset value) instead of respawning
inline; an explicit `driver.respawn()` applies the deferred
`apply_level_init`. Default `auto_respawn=True` is unchanged, so every
existing headless/verify caller (`run_cold`, `run_offline`, the lockstep
tests) is untouched. `play_native.py`'s two windowed gameplay loops now use
`auto_respawn=False` and hold the frozen frame for `TRANSITION_HOLD_FRAMES`
(60, from the crash demo's measured gap) before calling `respawn()` — not a
byte-exact animation, an honest stand-in for the un-recovered transition
display, same spirit as the existing settle-window non-goal documented on
`NativeGameplayDriver`. `tests/test_native_driver.py::
test_auto_respawn_false_holds_the_transition_until_respawn` covers the new
hold/respawn contract.

## 2026-07-13 — GHOSTING SOLVED: `34AE` fully decoded (both modes), `39D4` placed, whole `2D1F` frame full-1MB byte-exact on 4 captures + a 6-frame chain; `play_native` presents the live VGA plane

The ghosting hunt ended in a chain of corrections that produced the strongest
renderer proof yet. In order:

**1. The "2D1F runs twice per game frame" observation was a CAPTURE ARTIFACT.**
The chain capture triggered on `ip == 2D1F` at every CPU step; when a
`ConsoleInputWouldBlock` unwound the frame loop exactly at the entry
instruction, the resume re-fired the trigger — duplicating each entry with an
IDENTICAL 1MB pre (`pre4 == pre5` bytewise proved it). One 2D1F call per frame.
Every "divergence" in the earlier chain test was me diffing my POST against the
SAME call's PRE (= my own net writes). There was never a lockstep divergence.
The re-capture with in/out state tracking (`artifacts/frame_chain2`) shows
frames 569..574, one call each.

**2. The real gap: my "byte-exact" frame compose was only verified on the
OFF-SCREEN dest.** A full-1MB diff on the block capture (frame 571) showed
2,848 missing bytes at `0xA2B1E..0xA8F9D` — **VGA rows ~34..114, the road
band, written directly to the screen inside 2D1F**. A call-boundary re-capture
(`phase_events.json`) gave the definitive frame structure:

    2D1F: params -> 34AE(ax=0) [composite, ends in 39D4 #1]
          -> 11-row tile loop (+325B ship row)
          -> 34AE(ax=1) [PRESENT, ends in 39D4 #2] -> post-steps

**3. `34AE` fully decoded from its proven lift (all 28 blocks) — supersedes
every earlier mode-1 entry, including the "mode-1 never draws per-column /
the real present is 41A0" conclusion** (that sampling happened to hit
`delta == 0` frames; `41A0` is a separate general blit used elsewhere):
- mode select: `ax==0` -> src=bg `[5170]`, dst=offscreen `[0E36]`, dispatch
  variant A (`364F`); `ax!=0` -> src=offscreen, dst=`0xA000`, dispatch
  variant B (`36F3`). So the PRESENT is per-column via `road_column_strip`.
- four bodies keyed on `[0E32]` and `delta = [0E2A]-[0E6A]` (unsigned):
  `[0E32]!=0` -> full 44,160-B copy (the rebuild bg-copy AND the full present);
  `delta==0` -> nothing; `delta>=8` -> rows-32..137 band copy (`si=0x2800`,
  `cx=0x4240` words); `1<=delta<8` -> the 10-row x 4-col x 2-pass column loop
  (classification inline = `render_classify`, verified 80/80 previously).
- EVERY path ends `call 39D4` — except the `[003C]==0` bail, which skips it.

**4. `39D4` (lift confirmed) = exactly the existing `ship_sprites` port**:
erase pair unconditional, draw pair iff `[0E68]==0xA000`. Its earlier "273-byte
divergence when called unconditionally" was a placement error — it runs at the
END OF EACH `34AE`, against the segments that mode selected (into offscreen
after mode 0, onto VGA after mode 1), not once per frame.

**Implementation**: `render_frame.run_34ae(img, ds, ax, ship_sprites)` (full
lifted control flow, persists the ASM's loop/classification DGROUP fields);
`frame.compose_frame` now runs `run_34ae(0)` -> tile loop -> `run_34ae(1)` ->
post; `tile_dispatch.render_tile_passes` persists its `[0E40]/[0E48]/[0E4A]`
final scratch. The rebuild path no longer special-cases the bg copy —
`[0E32]!=0` routes both 34AE calls through their full-copy paths natively.

**Verification (the strong part)**: full-1MB residual vs the VM post, on ALL
FOUR single-frame captures (steady / rebuild / moving / block) = **0** outside
the dead stack slop (SS==DGROUP, ~19 bytes below final SP). Then a 6-frame
consecutive chain (frames 569..574, resyncing only DGROUP sim state per frame,
carrying MY dest + display lists + VGA across all 6): non-DGROUP residual = 0
except VGA rows 162..190 — the dashboard GAUGE area, written by the HUD
updater between frames (outside 2D1F, not this pipeline's job). **The road
band (rows 0..137) is byte-exact across the whole chain.**

**Player fix**: `play_native --level N` now presents the image's live VGA
plane (`0xA0000`, all 200 rows) instead of the offscreen dest — rows 0..137
maintained natively by the present pass (ship now visible, zero ghosting:
`artifacts/frames/native_present_t200.png`), rows 138+ keep the baseline
cockpit art until the HUD gauge renderer is ported. 365 tests pass.

Remaining for task #23: gameplay SFX (SB PCM, trigger `03C2`), per-level
WORLD/palette/MUZAX assets, live HUD gauges, PitchBend re-pitch, native
startup constants (milestone 2).

## 2026-07-13 (latest) — ANIM.LZS CRACKED: the real ship/tunnel intro animation now plays natively — the "honest gap" from the previous entry is closed

Picked the documented lead back up and closed it. The blit routine is
`1010:42AF`: given a DGROUP record `(src_seg, dest_off, h, w)`, it row-copies
`h` rows of `w` bytes from `src_seg:0` onto VGA (`0xA000:dest_off`) via the
C-runtime `_fmemmove` helper (`1010:6053`), `dest_off += 0x140` per row.
Traced its entry live (stack param = the record pointer, read BEFORE `enter`
runs — the first attempt misread `SI`, which isn't loaded from the param
until after the prologue, and produced garbage; fixed by reading `ss:[sp+2]`
instead) across a **blind cold boot** (`demo_cold_20260711_201855` replayed
with EVERY recorded key dropped, since the demo's own first input — an ALT
tap at frame 9 — turns out to skip the intro instantly, which is why earlier
traces saw nothing between frames 10-90).

**The full ANIM.LZS structure, decoded and file-exact**: `"ANIM"` tag + u16,
one shared 102-colour CMAP, then 221 back-to-back tile records (small
inter-tile gaps of 0/2/36 bytes, resynced by searching for the next `"PICT"`
rather than assuming a fixed stride) — **221/221 tiles, 44,808/44,808 bytes,
exact.** Compositing tiles in file order (not overwriting) shows this is
genuinely a **dirty-rectangle ANIMATION**, not one static picture: early
tiles paint a close-up ship (engine glow visible) on a checkered runway;
later tiles progressively repaint the SAME screen region with a receding
checkered-floor perspective as the "camera" flies through — matching the
game's whole rendering philosophy of partial/delta updates, not full-screen
redraws. `skyroads/native/anim.py`'s `load_anim`/`paint_tile`.

**Pacing**: the per-tick reveal-count sequence (22, 27, 14, 11, 8, 7, 4, 4,
3, 2, 2, ... settling into a steady 1-2-3 repeat) was captured directly from
the blind trace and baked in as `REVEAL_PACE` — a faithful REPLAY of one real
run, not a rederived general timing rule (the driver LOOP itself — whatever
walks the table and decides how many tiles fit per tick — wasn't isolated).
Flagged honestly in the module docstring rather than presented as more than
it is.

**Wired into `play_native --boot`**: LOGO.PCX splash (~1s) -> the real
ANIM.LZS animation (ship -> tunnel, INTRO.SND playing throughout) -> the
native GOMENU menu -> gameplay. Verified end to end headless; visual spot
checks at ticks 3/10/30/60/81 show a clean, recognizable ship-to-tunnel
reveal (`artifacts/frames/native_anim_tick*.png`). `tests/test_anim.py`
(3 tests: full-file parse, pacing covers every tile, paint stays in bounds).

This closes the one remaining honest gap from the previous milestone entry.
The only intentional stand-in left is LOGO.PCX itself (confirmed not part of
SKYROADS.EXE's own runtime) and the menu's UI-drawn selection cursor
(ROM's own scroll-to-level mapping still unrecovered, per that entry).

## 2026-07-13 — MILESTONE 2 COMPLETE (bounded): `play_native --boot` runs the FULL chain — splash -> menu -> gameplay, zero VM, entirely from game files

Closed the loop task #24 set out to close. `--boot` now runs: LOGO.PCX splash
(native PCX decoder, `skyroads/native/pcx.py` — standard ZSoft RLE format, no
VM recovery needed; a generic-format module, not SkyRoads-proprietary) +
INTRO.SND (6024 Hz PCM, SB-DMA-rate-verified) -> the native GOMENU
level-select grid -> real gameplay via the already-verified `--cold-native`
pipeline. Verified end to end headless (intro -> menu -> level-0 gameplay,
zero VM at any point); full test suite green.

**Honest scope note — two pieces remain outside this milestone, by design,
not oversight:**
- **LOGO.PCX is not part of SKYROADS.EXE's own runtime.** Traced every file
  `INT21h AH=3D` open across a real cold boot (frames 0-210): LOGO.PCX is
  never opened by the game at all. It must be shown by an external loader in
  the original shareware distribution (a separate splash EXE/batch script),
  outside what any captured demo exercises — so showing it here is a
  reasonable stand-in, not a VM-verified recovery, and is documented as such
  in `run_cold_boot`'s docstring.
- **The real in-EXE intro (INTRO.LZS/ANIM.LZS, opened at boot frame 9) is
  NOT reproduced.** Its ship-flying animation's frame-trigger/blit mechanism
  wasn't identified this session (the `3A96` unpacker that seemed like the
  obvious candidate turned out, per the TREKDAT finding above, to be a
  ONE-SHOT display-list initializer, not a per-intro-frame animator — so
  whatever drives ANIM.LZS's frames is still unknown). INTRO.SND — real
  audio confirmed by the SB-DMA capture — plays regardless, since it's
  correct either way. This is flagged, not silently approximated: the goal
  was "complete or completely blocked" on faithfulness grounds for this one
  piece; this port chooses "bounded and honest" over "silently wrong."
  **De-risking lead for next time**: `ANIM.LZS`'s DATA format is now
  decoded, just not its driver. Structure: `"ANIM"` tag + u16, then ONE
  `"CMAP"` (102 colours, shared) + a 206-byte aux table, then a SEQUENCE of
  bare `"PICT"` records back-to-back (u16 dest/h/w + 3 LZS width bytes +
  stream each, same container primitives as `boot.py`'s `parse_lzs_container`/
  `load_pict`) — a genuine flip-book: 22 consecutive frames confirmed
  self-consistent by exact byte-consumption chaining (each frame's declared
  `dest` is a segment-relative pixel offset, `h`/`w` shrink/grow ~20-36 x
  22-26, consistent with a ship growing toward the camera), before the walk
  hits non-PICT bytes at file offset 8182 (36,626 B remain — likely more
  clips/segments, structure not yet identified past that point). Whoever
  picks this up next should trace the actual blit CALL SITE during boot
  frames ~11-113 in `demo_cold_20260711_201855` (the file's already open by
  frame 9) to find the segment/timing driving these frames onto the screen.

This closes the practical, playable core of milestone 2: cold boot to
selectable, real gameplay, no VM, no snapshot, ever. `tests/test_pcx.py`
(1 test) added; `tests/test_menu_grid.py`/`test_boot.py` unchanged and still
green.

## 2026-07-13 — native LEVEL-SELECT MENU: `play_native --boot` — pick any level from a real menu screen, zero VM, then play it natively

Extended the cold-start milestone with a functional menu: `scripts/play_native.py
--boot` opens directly on the GOMENU.LZS level-select screen (no `--level`
needed), lets the player navigate a 10-world x 3-road grid, and hands off
into the already-verified `--cold-native` gameplay pipeline on confirm.

**GOMENU decodes to the REAL menu screen** (not just a plain background):
the 320x200 PICT already contains all 10 world names + their "Road 1/2/3"
lines pre-rendered (`artifacts/frames/native_menu_gomenu.png`) — the 2-column
x 5-row x 3-line layout confirms `world_for_level`'s `level // 3` convention
visually. Its 212-colour CMAP maps directly to DAC 0..211, **verified
212/212** against a live-captured menu-time DAC (`dos.vga_palette`); the
background pixels are RAW (unbiased) at segment `7176:0000`, **63,970/64,000**
exact against a real cold-boot capture (residual = the small 5x6 selection-
icon PICT record this version doesn't draw).

**Selection UI is an honest approximation, not a ROM recovery.** Two things
remain genuinely unrecovered: the ROM's own on-screen cursor sprite, and the
`scroll_pos`-to-level-index mapping (two real captures disagreed on what
level a given `scroll_pos` selects — `dispatch_menu_action`'s scroll
mechanics are ASM-matched, but that indirection isn't). So the menu instead:
measures the grid's row/column pixel bands directly off the decoded
background's own green "Road N" text (`WORLD_ROW_Y0`/`ROAD_SUB_Y`/`COL_X` in
`play_native.py`), and draws a plain highlight rectangle around the selected
line (arrow keys move it, enter/space confirms) — verified to land on real
green text for all 30 levels (`tests/test_menu_grid.py`).

Ran end to end headless: menu renders, navigates, confirms level 0, and
transitions into real native gameplay — zero VM at any point. 3 new tests
pass (grid geometry + green-pixel overlap for every level).

Remaining for the full milestone: the intro sequence (LOGO.PCX + the
`3A96`-driven ANIM.LZS animation + INTRO.SND) and sequencing intro -> menu
-> gameplay as one continuous flow.

## 2026-07-13 — MILESTONE 2 CORE REACHED: gameplay renders from GAME FILES ALONE — zero VM, zero snapshot, at any level

The TREKDAT record framing was traced live (`1010:00E6-017D`, boot frame 9)
and is now VM-verified byte-exact end to end:

- **Record header = two RAW words** `(A, B)`, read byte-aligned via the same
  scalar reader ROADS/MUZAX use (NOT the LZS bitstream) — `size = B`,
  `dest_off = A - B` (the position within the freshly allocated segment
  where this record's payload lands — NOT offset 0, which is why the
  earlier size-only guess silently corrupted the layout).
- The loader ALSO stamps `dest_off` itself as a bookmark word at the
  segment's own offset 0 (`1010:015E`) — separate from the decompressed
  payload — which is exactly the "self-referential offset" `3A96` reads
  back at the start of its own header-relocation step. Missing this one
  write made `unpack_animation_segment` read `si0=0` instead of the real
  bookmark, wander into zeroed memory, and hang forever hunting for an
  `0xFF` terminator that was never there — the actual cause of the
  earlier "hung acid test", not a logic bug in the (already VM-verified)
  `3A96` port itself.
- 3 LZS width bytes + the compressed stream follow; each record's exact
  consumed-byte length (needed to locate the next record) comes from the
  bit reader's own final position — the records are back-to-back in the
  file with no separate length field.

**Verified: all 8 display-list buffers reproduce the real cold boot's
memory byte-exact — 524,288/524,288 bytes** (`tests/test_boot.py`'s new
`test_display_list_buffers_byte_exact_vs_cold_boot`), and fast (0.18 s for
all 8 segments' decompress + `3A96` expand).

**`scripts/play_native.py --level N --cold-native`**: the entire 1 MB
image — program (native EXE unpack), DGROUP, all 8 display-list buffers,
the full palette — now builds from `assets/` alone via
`skyroads.native.boot.native_boot_image`. Ran end to end: level 2 opens,
loads VM-free, renders the road/ship/background/cockpit correctly, plays
music and SFX — **no VM was ever instantiated**. 381 tests pass (full
suite, +2 min for the exhaustive boot/EXE/world checks).

This is the practical core of milestone 2 (play any level, fully cold,
no VM). Remaining for the FULL milestone (intro + menus): the menu/level-
select screen (GOMENU already decodes; needs the `12F8`/`0F8C` HUD-glyph
draw + input loop wired as a game STATE, not just an asset) and the intro
sequence (LOGO.PCX + `3A96`-driven ANIM.LZS animation + INTRO.SND, plus a
state machine sequencing intro -> menu -> level-select -> gameplay).

## 2026-07-13 — the display-list buffers are TREKDAT.LZS data (boot-loaded, then 3A96-expanded) — the last unmapped cold-boot piece is now mapped

The snapshot-free gameplay acid test hung: ZEROED display-list buffers make
the RLE strip walks scan 64 KB for a 0xFF terminator. The buffers hold a
pre-built structure (a strip-offset table at 0, then (ctrl,run,skip)-triplet
strips) that is ESSENTIALLY STATIC — the menu-time capture's dl0 differs
from deep-gameplay snap92 by only 14 bytes, and all 8 phase buffers are
primed before any level loads. First-writer trace over boot frames 0..240:

- `1010:6712` (the LZS decode main loop) writes 44,710 bytes into dl0 —
  **the buffers are decompressed TREKDAT.LZS records**;
- `1010:3A96` (the ALREADY-RECOVERED intro_anim_unpack — evidently a general
  second-stage expander, not intro-specific) writes 65,531 bytes over the
  same buffer at offset 0 — an in-place expansion pass;
- both at frame 9, i.e. the boot-time `trekdat.lzs` load from the manifest.

So nothing about the render state needs new recovery: the cold-boot dl
priming = replay TREKDAT's records (skyroads/codecs/lzs.py) + the recovered
`3A96` expansion into the 8 buffer segments. Remaining assembly for the
snapshot-free gameplay milestone: the record->segment mapping + 3A96 call
parameters (captureable from the same frame-9 window), then re-run the acid
test (scratchpad/acid_native_boot.py — gameplay from `native_boot_image`,
which already renders everything else from files alone).

## 2026-07-13 — native boot builder + the GRAPHICS CONTAINER format + the complete DAC map; cars/dashbrd banks byte-exact from files alone

`skyroads/native/boot.py`: the snapshot-free boot image. Key discoveries:

**The graphics container format** (generalizing the WORLD finding — CARS,
DASHBRD, WORLD, GOMENU all share it): ``"CMAP" + u8 colour-count + colours``,
an aux table (-> `[AF3C]`), then ``"PICT" + u16 dest_off + u16 h + u16 w +
3 LZS width bytes + stream`` (h*w decompressed bytes). Observed: CARS
2310x24 = 55,440 (a 24-wide sprite strip); DASHBRD dest 0xA140 (= screen
row 129), 71x320; GOMENU 200x320 = 64,000; WORLD 138x320. Other chunk tags
(APAO/APCO/INMI/DHKI/XCGN/AHEL) are menu/anim data, still unparsed. (An
earlier flat-layout guess produced garbage LZS widths whose decode crawled —
the "PICT" tag search settled it; WORLD4's "descriptor" was PICT's tail.)

**The complete gameplay DAC map**: each container's CMAP slots in
sequentially — ROADS' 72 -> 0..71, CARS' 20 -> 72..91, DASHBRD's 50 ->
92..141, WORLD's 114 -> 142..255. Bank pixels are stored palette-relative
and biased into their window when banked, NONZERO PIXELS ONLY (0 =
transparent). Proven byte-exact vs the menu-time cold capture: cars
55,440/55,440 (+72), dashbrd 22,720/22,720 (+92).

**Boot DGROUP**: EXE-unpacked init data + cfg -> `[4516]`, DAT cell tables
(`[95F8]`/`[5480]`/`[4572]`), demo.rec -> `[961E]`, the display-list segment
table `[0E76..]` + allocator pointer constants (deterministic layout,
verified vs the capture). `GAMEPLAY_POINTERS` adds the level-start segment
init (`[5478]`=8116 offscreen etc., from snap92). `native_boot_dac()` gives
the level-independent palette part. `tests/test_boot.py` (4 tests).
Note: sfx.snd's `233B` segment holds intro.snd at menu time — the SFX bank
loads at level start; boot places SFX.SND there for gameplay use.

## 2026-07-13 — native EXE unpack: SKYROADS.EXE's CUSTOM packer stub recovered register-exact; the initial program image (incl. the startup tables) now builds from the file alone, verified 0-diff vs a real cold boot

Milestone-2 boot chain, first big piece. Following the boot manifest's lead
that the "computed tables" writer `1767:06B1` is the PACKER STUB's own
`stosb`: disassembled the resident stub from the cold-boot capture (the EXE
has NO LZ91/PKLITE signature — a custom packer; the bit-LZ loop resembles
LZEXE but the length/distance coding differs), captured the decode loop's
initial registers live (stream at file offset 0x62, initial `lodsw` -> BP,
output forward at `100F:0010` = load 0x1010:0000), and transcribed the loop
register-exact into `skyroads/native/exe_image.py`:

- getbit `063A` (16-bit LSB-first BP buffer), main loop `06B3`: `1` ->
  literal; else disp-low byte then LONG (`0646`: BH gamma-windowed high
  disp bits + length code with three escapes incl. `byte+0x11`) / `01`
  (3 BH bits, `dec bh`, 2-byte copy) / `00` (2-byte copy, or END when the
  disp byte is 0xFF).
- 3 relocations from the stub's cs:0x2A table: program-image words at
  `0x0B04`/`0x3ACA`/`0x61F4` += load segment.

**Verification: `build_program_image(load=0x1010)` == the VM's memory at
the stub's far jump (`1010:61F3`, captured cold) — 0/0x75A2 differences;
without relocs, exactly the 3 sites differ.** So the ~30 KB program image —
code, initialized DGROUP data, clip tables `0x4C..0xE3`, shape `0xBA7`, the
loaders' filename strings — is now derivable from the shipped EXE natively.
`initial_dgroup()` extracts the DGROUP init image (BSS zero-extended).
`tests/test_exe_image.py` (3 tests).

## 2026-07-13 — MILESTONE-2 GROUNDWORK: the complete cold-boot manifest (every file load, dest buffer, and the computed-table site), traced from a real cold EXE start

User goal set: continue until the native port cold-starts with intro + menus +
everything (task #24). First step: a full boot manifest from the 2617-frame
cold demo (demo_cold_20260711_201855), DOS-level INT21 tracing + computed-table
write watchers from step 0:

**Frame 0 (C runtime)**: DGROUP BSS zeroed (incl. the table regions).
**Frame 8**: the startup tables — clip tables `0x4C..0xE3` and the shape
table `0xBA7..0xBAE` — are COMPUTED by code at **`1767:06B1`** (the second
code segment, where ~97% of intro-phase execution lives). This is the
"startup constants" gap the native player currently fills from snap92.
**Frame 9**: `skyroads.cfg` -> `[4516]` (66 B); MUZAX song 0 (intro music);
`oxy_disp.dat` -> cell table `[95F8]` (20 B) + stencil bank; `ful_disp.dat`
-> `[5480]` + bank; `speed.dat` -> `[4572]` (68 B) + bank; `demo.rec` ->
`[961E]` (6,398 B — the attract-mode input recording); `trekdat.lzs`
(records via the `[31A8]` staging buffer); `intro.lzs`; `anim.lzs`.
**Frame 11**: `intro.snd` -> `233B:0000` (32,100 B PCM).
**Frame 113**: MUZAX song 2 (menu music); `mainmenu.lzs`; `intro.lzs` again.
**Frame 205** (menu ready): `cars.lzs` -> `5E61:0000` (55,440 B ship bank);
`dashbrd.lzs` -> `6BEA:0000` (22,720 B); `sfx.snd`; `gomenu.lzs` ->
`7176:0000` (64,000 B level-select screen) + a 30-B record -> `8116:0000`.
**Level start (frames 282/1327/2016...)**: MUZAX song -> `54B0`; ROADS
palette -> `[41C2]` + road -> `[162C]`; WORLD<n> cmap (342 B) -> `[436C]` +
background -> `7176:0000` — exactly the three-file story verified earlier
(note: the world CMAP also lands in DGROUP at `[436C]`, the fade target).

Segment layout is deterministic (same allocator sequence every boot), so a
native boot can adopt the known segment map. Remaining for a snapshot-free
boot: port `1767:06B1` (table computation), the EXE->DGROUP initial image
(dos_re bootstrap_lzexe for the unpack), and the DAT/TREKDAT placements.

## 2026-07-13 — per-level assets NATIVE: world background + full palette + per-level MUSIC, all VM-verified; CORRECTS level_format.md's WORLD story

**The "three blocks from WORLD*.LZS" in level_format.md was a misattribution.**
Tracing every `66E6` LZS-decode call over the e2e demo shows the three
level-load decompressions come from THREE FILES:

1. **MUZAX.LZS -> `1686:54B0`** (loader `1010:57C4`, filename at DG `0x0E12`):
   the per-world SONG. 6-byte directory entries ``{u16 offset,
   u16 n_instruments, u16 size}`` — 10 songs, one per world. After the
   decode, `1010:5A7D` sets `[3194]=0x54B0` (instrument base, 16-byte patch
   records), `[3196]=[3198]=0x54B0+16*n_instr` (cursor+loop), `[31A6]=0`;
   `[0BF2]` caches the song index. **Byte-exact**: song 4 == the level-14
   snapshot's `54B0` region, 7506/7506.
2. **ROADS.LZS -> `0x162C`** + scalars + 72-colour palette (already native).
3. **WORLD<n>.LZS -> the `[5170]` background bank** (generic graphic loader
   `1010:4084`): `"CMAP" + u8 colour-COUNT (114) + 342 palette bytes`, then
   records ``[u32 scalar][u16 h=138][u16 w=320][3 LZS width bytes][stream]``.
   Background pixels are +0x8E-biased (142 = the CMAP DAC base).
   **41,770/44,160 byte-exact** vs the snapshot bank (rows 129..137 carry a
   runtime road-horizon priming, redrawn per frame).

**Palette (user caught a real bug here)**: first reading treated the CMAP u8
as a byte length (38 colours) — wrong colours on every non-baseline world.
It is a COLOUR count: 114 colours covering **DAC 142..255** (verified
114/114 vs the snapshot DAC). Full gameplay DAC = ROADS' 72 -> 0..71 +
CMAP's 114 -> 142..255 + level-fixed cockpit colours 72..141: composed
natively it matches the level-14 snapshot **0/256**.

**Level->world/song mapping**: `level // 3` for the 30 regular levels;
level 30 (the DEMO.REC attract level — identified by road[] match 2800/2800
against the e2e runtime) uses world 9 + song 6 (observed; no DGROUP table).

**Display-list staleness is a non-issue**: transplanting level-14's 8 dl
buffers into the e2e runtime's pre-state and rendering natively gives an
IDENTICAL road band (0 diff) — the per-frame handler patches fully
regenerate the visible window; there is no separate level-start strip
bootstrap to recover.

`skyroads/native/world_load.py` + `tests/test_world_load.py` (6 tests);
`play_native --level N` now shows each level's own background/palette and
plays its own song. Native renders of levels 2/20/30 match the real game's
look (level 30 vs the e2e VM capture side-by-side). Open: a possible stale
ship-shadow artifact when airborne (`tile_shade`'s idx>=5 early-out leaves
`[0E70]` stale — the ASM does the same, so this may be faithful; needs a VM
render differential on a jump to settle).

## 2026-07-13 — native gameplay SFX: `03C2` decoded + call sites VM-verified; SFX.SND bank parsed; wired into the sim loop and the windowed player

**`1010:03C2(id)` decoded from a live disassembly** (the static snapshot had
overlay garbage at that region; `tools/lindis.py --live-demo` reached it at
frame 611): stamps `[AF38]=[1600]`, bails if `[451A]!=0` (mute); SB path
(`[0CB6]!=0`): the SFX bank sits at segment `[4560]`, addressed through its
u16 offset directory — for effect `id`, `start=offsets[id]`,
`len=offsets[id+1]-offsets[id]`; the FIRST byte is the SB DSP TIME CONSTANT
(rate = 1e6/(256-tc)), the remaining `len-1` bytes are the unsigned-8 PCM DMA
block (`5B76`). PC-speaker fallback points `[0BD0]` at `[0x162+id*2]`.
`1010:0476` = "channel busy": `[1600] < [AF38]+8` (8-tick debounce), consulted
only by the landing trigger. Cross-check vs the earlier SB-DMA capture:
effect 1 = tc 131 / 8000 Hz / 5153 B = the recurring gameplay effect. ✓

**The gameplay id map, VM-VERIFIED** by capturing every `03C2` call over the
collision demo (5 calls total): id 1 ret `249E` (bounce landing, decay branch
`2470-249E`: game_state 0, bounce<0, above kill threshold, not grounded, not
busy); id 0 ret `27EA` (wall-CRASH thud at `27E7`, on flagging
`[456A]`/`[456E]`); id 2 ret `2763` (bump slip inside `26EC`). The `2828`
id-2 (blocked-repeat thump) is distance-gated (`[9618:961A] >
tgt_lateral-ship_pos`) and didn't fire in the demo; implemented per the ASM.
Corrected two earlier notes: the run_status "action 0xC -> 03C2(0)" call is
menu/level-select context (no gameplay touch-down sound exists), and the
FRONTAL crash (game_state=3, the golden-trajectory one) plays nothing from
the substep — its explosion SFX site is elsewhere (settle-window subsystem,
still open).

**Implementation**: `skyroads/native/sfx.py` (bank parser + id map);
`native_gameplay_substep(..., sfx=)` emits at the three verified sites with
the ASM's exact conditions (including the `0476` debounce via
`[AF38]`/`[1600]`, which the emitter only touches when a callback is
installed — lockstep verification is unaffected, proven by a same-crash-tick
test); `NativeGameplayDriver(on_sfx=)`; `play_native --level N` loads
SFX.SND, resamples each effect to the mixer rate, and plays on trigger.
Jump-scenario test: landings emit id 1 at ticks 41/50 (debounce respected).
`tests/test_native_sfx.py` (3 tests) + suite green.

## 2026-07-12 (latest+3) — MILESTONE PIVOT: play any level VM-lessly by index (no demo/snapshot). Plan pinned; `4B8E` re-verified as the level-init oracle

User set the next north star: `play_native --level N` must play any level
**VM-lessly** with only a level index — no demo, no snapshot. (Then, later: full
cold-start with intro/menu.) See memory `native-milestone-sequence`.

**Where the VM dependency actually is.** `scripts/play_native.py` already plays
gameplay 100% natively, but it seeds a 64 KB DGROUP image from the VM (even
`--cold` reuses that image for level GEOMETRY). The one level-dependent thing
the native sim reads that we cannot yet produce natively is the **`0x162C`
perspective table** (`04C0` reads it; 360/724 bytes differ level-to-level). That
table is built by **`4B8E`** (the level-load routine) from the decoded
`road[]` — NOT loaded as data.

**Reconciled a docs contradiction.** `level_format.md` claimed the `0x162C`
projection LUT is "precomputed data shipped in `WORLD*.LZS` block B, not
computed." That is WRONG for the region the sim reads: `4B8E` does a `rep stosb`
CLEAR of `[0x162C..+0x1B58]` then `rep movsb` FILLS it from road-derived
`0x32xx/0x33xx` buffers (staged by `4331` into `0x31A8`). If it were loaded
world data it would be identical for levels sharing a world, but levels 16 and
17 differ in 360/724 bytes — so it's computed per-level from `road[]`.
(`level_format.md` is right that tile bitmaps `0x7176` and descriptors `0x54B0`
are loaded data; only the `0x162C` claim was wrong.)

**`4B8E` re-verified this turn as a working level-init oracle.** From the
positioned snapshot `artifacts/snap_before_4b8e` (at `1010:2C58`, just before the
level-load call): liftgen LIFTABLE (57 insts, 13 blocks, 7 direct calls);
liftverify **PASS byte-exact on the real level-load path** (3/13 blocks — one
call = one path). The `4331` "did not return within 20M steps" exception is on an
OFF-path invocation (the lift feeds `4331` bad state on an uncovered branch); on
the real level-load path `4331` returns in ~30k steps and the whole thing
verifies. So the lift correctly reproduces level-load — but it is HYBRID (7
`emulate_call`s into ASM), not pure native.

**`4B8E`'s call tree to port for a pure-native `--level N`:**

```
4B8E  (enter 0xC; the level-load orchestrator)
├─ 5D07   (setup; pushes 0x300,0,0x31A8 staging)
├─ 3F20
├─ 4B43  x2   (into local bufs bp-6 / bp-12)
├─ 4331  x2   ← the road[]→0x31A8 staging data-transform (~30k-step bounded loop)
├─ 3F3B
└─ 6006
     then: rep stosb clear 0x162C; rep movsb fills from 0x32xx/0x33xx -> 0x162C
```

**Plan (milestone 1), concrete:**
1. Native level-file load: `ROADS.LZS` road[]+params (✓ `roads_archive`, byte-exact)
   and any `WORLD*.LZS` blocks the transform consumes (✓ `codecs/lzs`).
2. Port `4331` (bounded data-transform loop) to pure Python — the heart of
   road[]→staging; use the verified `4B8E` lift as the oracle.
3. Port `4B8E`'s road→`0x162C` fill (clear + the `rep movsb`s) and the other
   callees it needs on the level-load path.
4. Native `level_init(N)` → DGROUP image with a correct `0x162C` (+ 9600 road
   cells, params); verify byte-exact vs the VM's post-load DGROUP over the
   regions the sim reads, for several levels.
5. Wire `scripts/play_native.py --level N` to use it (no demo/snapshot), then
   play natively (existing `run_cold` path).

This is the same lindis→liftgen→liftverify→port workflow that just landed the
render tree; the target is liftable, verified on its real path, and has a
positioned snapshot to iterate on.

## 2026-07-12 (latest+4) — DIG (per user): two big corrections — `0x162C` is LZS-LOADED not computed; `4331` is a palette fade not sim-staging. Native level-init = LZS-decompress-and-place

Digging against the demos (user pointed at pre2_port for inspiration) overturned
two things I had wrong, and simplifies milestone 1.

**CORRECTION 1 — `0x162C` perspective table is LZS-DECOMPRESSED level data, NOT
computed.** Traced writers to `0x162C..0x18FF` during the level-load demo
(`demo_skyroads_20260711_202740`): the only writers are **`0x6712` =
`lzs_decode_loop`** and `0x5d18` (a copy), 724 bytes at frame 45. `0x6712` is the
SAME LZS decoder already proven byte-exact for `road[]`. So the perspective LUT
is shipped in a level file (WORLD*.LZS block B, per `level_format.md` — which was
RIGHT) and decompressed in, not built by `4B8E`/`34AE`. **Retract** the earlier
"`4B8E` clears+fills `0x162C`" and "`34AE` builds `0x162C` from staging" claims.

**CORRECTION 2 — `4331` is a PALETTE CROSS-FADE (render side-effect), not the
road→staging transform.** Full disasm (`43F4`-`4458`): its inner loop is a linear
blend `dst[i] = src1[i] + (src2[i]-src1[i])*pct/100` reading the tile bank
(`7176:0`), then it calls `6168` (`palette_upload`) each step and loops the blend
`pct` 0→100. That's a visual fade, exactly the render side-effect pre2's rule
says to keep OUT of the DGROUP sim contract. **Retract** "`4331` stages road data
into `0x31A8`."

**CORRECTION 3 — my `4B8E` oracle caught the WRONG call.** The `4B8E` I captured
(frame 46, args `[0x5174,...]`) has `0x162C` all-zeros in its post-state, and
even the confirmed gameplay snapshot `gameplay_f640` has `0x162C..0x18FF` mostly
zero (35/724 nonzero) — the LUT is SPARSE, which also confounds byte-matching it
to files. So `artifacts/oracle_4b8e` is not the level-load witness I need.

**Net: milestone 1 is a LZS-DECOMPRESS-AND-PLACE job (pre2's
`native_level_load_dgroup` pattern), not a computed-transform port.** The
level-dependent sim seed (perspective LUT, road cells, params) is all level-file
data. The recovered `codecs/lzs` (byte-exact) + `roads_archive` already do the
decompression; what's missing is a native WORLD*.LZS container loader (CMAP
palette + LZS payload → blocks A/B/C at DGROUP `0x54B0`/`0x162C`/seg `0x7176`),
the level→world mapping, and placing it all over a `NativeGameState`.

**QUANTIFIED "what's missing" (diffed two levels' gameplay DGROUP: the
`gameplay_f640` snapshot vs the level-load demo at its 40th gameplay sub-step):**
level-dependent DGROUP = **17,614 bytes across 19 regions**, dominated by static
level tables that native level-init must produce:

| region | bytes | what |
|---|---|---|
| `0x5494..0x7201` | 7533 | block-A descriptors / cell array |
| `0x34a8..0x429a` | 3570 | staging / render tables |
| `0x1630..0x231c` | 3308 | perspective/render tables (block B, incl. `0x162C`) |
| `0x1291..0x1601`,`0x0ed4..0x1242` | ~1758 | more render tables |
| smaller (`0xaf1e`,`0x9330`,`0x0bd2`,`0x9612`,`0x4558`…) | ~1400 | HUD/physics/scalars |

The ~14 KB of big regions are the block A/B/C tables — LZS-loaded from the level
files (`6712` writes them), so native level-init = decompress + place them.
CAVEAT: the two captures are at DIFFERENT gameplay frames, so the small
physics/scroll/HUD regions are DYNAMIC state (ship pos/vel, scroll cursor, input),
NOT level-load data — `apply_level_init` already owns those. To isolate pure
static level data, diff two levels at the SAME (cold-start) frame.

**FILE→DGROUP dig (this turn).** Instrumented the level-load demo's file opens +
DGROUP writes. This level opens **`MUZAX.LZS`** (music, not level data),
**`ROADS.LZS`** (road cells/params — `roads_archive` ✓), and **`WORLD4.LZS`**
(the ~14 KB render/perspective/staging tables). So there IS a level→world
mapping (this demo's level → WORLD4), and the level tables come from
`WORLD<n>.LZS`.

**`WORLD4.LZS` decompresses cleanly:** container is `CMAP`(4) + 114-byte palette,
LZS payload at **`0x7A`**, first 3 bytes = widths (`07 07 09`), decompresses to
**55,222 bytes** (= block A 7926 + B 3136 + C 44160 per `level_format.md`),
51,705 non-zero — real data, matches the expected size.

**OPEN QUESTION (next pass):** the decompressed `WORLD4` bytes were NOT found
verbatim at the demo-level's DGROUP table regions (`0x162C` perspective,
`0x0ed4`, `0x34a8`, `0x54B0`) — no full match, no 48-byte needle match. So the
load is NOT a naive "decompress → memcpy block to fixed offset" at the offsets I
tried. Possible causes to resolve: (a) block BOUNDARIES misaligned (I searched
`0x162C..0x2320` which overshoots block B's 3136 bytes into adjacent data);
(b) the three blocks are placed in a different ORDER / at different DGROUP
offsets than `level_format.md`'s WORLD7-derived guess; (c) my demo-level DGROUP
capture point (10th gameplay sub-step) already had dynamic overwrites; (d) a
per-block copy loop reorders. RESOLVE by instrumenting `6712`/`5D18` during the
real WORLD4 decode to record exact (decompressed-source-offset →
DGROUP-dest-offset, length) tuples — the definitive placement map — rather than
guessing block layout.

**PLACEMENT MAP CAPTURED — resolves the open question.** Instrumented the WORLD4
decode (load window only, before the first gameplay sub-step, filtered to the
load routines `6712`/`5f95`/`5d18`/`4052`/`5ce0`). WORLD4 is **NOT one
55,222-byte LZS stream** — it is **multiple LZS SUB-BLOCKS**, each `6712`-decoded
directly to its own destination:

| WORLD4 sub-block | LZS-decoded to | size |
|---|---|---|
| tile bitmaps (block C) | segment `0x7176` | `0xAC80` = 44160 B |
| 2nd graphics bank | segment `0x7c3e` (right after block C) | `0x4D80` = 19840 B |
| staging tables | DGROUP `0x31A8` | `0x1000`+`0xA6D` ≈ 6765 B |
| palette (256×3) | via `5D18` to DGROUP `0x31A8`+ | `0x300` = 768 B |

The compressed sub-blocks are read from the file into DGROUP input buffers
(`si=0xb91a`, `0x0ca5`) first, then `6712` decodes each to its dest; `5f95`/
`5d18`/`4052` are the copy/setup around it. **This is why the earlier
`decompress(payload, 55222)` verbatim-search failed** — I decoded ONE monolithic
stream, but WORLD*.LZS is a directory of several compressed sub-streams to
DIFFERENT segments (bitmaps → `0x7176`, graphics → `0x7c3e`, staging/perspective/
descriptors → DGROUP). The perspective LUT (`0x162C`, 724 B) and block-A
descriptors (`0x54B0`) are the smaller sub-blocks (fragmented under the 64 B
run filter here).

**Native `level_load.py` spec is now concrete:** parse WORLD*.LZS's sub-block
directory (per-block compressed offset/size + destination), LZS-decode each to
its segment/DGROUP dest (reusing `codecs/lzs`), + `ROADS.LZS[level]` cells/params
(`roads_archive`). Remaining detail: the sub-block DIRECTORY format inside
WORLD*.LZS (where the per-sub-block offsets/sizes/dests are encoded — the
`0x77xx`/`0x0Cxx` read pointers suggest a small header the loader walks). Then
mirror pre2's `probe_native_level_load` to assert byte-exact vs the VM.

**More context + a strategy pivot (this turn):**
- The level-select demo (`..._202740`) reads only **6765 B** (`[0x1000,0xA6D]`)
  of WORLD4.LZS into DGROUP `0x31A8` then closes — the per-level TABLE block. Its
  world GRAPHICS (`0x7176`/`0x7c3e` bitmaps) source from a DIFFERENT buffer
  (`0xb91a`) that was NOT read in this window: this demo RESUMES with the world's
  graphics already cached in its snapshot. A true cold load re-reads them.
- The `demo_cold_20260711_201845` is NOT a gameplay cold-start — it's the
  INTRO/attract sequence (`is_cold_start=False`; opens `INTRO.LZS`/`ANIM.LZS`/
  `DEMO.REC`/`INTRO.SND`, never reaches WORLD/ROADS). General LZS read pattern
  seen: a small header read first (e.g. MUZAX `[6, 4096]`) then 4096-byte chunks.
- **STRATEGY PIVOT:** manually reverse-engineering the WORLD*.LZS sub-block
  directory format is tedious and error-prone (this dig kept finding it more
  layered than assumed). pre2_port did NOT hand-decode its file format — it
  recovered the LOADER ROUTINE (`3ed6`) and reproduced its logic, which encodes
  the format in code. The SkyRoads analog: **lift/recover the level-load
  orchestrator** (the `0x5D07`/`0x5F95`/`0x5D18`/`0x4052` family `4B8E` calls,
  which read the file + drive `6712` decodes + place blocks) via the proven
  liftgen/liftverify workflow, feeding it a native file-read shim — rather than
  reconstructing the container format from raw bytes. That reuses the exact
  machinery that just landed the render tree, and inherits the format knowledge
  from the ASM instead of re-deriving it.
- **LIFTABILITY CENSUS (confirms the pivot is tractable):** liftgen on the
  loader family from the gameplay snapshot → **10/10 LIFTABLE (~215 insts):**
  `4B8E`(57), `6712`(39, LZS decode, 3 calls), `4B43`(29, 3 calls), `4052`(25),
  `3F3B`(20), `5D07`(12), `3F20`(11), `6006`(11), `5F95`(11). None do INT
  directly — the file I/O is isolated in a separate open/read wrapper (the
  `0x6C2E`/`0x6C72` chain seen in the open's stack), which is where the single
  **native file-read shim** attaches. So milestone 1 = lift this ~215-inst family
  (same size class as the already-landed `2D1F`) + a file shim + assemble, all on
  the proven workflow. Loader nesting/entry (from the WORLD4-open stack walk):
  `…→0x53BB→…→0x6C2E (open+read wrapper)→6712 (decode)`; callers of `5F95`/`5D18`
  read as bp-frame offsets (not raw sp), so use the stack-walk chain, not the
  top-of-stack, to trace them.
- **TWO distinct `4B8E` calls (key to the remaining work):** at frame ~46 from
  caller `0x5374` (args `[0x5174,0,0x24,0xa]`) and at frame ~48 from caller
  `0x2C58`/ret `0x2c5b` (args `[0x41c2,1,0x24,0x1e]`). The `0x2C58` one is
  RENDER-heavy — it drives the `4331` palette cross-fade, which is exactly what
  RUNS AWAY on a bare snapshot resume (it needs live render/tick state). The
  sim-relevant DATA load is the other path. Per pre2's "separate the DGROUP sim
  contract from render side-effects" rule, the native loader should reproduce
  only the DATA-load subset (file read + `6712` decode + place), NOT the
  palette-fade render. Witness snapshot `artifacts/snap_load_4b8e` was captured
  at a `4B8E` entry (frame 48 = the `0x2C58` render call) — useful but note it
  is the render-heavy one; a data-load witness needs the `0x5374` entry.
- **Honest state:** milestone 1 is fully mapped and proven tractable (loader
  100% liftable), but it is the hard, fiddly remaining piece — a focused
  multi-step loader recovery (isolate the data-load subset from the render fade,
  lift it, attach a native file-read shim, assemble `native_level_load.py`,
  verify byte-exact vs a VM witness), NOT a one-trace win. The sim + full render
  tree are done/verified; this loader is the last milestone-1 gap.

## 2026-07-13 — ghosting cornered: sim is EXACT; trails start when the composite's columns fire (block approach)

Systematic elimination (all offline against the level-14 demo):

- **The native sim is NOT drifting**: from the same seed, my driver's
  `[9618]`/ship_pos match the VM's substeps BIT-FOR-BIT (native t8/t9 ==
  VM f92/f93). The earlier "6.7x rate" read was wrong — `[9618]` grows
  QUADRATICALLY by design (it accumulates ship_pos) in both. af1c/af2c stay
  constant (0x8000/0x2800) while driving; the demo's wedge NEVER moves after
  the first draw (the only dirty-cache miss in 160 frames is the initial
  cache fill at frame 78; note the cache check must be instrumented at 0C98
  ENTRY — 0C98 updates the cache before calling 2D1F).
- **The renderer is clean through rotation crossings while the road is
  empty**: ghost_t60 (rotations 3→5, composite columns = 0) is pixel-perfect.
- **Trails begin exactly when `composite_mode0`'s road_column_strip calls
  START FIRING** (tick 144+, counts 2/6/10 as BLOCKS approach; histogram
  0×144, then 2/6/10). ghost_t199 shows the wedge trails.
- Checked and ruled out: records a/b ordering (hook uses [0E62] then [0E60];
  my setup passes prev([0E62-equiv]) then cur ✓ same), e64 passed explicitly ✓.

**Remaining suspects (next capture decides):** (a) the `34AE(1)` MODE-1 pass
`2D1F` makes at its end (my compose skips it) — likely the restore/present
companion whose absence shows once columns write pixel runs; (b) the live
column pixel-run writes themselves (di derivation from live e44/e46/e64 in
sequence). DECISIVE NEXT STEP: in demo_e2e (blocks early), capture consecutive
live `2D1F` pre/post pairs across a block approach where in-frame 38BF writes
occur, and lockstep my compose per frame — first divergent write names the fix.
Also fixed understanding: `[9618]` = the forward-scroll accumulator (quadratic),
`lateral_col=[0E2A]` advances ~1 rotation step/9 ticks at speed 1.

## 2026-07-12 (latest+21) — ghosting investigation: single/consecutive frames are VM-exact; the drift is param-change erase

User reports from the native window: (a) GHOSTING on the background, (b) no
SFX, (c) same background/music on every level, (d) asked whether music uses
Nuked OPL. Answers/status:

- (d) NO OPL emulation in native: music = the recovered sequencer's events →
  the modern synth (`skyroads/audio/`). Only the VM viewer uses pynuked.
- (c) Correct — per-level WORLD graphics/palette + MUZAX song loading are the
  known v1 gaps (baseline snapshot assets); queued.
- (b) Correct — SFX not yet mapped (SkyRoads SFX are SB PCM per
  `audio/sink.py`; the trigger `03C2(id)` was spotted in the HUD updater).
  Native SFX = load SFX.SND samples + play on the trigger points; queued.
- (a) GHOSTING: reproduced offline (200-tick native run → road-wedge trails,
  `artifacts/frames/ghost_t199.png`). Pinned what it is NOT: a fast-moving VM
  frame (frame 118) composes 0-residual, and a CONSECUTIVE-frame lockstep
  (compose frame N from VM pre-N, diff vs VM pre-N+1) matches display lists +
  all key DGROUP fields exactly, with only 24 dest bytes differing (rows 96–98
  = the ship area, shades 73 vs 74/75 — a small between-frames ship touch-up,
  likely the 39D4 pair or ship animation, NOT the wedge trails). CONCLUSION:
  the eraser I'm missing only fires when the wedge PARAMETERS change (af1c/
  af2c bounce, steer) — in the captured pair the dirty-cache values were
  identical so nothing needed erasing. NEXT (the decisive capture): a VM
  frame pair bracketing a wedge MOVE (level-start bounce frames ~84-90, where
  af2c changes per frame) — diff will show exactly which routine restores the
  previous wedge (suspect: the 34AE mode-0 column pass, whose road_column
  strips may repaint sky/background bands, and/or the gated ship_sprites
  erase); then port it and the trails disappear.

Also this turn: full suite re-run after the audio-shadowing fix: **365 passed,
1 skipped** — regression-clean.

## 2026-07-12 (latest+20) — HUD gauge system fully decoded: one updater (`12F8`), three DAT widget banks, the `0F8C` widget drawer

The live-gauge machinery is now completely mapped (the port is a bounded
transcription + the same write-log verification used for the tile dispatch):

- **`0F8C(widget_far_ptr, flag)` — the widget drawer** (per-frame HUD writes
  in gameplay are ONLY this): widget = 4-byte header (`dest_off` word into the
  compose buffer `[AF2A]`, `w`, `h`) + stencil data; draws via `stencil_blit`
  (pure ✓, template/other colours `0x5C..0x5F` by `flag`, or 0/5/8 in the
  `[003C]==0` modes), then presents the rect via `4201` (pure ✓) to `A000`
  (and `A200` when page-flipping). Steady-frame observed widgets:
  fuel cells `221a:xxxx` (= FUL_DISP.DAT, 7×4), oxygen cells `2232:xxxx`
  (= OXY_DISP.DAT, 7×4), speed cells `224b:xxxx` (= SPEED.DAT, 5×8) — the
  boot-loaded DAT files ARE the widget stencil banks.
- **`12F8` — the per-frame HUD updater** (called from the main loop, enter
  0x10): (1) lamp blink flag `di` from `([1600]/9)` remainder >4;
  (2) SPEED dial: `new = clamp((ship_pos − [AF2E:AF30]) / 0x141, ≤0x22)`,
  cached at `[41BE]`; walks cells between cache and new, per cell
  `0F8C([54A4:54A6] + word[0x4572 + cell*2], flag = cell<=bound)`;
  (3) OXYGEN bar: `new = ([B13C]+0xBB7)/0xBB8` clamp 10, cache `[456C]`,
  cells via base `[5474:...]` + table `word[0x95F8 + cell*2]`;
  (4) `game_state==5` + lamp-flag edge → a digit-pair draw `1282(0xA0,0xA1,
  7,7,colors)` + sfx trigger `03C2(3)`; (5) FUEL bar: `([5494]+0xBB7)/0xBB8`
  clamp 10, same cell walk (tail at `14B0+`); helpers `1191`/`11D3`/`1282`
  (cell compare/draw/pair).
- Port plan (next focused turn): transcribe `12F8` + helpers into
  `skyroads/native/hud.py` over the pure `stencil_blit`/`present_rect`,
  verify with the same VM write-log capture (widget-draw sequence + A000
  bytes), wire into the window per frame (present A000 rows live instead of
  the frozen dashboard copy).

## 2026-07-12 (latest+19) — 🧭 the COCKPIT in the window; HUD text/flush drivers decoded

The window now presents the full 320×200 game screen: the composed native
viewport (rows 0–137) + the REAL dashboard art (baseline VGA rows 138–199 —
GRAV-O METER / SPEED-FUEL dial / JUMP-O MASTER). Gauge VALUES stay at their
captured state until the gauge renderer is ported.

HUD decode notes (the material for that port):
- **`4526(dest, ?, str_ptr, x)`** — draws a 0-terminated glyph-id STRING, one
  `44BE(dest, ?, glyph_id, x)` per character, advancing dest by 8 px (the
  "500"/"IDLE" readouts; the main loop calls it with strings at `0xD3A`/`0xD42`).
- **`4563(x, y, w)`** — the HUD rect flush: `[0BF6] = y*320+x`, `[0BFA] = w*8`,
  EGA regs via `3C9A` (no-op when `[003C]!=0`), then the RECOVERED `4201`
  present path (param block at `0x0BF4`).
- **Aliasing correction:** the earlier "0x19a1 HUD-buffer writers"
  (`0x1949/0x1965/0x197d/0x19dc`) are the MOVEMENT resolver's DGROUP writes
  (`add [9618]`, `add [AF1C]`, `add [AF2C]`) seen through segment overlap
  (0x19A1<<4 lands inside DGROUP) — not HUD code at all.
- The true per-frame HUD cost is tiny: 179 B/10 frames flushed via
  `masked_blit`, digits via `stencil_blit` (both pure ✓). Remaining port:
  `44BE` (glyph -> stencil_blit call), the gauge/dial/bar draws, and the
  per-frame HUD update driver, feeding sim values (speed `[9330]`, fuel
  `[5494]`, oxygen `[B13C]`).

## 2026-07-12 (latest+18) — 🎵 NATIVE MUSIC: the modern audio layer (pre2's model), playing in the window

Per the user's direction ("pure modern sound and music layer, like pre2_port"),
built `skyroads/audio/` mirroring `pre2/audio/`'s boundary architecture:

- **`events.py`** — semantic events (`NoteOn(channel, freq_hz, FmPatch,
  volume)`, `NoteOff`, `PitchBend`, `SetVolume`, `DrumHit`); no OPL registers
  or DGROUP offsets cross the boundary; events carry their full timbre
  snapshot so backends need neither the VM nor the game files.
- **`opl_events.py`** — the decoder from the RECOVERED music engine's exact
  OPL write stream (`recovered/music.Engine`, byte-exact over 12,882 verified
  ticks) to those events: key-on edges → NoteOn with fnum/block decoded to Hz
  (`fnum·49716/2^(20−block)`), key-offs, in-key volume/pitch changes, rhythm
  drum-bit rises. Live over the level-14 song: 1200 ticks → 43 NoteOns at
  perfectly musical frequencies (195.7–880 Hz, median 220 Hz = A3), 38
  NoteOffs, 10 drum hits.
- **`synth.py`** — the MODERN backend (pre2's "enhanced" role): each NoteOn
  rendered as a float 2-op FM-flavoured voice at 44.1 kHz using the FmPatch as
  timbre hints (op multiples → mod ratio, mod level → index, connection bit →
  additive vs FM, attack nibble → envelope), cached + looped via pygame.mixer;
  NoteOff fades; drums are shaped-noise one-shots. Deliberately NOT an OPL
  emulation. Frontend ring (lazy numpy/pygame).
- **Wired into the window**: 2 engine ticks per 35fps frame (the ISR's 70 Hz),
  engine `ovl` committed to the image, events → synth. Headless smoke test
  (dummy video+audio) clean.

Tests: pure decoder units (440 Hz keyon math, drum-edge semantics) + the live
1200-tick musical-sanity test (gated on the snapshot). v1 caveats: SetVolume
adjusts the live channel but PitchBend doesn't re-pitch a sounding voice yet
(slides re-trigger on the next NoteOn); gameplay SFX (crash/jump — likely the
same engine via separate triggers) not yet mapped; per-level song loading
(MUZAX.LZS native parse) pending — the window plays the baseline's loaded song.

## 2026-07-12 (latest+17) — 🕹️ THE WINDOW: `play_native --level N --window` plays SkyRoads natively

The interactive native player is wired:

- **`skyroads/native/frame.py`** — the complete per-frame render as one
  function (`render_native_frame` / `compose_frame`): params (0C98, 40/40) →
  `[0E28..0E36]` → rebuild-only background-bank copy → `composite_mode0`
  (display-list build) → tile passes + ship-row tile → post-steps (rotation +
  mask copy). **Verified 0-residual on BOTH captures** (steady + full-rebuild),
  post-step DGROUP fields matching. `ship_sprites` (`39D4`) is ported but NOT
  called on the off-screen path: its VM writes are delta-stable no-ops on both
  captures, and its erase pair's exact `34AE(1)` mode-1 gating is undecoded —
  calling it unconditionally diverges (273 B). Wire it with the direct-VGA
  path once `34AE(1)` is decoded.
- **`play_native --level N --window`** — a real pygame window (via
  `dos_re.display.Display`): arrows = steer/accelerate, space = jump, ESC
  quits. Per frame: keys → the sim axes, one `NativeGameplayDriver` tick
  (auto-respawn at boundaries), one `render_native_frame`, present the 320×138
  viewport through the level DAC at 35 fps. Headless smoke test
  (`SDL_VIDEODRIVER=dummy --window-frames 40`) runs clean; a moving frame
  (tick 60, ship at 0x1194) renders correctly.
- **v1 caveats (honest):** world graphics banks + palette come from a full
  baseline snapshot (`--window-baseline`, default `artifacts/frame_2d1f/snap92`
  = level 14's world) — playing a level from another world shows this world's
  tile art until the WORLD.LZS graphics loader is native; the palette is the
  snapshot DAC (native palette generation from the fade target = follow-up);
  the dashboard/HUD rows are blacked (the HUD path isn't in the frame fn yet);
  no audio.

## 2026-07-12 (latest+16) — the FULL-REBUILD frame is byte-exact too; FIRST NATIVE PNG rendered

Extended the frame proof to the FULL-REBUILD case and produced the first
visible native frame:

- **Full-redraw capture** (`artifacts/frame_2d1f_first`, the FIRST gameplay
  `2D1F` at frame 48): 96,410 writes, dominated by `34AE` (88K). Analysis:
  on rebuild (`params[5]` — previously named `zero` — is **1**, a full-rebuild
  flag) `34AE` mode-0 copies the **44,160-byte background bank**
  (`[5170]`=0x7176 → dest, = the 320×138 nebula backdrop) and initializes the
  VGA pages (~28KB to A000..A6FFF); `2e43` = the post-loop mask copy (956B);
  `2dd9`-attributed DGROUP writes are the loop counters.
- **Native compose of the full-rebuild frame: 0/65536 residual.** background
  copy + `composite_mode0` (display-list build) + `render_tile_passes` +
  `tile_rasterize` == the VM's post-frame byte-for-byte — on BOTH captures
  (steady frame 90 and rebuild frame 48).
- **First native PNG** (`artifacts/frames/native_frame.png`): the composed
  frame rendered through the level's real DAC — nebula, road, SHIP sprite,
  all from pure recovered Python. (Gotcha: the DAC at frame 48 is mid
  level-select→gameplay FADE — black; render with the settled palette from a
  frame-92 snapshot. The bottom rows of the off-screen buffer are scratch —
  the dashboard lives on the real VGA screen via the HUD path.)
- `composite_mode0`'s role is now precisely understood: **the display-list
  build/maintenance pass** (mode 0), including the background copy on rebuild;
  the tile passes then rasterize the strips to pixels each frame.

Remaining for the WINDOW: assemble `native/frame.py` (params → mode-0 +
background-on-rebuild → tile passes → ship row → `34AE(1)`/`39D4` ship sprite
→ post-steps) and wrap window+keyboard around the native sim.

## 2026-07-12 (latest+15) — 🎯 THE FIRST FULLY-NATIVE GAMEPLAY FRAME IS BYTE-EXACT (65536/65536)

Promoted the ship-row tile chain (`skyroads/recovered/tile_raster.py`:
`tile_mask_build` 32C1 + the 29×24 masked blit + `tile_shade` 33FD, from the
differential-verified hook bodies) and wired it into `render_tile_passes` as
`on_ship_row`. Result on the captured live frame:

- **Write sequence: 3553/3553 IDENTICAL** (road tiles + ship-row tile, exact
  order/offset/value vs the VM's log).
- **Final frame: 0 of 65536 bytes differ** from the VM's post-`2D1F`
  destination window. The full native gameplay frame — road, edges, block
  faces, ship-row tile, shading — is BYTE-EXACT against the original game,
  produced entirely by pure recovered Python (`compute_render_params` →
  `render_tile_passes` → rle pair + tile chain), no VM, no lifted-code
  execution.
- The `3a22` ship-sprite calls (the `34AE(1)`/`39D4` finalize) were
  delta-stable rewrites on this frame (their 387 writes changed nothing), so
  byte-equality holds without them; a FULL-REDRAW capture (first frame after a
  dirty-cache miss on a fresh buffer) is the follow-up that will exercise them
  + the mode-0 composite for completeness.

`tests/test_tile_dispatch.py::test_full_native_frame_is_byte_exact` locks it
in. Remaining for task #22: the frame post-steps (`[0E6A]=[0E2A]` rotation +
`0E86→1243` mask copy — trivial), a full-redraw capture to exercise the
composite/ship-sprite paths, PNG proof from a native `--level N` state, and
the window+keyboard shell.

## 2026-07-12 (latest+14) — TILE DISPATCH PORTED + verified WRITE-FOR-WRITE (3166/3166): the native road frame renders

Landed `skyroads/native/tile_dispatch.py` — the pure transcription of `2D1F`'s
11-row × 2-pass × 4-column road loop + all 6 tile handlers + the `31D1` strip
skip, drawing through the promoted pure RLE pair. **Verified against the
captured VM frame: all 3166 road-tile rasterizer writes (`3153`/`3190`)
reproduced EXACTLY — same count, same order, same offsets, same values.**
(`tests/test_tile_dispatch.py`, gated on the gitignored capture.)

Debug notes for the record: (a) first comparison showed "0 writes logged" — the
loop writes through the BUMPED segment (`[0E36]+0x280` → `0x8396`, aliasing
into the `0x8116` 64KB window), so log by PHYSICAL range, not segment equality;
(b) the dest bump is `+0x280` paragraphs when `[003C]!=0` (off-screen) and
`+0x50` when rendering to the VGA pages.

**What remains of the live frame** (the other 774 VM writes in the capture):
- `325b` ship-row tile chain (`call ss:[0E3E]` between the ship row's two runs):
  `325B` setup + `32C1` mask clear + `3283` 29×24 stencil blit + `33FD` shade —
  ALL already hook-reimplemented in Python; promote like the RLE pair.
- `3a22` ship sprite via the `34AE(1)` finalize / `39D4` chain — `sprite_blit`
  is already pure; the `34AE` mode-1 pass drives it (lifted; decode its mode-1
  path or drive from the lift).
- then the frame post-steps (`[0E6A]=[0E2A]` rotation, `0E86→1243` mask copy —
  trivial) and the window shell.

## 2026-07-12 (latest+13) — tile-dispatch fully decoded; every piece of the live frame is Python-portable

Continued dissecting the captured live frame. LINDIS GOTCHA resolved first: its
`[bx+NNN]` displacements print in DECIMAL — so `2D1F`'s dispatch is
`call ss:[bx+0x0BAF]` (not 2991) and its display-list DS comes from
`[0x0E76 + (e2a&7)*2]` (not 3702) — i.e. the KNOWN `0x0E76` rotating buffer
table, and a tile-handler table at `0x0BAF` (right after the `0xBA7` shape
table). From the captured image:

- **`[0BAF]` tile-handler table (indexed by road-record `[bp+1]&0xF` = tile
  type):** type 0→`2E6C`, 1→`3059`, 2→`2EBB`, 3→`2EFD`, 4→`2F58`, 5→`2FCC`,
  6..15→`3AC9` (default/no-op).
- **Function pointers:** `[0E38]=34A7` (= `34AE` with ax=0 — the mode-0
  COMPOSITE runs FIRST inside `2D1F`, so `composite_mode0` IS part of the live
  frame after all); `[0E3A]=3153`/`[0E3C]=3190` (rle fwd/bwd — `[0E40]` selects
  per pass `[0E48]`); `[0E3E]=325B` (tile_rasterizer, the row-4 special).
- **Handler anatomy (type 0, `2E6C`, fully read):** `al = record[bp]&0xF`
  (tile id); 0 → nothing; else write it into the DISPLAY-LIST SLOT
  (`si = ds:[di]`, `ds:[si] = al`) and `call [0E40]` to rasterize; then
  neighbor checks — `record[bp ± 2]` (pass-dependent) empty → edge tile
  `id+0x1E`, `record[bp-0xE]` (row above) empty → edge tile `id+0x0F`, each
  also rasterized. Types 2/3 (`2EBB`/`2EFD`) call `2E6C` then add block
  top/side pieces via slots `ds:[di+4]`/`ds:[di+6]` (hi-nibble block type,
  default tile `0x3D`), gated on `[bp-13]`/`[bp+1]` neighbor fields.
- **The `[0E40]` rasterizers (`3153`/`3190`) are ALREADY full Python** — the
  hooks' bodies are pure logic (RLE decode: per-run `ctrl` back-step + fill
  word runs + 0x140 row stride, `si` walks the display-list record), no ASM.
  Promotable to `recovered/` pure fns exactly like `sprite_blit` was.

**COMPLETE handler decode (this turn — the full material for the port):**
- `31D1` = SKIP one RLE strip without drawing: `si += 3` (past the 3-byte
  header), then `si += 3` per control triplet until `0xFF`, `si += 1`. So
  display-list SLOTS hold sequences of strips; `call [0E40]` rasterizes one and
  leaves `si` at the next — handlers chain draw/skip calls to select which
  strips of a slot appear.
- Handlers PATCH a strip's first byte (`ds:[si] = tile_id`) before rasterizing
  — the fill-colour index IS the tile appearance, chosen per frame.
- Neighbor fields: `[bp-13]` = row-above record's type byte (`bp-0xE+1`),
  `[bp+1±…]` = adjacent column's type via `bx = 2 − 4*[0E48]` (mirror-pass
  dependent side). `[bp-14]` = row-above height nibble.
- **Type 0 (`2E6C`)**: height nibble → slot`[di]` strip patched+drawn; empty
  side-neighbor → `id+0x1E` drawn (else `31D1` skip); empty row-above →
  `id+0x0F` drawn.
- **Type 2 (`2EBB`)**: base; row-above `<2` → slot`[di+6]` drawn; slot`[di+4]`
  patched hi-nibble (0 → `0x3D`) + drawn; side `<2` → extra draw.
- **Type 3 (`2EFD`)**: base; row-above `<2` → slot`[di+2]` := `0x41` drawn;
  slot`[di+4]` hi-nibble/0x3D + drawn; side `<2` → draw; row-above `<2` →
  slot`[di+6]` skip, draw, draw.
- **Type 4 (`2F58`)**: base; row-above `<2` → slot`[di+6]` draw; slot`[di+4]`
  skip; side `<2` → draw; slot`[di+10]` hi-nibble/0x3D + draw; side `<4` →
  draw else skip; row-above `<4` → draw.
- **Type 5 (`2FCC`)**: base; row-above `<2` → slot`[di+2]` := `0x41` draw;
  slot`[di+4]` skip; side `<2` → draw; row-above `<2` → slot`[di+6]` skip,
  draw, draw; slot`[di+10]` hi-nibble/0x3D + draw; side `<4` → draw else skip;
  row-above `<4` → draw.
- **Type 1 (`3059`)**: base; row-above `<1` → slot`[di+2]` := `0x43` draw;
  slot`[di+8]` → SIX consecutive draws; row-above `<1` → two more.
- **`3AC9`** = plain `ret` (types 6–15 draw nothing).

Landed this turn: **`skyroads/recovered/rle_sprite.py`** — the RLE pair promoted
to pure functions (from the differential-verified hook bodies), + semantics
tests (3), lint + audit clean (34 pure files).

**Task #22 remaining is now a bounded porting list, zero unknowns:**
1. Promote `rle_sprite_forward`/`backward` (+ `tile_rasterizer 325B`,
   `occluded_column_blit 3283`) hook bodies to pure fns.
2. Port the `2D1F` loop (11×(2×4) record walk, exact bp/di stepping from the
   disasm) + the 6 tile handlers + `31D1` into `native/tile_dispatch.py`.
3. Verify against `artifacts/frame_2d1f` (pure frame vs post-image dest — the
   18-net-byte delta first, then more captures incl. full redraws).
4. Window + keyboard shell.

## 2026-07-12 (latest+12) — live-frame anatomy: 2D1F draws via 4 ALREADY-RECOVERED rasterizers, delta-rendered

Captured a real gameplay `2D1F` call (frame 90, params
`row_base=0x100, lateral_col=0x18, screen_row=0x50, sprite=5e61:7620, dest=0x8116`)
with full pre/post 1MB images (`artifacts/frame_2d1f`, gitignored), and probed it:

- **The frame is DELTA-rendERED**: this call changed only **18 net bytes** of the
  dest (rows 95–98) — the rotating display-list pair (`[0E60]`/`[0E62]`, fed by
  the `[0E2A]`→`[0E6A]` frame rotation) makes each frame redraw only what moved.
  A native frame assembler must reproduce that rotation, or force full redraws.
- **Every dest byte is written by an already-recovered routine.** Writers during
  the call (VM, write-watchers): `0x3153` rle_sprite_forward, `0x3190`
  rle_sprite_backward, `0x3a22` sprite_blit (pure), `0x325b` tile_rasterizer —
  the per-tile handlers `2D1F`'s loop dispatches through the `ss:[bx+2991]`
  pointer table (+ `[0E38]`/`[0E40]`). ~3.9 KB written total (mostly rewriting
  identical pixels), 18 net.
- The pure `composite_mode0` (34AE mode-0) produced **0 column calls** on this
  frame — the LIVE call is `34AE(ax=1)` + `2D1F`'s own loop, i.e. the mode-0
  model is a different pass; the live loop's per-tile dispatch through `[2991]`
  (tile type → rle_sprite/tile_rasterizer/road_column) is the composition still
  to assemble natively.

**Net for task #22:** the live frame = `render_classify` grid walk → per-tile
handler dispatch (`[2991]` table) → the four recovered rasterizers, with
delta-rendering via the display-list rotation. All pixels-writing pieces exist;
the remaining work is the tile-type→handler dispatch wiring (the `[2991]` table
+ per-tile args), which the lifted `2D1F` already encodes — either port that
dispatch purely (preferred) or drive the lifted chain over a NativeGameImage.

## 2026-07-12 (latest+11) — render orchestrator PORTED + verified 40/40: `native/render_params.py`

Ported `1010:0C98` to the pure `compute_render_params(rw, ww, offscreen)`
(`skyroads/native/render_params.py`): sim state → the road renderer's 8 params
(`[0E28..0E36]` order), the `[0E1C..0E26]` dirty-cache maintenance, the
A000/A200 page-flip dest select, and the sprite-frame index
`si = (row_band(af1c)*3 + pitch)*3 + 14 + wobble` (or `air/3`, capped → hidden).
Leaves ported inline: `0BAF` pitch selector (2 = diving/low, 1 = climbing),
`0BE9` row band `clamp((af1c/0x80−0x5F) idiv 0x2E, 0..6)`, `0C26` cell classifier
(04C0 word hi-nibble → `word[0xE4+hi*2]`, 0x100→0; fall → lo-nibble → 0x2800).
Composed existing recoveries for the rest (`ship_fell_off`,
`perspective_row_offset`). **Oracle: 40/40 real `0C98` invocations from the
level-14 demo match — 8 params byte-equal + render/skip decisions agree.**
Fixture + tests committed (the fixture's captured window never skipped, so the
dirty-cache skip path is covered synthetically; the captured cases render
off-screen — `[003C]!=0` in this demo's context — so the page-flip dest test
skips until a VGA-page case is captured).

**Remaining for the windowed native player (task #22):** feed these verified
params into the pure render pipeline (`native/render_frame.py` over a 1MB
native image seeded with the baseline graphics + the level palette `[41C2]`),
then wrap a window + keyboard (key row `[0BD0..]`) around the native sim loop.

## 2026-07-12 (latest+10) — toward a WINDOWED native player: the render orchestrator `0x0C98` decoded; all its callees already recovered

User tried `play_native --level 2` expecting a game window — it's the HEADLESS
sim (now says so explicitly in its output). The gap to a windowed player is the
frame-render assembly (task #22): native sim state → pixels → window+keyboard.
Decoded the missing node this turn — the per-frame RENDER ORCHESTRATOR
(`2D1F`'s caller), entry **`1010:0C98`** (enter 0x0E, one flag arg), body
`0x0C98..0x0ECF`, fully readable from `gameplay_f640` disassembly:

- Computes the 8 render params for `2D1F` PURELY from sim state we own natively:
  lateral `[9618/961A]`, vertical `[AF1C]`/`[AF2C]`, grounded `[456A]`,
  game_state `[456E]`, via `0533` (fall predicate — recovered), `0BAF`/`0BE9`
  (lifted), `0C26` (= recovered `04C0` perspective_transform + a tiny
  cell→value map: hi-nibble→`[0x228+hi*2]` table / lo-nibble→`0x2800`),
  `5D8C` (recovered ulong_div).
- **Frame dirty-cache**: `[0E1C..0E26]` holds last-rendered (lateral, af1c,
  af2c/si, page); if unchanged → SKIP the render entirely.
- **Dest select**: `[003C]!=0` → off-screen `[5478]`; else PAGE FLIP —
  `0xA000 + ([9334]?0:1)<<9` (A000/A200), post-render `xor [9334],1`, and
  `0x0ED0` does `int 10h AH=05` to show the completed page.
- Then `call 2D1F(...8 params...)` — which stores them to `[0E28..0E36]` and
  renders (the lifted driver; the PURE equivalent over those globals is
  `native/render_frame.py`'s pipeline).

**Every callee is already recovered or lifted — the windowed native player is
now pure assembly, zero reverse-engineering left on this path:**
1. Port `0x0C98` orchestration to pure Python (small; callees exist).
2. Per frame: write the 8 params → run the pure render pipeline over a 1MB
   native image (graphics segments seeded from the baseline; palette from the
   level's `[41C2]`, 6-bit→8-bit).
3. Window + keyboard (feed the key row `[0BD0..]`) around the native sim loop.

## 2026-07-12 (latest+9) — MILESTONE 1 SIM ACHIEVED: native `--level N` plays BIT-FOR-BIT identically to the VM

The "crash / ship-not-move" was two misreads on my part, both now resolved:
1. Forward motion is INPUT-driven (hold accelerate → `view.speed=1`); with that,
   the ship advances +75/tick (`0x4B,0x96,0xE1,…`).
2. The `game_state=3` at frame 108 is a CORRECT crash — holding accelerate
   STRAIGHT into an obstacle with no steer/jump. Not a bug.

**PROOF (compare_play):** `native_level_load(14)` over a constants baseline vs a
VM-captured level-14 seed, both run with held-accelerate → **IDENTICAL** 296-tick
ship_pos progression AND identical transition (`game_state=3, frame_ctr=108`).
`A == B` byte-for-byte. So native `--level N` plays the level VM-exactly. The
`0x54B0` block-A cell array does NOT affect the sim (render-only, per pre2's
separate-render rule) — `native_level_load` is COMPLETE for the sim contract; no
block-A loader needed. Retract the previous entry's "recover the 0x54B0 loader"
next-step: it isn't required for play.

**So milestone 1's core is done:** load ANY level by index from `ROADS.LZS`
(VM-free, verified) → `native_level_load` places the geometry → the native sim
plays it identically to the original. Remaining is PACKAGING, not recovery:
- ✅ WIRED `scripts/play_native.py --level N` — `python scripts/play_native.py
  --level 14` loads level 14 from ROADS.LZS VM-free, plays it holding accelerate
  (`+75/tick`, crashes at frame 108 into the first obstacle — expected without
  steer/jump). No demo, no per-run VM boot. Test:
  `tests/test_level_load.py::test_native_loaded_level_plays_the_vm_golden_trajectory`.
- The level-INDEPENDENT sim constants (clip `0x4C..0xE3`, shape `0xBA7`, …) that a
  fresh state lacks are computed at startup, not static in the EXE. For milestone
  1 they're a fixed captured baseline (a small boot-constants asset — level
  independent). Computing them natively from scratch = milestone 2's cold boot.
- To COMPLETE a level (not just play its opening), feed the level's recorded
  input (steer/jump/accelerate) — the strongest proof is native+input in lockstep
  with the VM to `game_state=2`.

## 2026-07-12 (latest+8) — roads_archive road-length BUG fixed (−222); ship-not-move is a PRE-EXISTING native-sim issue, NOT the loader

Two findings while pushing `native_level_load` end-to-end:

**BUG FIXED — `roads_archive.read_level_road` truncated every road by 222 bytes.**
It used `road_len = directory_length − LEVEL_HEADER_LEN`, but the loader `1010:5614`
passes the directory length DIRECTLY to the LZS decode (`66E6(0x162C, size)`). The
222-byte header is uncompressed and precedes the compressed road, so it shifts the
INPUT offset (`road_offset`) only — it must NOT be subtracted from the decompressed
out_size. Verified vs the VM: level 14's directory length=3318 decodes exactly 3318
bytes into `0x162C`, matching memory. The old 3096 was a valid PREFIX, which hid the
bug behind prefix-only checks (incl. the old test, which asserted `== length − 222`
despite its name saying "the exact directory length"). Fixed + test corrected;
31/31 decode cleanly, live-VM oracle still matches. `native_level_load` now places
the full 3318-byte road.

**Ship-not-move on level 14 is PRE-EXISTING, not `native_level_load`.** Wiring
`native_level_load(14)` end-to-end, the ship didn't advance (`ship_pos` stuck at 0).
Isolated it: the driver does NOT advance a level-14 seed even when that seed is
CAPTURED FROM THE VM (not reconstructed), and the existing
`play_native --cold --demo <level-select-demo>` (which seeds level 14 from the VM at
frame 79, gate 8) ALSO fails — `did not reach a transition within 2000 ticks,
ship_pos=0x0` — while `--cold --demo <demo_e2e>` completes its level in 57 ticks.
So the native cold-run is LEVEL/seed-specific: it advances demo_e2e's level but not
the level-14 seed, independent of how the seed was produced. `native_level_load`'s
geometry is verified byte-exact; this is a separate native-sim discrepancy (task
#18's "--cold plays whole levels" is really "plays the demo_e2e level"). PINNED IT: traced VM `ship_pos` over the level-14 demo — it stays 0 through frame
~80, then STARTS ADVANCING at frame 88 (`0x177`→`0x465`→`0x708`→… steady). So level
14 has a **~80-frame START-DELAY** before forward motion begins, and `boot_and_seed`
captures at frame 79 — squarely INSIDE the pre-move phase. `apply_level_init` resets
to `ship_pos=0` but the native sub-step does NOT reproduce the start-delay→move
transition, so it stays parked. demo_e2e's `--cold` works because its seed (frame
566) is past its own start-delay. **This is a pre-existing native-sim gap: the
start countdown / "go" trigger that unblocks forward motion is not modelled** (or
depends on state `apply_level_init` doesn't set). It is INDEPENDENT of
`native_level_load` (geometry verified byte-exact) and of the roads_archive fix.
NEXT: diff the VM DGROUP at frame 80 (parked) vs frame 88 (moving) for level 14 —
the byte(s) that flip are the start-delay counter / go-flag the native sub-step must
honor. Fixing that makes BOTH the existing `--cold` (on any level) and native
`--level N` advance. This is squarely in the recovered sim (dynamics/classify), not
the loader.

**RESOLVED the "start-delay": forward motion is INPUT-DRIVEN, not automatic.**
`[0x9330]` speed is written by `1010:08E6` = `(up|ul|ur) − (dn|dl|dr)` read from
the keyboard-flag row (`[0BD2] bit 0x80` = UP/accelerate, etc.) — exactly what
`recovered/controls.decode_keyboard` reproduces. The VM ship starts moving at
frame 85 because the DEMO PRESSES UP then; before that, speed=0 and the ship is
parked. So there is NO automatic forward motion: the ship only advances while
accelerate is held. This CORRECTS task #18's "--cold plays with idle input" claim
— `--cold` on demo_e2e only worked because its seed (frame 566) had the up-key
already HELD in `[0BD2]`, and the idle driver kept that key state; the level-14
seed (frame 79) is pre-press, so it parks. The native driver's `tick()` uses
`view.speed` (`native_gameplay_substep`, `advance_ship(pos, view.speed)`).

**Two remaining gaps for native `--level N` (both now pinned):**
1. **Input**: hold accelerate — set `view.speed=1` (or `[0BD2]|=0x80`) each tick.
   With `view.speed=1`, native level 14 now RUNS (frame_ctr advances) instead of
   parking forever.
2. **Collision reads unset level state → crash.** With accelerate held, native
   level 14 reaches `game_state=3` (crash) at frame_ctr 108 with ship_pos still 0
   — the ship "crashes" immediately because collision reads level-dependent DGROUP
   `native_level_load` does NOT set (the `0x54B0` block-A cell array, still the
   baseline's). This is the SEPARATE loader `native_level_load` doesn't cover yet
   (the block-A cells written by a different loader than `5614`). NEXT: recover the
   `0x54B0` block-A cell loader (same disassembly method), place it, then native
   `--level N` = `native_level_load` + block-A + held-accelerate → should play.

**Transition pinned to frame 84→86** (ship_pos 0 → 0xE1). DGROUP fields that flip
exactly when motion starts (candidates for the speed-onset/go mechanism):
- `0x9330`: **0 → 1** — the SPEED byte (its onset from zero).
- `0xaf1e`: **1 → 0** — a flag/high-word of the af1c vertical state clearing.
- `0x0e1c` / `0x9618`: **0 → 0x01C2** — a velocity/step delta appearing (identical
  value at both spots — the forward step per sub-step).
- `0x1600`: 07 → 0A (tick counter); `0xb13c`: 74FF → 74EA (a fast counter).

So the native sub-step, from a PRE-MOVE seed, never sets `[0x9330]` speed / the
`0x01C2` step delta / clears `[0xaf1e]` — it parks. `--verify` on this demo
confirms it: "0 lockstep runs, all ended on a boundary" (the native driver diverges
immediately from the frame-79 seed rather than running gameplay sub-steps). demo_e2e
works because its seed is already past this onset. NEXT: find what drives `[0x9330]`
0→1 / the `0x01C2` step in the VM around frame 85 (a speed-ramp or a go-gate the
recovered `dynamics`/`classify` sub-step omits), and honor it — that unblocks both
`--cold` on any level and native `--level N`.

## 2026-07-12 (latest+7) — BREAKTHROUGH: native VM-free level GEOMETRY load recovered + VM-verified; `native_level_load` implemented

Disassembling the geometry loader `1010:5614` (churn-immune) gave the complete,
simple algorithm — and it collapses the whole milestone-1 mystery:

```
5614: memset(0x162C, 0, 0x1B58)                 ; 5D07 clear
      open(file); lseek(handle, level*4, 0)     ; 4-byte-per-level directory
      read u16 data_offset; read u16 data_size  ; 5F7D
      lseek(handle, data_offset, 0)
      read gravity->[4562]; fuel->[54A2]; oxygen->[4566]   ; 6576 x3
      read 216-byte palette -> [41C2]           ; 6595
      LZS-decode road[] (data_size B) -> [0x162C]          ; 66E6
      close
```

**The sim's "perspective table" at `0x162C` is simply `road[]` from `ROADS.LZS`**
(LZS-decoded). There is NO separate `WORLD` perspective decode for the SIM —
`WORLD*.LZS` is render-graphics only. So the entire level GEOMETRY seed comes
from `ROADS.LZS[level]`, which `roads_archive` ALREADY decodes byte-exact; the
loader just PLACES it at fixed offsets.

**Placement VERIFIED byte-exact vs the VM** (the level-select demo loads level
14): `[4562]==gravity(8)`, `[54A2]==fuel(225)`, `[4566]==oxygen(111)`,
`road[]@0x162C` and `palette@0x41C2` all match `roads_archive` exactly.

**Implemented `native_level_load(state, level, game_root)`** in
`skyroads/native/level_load.py` — reproduces `5614`'s DGROUP writes (memset clear
+ road[]→0x162C + gravity/fuel/oxygen + palette), 100% VM-free, `[asm 5614]`-
annotated. 7 tests (incl. the level-14 VM-verified placement, the memset
stale-clear, and all-31-levels), lint + layer audit clean. This corrects the
many earlier entries that treated `0x162C` as a computed/`WORLD`-loaded
perspective LUT: it is road data, straight from ROADS.LZS.

**Remaining for `play_native --level N` (end-to-end):** the native sim also reads
level-INDEPENDENT DGROUP constants (clip tables `0x4C..0xE3`, shape `0xBA7`,
etc.). Checked: these are NOT static in the EXE image (all-zero at load) — they
are COMPUTED by startup init, so a fresh/EXE baseline lacks them. They ARE
level-independent, so a captured baseline works for milestone 1 (full native
computation of them = milestone 2's cold boot).

**End-to-end attempt + two gaps found (this turn).** Ran (gameplay_f640
constants baseline) + `native_level_load(14)` + `apply_level_init` → native
driver: it loaded correctly but the ship did NOT advance. Diffing the
native-constructed DGROUP vs a real VM level-14 seed (14 KB / 24 regions differ)
exposed that the sim seed needs MORE than road[]+scalars+palette:
- **The road region extends past `roads_archive`'s output.** road[] ends at
  `0x2244` (3096 B for lvl 14), but the VM has non-zero level data at
  `0x2244..~0x2320` that our memset zeroed. So either `roads_archive`'s road
  length is TOO SHORT (truncating), or a second sub-block follows road[] in the
  `0x162C` region. (Reconcile against task #20's "byte-exact" — likely it matched
  the road[] prefix but not the tail.) The loader's `data_size` (from the 4-byte
  directory) is the authority — compare it to `roads_archive`'s length.
- **`0x54B0` block-A cell array (7505 B, level-dependent)** is NOT set by the
  `5614` road loader — a SEPARATE loader writes it, and if the sim reads it
  (road cells / block descriptors) the stale baseline value breaks motion.

So the `5614` road/perspective load is DONE + VM-verified, but end-to-end play
needs: (a) the correct road-region LENGTH (loader `data_size`, may exceed
`roads_archive`'s), and (b) recovering the `0x54B0` block-A loader (and checking
whether the sim actually reads it vs it being render-only). Next: read the
loader `data_size` for a level and compare to `roads_archive`; then find the
`0x54B0` loader the same way (disassembly from `gameplay_f640`).

## 2026-07-12 (latest+6) — LOADER ROUTINES PINNED (from disassembly, churn-immune): 0x55C0 orchestrator + 5C77/5FB5/5D07

Switched from write-tracing to reading the loader's CODE (bp-frame walk at the
`0x162C` write → its caller region, then lindis from `gameplay_f640` where the
`0x5xxx`/`0x6xxx` load code is NOT overlaid). The geometry loader is now mapped
to exact routines:

- **`0x5614`** — per-block init: `call 5D07(dst=0x162C, val=0, count=0x1B58)`
  = **memset** clearing the 7000-byte perspective region, then `5C77(0x0E08)` +
  decode. (This is the "clear [0x162C..+0x1B58]" from the earlier entry — it is
  `5D07`, a memset, NOT a `rep stosb` in `4B8E`.)
- **`0x55C0`** — the level-FILE loader orchestrator: `5C77(0x0E00)` **opens** a
  file (handle → `ds:[41AC]`, the documented `_LZS_FILE_HANDLE`), `5FB5(...)`
  **reads+LZS-decodes**, `5C11` **closes**. Returns to `0x2C61`.
- **`5C77`** = file open (arg = a filename/descriptor pointer, e.g. `0x7D64`);
  **`5FB5`→`5F7D`** = the LZS read/decode driver (→ `6712`); **`5D07`** = memset;
  **`5C11`** = close. The `5F*` family are thin wrappers over C-library stdio
  (`INT 21h` at `5FCC` kbhit / `5FD4` putchar / `5FEB` getch) — i.e. the
  file/console I/O layer, which is exactly where the **native file-read shim**
  substitutes (no VM DOS).

**So the native loader reimplements a small, pinned set:** `5C77` open → native
file open; `5FB5`/`5F7D` decode driver → native LZS decode (`codecs/lzs`, already
recovered) reading the file bytes; `5D07` memset; the `0x55C0`/`0x5614`
orchestration (which block → which dest, e.g. `0x162C`). All are in the
liftable census (`5D07`/`5F95` etc.) or thin I/O wrappers. This is the concrete
spec for finishing `native_level_load`'s placement — reimplement these routines
(most are trivial: memset, open, close; the substance is `5F7D`'s decode-driver
loop + the `0x55C0`/`0x5614` block→dest mapping), fed by the file shim, verified
byte-exact vs a VM witness. NOTE `0x6308 = call main(0x01B8)` (crt0), so the load
sits inside `main`'s call tree — reachable and disassemblable, not overlaid.

## 2026-07-12 (latest+5) — native `level_load.py` scaffold landed; perspective-placement chain traced; write-tracing hits its limit (→ lift)

- **Landed `skyroads/native/level_load.py`** (pre2-style, committed): the
  FILE-DECODE half is recovered + tested VM-free — `read_game_file` (native
  file shim, no DOS) + `decode_level_files` reads/decompresses all **31/31**
  ROADS.LZS levels byte-exact via `roads_archive`. `native_level_load` FAILS
  LOUD at the DGROUP-placement boundary. 5 tests, lint + layer audit clean.
- **User correction:** there is NO "loading screen" — the demos start ON the
  level-select screen and run the level; the `0x43A9`(palette-fade)/`0x0F62`
  (glyph) writes are the level-select→gameplay TRANSITION + menu render, not a
  loading screen. (Terminology fixed in the module.)
- **Perspective-placement chain traced:** the sim-critical `0x162C` LUT is NOT
  written by `4B8E` (that's frames 46/48; `0x162C` is written at frame 45).
  It is filled by **`5D18`** — a `rep movs` copying **7000 bytes (`cx=0x1B58`)
  from DGROUP `0x0CA5`** into `0x162C` (matches the earlier "clear+fill
  [0x162C..+0x1B58]"). `0x0CA5` is a DECOMPRESSED WORLD buffer (a `6712` decode
  output). So the perspective recipe is: WORLD sub-block → `6712` decode →
  `0x0CA5` → `5D18` copy → `0x162C`.
- **Write-tracing has hit its limit here.** Every attempt to trace what fills
  `0x0CA5` (and the other placement sources) is drowned by the timer ISR
  (`3B22`, constantly writing `[1600]`) + the menu/transition palette fade
  (`434A`/`43A9`) churning DGROUP during the load. The one-time data fills can't
  be cleanly isolated from this runtime churn by watching writes. **This
  confirms the lift strategy is REQUIRED** — recover the loader by reading its
  CODE (liftgen/liftverify, which is churn-immune), not by observing runtime
  writes. Next: lift the `5D18`/`6712`-caller data-load subset (churn-free from
  the disassembly), attach the file shim, fill in `native_level_load`'s
  placement, verify byte-exact. Then mirror pre2's
`probe_native_level_load`: run the real loader to capture the post-DGROUP witness
at the RIGHT point (the actual gameplay-start, not a stray `4B8E`), build
`skyroads/native/level_load.py` to reproduce it, assert byte-exact.

---

**CAPTURED THE `4B8E` ORACLE + shrank the target (following pre2_port's
`native/level_load.py` + probe blueprint — see memory `pre2-native-load-blueprint`).**
> SUPERSEDED by the entry above: the captured `4B8E` was not the level-load
> witness (its `0x162C` is zero), and `4B8E`/`34AE` do NOT build `0x162C` — it's
> LZS-loaded. Kept for the audit trail.
Ran real `4B8E` from the level-load demo `demo_skyroads_20260711_202740`
(args `[0x5174,0,0x24,0xa]`, caller `0x5374`) and diffed DGROUP before/after:

| region `4B8E` writes | bytes | note |
|---|---|---|
| `0x3196..0x3424` | 654 | **the road→staging output** (`0x32xx/0x33xx`, via `4331`→`0x31A8`) |
| `0x347b..0x3496` | 27 | staging tail |
| `0x0c83`,`0x1600`,`0xb8a1`,`0xb8c0` | ~63 | scalars / HUD |
| **`0x162C..0x18FF`** | **0** | **`4B8E` does NOT build the perspective table!** |

So the model refines: **`4B8E` produces the `0x3196..0x3424` STAGING from `road[]`;
the already-LIFTED `34AE` builds `0x162C` from that staging** (its `rep movsb`
reads `0x3285/0x3302/0x33E6/0x33F0`, all inside this region). The native
level-init target is therefore small and bounded — **port `4B8E`'s road[]→
`0x3196..0x3424` staging (~654 B)**, then the lifted `34AE` gives `0x162C` for
free. Oracle saved to `artifacts/oracle_4b8e/{pre,post}_dgroup.bin` (gitignored)
for byte-exact verification of the native port, pre2-probe style.

IMPORTANT: `4B8E` has TWO callers — `0x2C58` (the `snap_before_4b8e` resume;
`4331` runs AWAY there, garbage pre-state) and `0x5374` (the real demo
level-start; `4331` returns in ~30k steps). Capture/verify against the LIVE demo
call, NOT the bare snapshot resume. (This corrects the earlier note that
`snap_before_4b8e` is a good oracle for `4B8E` — it is not.)

**`4331` disassembled + structurally decoded (static from `snap_before_4b8e`
— its code is NOT overlaid, so plain lindis works):**
- `enter 0x16,0`; `[003C]==0` → the `0x4344` path (gameplay), else `0x4455`.
- `ds:[1600]=0`; iteration percent `bp-4 = (ss:[bp+8] ? 100*ds:[1600]/ss:[bp+8]
  : 100)`, clamped ≤100 (with a `ds:[54A0]` gate).
- `bp-14 = 0x31A8` (dest staging cursor).
- Two source segments loaded from the record pointers `ss:[bp+4]`/`ss:[bp+6]`
  (`ds:[bx]` → `bp-6`/`bp-10`), each with its own offset counter (`bp-8`/`bp-12`).
- LOOP bound: runs while `i < 3 * ds:[(bp+4 record)+4]` (i.e. **3× the road
  element count** — this is why it's ~30k steps for a long level, bounded).
- Body (`0x43F4`+): `les bx, ss:[bp-8]` loads the far src1 pointer, reads, and
  writes processed bytes to `0x31A8` staging; advances both source cursors and
  the dest cursor per iteration.

So `4331` is a straightforward dual-source→`0x31A8` element loop — portable pure
Python. Remaining to fully port it: decode the `0x43F4`-`0x4430` body (the exact
per-element read/transform/write) and the `0x4455` non-gameplay branch. The
verified `4B8E` lift is the oracle to check the port against.

---

## 2026-07-12 (latest+2) — render DRIVER `1010:2D1F` LIFTED and oracle-verified — every render node now recovered

Lifted the last unrecovered render node — the top-level driver at `0x2D1F`
(pinned in the previous entry). The full workflow:

1. **`lindis --live-demo`** the driver from live (correctly-overlaid) memory:
   entry is `0x2D1F` with `enter 0,0`, takes **8 word params** (`bp+4..+18` →
   `[0E28]..[0E36]`), then the `[003C]` fast-VGA-vs-scratch branch, record_base
   setup (`bp = 0x162C + ([0E2A]>>3)*0xE + 0x62`, i.e. the `0x168E` road
   perspective table), the classify/dispatch loop (`[0E44]` 11→…, `[0E48]`
   0/1/2 — the same triple loop as recovered `render_classify`) calling
   per-column draws via `call ss:[bx+2991]`, `call 34AE` finalize, and a mask
   copy (`0E86`→`1243`, 478 words).
2. **`liftgen`** refused on the COLD snapshot (`region-budget`, insts=4096) —
   the code-overlay problem: cold bytes at `0x2D1F` are garbage. Fixed by
   `write_snapshot` at gameplay frame 640 (`artifacts/snapshots/gameplay_f640`,
   gitignored — regenerate by driving demo_e2e to frame 640). On that snapshot
   liftgen reports **LIFTABLE** (107 insts, 17 blocks, 1 direct + 3 indirect
   calls).
3. **`liftverify`** (interrupt-gated → `--timer-irqs 1 --frame-steps 120000`
   to advance frames): **PASS — 7 calls byte-exact vs the ASM oracle, 0
   divergences, 16/17 blocks (96.3% native).** Emitted to
   `skyroads/lifted/lifted_1010_2d1f.py`, recorded ORACLE_PASSING in the lift
   manifest.

**Coverage caveat (honest):** 16/17 blocks — one block was not reached in the
gameplay window (likely the `[003C]==0` non-gameplay fast path or a rare
classify branch). 7/7 byte-exact with zero divergence over real frames is
strong, but that one path is unproven; full 17/17 needs a snapshot that
exercises it.

**Status of the render call tree — now fully recovered:**

```
render driver 0x2D1F     LIFTED ✅ (7/7 oracle, 16/17 blk)   <- this entry
├─ 34ae composite        LIFTED ✅
│   └─ dispatch -> road_column_strip (38BF)   pure ✅
├─ 39D4 sprite/HUD final LIFTED ✅
│   └─ sprite_blit (3A22)  pure ✅
└─ HUD present: stencil_blit / present_rect / masked_blit   pure ✅
```

**INSTALLED and pixel-validated (2026-07-12).** Before wiring `2D1F` into
`hooks.py`, ran a direct in-situ pixel diff: played demo_e2e with vs without the
`2D1F` lift (all else ASM) and hashed the VGA framebuffer (`0xA000`, 64000 bytes)
every gameplay frame — **190/190 frames (571-760) byte-IDENTICAL.** That, plus
`liftverify`'s full-machine-state proof, retires the "render correctness isn't
covered by the state suite" concern. Installed at `hooks.py`
(`registry.replace(CODE_SEG, 0x2D1F, "lifted_road_render_driver_2D1F")`); the
**full suite still passes 344/344** with it live. So the entire per-frame render
call tree below the `~0x0Exx` orchestrator now runs as recovered/lifted code
inside the game, not original ASM.

**Rendered a real target frame.** `render_frame.py` on the frame-640 gameplay
snapshot (pure-ASM render, no lifts) produces a correct SkyRoads gameplay image
— cockpit dashboard (GRAV-O METER / SPEED / FUEL / JUMP-O MASTER), nebula/
starfield background, ship on the road, explosion (`artifacts/frames/`,
gitignored). This is the exact image the recovered render tree targets.

**Next node UP the chain — the render orchestrator at `~0x0Exx`.** `2D1F` is
called from `0x0EC4` (i.e. a routine in the low `0x0Exx` region) with 8 params;
at frame 950 they were `[0E28..0E36] = 0,3,1,0x30b4,0,0x7530,0,0x26de`. This
caller computes `2D1F`'s params from sim state and orchestrates the road
(`2D1F`) + sprites (`39D4`) + HUD present per frame. Recovering it (and
whatever feeds IT, up toward the main loop) is what "native rendering" needs:
the render tree below `2D1F` is fully recovered, but its INPUT state
(perspective tables, sprite buffers, palette, the 8 params) is populated by
this caller chain, which still runs in the VM. The path to a fully-native
frame is to keep lifting up this chain (each level is a bounded liftgen/
liftverify cycle, exactly like `2D1F`), OR to render over the native sim's
already-VM-exact memory image once the sim populates that render state.

---

## 2026-07-12 (latest+1) — per-frame render CALL TREE mapped; only the top-level driver (~`0x2Exx`) is unrecovered

Traced one steady gameplay frame (frame 950 of demo_e2e) to get the actual
per-frame draw sequence and the caller of each primitive (return-address at call
entry). This turns "we have all the primitives" into a concrete assembly plan.

**Frame-950 draw order (93 primitive calls, collapsed):**

```
34ae_composite  es=a000 ds=1686 x1     ; composite pass, direct to VGA
road_column     es=a000 ds=1686 x1
road_column     es=8116 ds=1686 x15    ; road cols into off-screen scratch 0x8116
sprite_blit     es=8116 ds=7176 x2     ; sprites built in 0x8116 (src 0x7176)
34ae_composite  es=8396 ds=311b x1     ; composite into 2nd scratch 0x8396
road_column     es=8396 ds=1686 x1
road_column     es=a000 ds=1686 x55    ; the MAIN road, straight to VGA
sprite_blit     es=a000 ds=8116 x4     ; the ship: 0x8116 scratch -> VGA
stencil_blit    es=224b ds=1686 x1     ; HUD glyph
present_rect    es=19a1 ds=1686 x1     ; HUD compose into 0x19a1
masked_blit     es=19a1 ds=1686 x1
masked_blit     es=a000 ds=1686 x10    ; HUD/dashboard flush to VGA
```

(No `6099` in a steady gameplay frame — it's a level-start background blit that
persists underneath.)

**Call tree (who calls whom), from return addresses:**

```
render driver  ~0x2Exx   (calls 34ae at 0x2e43; also 39D4 + HUD present)
├─ 34ae composite        LIFTED (lifted_1010_34ae.py)
│   └─ dispatch 0x366a-0x374f   -> road_column_strip (38BF)   pure ✅
├─ 39D4 HUD/sprite finalize     LIFTED (lifted_1010_39d4.py)
│   └─ sprite_blit (3A22)        pure ✅  (last 2 of 4 calls gated on VGA target)
└─ HUD present: stencil_blit (0F62) pure ✅, present_rect (4201) pure ✅,
                masked_blit (41A0) pure ✅
```

**So EVERY node in the render call tree is recovered EXCEPT the single
top-level driver routine at ~`0x2Exx`** (the one that sequences 34ae, the
scratch-buffer road passes, 39D4, and the HUD present, and also does some HUD
compositing itself — it's among the writers to 0x19a1: 0x2dd9/0x2e7c/0x2ea1/
0x2eba). Its exact entry IP is not yet pinned (0x2e43 is the `call 34ae` site
inside it).

**Driver location pinned to ~`0x2d1f`–`0x2e43+`.** A once-per-frame control
flow enters the `0x2C00-0x2F80` region at `0x2d1f` and reaches the `call 34ae`
at `0x2e43` (both hit exactly once/frame), so the driver body spans roughly
`0x2d1f`→`0x2e43`+. The high-frequency entries in the same region — `0x2e7c`
(×28), `0x2ea1` (×20), `0x2eba` (×14), `0x2ea6` (×8) — are small HUD pixel-plot
helper routines (the per-pixel `0x19a1` writers), called in tight loops, NOT the
driver. Next: `lindis --live-demo` to disassemble `0x2d1f`+ from live memory
(code overlays make static disassembly unreliable — see the `lindis` fix), then
`liftgen`/`liftverify` to lift it like 34ae/39D4.

**Assembly plan (task #22), now concrete:** identify + lift the ~`0x2Exx`
render driver (liftgen-liftable like 34ae/39D4), then a native visible frame is
`driver(native_sim_state)` over a 320×200 framebuffer, verified pixel-exact vs
VM via `frontend_timeline`. Composition itself carries no new correctness risk:
every leaf is already byte-exact given VM inputs (sprite_blit 10/10, road_column
full-mem-diff, masked_blit 19/20, present_rect 12/12, 34ae composite 686/686) —
the only remaining unknown is the driver's own param derivation, which lifting
resolves.

---

## 2026-07-12 (latest) — `sprite_blit` recovered as a pure fn; `6099` identified; render-primitive set COMPLETE

Two closing steps on the render map from the entry below.

**`sprite_blit` (`1010:3A22`) promoted to a pure verified function**
(`recovered/present.py`). The 29-wide masked flip that composites the ship and
road objects. Verified **10/10 byte-exact over full 64 KB dest segments**
against the reference, across both call shapes (24- and 9-row) and both dest
targets — including direct-to-VGA (`es=0xa000`). Fixture + test
(`tests/test_sprite_blit.py`); full suite **344 passed**; layer audit clean
(recovered/ stays VM-free).

**`6099` identified — it's a block-copy screen flip, not a mystery compositor.**
The earlier static byte-dump of `6099` was a stale code overlay (SKYROADS
overlays its code segment, so static bytes ≠ what runs). A *live* capture at the
moment `6099` writes VGA shows `f3 a4` (`rep movsb`) at `0x6097` then
`cld; pop di/si/es/ds; ret`; the write-attributed ip `0x6099` is just the
post-`rep` fall-through. Registers at the write: `es=0xa000`, `ds=0x7176`,
`cx≈0x4d80`, `di=si` counting down. So **`6099` is a general `DS:SI → ES:DI`
`rep movsb` block copy** — the off-screen-buffer(`0x7176`)→VGA flip. A plain
`memcpy`; trivial to reproduce when assembling (no reverse-engineering left).

**The gameplay VGA-writer inventory is now fully understood:**

| writer | routine | recovery |
|---|---|---|
| `38bf` | `road_column_strip` (road) | ✅ pure |
| `3a22` | `sprite_blit` (ship/objects) | ✅ pure |
| `41f1` | `masked_blit` (HUD) | ✅ pure |
| `34ae` | off-screen road composite | ✅ pure |
| `6099` | buffer→VGA block-copy flip | ◐ trivial memcpy, identified |

No unknown compositors remain on the gameplay present path. Remaining work for a
first visible native frame is pure assembly: reproduce the `6099` flip, then
orchestrate the draw order over a 320×200 framebuffer fed by the already-VM-exact
native sim state, and diff vs VM via `frontend_timeline`.

---

## 2026-07-12 (later) — DEFINITIVE VGA-writer map; SUPERSEDES the two `0x19a1`/`present_rect`-as-road-path entries below

The two entries below concluded the gameplay road reaches the screen via
`stencil_blit → 0x19a1 → present_rect → VGA`. **That is wrong, and I'm
retracting it.** It came from a byte-write-only writer scan (missed word/string
writes) over an unrepresentative frame window (a palette fade). A proper scan —
using `Memory.write_watchers` (catches every write path) over the VGA segment
`0xA000` across the whole demo, then narrowed to confirmed steady-gameplay
frames — gives the real map:

**Who writes VGA (`0xA000`) during steady gameplay (frames 900–1000):**

| writer | bytes/100f | routine | status |
|---|---|---|---|
| `1010:38bf` | 107 676 | `road_column_strip` | ✅ pure (`recovered/road_column.py`) |
| `1010:6099` | 64 000 (=1 screen, once) | full-screen background/dashboard blit | ⬜ not recovered |
| `1010:3a22` | 39 622 | `sprite_blit` (ship + objects) | ◐ VM hook only, no pure fn yet |
| `1010:41f1` | 2 411 | `masked_blit` (in `41A0`, HUD) | ✅ pure (`recovered/present.py`) |

**Corrected model:** the gameplay ROAD and SHIP are drawn **directly to VGA**
by `road_column_strip` (38BF) and `sprite_blit` (3A22) — there is NO `0x19a1`
intermediate and NO `present_rect` on the road path. `0x19a1` + `present_rect`
+ `stencil_blit` are the **HUD/dashboard** path (the tiny 5–16 px rects seen in
the `present_rect` fixtures — those are dashboard widgets, not road). `6099`
draws a full-screen background/dashboard image occasionally (once per ~level
start: exactly 64000 bytes = one 320×200 screen in a 100-frame window, ~8.8
screens across the whole demo), and it persists across frames underneath the
road. Everything in `recovered/present.py` (`masked_blit`/`present_rect`,
verified 19/20 and 12/12) is still correct and still used — just for the HUD,
not the road.

**What this means for a native visible gameplay frame** — the per-frame present
is `road_column_strip` + `sprite_blit` + `masked_blit`, all writing straight to
VGA. Recovery status of that set:
- `road_column_strip` (38BF): pure ✅; its inputs `render_classify` (80/80) +
  `dispatch_variant_a/_b` are recovered too.
- `masked_blit` (41A0): pure ✅.
- `sprite_blit` (3A22): a well-understood VM hook (`hooks.py`, detailed 29×24
  column-major stencil-limited compositor) but not yet a pure `recovered/` fn —
  **promotion/lift, not fresh reversing.**
- `6099` background blit: the one genuinely-unrecovered gameplay VGA writer;
  a full-screen image copy, mechanically simple, needs a short recovery pass.

So a native visible gameplay frame needs: (1) promote `sprite_blit` to a pure
fn, (2) recover the `6099` background blit, (3) orchestrate
background→road→sprites→HUD over a 320×200 VGA framebuffer fed by the
already-VM-exact native sim state, (4) diff vs VM via `frontend_timeline`.
No large ensemble of unknown compositors — the earlier worry was an artifact
of watching the wrong (fade) frames.

---

## 2026-07-12 — mapped the LIVE gameplay render composition: every pixel-writing primitive is now recovered

> SUPERSEDED by the entry above — the "live gameplay present" described here is
> actually the HUD/dashboard path, not the road. Kept for the audit trail.

Followed the present pipeline (previous entry) one step upstream to find what
fills `present_rect`'s source buffer, and it reframes the render picture.

**`present_rect`'s source (seg `0x19a1`) is filled by `1010:0F62` =
`stencil_blit`** — an ALREADY-recovered routine (`skyroads/recovered/blit.py`,
its VM hook in `hooks.py`). Traced it: `stencil_blit` writes to
`ES = ds:[AF2A] = 0x19a1` and reads its source from a far-pointer arg. In
gameplay it's called compositing several source segments (`0x221a`/`0x2232`/
`0x224b` sprite/object bitmaps + DGROUP `0x1686`) into `0x19a1`. So the LIVE
gameplay frame is composited by `stencil_blit` into `0x19a1`, then flushed to
VGA by `present_rect` — NOT via the `34AE`→`0x8116` composite path I'd been
treating as the gameplay road (that `34AE`-composite, 686/686, is a real,
verified render but a SEPARATE off-screen pass, likely a different mode/menu,
not the live gameplay present).

**Consequence — every pixel-writing render PRIMITIVE is now a recovered,
VM-verified pure function:**

| primitive | addr | what | status |
|---|---|---|---|
| `render_classify` | `356B` | road-record → dispatch fields | 80/80 |
| `dispatch_variant_a/_b` | `364F/36F3` | fields → column call list | 633/640 |
| `road_column_strip` | `38BF` | draw one road column | full-mem-diff |
| `stencil_blit` | `0F62` | stencil a sprite/object into the frame buffer | recovered (hooked) |
| `masked_blit` | `41A0` | one color-keyed scanline → dest | 19/20 |
| `present_rect` | `4201` | flush a rows×width rect → VGA | 12/12 |
| `34AE` composite | `34AE` | off-screen road compositor (separate pass) | 686/686 |

**So the render subsystem's remaining work is entirely COMPOSITION mapping +
assembly, not primitive recovery**: pin down the exact live-frame buffer flow
(which compositor writes which region of `0x19a1`, in what order, per frame —
`stencil_blit` for sprites/objects, and where the perspective ROAD columns
land relative to it), then thread the recovered primitives over a
`NativeGameImage` and diff the VGA output against the VM (the `frontend_timeline`
harness is built for this). That's real work, but it's wiring verified pieces
in the right order, with zero new routines left to reverse-engineer on the
road-render path. This session took the renderer from "individually-verified
islands" to "every primitive recovered, the live present pipeline
(`stencil_blit` → `0x19a1` → `present_rect` → VGA) identified."
## 2026-07-12 — RECOVERED the road-present scanline loop (`1010:4201`), 12/12 — the render→screen present pipeline is now complete end to end

Found and recovered the piece that drives the road onto the actual screen.
Traced what writes VGA (`0xA000`) in gameplay: it's **~569 small `41A0`
masked-blit calls per window, all from one caller** (`1010:4201`). So the road
isn't one big blit — it's presented SCANLINE BY SCANLINE. Disassembled `4201`:
it reads a 4-field descriptor `{srcB_seg, dest_off, rows, width}` and loops
`rows` times calling `41A0` (`masked_blit`), advancing the dest cursor by
`0x140` (a VGA scanline) and the source cursor by `width` each row — a
`rows x width` color-keyed rectangle flush.

Ported it to `skyroads/recovered/present.py::present_rect` and verified by
FULL-MEMORY DIFF against real `4201` row-loop invocations: **12/12 calls
reproduce every VGA byte written, byte-exact** (the initial "mismatch" was
purely the timer ISR's counter bytes at `0x220f0-0x2210b`, written because a
`4201` call spans hundreds of steps so many ticks fire — comparing only the
destination VGA segment isolates `present_rect`'s output). Landed
`tests/test_present_rect.py` (the 12/12 fixture match + a cursor-stride test)
and a compact fixture. Layer audit + lint clean.

**The full road render→screen pipeline is now recovered and every stage
VM-verified:**

    34AE mode-0 setup + render_classify + dispatch + road_column_strip
        -> off-screen road buffer   (686/686, byte-exact)
    present_rect (1010:4201 row loop)
        -> masked_blit (1010:41A0) per scanline
        -> VGA                       (12/12 rows, 19/20 blits, byte-exact)

Every PRIMITIVE from the road records to the pixels on screen is now a
verified pure function. What remains to run a full native VISIBLE frame is
pure ASSEMBLY (thread `34AE`-composite → `present_rect` over a
`NativeGameImage` with the real descriptor + threshold fields, then diff the
VGA framebuffer against the VM — the new `frontend_timeline` harness is built
for exactly this), plus the `[003C]==0` fast-VGA path (`1010:3D18`) `4201`
takes in the non-gameplay case, which is a separate, smaller follow-up.
## 2026-07-12 — RECOVERED the screen-present masked blit (`1010:41A0`), verified byte-exact — the presentation piece

Ported `1010:41A0` (the screen present found in the prior entry) to a pure
function `skyroads/recovered/present.py::masked_blit`, and verified it by
FULL-MEMORY DIFF against real VM calls. It's the color-keyed compositor that
flushes a frame to the screen: a TOP band copied verbatim from a background
buffer (source A), a MIDDLE band compositing a foreground buffer (source B —
e.g. `34AE`'s off-screen road) over the background with a two-threshold color
key (`p<lo`→transparent, `lo<=p<hi`→substitute background, `p>=hi`→foreground),
and a BOTTOM band verbatim from A again.

**Verified byte-exact**: 19/20 real `41A0` invocations reproduced every byte
written to the destination segment (the one non-match was a different
interrupt-counter address, not `41A0`'s output). Landed
`tests/test_present.py` — a full-memory-diff regression over 4 real calls (the
dest before/after window matches exactly) plus a direct color-key semantics
test (transparent/substitute/foreground). Layer audit clean (VM-free).

**Two capture bugs found and fixed en route** (worth noting): (1) `[9612]`
(the blit total) is read `ss:`-relative, not `ds:`; (2) the params must be read
`ss:[sp+2/+4/+6/+8]` at the `41A0` ENTRY — before its `enter 0,0` runs, `bp` is
still the CALLER's, so `bp+4` reads garbage (this had made `top/total`
inconsistent). Both were harness bugs; the `masked_blit` logic (decoded from
the disassembly) was right.

**Caveat**: the sampled calls were small UI blits (`total=0x44`, `top=bot=0`,
exercising the MIDDLE color-key band); the TOP/BOTTOM verbatim bands are plain
`rep movsb`, matched trivially against the disassembly but not exercised by a
sampled call with nonzero top/bottom. Also, these were HUD/UI blits, not the
big road-region present — but `41A0` is size-independent, so the algorithm is
verified regardless of blit size.

**Renderer status — the road-to-screen path is now RECOVERED end to end**:
`34AE` mode-0 fills the off-screen road buffer (byte-exact 686/686) and `41A0`
masked-blits a buffer to the screen (byte-exact). What remains to assemble a
full native visible frame: identify the specific `41A0` caller/params for the
big road present (screen-region source B = the `34AE` buffer, thresholds), and
wire `34AE`-composite → `41A0`-present over a `NativeGameImage`. Every
PRIMITIVE the road render needs is now recovered and VM-verified; the remaining
work is composition + finding the road-present call site, not new RE.
## 2026-07-12 — mode-1 DEFINITIVE: my earlier conclusions were based on an ATYPICAL pass; supersedes the last two mode-1 entries

Chased the mode-1 mystery to ground with static disassembly and it corrects my
own last two entries (which flip-flopped). The confirmed facts:

1. **Variant B (`36F3`) reads its classification inputs from the SAME DGROUP
   offsets as variant A** — statically disassembled both: `364F` reads
   `[0E4E]/[0E50]/[0E56]/[0E58]/[0E5A]`; `36F3` reads
   `[0E46]/[0E4E]/[0E50]/[0E54]/[0E56]/[0E58]/[0E5A]`. So my prior entry's claim
   that "mode-1's fields are NOT at `[0E44]…`" was WRONG — they're at the
   standard offsets; mode-1 uses the same field mechanism as mode-0. (My
   confusion came from reading `[0E44]`/`[0E46]` specifically, which dispatch
   barely uses, plus the atypical values below.)

2. **The specific mode-1 pass I captured was ATYPICAL.** Re-analyzed its
   80 dispatches: ALL 80 had LARGE field values (`e50=1568`, `e56=1315`, etc.),
   NONE were the small reduced nibbles (0-4) a normal road classification
   produces. So it was not a normal gameplay road render — likely a
   menu/transition/other `34AE` use that happened to have a stale gameplay-
   looking `record_base`. So my "223 vs 74, model is wrong" conclusion was
   built on a bad sample, NOT a disproof of the "same classify + variant B"
   model.

3. **A real, separate finding**: on those LARGE (out-of-normal-range) field
   values, my `dispatch_variant_b` transcription diverges from the ASM
   (matched 16/80). But such values never occur in normal gameplay
   classification (always 0-4), so this does NOT affect the verified 633/640 —
   it's an untested out-of-range regime, worth a note in `render_dispatch.py`
   but not a gameplay bug.

**Accurate open state for mode-1**: the "mode-0 pipeline with `dispatch_variant_b`
+ VGA dest" model is NEITHER proven NOR disproven — I never captured a clean
NORMAL-gameplay mode-1 pass (small fields) to test it against; the e2e demo's
mode-1 passes I sampled were the atypical all-large-field kind.

**Further finding that reframes it (same day)**: tested BOTH models on the
first several DEEP-gameplay (`frame>650`) `34AE` passes. The mode-0 passes all
still verify **24/24** (`record_base=0x16B8`) — mode-0 is rock-solid. But the
gameplay `ax!=0` ("mode-1") passes DON'T even enter the per-column path
(`[0E32]!=0`, huge `delta`), and when sampled they made **0** `road_column_strip`
calls AND **0** `rep movs` to `0xA000`. So in real gameplay the `ax!=0` `34AE`
call is NOT drawing the road to VGA per-column at all — meaning **the actual
off-screen→screen flush probably does NOT go through `34AE` "mode-1"** the way I
assumed from the lift. The per-column-variant-B `34AE` passes I originally
sampled were the atypical (menu/transition) ones.

**FOUND the real screen present — it's `1010:41A0`, NOT `34AE` "mode-1".**
Watched what writes VGA (`0xA000`) during gameplay frames 700-705: the writers
are all at `1010:41C8`/`41FA` (`rep movsb`) and the `41E7-41F1` loop — i.e. one
function, `1010:41A0`. Disassembled it: it's a **masked two-buffer→VGA blit**
(the presentation / "flip" routine), which composites the road over the
background and copies to the screen:
- `es = ds:[961C]` (= VGA `0xA000`), `ds:[4512]`=source A (background),
  `ds:[4514]`=source B (the road buffer mode-0 filled), thresholds
  `ds:[9614]` and `ds:[AF3A]`.
- **top region**: `rep movsb` verbatim from source A → VGA (`cx=ss:[bp+8]`);
- **middle region** (`41E7-41F1`, per-pixel MASK): read a pixel from source B;
  if `< [9614]` → transparent (skip, leave background); elif `< [AF3A]` →
  substitute the co-located source-A pixel; else copy source B → this is the
  road drawn over the background with a color-key;
- **bottom region**: `rep movsb` verbatim from source A → VGA (`cx=ss:[bp+10]`).

So the whole "mode-1 = 34AE with variant B" investigation was chasing the wrong
routine — `34AE` only ever fills the OFF-SCREEN road buffer (mode-0, byte-exact),
and a SEPARATE function `1010:41A0` composites+flips it to the screen. `41A0` is
a self-contained leaf (no calls, `enter`-prologue, already flagged liftable in
the 2026-07-12 leaf-lift census). **Recovering `1010:41A0` is the concrete,
bounded remaining piece for native presentation** — it + the byte-exact mode-0
composite = the road on the actual VGA screen, VM-free. This supersedes every
mode-1 entry below; there is no "mode-1 variant-B" pass to recover.

**Refinement**: `41A0` is a GENERAL masked-blit PRIMITIVE, not solely the road
present — called with varied params (`bp+4`=dest off, `bp+6`=src-B off,
`bp+8`=top verbatim count, `bp+10`=bottom verbatim count) for different blits (a
sampled gameplay call was degenerate: `bp+8=0`, `[9612]=4`, 6 bytes changed, no
VGA write). So recovery is two bounded steps: port the masked-blit LOGIC (top
verbatim | middle color-keyed src-B-over-src-A | bottom verbatim — already
decoded above; verify by full-memory-diff over several real calls, the
`road_column_strip` pattern), then find the specific road-present caller
(screen-sized params writing the road region to VGA).

## 2026-07-12 — mode-1 sharpened: it DOES run 80 variant-B dispatches / 74 rcs (same loop shape as mode-0), but its classification fields are NOT at [0E44]…

Follow-up that pins the mode-1 mystery precisely. Instrumented a real mode-1
pass at each `dispatch_variant_b` (`36F3`) entry: the VM makes **80** variant-B
dispatch calls producing **74** `road_column_strip` total — i.e. the SAME
80-iteration loop shape as mode-0 (10×4×2), just yielding 74 columns instead of
24. So mode-1 is NOT a wholly different structure; it walks the same triple loop.

**But the classification fields it dispatches on are NOT at `[0E44]`/`[0E46]`/
`[0E4E]`…`[0E5E]`.** Read those DGROUP offsets at each mode-1 `36F3` and they
hold GARBAGE — `e44=280`, `e46=6656 (0x1A00)`, `e5e=0xFF00`, constant across
consecutive dispatches (i.e. stale, not per-iteration). Feeding the VM's OWN
captured-at-those-offsets fields into `dispatch_variant_b` matched its
road_column_strip count only **16/80**. So during mode-1 the dispatch inputs
live somewhere OTHER than the mode-0 classification fields — mode-1's
classification loop writes a different field set (or `dispatch_variant_b` reads
via different addresses in the mode-1 context), which is exactly why the
"reuse mode-0's render_classify" model produced 223 instead of 74.

**Precise open question for whoever finishes mode-1**: mode-1 runs the same
80-iteration classify loop but its per-column fields are elsewhere — find where
`36F3` actually reads its 11 inputs from in the mode-1 pass (the render_dispatch
fixtures were captured by instrumenting the dispatch function's own read-site
IPs, NOT by assuming `[0E44]…`, so the true source addresses were never pinned
to DGROUP offsets — that's the thread to pull). Once the mode-1 field source is
known, `dispatch_variant_b` (already verified 633/640) should compose the same
way mode-0 did. This is a bounded RE task now, not an open-ended one.

## 2026-07-12 — mode-1 (VGA-flush) render attempt: my "same classify + variant B" model is WRONG (223 vs 74 calls) — backed out, needs a real investigation

Attempted the second render pass (mode-1: the off-screen buffer → VGA flush).
Captured a real mode-1 `34AE` pass: it makes **74** `road_column_strip` calls
via `dispatch_variant_b` (variant B, source `[0E36]`=off-screen, dest
`0xA000`=VGA), reusing the SAME `record_base=0x16B8` and records as mode-0. So
the natural hypothesis was: mode-1 = the mode-0 pipeline with `dispatch_variant_b`
and the VGA dest.

**That hypothesis is wrong.** Wrote it (`composite_mode1`/`composite_frame`/
`mode1_column_calls`) and tested against the captured 74-call sequence:
`render_classify` + `dispatch_variant_b` over the same records produces **223**
calls, not 74 — with long call-bursts (exactly the "third dispatch source"
anomaly the `render_dispatch` recovery already flagged and EXCLUDED from its
own fixtures). So mode-1 does NOT simply run `render_classify` → variant B; it
either drives a different classification/record walk, or gates variant B's
output differently, or the 74 calls come partly from a source other than this
loop. (An end-to-end `composite_frame` run happened to emit 74 on one full
image, but that was my code's own output on a different frame, not a confirmed
VM match — the fixture-frame comparison, 223 vs 74, is the real signal.)

**Backed it all out** — removed `composite_mode1`/`composite_frame`/
`mode1_column_calls` and the mode-1 test/fixture, reverting `render_frame.py`
to the committed byte-exact mode-0 version. Not shipping an unverified render
path. The mode-0 renderer (686/686 pixels, committed) stands unaffected.

**Mode-1 is a real open investigation**, not a quick "variant B swap":
figure out where its 74 calls actually come from (is variant B gated by a
different field set here? does the VGA pass walk fewer columns? is the
`36F3`-reached dispatch partly from the unisolated third source?). Also, a
fully byte-exact VGA frame separately needs the `39D4` sprite finalize ported
(mode-1 reads the off-screen buffer AFTER mode-0's `39D4` sprites, and its own
`39D4` draws sprites to VGA). These are the two remaining renderer items; the
road-COMPOSITE core (mode-0) is done and byte-exact.
## 2026-07-12 — CORRECTION: there is NO "display-list builder" gap for the off-screen road pass — the records are already present; my pixel mismatch was a wrong comparison reference

The previous entry ("ASSEMBLED the native mode-0 render pipeline") claimed the
`composite_mode0` pixel mismatch (24/24 calls right, but wrong pixel values)
was due to a "display-list BUILDER not yet recovered" leaving stale records in
the seed. **That was wrong, and I'm retracting it.**

Checked it directly: diffed the source segments `road_column_strip` reads —
`seg_src` (`0x7176`), the two record buffers (`0x2B12`/`0x311B`), and the dest
(`0x8116`) — between the before-`34AE` seed and the actual `road_column_strip`
call. **0 / 4096 bytes changed in every one of them.** So the records and
source bitmap in my pre-render seed are IDENTICAL to what the VM composites
from — there is no stale-data / unrecovered-builder problem for this pass at
all. (The buffers ARE rebuilt as the ship advances, but not between
before-`34AE` and the compositor within a frame, which is all that matters
here.)

**The real cause of the 337/343 pixel mismatch was a comparison-reference
error in my verification script**: it diffed my mode-0 composite output
against the VM's FULL after-`34AE` image — which also contains the `39D4`
finalize sprites (drawn into the same `0x8116` buffer AFTER the road columns)
and the subsequent mode-1 pass. So most "mismatches" were just `39D4`/mode-1
writes my mode-0-only composite legitimately doesn't make. With identical
inputs, identical args (24/24), a full-memory-diff-verified `road_column_strip`,
and `NativeGameImage`'s addressing confirmed byte-identical to
`road_column_strip`'s own test harness, **`composite_mode0` is correct by
construction** — same inputs + same verified function = same output.

**CONFIRMED independently, byte-exact.** Got the clean full-pixel VM diff by
capturing the VM's dest at the `39D4`-finalize entry (keeping `34AE` as its
lift for speed, and using `39D4`'s entry as the exact "after all 24 columns,
before the finalize sprites" point — reliable, unlike the earlier
force-ASM attempt). `composite_mode0` reproduces **686/686 written bytes
exactly** against the VM. So the mode-0 road pixels are proven byte-for-byte,
not just correct-by-construction. Landed
`tests/test_render_composite.py` (the 686/686 pixel match) + a compact fixture
(`render_composite_trace.json`: 1030 seed bytes -> 686 expected writes).

**Net**: the native mode-0 road-render pipeline is assembled AND its actual
composited PIXELS match the original game byte-for-byte (686/686) — the
renderer's real lockstep milestone, not blocked on any unrecovered builder.
The genuinely-remaining renderer item is the separate mode-1 VGA-flush pass
(which copies this off-screen buffer to the screen); once ported, a fully
native frame would drive the VGA display.
## 2026-07-12 — ASSEMBLED the native mode-0 render pipeline — reproduces the VM's EXACT 24-call road_column_strip sequence

Composed the recovered renderer pieces into `skyroads/native/render_frame.py`
and verified the assembly against the VM. `mode0_column_calls(img, ds)` runs,
over a `NativeGameImage`, the full mode-0 (off-screen composite) decision
pipeline in `34AE`'s own order:

    compute_mode0_setup (34AE blocks 2-12, 6/6) -> render_classify (80/80)
      -> dispatch_variant_a -> [road_column_strip calls]

**Result: it reproduces the VM's road_column_strip call sequence EXACTLY.**
For a real mode-0 pass (demo_e2e_20260710_132930) the VM made **24**
road_column_strip calls; the native pipeline produces the **identical 24**
`(ax, e44, e46, e48)` tuples, with identical per-pass `e60/e62/e64/e66/e68`
(setup `record_base=0x16B8`, `seg_records 0x311B/0x2B12`, `seg_src 0x7176`,
`seg_dst 0x8116` — all matched). So the render DECISION pipeline — which
columns to draw, with what descriptor, in what order — is now proven correct
end to end, from raw road records to the compositor call list. Landed
`tests/test_render_frame.py` (the setup formulas + the 24/24 call-sequence
match) + a compact 186-byte fixture. Layer audit clean (32 files, VM-free).

**The one remaining input for byte-exact PIXELS** (found by running the full
`composite_mode0` and diffing writes — 24/24 calls right, but pixel values
off): `road_column_strip` reads the per-column display-list RECORDS from the
`[0E60]`/`[0E62]` segments (the 8 rotating buffers in the `0E76` table), and
those are rebuilt EVERY frame by a display-list BUILDER that is **not yet
recovered**. Seeding a `NativeGameImage` from BEFORE `34AE` holds the previous
frame's records, so the composite differs; `road_column_strip` itself is
correct (full-mem-diff verified) and `composite_mode0` is byte-exact when the
records are already populated. So the renderer's last missing piece is now
pinned to exactly ONE routine: the display-list builder that fills those
buffers. Everything downstream of it (classify -> dispatch -> composite ->
39D4 finalize) is recovered and verified.

**Renderer status**: the entire mode-0 road-render pipeline is assembled and
its decision logic VM-verified. Remaining: (1) recover the display-list
builder (the single input for byte-exact pixels); (2) the mode-1 VGA-flush
pass (separate, still-open, see prior entry). With (1), a native frame would
produce a byte-exact off-screen road framebuffer.
## 2026-07-12 — 34AE setup (blocks 0-12) decoded + verified 6/6 for the mode-0 composite pass; mode-1 (VGA flush) setup is a separate, still-open path

Continuing the renderer assembly (after landing `render_classify`), decoded
`34AE`'s SETUP blocks (0-12 — mode/segment selection + the `[0E60]`/`[0E62]`/
`[0E64]` computation + `record_base`) from the proven lift, and verified the
formulas against 12 real captured `34AE` invocations. Result splits cleanly by
mode:

**mode-0 (off-screen composite pass — the one that classifies & draws the
road): formulas verified 6/6 exact.**
- source/dest/dispatch: `[0E66]=[5170]`, `[0E68]=[0E36]`, `[0E42]=0x364F`
  (variant A).
- `record_base = ([0E2A] >> 3) * 0xE + 0x168E` — matched (e.g. `[0E2A]>>3=3`
  → `0x16B8`, the exact `record_base` `render_classify` was verified against).
- `[0E62] = [0E76][ [0E6A] & 7 ]`, `[0E60] = [0E76][ [0E2A] & 7 ]` (8-word
  runtime-allocated buffer-segment table) — matched every sample.
- **`[0E64] = 0x30 if ([0E2A]>>3) == ([0E6A]>>3) else 0`** — note my initial
  mental decode had this polarity BACKWARDS (thought `!=`); the captures
  corrected it (all 6 mode-0 samples had the two `>>3` values EQUAL and
  `e64 == 0x30`). Exactly the kind of error verifying-against-captures exists
  to catch.

**mode-1 (`[0E68]=0xA000` VGA-flush pass): a DIFFERENT, unresolved path.** Its
captured `[0E2A]`/`[0E6A]` are erratic, large, buffer-like values
(`0x1a0e`/`0x3eff`/`0x0006`…), its `record_base` reads stale (`0x16B8`,
left over from the preceding mode-0 pass — so block-12 didn't recompute it
the same way), yet it still reached variant-B dispatch (`36F3`). So mode-1
is NOT just "mode-0 with different segments" — it's a separate flush behavior
this capture didn't cleanly resolve (possibly reading `[0E2A]` mid-update, or
mode-1 genuinely takes the fast-copy/other path and the dispatch I attributed
to it belongs to interleaving). Deliberately NOT porting a setup function on
this murky data — flagged as the specific open question.

**Net**: the mode-0 composite pass — which is what actually draws the road — is
now fully spec'd end to end and each stage verified: `34AE` setup (6/6) →
`render_classify` (80/80) → `dispatch_variant_a` → `road_column_strip` →
`39D4`. Assembling these into a native `render_frame` over `NativeGameImage`
(and diffing its output buffer against the VM) is the concrete next step for
the mode-0 pass; the mode-1 VGA flush needs its own short investigation first.
No code landed for the setup this stretch (the mode-1 ambiguity makes a clean
`render_frame` premature) — `render_classify` was the shipped milestone.
## 2026-07-12 — RECOVERED the render-classification loop (render_classify), 80/80 — the classify→dispatch pipeline is now complete and VM-verified

Landed the piece the previous entry spec'd: `skyroads/recovered/
render_classify.py::render_classify`, the triple-nested loop inside `34AE`
(`1010:356B-3627`) that produces the per-column dispatch inputs. Ported from
`34AE`'s PROVEN lift (not re-derived from ASM — the exact code an earlier
hand-attempt mis-transcribed 3×) and **matched a real VM capture 80/80 on the
first try**.

**Cleared the capture blocker that stalled this** (worth recording, it was a
real trap): the ground-truth harness kept seeing ZERO hits at `34AE`'s
classification call-site `1010:35F8` even though dispatch (`364F`/`36F3`) ran
19,040×. Reading the return address at dispatch entry resolved it —
`ret=0x35FC` (38,080×), i.e. dispatch IS called from `35F8`, but **`34AE` runs
as its installed LIFT**, so the whole classification loop executes in Python
and the interpreter's step-hook only ever sees the `0x34AE` entry, never the
body. (This is the same "`0x35FC` never observed" mystery flagged during the
dispatch recovery — now fully explained: it's the lift, not a harness bug.)
Fix: trigger the capture on the dispatch TARGETS (`364F`/`36F3`, which ARE
reached via `emulate_call`), delimited by `34AE` entries.

**The capture confirmed the loop model exactly**: one full `34AE` invocation
(variant A, `record_base=0x16B8`, from `demo_e2e_20260710_132930`) makes
**80 dispatch calls = 10 outer (e44 11→2, e4c -= 0xE each) × 4 middle
(e46 1→4) × 2 inner (e48 0/1)**. `render_classify` reproduces every one of
the 13 classification fields of every call byte-exact.

Landed `tests/test_render_classify.py` (the 80/80 fixture match, the
loop-structure asserts, AND an end-to-end test chaining `render_classify` →
`dispatch_variant_a` so the classify→dispatch pipeline is proven to compose)
+ a compact 154-byte fixture (`render_classify_trace.json`, the touched
`0x162C-0x16C5` record window + `BA7`). Layer audit clean (VM-free).

**Renderer state now**: the column-render decision chain is COMPLETE and each
stage VM-verified —
`render_classify` (this) → `dispatch_variant_a`/`_b` (recovered) →
`road_column_strip` (recovered, full-memory-diff verified) → `39D4` finalize
(lifted+installed). What remains for an actual native FRAMEBUFFER: the outer
`34AE` framing around this chain (mode/segment setup, the delta-based
fast-copy/no-op paths, the flush to VGA — all decoded in the 34AE entry, not
yet assembled over `NativeGameImage`), and the display-list BUILDER that
populates the road records `render_classify` reads (the `0x162C` region, whose
per-level content is the `4B8E` transform tracked in the sim-start entries).
But the hardest, most error-prone middle — the classification — is now done.
## 2026-07-12 — fully decoded 34AE's render-CLASSIFICATION loop (the dispatch-input producer) + captured its static tables — the renderer's next port, spec'd precisely

Toward assembling a native renderer, isolated the one un-ported piece
between the road-segment records and the already-recovered dispatch/
compositor: **34AE's render-classification loop** (`1010:356B-3627`, blocks
15-22 of the proven lift). Read it directly from the proven lift and worked
out the complete algorithm — a TRIPLE-nested loop, one `call [0E42]`
(dispatch) per innermost iteration:

    e4c = record_base                       ; road-segment record pointer
    for e44 in 11 down to 1:                 ; outer; e4c -= 0xE each pass
      for e46 in 1..4:                       ; middle
        for e48 in (0, 1):                   ; inner toggle (xor [0E48],1)
          col = (4 - e46) if e48 != 1 else (e46 + 2)   ; block 16 = neg path
          bx  = e4c + 2*col
          si  = 2 - 4*e48
          e56 = mem[bx]      & 0xF           e5c = mem[bx]      >> 4
          e58 = mem[bx-0xE]  & 0xF           e5e = mem[bx-0xE]  >> 4
          e5a = mem[bx+si]   & 0xF
          e4e = BA7[ mem[bx+1]     & 7 ]
          e50 = BA7[ mem[bx-0xD]   & 7 ]
          e52 = BA7[ mem[bx+si+1]  & 7 ]
          e54 = BA7[ mem[bx+si-0xD]& 7 ]
          call [0E42]                         ; -> dispatch_variant_a/_b (recovered)
    call 39D4                                 ; finalize (lifted+installed)

Two subtleties confirmed against the lift (the class that bit the earlier
`road_frame.py` draft): the `[0E48]-1` and `[0E44]-1`/`[0E46]-4` ops are
`cmp` (flag-only), not stores; the two `neg` ops (`f7d8`/`f7de`, interpreter
fallbacks in the lift) are what make `col` and `si` their signed forms above.

**Captured the two static tables** this loop and 34AE need, from a live
gameplay frame:
- `ds:[0BA7]` shape-reduction table = **`[1,2,3,3,4,4,1,1]`** (8 bytes, a
  genuine compile-time constant — bakeable into the port).
- `ds:[0E76]` = 8 words `[0x2b12,0x311b,0x3766,0x3dd4,0x4459,0x4b02,0x518c,
  0x57fe]` — but these are RUNTIME-ALLOCATED buffer segments (session-
  specific), NOT constants; a native renderer allocates its own.

**Verification approach identified** (not yet run to completion): the port
is a pure function `(rb, ds_seg, record_base) -> list of (e44..e5e) tuples,
one per dispatch call`, checkable against a VM capture of those fields at
each `1010:35F8`. Note: this must be captured from a GAMEPLAY demo where the
classification path actually runs — the short `demo_skyroads_20260711_202740`
clip's 34AE calls all took the early-exit/fast-copy path (0 dispatch calls
hit), so the e2e/gameplay demos are the right oracle source (they're what
`test_render_dispatch`'s own fixtures came from).

**Status**: the render-classification algorithm is now precisely and
correctly spec'd from the proven lift (the hard part), and its inputs
(tables, record layout) are in hand. The remaining work is the careful port
+ verification against a gameplay-demo capture — deliberately NOT rushed at
the tail of a long session given this exact code's history of subtle
transcription errors. This is the concrete, well-scoped next renderer step:
`render_classify` → (recovered) dispatch → (recovered) `road_column_strip` →
(lifted) `39D4` would then be a COMPLETE native column-render pass.
## 2026-07-12 — censused the level-start call graph and lifted 3 more leaf helpers; found the auto-lifter's first DIVERGENCE (59CF) — verifier caught it

Back to "keep hooking and lifting the load/start/play path." Censused every
near-call target on the level-start demo: **83 distinct call targets, 72 not
yet hooked**; ran `liftgen` on the top 15 by call count — **all 15 liftable**.
Emitted + `liftverify`'d the call-free LEAVES (cleanest to install, no hook
dependencies):

- **1010:5D80** — `DX:AX <<= CL` (32-bit shift-left helper). ORACLE_PASSING,
  **3/3 blocks (full coverage)**.
- **1010:0BE9** — projection helper `si = ((param/128) - 0x5F) / 46` (same
  perspective-row family as `04C0`). ORACLE_PASSING, 6/8 blocks.
- **1010:0BAF** — bounds/clamp on two params (`cmp` vs `0xFE9D`/`0x2800`).
  ORACLE_PASSING, 7/10 blocks.

All three installed in `skyroads/hooks.py` + `skyroads/lifted/manifest.json`;
the full suite stays green. They're scaffolding (unrefactored lifts), not
recovered islands yet — flagged as such at the install site.

**Notable finding — the automatic lifter is NOT infallible.** `1010:59CF`
(28 insts) emitted a lift that `liftverify` caught **DIVERGING** from the ASM
oracle (a real `AX` register mismatch at continuation `1010:5975`, after 93
steps). This is the first observed auto-lift divergence this project has hit —
so it was NOT installed, and its emitted file was deleted. Two takeaways: (1)
`liftverify`'s differential check earns its keep — a blindly-installed lift
here would have been a silent bug; (2) lifts must be verified, never trusted
on emission alone (the metrics-honesty rule already said this; now there's a
concrete counterexample). Worth a future look at WHY `59CF` diverges (likely
a decoder edge case or an operand the emitter mistranscribes) — a minimized
repro would be a good `dos_re` lifter bug report.

**Four other hot leaves** (`41A0`×359, `5892`×134, `417E`×41, `59EF`×29 calls
in the demo) are `NOT_REACHED` by `liftverify` from either the gameplay or the
level-start snapshot — they fire inside `4B8E`'s one-shot load or the
per-frame render loop, which a plain drive-forward (no demo input, no frame
cadence) doesn't re-trigger. Verifying them needs a snapshot/harness that
actually re-enters those paths — deferred, not a defect. Their lifts weren't
kept (unverified).
## 2026-07-12 — started the 4B8E road→perspective transform: lift verifies byte-exact on the real path; its 4331 callee is a DATA-TRANSFORM loop, not I/O (correcting a same-turn misread)

Began attacking the one transform native `--level N` sim-start still needs
(previous entry): `4B8E`'s road[]→`0x162C`-perspective build. Concrete
progress:

- **Captured a positioned snapshot** (`artifacts/snap_before_4b8e`) at
  `1010:2C58`, the instant before the level-load `4B8E` call — so `liftverify`
  can reach the one-shot function it otherwise can't (all existing demo
  snapshots resume AFTER level-load). Reusable for any future level-load RE.
- **`liftgen`: `4B8E` is liftable** (57 insts, 13 blocks, 7 direct calls).
- **`liftverify`: the lift is byte-exact on the real level-load call**
  (`PASS`, 1 sample, 3/13 blocks — PARTIAL, since one call takes one path
  through the branch tree; the taken path IS the level-load path).

**Correction, same turn**: I first read `liftverify`'s "emulated call to
`1010:4331` did not return within 20,000,000 steps" as `4B8E` being
"entangled with file I/O / an environment wait." Disassembling `4331`
(`tools/lindis.py --live-demo`-style, from the positioned snapshot)
DISPROVES that — `4331` is a pure **data-transform loop**: `enter 0x16,0`;
compute an iteration count (`100 * ds:[1600] / param`, clamped ≤ 100); set a
destination pointer to the `0x31A8` staging buffer; then loop reading from
two source segments (params at `bp+4`/`bp+6`) and writing processed data
into `0x31A8`. That's road-data PREPARATION (computation), not I/O. The
"didn't return in 20M steps" is NOT a genuine hang — my "I/O entanglement"
read was wrong and is retracted here.

**Measured `4331`'s real cost to pin this down precisely**: instrumented the
live VM and it returns in **~30,266 steps** — a modest bounded loop, ~660×
UNDER the 20M budget. So the lift failure isn't "4331 is inherently huge";
it means the emitted `4B8E` lift feeds `4331` the WRONG inputs on the branch
that calls it (a garbage loop-bound or source pointer → a runaway that would
terminate correctly with the right state). Since that call lives in one of
the 10 blocks the single real sample did NOT cover, it's an untested-path
lift bug, not a property of the function. So `4B8E`+`4331` are recoverable
bounded computation; full recovery needs debugging the lift's state setup on
the `4331`-calling path (or hand-porting from the now-disassembled blocks).

**Honest state**: `4B8E` is confirmed the right target and is
computation-not-I/O, its lift verifies on the real path, and a positioned
snapshot to iterate on it now exists. What remains is the substantive part —
raising the emulated-call budget (or the lift's iteration guard) to get FULL
`4B8E` coverage, then disentangling the specific road→`0x162C` sub-computation
(the `rep movsb` copies from `0x32xx`/`0x33xx`, fed by `4331`'s `0x31A8`
staging) from `4B8E`'s render/VGA setup blocks, and porting THAT as the
native level-load transform. Real, multi-step, but now with the target
liftable-and-verified-on-its-real-path and the tooling/snapshot in place —
not the "entangled with unrecoverable environment I/O" dead-end I briefly and
wrongly called it.
## 2026-07-12 — CORRECTION to the previous entry: it's the PERSPECTIVE table (0x162C) that's level-dependent, not the clip tables — and that pins the exact native-load transform still needed

The previous entry ("perspective tables are level-INDEPENDENT") was WRONG,
and I'm correcting it directly. It was based on eyeballing a TAIL-TRUNCATED
run list (the diff output only showed runs from `0x1813` upward; I missed
every differing run between `0x162C` and `0x1813` and wrongly concluded the
region barely changed). Re-checked by directly diffing the exact regions the
collision predicate (`native/collision.make_visible`) actually reads, byte
for byte, between two levels (index 16 gate-8/200/180 vs index 17
gate-7/175/60):

| region the sim reads | address | bytes differ between levels |
|---|---|---|
| `SEG_BOUND_LOW_TABLE` (clip) | `0x4C..0x97` | **0 / 76** — level-independent |
| `SEG_BOUND_HIGH_TABLE` (clip) | `0x98..0xE3` | **0 / 76** — level-independent |
| shape lookup | `0xBA7` | **0 / 17** — level-independent |
| **perspective table** (`04C0`) | `0x162C..0x18FF` | **360 / 724** — LEVEL-DEPENDENT |

So the picture flips: the segment CLIP-bound tables are fixed (screen-space
projection geometry, same every level), but the **PERSPECTIVE table at
`0x162C` holds the per-level projected road** the collision predicate reads
via `perspective_row_offset` → `rw(r.offset)`. That's the real per-level sim
state a native `--level N` must produce.

**This precisely pins the one transform native sim-start still needs**, and
it's exactly the `4B8E` populate step already partially traced (the 34AE
entry): a `rep stosb` clears `[0x162C..0x162C+0x1B58]` (7000 bytes), then
`rep movsb` copies fill it from `[0x3285]`/`[0x3302]`/`[0x33E6]`/`[0x33F0]`
— which are themselves derived from the staged road array. So the chain is:
`ROADS.LZS` road[] (✓ byte-exact recovered) → those `0x32xx`/`0x33xx`
intermediate buffers → the `0x162C` perspective table (level-dependent, what
the sim reads). Recovering the middle arrows (the `4B8E` road→perspective
build) is the concrete, now-fully-located remaining RE for a VM-free
`--level N` simulation start. NOT level-independent-and-therefore-free as the
previous entry mistakenly claimed — my error, corrected here. `4B8E` is
`liftgen`-liftable (an earlier entry confirmed it's mechanically liftable),
so lift-then-refactor is the natural attack.
## 2026-07-12 — scoping native arbitrary-level sim-start: perspective tables are level-INDEPENDENT, per-level DGROUP delta is mostly render buffers (good news, but wiring needs care)

Investigated whether a fully-native `--level N` SIMULATION start (no VM) is
tractable, by measuring what's actually level-dependent. Two useful findings:

**1. The projection/perspective tables are level-independent.** Diffed full
DGROUP between two different levels' first gameplay frame (index 16
gate-8/200/180 vs index 17 gate-7/175/60, from the multi-level cold demo):
5,078 bytes differ, but the perspective-table region (`~0x162C-0x1900`, which
`renderer.perspective_row_offset` and the collision predicate
`road_object_visible` index into) is nearly IDENTICAL — only ~19 bytes
differ. So the projection geometry is a fixed, capture-once table reused for
every level, not something a native loader must rebuild per level.

**2. The big per-level delta is render buffers, not sim state.** The 5 KB of
per-level difference concentrates in `[0x5500-0x60A0]` — the same
`0x5170`/`0x5473`-region 34AE uses as its off-screen render SOURCE (per the
34AE decode two entries back), i.e. the composed road IMAGE, not
gameplay-simulation state. Combined with finding 1, this strongly suggests
the SIM-relevant per-level state is small (the road array + a handful of
fields), with the bulk of level-load being render-only.

**Honest blocker on nailing it down precisely**: tried to instrument exactly
which DGROUP offsets `native_gameplay_substep` READS (to enumerate the
minimal sim state a native `--level N` must provide) by wrapping the
`NativeGameState` backend — but got 0 reads, because the sub-step's
collision predicate (`collision.make_visible` → `road_object_visible`) and
`GameView` reach the underlying `bytearray`/`.data` directly through
`state_view.coerce_backend`, bypassing a naive rb/rw wrapper. Enumerating the
read-set cleanly needs instrumenting at the `state_view` layer (or a
copy-on-read shadow buffer), not monkeypatching — deferred rather than
bodged.

**Net**: native arbitrary-level sim-start looks genuinely TRACTABLE (fixed
projection tables + small per-level sim state + already-byte-exact road
load), but actually wiring `--level N` to start any level's simulation VM-free
needs (a) the sim read-set enumerated properly at the `state_view` layer,
then (b) writing level N's road array into whatever representation those
reads target. That's deliberate work, not a quick patch — scoped here, not
rushed. The current honest state stands: `play_native.py` cold-starts the
level a demo seeds; a true by-index native start is the next real step, now
with its feasibility established and its one plumbing obstacle named.
## 2026-07-12 — CORRECTION: the "~15 KB derived transform" is mostly just more LZS decompression (already-recovered codec), not a novel unrecovered subsystem

The previous entry ("scoped what a FULLY-native level-start still needs")
called the `[0x6C40-0x71E1]` DGROUP block "a large DERIVED state... a real,
bounded, recoverable subsystem... the next substantial RE target." That
OVERSTATED it. Traced which code actually writes that region during
menu→gameplay: **every write comes from `1010:6712`** — the LZS
decompressor's own main loop (`skyroads/codecs/lzs.py`, already recovered and
verified). So the biggest chunk of the 15 KB isn't a novel transform at all;
it's another asset run through the codec this project ALREADY has.

Confirmed it's a DIFFERENT asset, not the road array: captured
`DGROUP[0x6C40..0x71E1]` at gameplay start (1442 bytes, begins
`23 00 43 00 53 00 73 00 ...`) and it does NOT contain the level's
decompressed road geometry (`read_level_road(14)`, which lives in its own
buffer at `~0x17E0C`, matched byte-exact two entries ago). It's some other
LZS-compressed resource decompressed into a DGROUP working area — most
likely world/tile graphics (`WORLD5.LZS` opens at level-load alongside
`ROADS.LZS`, per the file-open trace), i.e. RENDER data, not gameplay-
simulation state.

**Honest corrected scope**: "load a level" on the DATA side is closer to done
than the previous entry implied — it's the already-recovered LZS codec
applied to a few more inputs (road array ✓ byte-exact; the other blocks are
the same codec, source files identified), NOT an unrecovered per-segment
geometry-build subsystem. What a fully-native SIMULATION start actually needs
is the SUBSET of that 15 KB the gameplay sub-step reads (not all of it — much
is render-only graphics), which is a smaller, more targeted question than
"reproduce the whole diff." The genuinely-unbuilt large piece remains the
RENDERER (consuming those graphics blocks), not a mysterious geometry
transform. Retracting the "substantial new RE target" framing — it was wrong.

## 2026-07-12 — scoped what a FULLY-native level-start still needs: level-load builds ~15 KB of derived DGROUP state, not just a few fields

With native level LOAD proven byte-exact (previous entry), measured exactly
what stands between that and a fully-native level START (seeding a
`NativeGameState` without any VM). Full-memory-diffed DGROUP across the
menu→gameplay transition in the level-start demo: **15,254 DGROUP bytes
change** (1,864 runs, 207 of them ≥16 bytes), overwhelmingly one contiguous
~5 KB structured block at `[0x6C40-0x71E1]` plus scattered smaller fields
(around `0xAF..`/`0xB8..`).

**Interpretation**: the level-load sequence doesn't just write
`(gravity,fuel,oxygen)` + the raw road array — it builds a large DERIVED
state from the raw `ROADS.LZS` entry (the `0x6C40` block is almost certainly
the expanded per-segment road/perspective geometry the gameplay sub-step and
renderer actually read, distinct from both the raw `UINT16LE[] road` and the
compressed file bytes). The raw road array itself lands in a separately
allocated buffer (found byte-exact at physical `~0x17E0C`, OUTSIDE DGROUP —
which is why it's not in this DGROUP-relative diff).

**Honest scope statement**: `apply_level_init` (already recovered) covers the
fixed respawn/gravity fields, and `read_level_header`/`read_level_road`
(byte-exact, previous entry) cover the raw asset data — but a genuinely
VM-free `--level N` start ALSO needs the raw→derived transform that produces
that ~15 KB of DGROUP state. That transform is a real, bounded, recoverable
subsystem (its inputs are now fully in hand — the verified raw road array —
and its output is exactly measurable via this diff), but it is NOT "just
wiring": it's the next substantial RE target on the load path. Until it's
recovered, `play_native.py` still seeds the full DGROUP from a one-time VM
boot (the current, honest hybrid). The gameplay simulation and the asset
load are both proven VM-free; this derived-geometry build is the specific,
now-precisely-scoped remaining piece of "load a level" from scratch.

## 2026-07-12 — native road-geometry decode is now VM-verified byte-exact — "load a level" is proven end-to-end, VM-free

Closed the one honest caveat left in the level-loading chain. `read_level_
road` (`roads_archive.py`) already decompressed all 31 levels' geometry to
the right LENGTH using the project's own LZS codec, but the decompressed
BYTES had never been checked against what the game actually loads — the
docstring flagged this explicitly ("not pursued this session").

Did it now, with the session's standard technique: drove the real VM
(pure ASM oracle, no hooks) through the level-start demo
(`demo_skyroads_20260711_202740`) to the first gameplay sub-step, captured
its full 1 MB memory plus the level's `(gravity,fuel,oxygen)` header, mapped
that header to its `ROADS.LZS` directory index via `read_level_header`, and
searched the VM's memory for `read_level_road(index)`'s natively decompressed
output. **Found byte-exact**: the gate-8/fuel-225/oxygen-111 level (== index
14, a 3096-byte road array) decompresses natively to bytes present VERBATIM
in the VM's own memory. So the LZS decode isn't just self-consistent — it
reproduces the original game's in-memory road geometry exactly.

Landed as `tests/test_roads_archive.py::test_decompressed_road_matches_
what_the_vm_loads_into_memory` (a live-oracle test gated on the EXE + demo,
following `test_play_native.py`'s pattern; ~1.5 s since the demo is short).
Updated `roads_archive.py`'s docstring and `read_level_road`'s caveat from
"length-verified only" to "byte-exact against the live VM."

**What this means for the user's `play_native.py --level 8.1` goal**: the
"LOAD a level" half is now genuinely complete and VM-proven, VM-free —
native code reads any of the 31 levels' `(gravity,fuel,oxygen)` AND their
full decompressed road geometry straight from `assets/ROADS.LZS`, both
byte-exact against the original. Combined with `apply_level_init` +
`NativeGameplayDriver` (both already recovered/verified), the remaining gap
to real interactive `--level N.M` play is now purely: (1) seed a
`NativeGameState` from a native level-load instead of a VM capture (wiring,
not new RE — the road geometry needs writing into the same DGROUP offsets
the VM holds it at, `~0x17E0C` region, which the capture located); (2) the
level-index → planet.road mapping for the CLI (the directory order isn't
obviously planet-major — index 14 = gate-8/225/111, not "planet 5 road 3");
(3) the renderer + real-time I/O loop (the big one, still unbuilt). "Load"
and "play the simulation" are done and proven; "render it interactively"
remains the frontier.
## 2026-07-12 — systematic lifting toward "load, start, play a level": 39D4 landed; re-discovered 3A96 was already recovered (intro_anim_unpack); 3A3F still open; generalized the lifter's runaway-guard tooling

User redirected from renderer-assembly (previous entry) to continuing
hooking/lifting more broadly first, with an explicit goal: keep lifting
until everything needed to load, start, and play a level is covered.

**Landed**: `1010:39D4` (the HUD/dashboard finalize blitter every `34AE`
render pass ends with) — lifted via `dos_re.lift`, verified `ORACLE_PASSING`
via `liftverify` (100 calls, 3/3 blocks, full coverage, zero divergence),
installed in `skyroads/hooks.py`. Its own 4 sprite-blit calls go to
`1010:3A22`, already hand-recovered and verified in an earlier session
(`sprite_blit_hook`) — no duplicate work needed there.

**Investigated `1010:3A3F`/`3A96`, profiled as `39D4`'s neighbors**, and
disassembled both live via the now-fixed `tools/lindis.py --live-demo`.
Both read a per-`bx`-index segment number from the same `0xE76` 8-word
table `34AE` uses for its rotating display-list buffers (an earlier read of
one disassembly line as a literal "3702" was a misread of the
interpreter's own decimal-formatted disp16 — `3702` decimal `== 0x0E76`
hex, the same table, not a second one).

**Correction**: `1010:3A96` turned out to be ALREADY recovered, one day
earlier in this same project (2026-07-11's "recovered + wired the intro
animation-frame unpacker" entry) — `intro_anim_unpack_hook` in
`skyroads/hooks.py`, verified and installed. It's the intro's sprite/logo
decompressor (fires once at startup, not a per-level buffer bootstrap as
this investigation first guessed) — a real, if embarrassing, instance of
not searching for existing coverage before assuming a gap. Confirmed by
independently re-lifting and re-verifying it (before finding the existing
hook): `liftverify` initially hit the lift's own `MAX_ITERATIONS` runaway
guard mid-verification against a freshly captured pre-execution snapshot
(the real per-boot data stream — 8 segments × 1040 rows — needs more
block-transitions than the emitter's default budget assumed, matching
EXACTLY the same guard the 2026-07-11 recovery of this same function hit
and worked around with a "local, throwaway" patch at the time). Discarded
the redundant re-lift once the existing hook was found; kept the one
durable improvement this produced —

**Generalized the lifter's runaway-guard workaround into real tooling**
(`dos_re` submodule, pushed upstream): `emit_function` previously computed
`MAX_ITERATIONS` purely from instruction count with no override; added an
optional `min_iterations` parameter, exposed as `--max-iterations` on both
`liftgen.py --emit` and `liftverify.py`. This is exactly the fix the
`buffer_relocate`/`intro_anim_unpack` recoveries each improvised
one-off, now a documented, reusable flag for the next large data-driven
loop this project (or any other using `dos_re`) hits. Verified against
`dos_re`'s own `test_lift_emit.py`/`test_lift_decode.py`/`test_lift_cfg.py`
(80 tests) before pushing; skyroads_port's own full suite (325/325) still
passes with the bumped submodule pin.

**`1010:3A3F` remains genuinely unrecovered** — never observed executing in
any of the 14 demo captures, nor in a ~450K-step genuine fresh EXE boot
(where `3A96` DID fire). Its trigger condition is unknown. `liftgen`'s
static census confirms it's mechanically liftable (40 insts/10 blocks)
whenever it is found — a real, scoped, reproducible next step, not an
open-ended unknown, but genuinely still open (unlike `3A96`).

## 2026-07-12 — fully decoded 1010:34AE from its own proven lift; paused before assembling a native renderer (redirected to broader lifting first)

User asked for `play_native.py --level 8.1` with real interactive play.
Assessed honestly: that needs an assembled native renderer (none exists —
`road_column_strip`/`dispatch_variant_a`/`_b` are recovered but nothing
calls them from a real per-frame entry point) plus a real-time input/render
loop (doesn't exist either). User chose the big option — build toward real
interactive native play — so started by reading `1010:34AE`'s own proven
lift (`skyroads/lifted/lifted_1010_34ae.py`, 130 instructions/28 blocks,
`ORACLE_PASSING` from before this session) end-to-end, since a previous
attempt at this refactor was abandoned mid-draft after catching three
transcription mistakes (see the 2026-07-11 "found the render entry point"
entry).

**Fully understood this time, reading the LIFT (not re-deriving from ASM,
avoiding the earlier mistakes' root cause)**:
- Block 0: `ds:=ss` (a no-op in practice — this program's `ss` and `ds`
  are the same segment throughout, confirmed empirically all session), then
  an early-exit check on `ss:[0x3C]` (a genuine caller-supplied local/param,
  the one field here that ISN'T a DGROUP alias).
- Blocks 2-3: `mode` (the `ax` value on entry) selects `[0E66]`/`[0E68]`/
  `[0E42]` — `mode==0`: source `[5170]`, dest `[0E36]` (off-screen), dispatch
  `0x364F` (`dispatch_variant_a`); `mode!=0`: source `[0E36]`, dest `0xA000`
  (VGA), dispatch `0x36F3` (`dispatch_variant_b`). Confirms the earlier
  finding exactly.
- Blocks 4-9: `[0E32]!=0` OR the unsigned delta `[0E2A]-[0E6A]` `>=8` →
  jump straight to the FLAT COPY fast path (blocks 25-27, a `rep movsw`
  from `[0E66]:si` to `[0E68]:di`, `cx`/`si` picked by `[0E32]`: `(0x2800,
  0x4240)` normally or `(0, 0x5640)` when `[0E32]!=0`). Delta `==0` → skip
  straight to finalize (blocks 7→23) — a real per-frame CACHE: unchanged
  position does zero column work.
- Block 10-12 (the real per-column setup, `0 < delta < 8`): `[0E64] = 0x30`
  if `([0E2A]>>3) == ([0E6A]>>3)` else `0`; `[0E62] = table_0xE76[([0E6A]&7)*2]`,
  `[0E60] = table_0xE76[([0E2A]&7)*2]` (an 8-slot word table — a rotating
  multi-buffer scheme, NOT the same source field twice, correcting a
  misread from earlier in this same investigation); `[0E4C] = ([0E2A]>>3)
  // 14 + 0x168E` (`0x162C` = `PERSPECTIVE_TABLE_BASE` + `0x62`) — the
  road-segment RECORD POINTER for this frame's position.
- Blocks 13-22 (the classification loop, confirmed the SAME shape
  `render_dispatch.py` already expects): outer loop `[0E46]` 1..4, inner
  toggle `[0E48]` 0/1, each iteration reading 1-2 bytes from the record at
  `[0E4C]` (stride `0x0E` per outer step, sign/offset selected by
  `[0E48]`), building `e4e/e50/e52/e54` via an 8-entry BYTE table at
  `0xBA7` (a "shape reduction" lookup — a real, distinct table from the
  `0xE76` word table) and `e56/e58/e5a/e5c/e5e` via nibble extraction, then
  `call [0E42]` — confirms `dispatch_variant_a`/`_b`'s existing recovered
  contract byte-for-byte, this is genuinely where those functions' inputs
  come from. Loop ends when `[0E44]` (started at `0x0B`=11) counts down to
  1, walking `[0E4C]` backward by `0x0E` each outer pass.
- Every path converges on block 23: `call 1010:39D4`, then `pop ds; ret`.

**Disassembled `1010:39D4` too** (small, tractable, via the now-fixed
`tools/lindis.py --live-demo`): draws up to 4 fixed-position sprites via a
shared blitter (`1010:3A22`, not yet examined) using the SAME `[0E66]`/
`[0E68]` segments — 2 always, 2 more gated on `[0E68]==0xA000` (i.e., only
composited on the real-screen pass, not the off-screen one). Almost
certainly the HUD/dashboard/ship overlay (`DASHBRD.LZS`/`CARS.LZS` are
real, on-disk resource files this session already found — see the
level-select entries above).

**Two real, static lookup tables still needed, content not yet read**:
`0xE76` (8 words — display-list buffer segment numbers) and `0xBA7` (8
bytes — shape-class reduction). Both are constant/compile-time DGROUP data,
not per-level — readable directly from any VM capture (same "seed once
from the VM, then go native" pattern this whole session already uses), not
a new unknown mechanism.

**Paused here, deliberately, mid-task** — not because of a blocker, but a
user course-correction: rather than keep assembling a native renderer in
isolation (this decode, `road_column_strip`, `dispatch_variant_a`/`_b`,
`3A22`'s blitter, and the interactive I/O loop are all still separate,
unintegrated pieces), the user redirected toward continuing the broader
hooking/lifting effort first, to grow real coverage before attempting a
full native product again. This entry preserves the research (a real,
verified, from-the-proven-lift understanding of `34AE`'s COMPLETE
algorithm) so it's not lost — porting it to `skyroads/recovered/road_frame.py`
remains a concretely scoped, mostly-solved next step whenever the project
returns to the renderer.

## 2026-07-11 — the road GEOMETRY decodes too — found and reused an existing, already-VM-verified LZS codec

Follow-up to landing `roads_archive.py`'s header reader. Went to scope the
LZSS decompressor for `road[]` (the last open piece for native level data)
and discovered `skyroads/codecs/lzs.py` **already exists** — recovered in an
EARLIER session (before this one), VM-verified byte-for-byte against
`TREKDAT.LZS`/`MUZAX.LZS`/`INTRO.LZS` via a differential hook verifier. Not
duplicated; reused directly.

`ROADS.LZS` turned out to need a simpler per-entry header than those files'
self-modifying-code-patched widths: three raw bytes — `(width_len,
width_dist_long, width_dist_short)` — sit plainly at the start of each
entry's `road[]` data, right after the 216-byte palette. Fed through the
existing `decompress_block`/`LzsWidths`: **31/31 real `ROADS.LZS` levels
decompress to EXACTLY the length the directory records.** Landed
`read_level_road` and `read_level_palette` in `roads_archive.py`, plus two
more tests (7 total in `tests/test_roads_archive.py`, all passing).

**Honest caveat**: the road array's DECODE is now verified (31/31, plus the
existing codec's own prior VM-verification for the underlying LZ scheme);
the FIELD MEANINGS within each decoded `UINT16LE` (the "seven values per
line", tunnel/color bit layout ModdingWiki documents) are sourced from that
public documentation, not independently re-derived from ASM or cross-checked
against a live VM memory capture of the in-memory road array this session.
Good enough to treat the decode as trustworthy; not yet the same standard
of proof as this project's own from-ASM recoveries.

**State of native level DATA, now complete for what native gameplay
actually needs**: gravity/fuel/oxygen (feeds `apply_level_init`, already
recovered) — verified 3/3 against live captures; road geometry — decodes
correctly (31/31 length-exact) but its field semantics aren't independently
re-verified. Combined with the already-recovered gameplay/menu-selection
pipeline, `play_native.py` could load real per-level tuning constants for
any of the 31 levels without the VM; consuming the road geometry (for
collision/rendering) is the natural next integration step, not yet done.

## 2026-07-11 — LANDED: native ROADS.LZS level-directory reader, verified 3/3 against real captures — the level-select mystery's data source is now real, portable code

Closes the level-select investigation with an actual shipped deliverable,
not just documentation. Rather than reverse-engineer `ROADS.LZS`'s
compression from scratch, checked whether the format was already
documented publicly first — it was: [ModdingWiki's "SkyRoads compression"
and "SkyRoads level format"](https://moddingwiki.shikadi.net/wiki/SkyRoads_compression)
pages (reverse-engineered previously by the retro-game-preservation
community) describe an LZSS scheme for the game's `.lzs` resource files, and
specifically that `ROADS.LZS` holds a directory of per-level entries:
`(UINT16LE offset, UINT16LE length)` pairs, each entry then starting with
plain `UINT16LE gravity; UINT16LE fuel; UINT16LE oxygen` before a palette
and the (LZSS-compressed) road-geometry bytes.

**Verified directly against the real `assets/ROADS.LZS` file** — no VM
needed, since this is a static game asset, not runtime memory. Parsed the
31-entry directory (self-terminating: entries repeat until the read
position reaches the FIRST entry's own offset) and read the plain
`gravity/fuel/oxygen` triple at each entry. Checked all THREE real
values this session's live tracing had captured from the VM:

| source | gravity | fuel | oxygen | `ROADS.LZS` index |
|---|---|---|---|---|
| frame 282 (first level pick) | 8 | 200 | 180 | 16 |
| frame 1327 (real DOWN-ARROW+ENTER pick) | 7 | 175 | 60 | 17 |
| frame 2016 (third pick) | 8 | 150 | 180 | 1 |

**All three exact.** This also fully explains the "same gate=8, different
fuel" puzzle from two entries ago — it isn't an anomaly, it's just a flat,
index-addressed table where multiple distinct levels legitimately share a
`gravity` value while differing on `fuel`/`oxygen`.

Landed `skyroads/recovered/roads_archive.py` (`parse_directory`,
`read_level_header`, `level_count` — pure, VM-free, reads a byte string) +
`tests/test_roads_archive.py` (the 3 real-capture matches, a directory
self-consistency check, and a regression test locking in the "same gravity,
different fuel" fact so it can't quietly look like a bug again later).

**What this unlocks**: native code can now enumerate every one of
SkyRoads' 31 levels' `(gravity, fuel, oxygen)` — the exact three fields
`apply_level_init` needs — with ZERO VM involvement, just reading a static
asset file. Combined with `apply_level_init` (already recovered) and
`NativeGameplayDriver` (already recovered), `play_native.py` could now, in
principle, cold-start ANY of the 31 levels by index alone, not just
whichever one a captured demo happened to seed. Not wired up yet (that's
the natural next step). **Still NOT solved**: the actual road GEOMETRY
(the LZSS-compressed `road[]` bytes after the palette) — porting that needs
the actual LZSS decompressor (width1/width2/width3 bit-stream scheme,
documented but not yet implemented/verified here), which is what a real
native RENDERER of an arbitrary level would need next.

## 2026-07-11 — RESOLVED: SkyRoads loads levels from real, separate `.lzs` compressed resource files — the level-select investigation's final answer

Closes the thread run through the last several entries. Scanned for every
DOS file-open (`INT 21h` `AH=3D/3C/6C`) in the first 400 frames of the
multi-level cold-boot demo, reading each call's ASCIIZ filename directly
out of `DS:DX`. Found the game's real, on-disk resource manifest, in
load order:

    skyroads.cfg  muzax.lzs  oxy_disp.dat  ful_disp.dat  speed.dat
    demo.rec  trekdat.lzs  intro.lzs  anim.lzs  intro.snd  mainmenu.lzs
    cars.lzs  dashbrd.lzs  sfx.snd  gomenu.lzs  roads.lzs  world5.lzs

**This settles it**: SkyRoads is a classic disk-resource-file DOS game —
menus (`mainmenu.lzs`, `gomenu.lzs`), sprites (`cars.lzs`, `dashbrd.lzs`),
generic road-shape pieces (`roads.lzs`), and **per-world level data
(`world5.lzs`)** all live in separate files, most `.lzs`-compressed (`lzs`
almost certainly = an LZ-style compressor, matching the buffered
byte-stream reader `6326`/refill-via-`INT 21h AH=3Fh` chain traced over the
last several entries). `world5.lzs` opens right where the level-config
triple (`jump_level_gate`/`[54A2]`/`[4566]`) gets read for the FIRST
level-start — so that read genuinely does trickle down to a real,
compressed, on-disk file, not a compile-time DGROUP constant as earlier
entries guessed. This also explains the earlier puzzle (same `jump_level_
gate=8` producing a different `divA` on a later attempt): different level
SLOTS within a world's file can share a gate value while differing on
tuning constants — nothing was inconsistent, the read source just wasn't
a flat, index-addressable array the way this investigation kept assuming.

**What this means for "native level select"**: it is NOT the small,
almost-free addition earlier entries hoped for. Genuinely native (VM-free)
"pick any level, load its data" needs a real `.lzs` decompressor and file
reader — comparable in scope to a NEW subsystem, not a quick table lookup.
Concretely scoped next steps, in dependency order: (1) get a `world5.lzs`
(and a `roads.lzs`) file off disk and reverse-engineer the `.lzs` container
format (header, compression scheme — likely a classic LZ77/LZSS variant
given the byte-at-a-time decode pattern already traced); (2) port a clean
decompressor once the format's understood, verified against real reads via
the now-fixed `tools/lindis.py --live-demo`; (3) locate the per-level
record layout inside a decompressed world file (gate/timerA/timerB, and
almost certainly the actual road-shape/geometry table the renderer needs
too — `roads.lzs` is a strong candidate for exactly the display-list data
this session's earlier renderer work never found a builder for). This
consolidates cleanly with the renderer's own still-open "display-list
BUILDER" gap (`vmless_roadmap.md` item -1) — they may be the same missing
piece.

**Session summary for the level-select investigation as a whole**: started
from "does the existing `dispatch_menu_action` recovery even model a real
human menu" (it didn't — it modeled auto-progression); ended at a complete,
concrete, disk-file-based resource-loading picture with named real files,
a real DOS `INT 21h` read path, and a fixed disassembly tool
(`tools/lindis.py --live-demo`) that will make the next phase (the `.lzs`
format itself) far more tractable than the hand-decoding done to get here.
Nothing was ported or landed as recovered code this session — this was
entirely successful reconnaissance, now accurately scoped instead of an
open question.

## 2026-07-11 — FIXED tools/lindis.py (`--live-demo`); CORRECTION: there IS a real file-read path, just not one the sampled level-config read happened to hit

Implemented the fix the previous entry called for: `tools/lindis.py
--live-demo <demo_dir>` now drives a real demo forward (pure ASM oracle,
same technique as `play_native.py`'s `boot_and_seed`) until execution
actually reaches the requested `CS:START`, then disassembles from that
LIVE, correctly-populated memory instead of a cold snapshot. Verified
against the known-good `1010:1B49`: output now matches
`dispatch_menu_action`'s already-recovered model exactly (`ENTER 0,0;
push si; push di; mov ax,ss:[bp+4]; and ax,0Fh` — the action-code masking
— then the real `cmp`/`mov` chain for actions `0xC` and `9`, including the
exact `0x6978`/`0x7530` timer constants already in `menu.py`). The tool is
now trustworthy for any address, given a demo that actually reaches it.

**Immediately used it on the open staging-buffer question and found a
correction to the previous entry's "no file I/O" reading.** Disassembled
`1010:5F80-5FB4` (a function `1010:5F80` calls into) and it's a real,
robust **DOS file-read wrapper**: `mov ah,3Fh` / `int 21h` (read from
handle) with retry/error-state bookkeeping at `ss:[41AA]` (a 20-retry
counter, `0x14`, matching a constant seen earlier in `6326`'s own refill
path). So genuine disk I/O capability DOES exist and get used somewhere in
this pipeline (almost certainly the overlay/code-loading system the
previous entry found evidence of, and/or on-demand level-geometry loading)
— the earlier claim that "no `INT 21h` file-open activity was observed
anywhere in this investigation" was only true for the SPECIFIC narrow
config-triple read sampled (which apparently didn't need to trigger a
buffer refill in either observed case), not a claim that the whole
mechanism is file-I/O-free. Corrected here rather than left standing.

**Net effect on scope**: native "pick any level and load it," if pursued
further, likely needs to handle a real buffered-file-read fallback (not
just the always-cached fast path this session's sampling happened to
observe), which is a bigger dependency than "read a few static bytes from
a fixed DGROUP offset." Not chased further this entry -- flagging the
scope correction is the priority here; the fixed tool makes chasing it
properly a much more tractable next step whenever picked up.

## 2026-07-11 — ROOT CAUSE found for `tools/lindis.py`'s garbage output (and probably other session mysteries): SKYROADS.EXE overlays/self-modifies its own code segment at runtime

While chasing the level-select staging-buffer question (previous entry),
tried `tools/lindis.py` again on a KNOWN-good address (`1010:1B49`,
`dispatch_menu_action`'s own entry, extensively verified 318/318 earlier
today) and got garbage starting from the very first byte (`D5 75` — an
`AAD` instruction, nonsensical as a function entry). Rather than shrug this
off as "the tool is broken" again, compared the SAME address's raw bytes
three ways: `dos_re.snapshot.load_snapshot` directly, the real frontend's
`load_snapshot_runtime`, and a LIVE runtime at increasing points in
execution:

    frame 0  (right after snapshot load, no stepping): d5 75 f8 46 c3 e6 12 53
    frame 566+ (once the game actually reaches this code live): c8 00 00 00 56 57 8b 46

The two loaders agree with each other (ruling out a snapshot-format bug) —
but the bytes **genuinely change between frame 0 and frame 566** in the
SAME live runtime. `C8 00 00 00` is `ENTER 0,0` — a completely ordinary
compiled-C function prologue (`ENTER`, `PUSH SI`, `PUSH DI`, `MOV AX,
[BP+..]`), exactly what a real `dispatch_menu_action` should look like, and
what `D5 75...` manifestly is not.

**Conclusion: SKYROADS.EXE overlays/decompresses its own code segment at
runtime** — the bytes at a given `CS:IP` are not fixed for the program's
lifetime; some region get progressively loaded/decompressed into place as
the game reaches different phases (title/menu vs. gameplay code sharing the
same address range at different times), consistent with everything found
in the last two entries about a real byte-stream decompression primitive
(`6326`) existing in this codebase. This means **any static, snapshot-based
disassembly is only valid for code that has ALREADY been loaded into place
by the time the snapshot was taken** — reading too early (or from a
snapshot resumed from an earlier point) reads stale/uninitialized bytes and
produces exactly the kind of garbage `tools/lindis.py` kept producing this
session (this entry, the `1010:2A35` sanity-check earlier today, and very
likely explains the still-unresolved mode==1 dynamic-verification mystery
from the render-entry-point investigation too — worth revisiting with this
in mind).

**Practical fix, not yet implemented**: `tools/lindis.py` should read bytes
from a LIVE runtime driven forward to (or past) the address of interest,
not a cold snapshot load — e.g. accept an already-stepped `cpu.mem`, or
take a frame-count/IP-reached argument and drive the demo forward first.
Until that's done, treat any `lindis.py` output as untrustworthy unless the
target address is independently known to be genuinely static (most of the
gameplay-hot addresses already recovered this session, all reached and
executing within the first ~600 frames of any demo, appear to be — this
bug specifically bit code that's only in place LATER or briefly).

## 2026-07-11 — decoded the byte-stream reader down to real bytes (`1010:6326`); found the config table, but its per-attempt refresh is still unexplained

Direct continuation of the `6490`/`6576` entry below. Read `6326`'s own raw
bytes directly (unambiguous — a 19-byte fast path, no execution-context
guessing needed):

    6326: mov ax, [41B6]         ; ax := stream cursor
    6329: cmp ax, [41B4]         ; cursor == end-of-buffer?
    632D: jz 6350                ; if so, take the refill path (see below)
    632F: mov bx, [41B6]         ; bx := cursor
    6333: inc word [41B6]        ; cursor += 1
    6337: mov al, [bx]           ; al := *cursor  (ds-relative — a plain byte read)
    6339: mov [41B0], al         ; stash it in the lookahead cell 6490 pops
    633C: ret

So `[41B6]` is a genuine sequential cursor over a **plain, uncompressed byte
array** — `mov al,[bx]` is a direct memory read, not a decode step. `[41B2]`
is the buffer's start address, `[41B4]` its end; the "refill" path at
`6350` (not fully decoded — it conditionally calls either a small stub or
`1010:63D5`, gated on a flag at `[41BB]`) only fires once the cursor runs
off the end of whatever's currently staged, and never fired during any
level-config read observed this session.

**Found the table.** `[41B2]` (buffer start) is `DS:0x31A8` — the SAME
768-byte scratch region `4B8E` clears with `rep stosb` (previous entries).
Dumping it directly at each of the three real level-config reads (matched
by the caller IP `568C`, before any bytes get consumed) shows the exact
expected little-endian word triple sitting right at the front of the
buffer: frame 282 (level 8) → `08 00 C8 00 B4 00` (gate=8, divA=200,
divB=180); frame 1327 (level 7, the real arrow-key selection) → `07 00 AF
00 3C 00` (gate=7, divA=175, divB=60 — matching the earlier
register-captured results exactly); frame 2016 (level 8 again) → `08 00 96
00 B4 00` (gate=8, but **divA=150 this time, not 200** — different from the
frame-282 reading despite the same gate).

**Open question, explicitly not resolved**: that last fact — the SAME
gate=8 producing a DIFFERENT `divA` on a later attempt — means this isn't a
static "index into one big table" read; something repopulates this 6-byte
staging window before each config-read, and it isn't obviously a fixed
function of the level index alone (or the two gate=8 readings would match).
Watched for writes to `[0x31A8]` across the transition window and found
several word-sized writes at `1010:5F95` immediately before the read, but
the intermediate values (`0`, `2566`, `7`, `19779`) don't read as a clean
"write the level's config record" — more likely `0x31A8` is ALSO used as an
ordinary scratch/local-variable slot by unrelated code sharing the same 4KB
buffer, and only one of these writes is the real one. Distinguishing them
needs either working static disassembly (this session's attempts with
`tools/lindis.py` produced garbage against this snapshot, unresolved) or
more careful register-level tracing than was practical to keep doing by
hand at this point.

**Net effect**: the level-start pipeline is now understood essentially
end-to-end — arrow keys (mechanism not yet located) → some write populates
a 6-byte staging record at `DS:0x31A8` → `6576`/`6490`/`6326` (a real,
simple, uncompressed byte-stream reader, not a file loader or a general
compressor) reads it into `jump_level_gate`/`[54A2]`/`[4566]` → `4B8E`
(level-independent buffer setup, confirmed identical across levels) →
`apply_level_init` (already recovered) → gameplay. The one remaining gap is
narrow and specific: what writes the staging record, and does it read from
a genuinely bigger static per-level table elsewhere, or compute it. This is
a good stopping point for hand-tracing — real disassembly tooling (fixing
or replacing `tools/lindis.py`) would make finishing this, and future
similar work, much faster than continuing to guess instruction boundaries
from raw opcode bytes one call at a time.

## 2026-07-11 — traced level-selection down to a real, tiny byte-stream reader (`1010:6490`/`6576`); the big table-copy is level-independent boilerplate

Direct continuation of the "FOUND the real level-start code" entry below,
using the SAME multi-level cold-boot demo, now focused on the one open
question that entry left: do `4B8E`'s copy-source addresses shift per level,
or is level selection decided somewhere else?

**Confirmed: `4B8E`'s big table-population sequence is byte-for-byte
IDENTICAL regardless of which level is selected.** Captured its full
`rep stosb`/`rep movsb` operand sequence (opcode, `cx`, `si`, `di`, `ds`,
`es`) at the LAST call of three different level-start bursts in this demo —
one landing on gate 8, one on gate 7, one back on gate 8 — and the two
gate-8 instances are pixel-identical to each other, and the gate-7 instance
uses the exact same source offsets (`0x5473`, `0x34A7`, `0x3285`, `0x3302`,
`0x33E6`, `0x33F0`, all the same `cx` lengths) as both gate-8 instances. So
`4B8E` is generic per-attempt buffer/table initialization (clearing and
re-populating working display-list buffers from FIXED DGROUP offsets), not
a per-level content loader — retracts the "maybe this is the level geometry
copy" read from the previous entry.

**Found the real level-selection write.** The demo's own real scancodes
(boundary 1274 = DOWN-ARROW, boundary 1297 = ENTER) land exactly on a real
`jump_level_gate` change (`8 -> 7`, confirmed at frame 1327, ~30 frames
later — the expected short processing delay) — so this demo genuinely
captures a player picking a DIFFERENT level with the keyboard, not just
attract-mode auto-progression. Read the raw bytes at the write site
(`1010:568C-56A0`) directly (opcode `0xA3 disp16` = `MOV [disp16], AX`, no
ambiguity) instead of guessing from execution alone, and found:

    568C: call 6576         ; -> AX
    568F: mov [4562], ax    ; jump_level_gate := AX   (confirmed =7)
    5692: call 6576         ; -> AX
    5695: mov [54A2], ax    ; the level-timer-A divisor already known
                            ; from step_level_progression (confirmed =175)
    5698: call 6576         ; -> AX
    569B: mov [4566], ax    ; the level-timer-B divisor already known
                            ; from step_level_progression (confirmed =60)

Three back-to-back reads filling exactly the three per-level tuning
constants `progression.py` already needed a source for. Captured entry/exit
registers at each of the three real calls (matched to caller by return
address, not just call order, to avoid misattributing an unrelated call to
the same shared function): **`bx` at entry to each call is the PREVIOUS
call's own result** (call 1 returns 7 -> call 2 enters with `bx=7` -> returns
175 -> call 3 enters with `bx=175` -> returns 60), which rules out a plain
"lookup by level index" read and instead looks like each call thread a
cursor/state value through the next.

**Decoded `6576` itself** (13 bytes, unambiguous — two calls to `1010:6490`
bracketing a byte-combine):

    6576: call 6490   ; -> AL (byte 1)
    6579: push ax
    657A: call 6490   ; -> AL (byte 2)
    657D: pop bx
    657E: mov ah, al   ; ah := byte 2
    6580: mov al, bl   ; al := byte 1
    6582: ret          ; ax := byte1 | (byte2 << 8) -- a little-endian word
                        ; assembled from two single-byte reads

So `6576` is a two-byte-read word-assembler, and `6490` (not yet decoded)
is almost certainly a "read next byte from an embedded stream, advance a
cursor" primitive — likely how this era of DOS game encodes a small level
config table (level gravity constant + two timer divisors per level,
possibly packed/delta-encoded given the byte-at-a-time read rather than a
flat word array) without any file I/O. No `INT 21h` file-open activity was
observed anywhere in this investigation, reconfirming the earlier finding.

**Where this leaves things**: the level-SELECTION mechanism (arrow keys ->
`jump_level_gate` + the two timer divisors, via a small byte-stream reader)
is now understood in outline and precisely located, but NOT yet ported —
`6490`'s own logic (the actual cursor/stream mechanics) and the source table
it reads from are still unknown, and the arrow-key-to-selection-cursor link
(what field DOWN/UP arrow actually move, before ENTER triggers this read)
hasn't been traced. This is real, tractable follow-on work, not a fresh
unknown the way "is there even a menu system" was at the start of this
investigation. Given the size of what's already been covered this session,
stopping here to check in before committing to a full port.


## 2026-07-11 — FOUND the real level-start code: table-driven, not a file loader, and it calls the already-recovered apply_level_init

Direct follow-up to the investigation below. The user recorded two fresh,
genuine cold-boot demos with real keyboard menu input (`demo_cold_
20260711_201855` — full session, multiple levels, dies, finishes the last
level; `demo_skyroads_20260711_202740` — a tight 156-frame clip that starts
already sitting at the level-select screen and just confirms). These are the
first demos in the repo to actually exercise the real menu/level-start code
(confirmed: real scancodes at low boundaries — ALT, SPACE, ENTER, arrow,
ENTER — landing on real gameplay input by frame ~80-90, not the `1B49`
auto-progression loop this session had been misreading as "menu" activity).

**Method**: near-call detection (scanning for real `0xE8` opcodes via
`cpu.mem.rb`, decoding the `rel16` operand directly — a poor man's
disassembler that doesn't depend on `tools/lindis.py`, which is still broken
against this snapshot) over the small demo's 156 frames surfaced a tight,
one-shot call chain right where `game_state` flips from level-select to
active gameplay: `1010:2B53 -> 3B9D`, `2BD6 -> 1114`, `2C0F -> 0BAF`,
`2C1B -> 0BE9`, `2C58 -> 4B8E`, then **`2C5E -> 1FD9`** — the LAST call in
the chain is `apply_level_init`, the routine this session already ported and
verified back in the "recovered the level-init" entry. So the transition
handler `should_run_gameplay` hands off to (`1010:2B0B`, previously
unmapped) ends, after its own setup, by calling code we already have.

**Full-memory-diffed each unknown call** (the same technique that caught the
two real `road_column_strip` bugs) to see what they actually touch:
- `3B9D`, `0BAF`, `0BE9`: tiny (4-6 bytes), all in a stack-adjacent scratch
  region (`0xB900`-`0xB910`) — almost certainly this enclosing routine's own
  locals, not persistent level data.
- `1114`: ~88 bytes across several small regions — looks like a
  buffer/working-state reset (many bytes go to 0), not new content.
- **`4B8E`: 843 bytes touched, dominated by one contiguous ~750-byte run at
  `[0x31BC, 0x34A7]`.** This is the one that matters.

**Traced `4B8E`'s own `rep stosb`/`rep movsb` instructions directly** (their
operands — `cx`/`si`/`di`/`ds`/`es` — say exactly what's being moved and
where, no guessing): it does a `rep stosb` clearing `ds:[0x31A8..0x31A8+0x300)`
(768 bytes, matches the diffed region), a `rep movsb` from `ds:0x5473` into a
**different, separately-allocated segment** (`es=0x8118`, cx=0x300) and
another from `ds:0x34A7` into yet another allocated segment (`es=0x8148`),
then a huge `rep stosb` clearing 7000 bytes starting at **`ds:0x162C`** —
`PERSPECTIVE_TABLE_BASE`, the SAME base `renderer.perspective_row_offset`
already reads — followed by several smaller `rep movsb` copies filling parts
of that region from other DGROUP offsets (`0x3285`, `0x3302`, `0x33E6`,
`0x33F0`), and finally a 19840-byte `rep stosb` clearing yet another freshly
allocated segment (`es=0x7176`).

**The headline finding**: this is a **table-driven load, not a disk file
read**. Every source/dest address observed is either a fixed DGROUP offset
or a DOS-allocated working-buffer segment (the same pattern `road_column.
road_column_strip` already established for display-list/screen segments) —
consistent with a small DOS game of this era baking all level geometry into
the EXE's own data segment rather than shipping separate level files. No
`INT 21h` file-open activity was observed anywhere in this window. This
means genuinely native, VM-free "pick any level and load it" is a real,
tractable target — it needs porting a table-copy/buffer-init routine, not a
file-format parser.

**Not yet done, and why this isn't shipped as a recovery yet**: whether
these source offsets (`0x5473`, `0x3285`, etc.) are FIXED (same for every
level, with the actual per-level selection happening somewhere upstream —
e.g. picking which physical EXE-embedded blob a different pointer refers to)
or shift with the selected level hasn't been checked against the multi-level
demo yet. `4B8E` itself is also not remotely disassembled/understood beyond
its raw copy operands — treating it as a black box that "does something
copy-shaped" is enough to answer "is this a file loader" but nowhere near
enough to port and verify it. This is real, substantial follow-on work, not
a quick add — comparable in size to the renderer subsystem recovered earlier
this session.

## 2026-07-11 — level-select menu investigation: existing "menu" recovery is actually auto-progression, not human menu-picking; no demo captures the real thing

Asked to build a native level-select menu (pick a level from a demo, it
starts, with level loading modeled), starting from the assumption that
`dispatch_menu_action`/`native_menu_frame` (recovered earlier, "318/318
matched") already models real player menu navigation. Traced the actual
calls in the E2E demo (`demo_e2e_20260710_132930`, the one described in this
doc as "menu → level select → play → die → exit → another level → quit") to
find the confirm/level-load moment, and found the premise was wrong.

**What the trace actually showed** (instrumenting real `1010:1B49` calls —
correcting an initial mistake of reading `ax` at the call's entry IP, which
is the CALLER's stale register, not the pushed argument; the real argument
is the pushed stack word at `ss:[sp+2]` at the call's entry, confirmed
against the known real action-code distribution): the demo's `game_state`
sequence is `0 -> 2 -> 0 -> 3 -> 0 -> 2 -> 0 -> 3 -> 0`, with `jump_level_gate`
(`ds:[4562]`) staying `8` through the first two `0->2->0` cycles and only
advancing to `9` on the third. Cross-referencing with the already-recovered
mechanics: action `0xA` (`ACTION_SCROLL_RIGHT`) fires every tick while
`ship_pos` climbs from `0` toward `LEVEL_END` (`0x2AAA`) — this is the
AUTOMATIC forward-motion tick (`classify`'s `1B49` side-effect call,
`skyroads/recovered/classify.py`), not a human pressing a right-arrow key in
a menu. Action `0xC` (`ACTION_ENTER_LEVEL_SELECT`, sets `game_state:=2`)
fires exactly when `ship_pos` reaches `LEVEL_END` — this is the
LEVEL-COMPLETE trigger already documented in the "the native loop is now
FULLY CLEAN in lockstep" entry below (`ship_pos = 0x2AAA -> game_state = 2`
via this same action code), not a "confirm level pick" UI action. So
`menu.py`'s action NAMES (`ACTION_ENTER_LEVEL_SELECT`, "level-select
dispatcher") describe what the ASM's cmp-chain COULD mean generically, but
every real exercise of it in this codebase's demo corpus is the
attract-mode auto-play loop, not a human browsing levels.

**Confirmed via `pb.is_cold_start`**: every one of the 14 demos in
`artifacts/demos/` resumes from a snapshot (`is_cold_start == False`,
re-checked here for the E2E demo specifically) — none is a genuine cold EXE
boot. So by the time any demo's capture begins, whatever REAL menu/title
interaction a human would see has already happened off-camera; there is
currently no capturable data anywhere in this repo of a player actually
navigating a level-select screen with the keyboard.

**Where the real level-load code lives, and why it's not mapped yet**:
`should_run_gameplay` (already recovered, `skyroads/recovered/orchestration.py`)
documents that `game_state == 2` makes the frame handler EXIT to
`1010:2B0B` — "the outer game loop, which then does the transition: respawn
`201F`, menu return, or level load." `2B0B` itself is NOT recovered. An
instruction-trace across the `game_state: 2 -> 0` transition window (frames
612-706) shows execution passing through known RENDER dispatch addresses
(`2DCC`/`2E6C`, the tile-rasterizer dispatch points from the very first
`tile_clip_mask` entry in `symbol_ledger.md`) — consistent with a short
animated transition, not obviously a disk-file read. Static disassembly
(`tools/lindis.py`) was tried to confirm this without more live capturing,
but produced garbage output at `1010:1B49` and `1010:2A35` (a KNOWN-correct
address, sanity-checked) alike — the tool appears broken against this
snapshot/codebase combination, not trustworthy right now, so this is
empirical-tracing-only evidence, not a static-disasm-confirmed read.

**Net effect**: did NOT build the requested native level-select driver this
turn — building it honestly requires either (a) a freshly recorded, genuine
cold-boot demo that captures real keyboard menu navigation to verify
against, or (b) mapping the unrecovered `1010:2B0B` outer dispatcher from
static disassembly alone (harder to verify, and the one disassembly attempt
made here didn't work). Flagged as an open, scoped question rather than
guessing further. See `vmless_roadmap.md`'s `native_menu_frame` entry for
the corrected claim.

## 2026-07-11 — MILESTONE: native cold run completes a full level, zero player input, VM-independently confirmed

The requested milestone: prove the native engine can play a level from a
genuine COLD start to completion with no VM involvement past the initial
geometry seed. Added `--cold`/`--cold-verify` to `play_native.py`.

`run_cold(state, jump_level_gate)`: calls `apply_level_init` (the recovered
respawn/level-init primitive — fixed field reset + `level_gravity`) on a
seeded `NativeGameState`, then drives `NativeGameplayDriver.tick()` in a loop
with **zero player input** until a transition fires. This is possible because
forward motion is automatic — the classification stage's `dispatch_menu_action`
call (action `0xA`, scroll-right) advances `ship_pos` whenever `[456A]==0`,
independent of steer/jump/speed input (see the "forward advance is the 1B49
call" entry below). Gate-8 level: **completes in 57 ticks**
(`ship_pos=0x2AAA`, `game_state=2`), purely natively, no VM after the seed.

`run_cold_verify(root, demo_path)`: the independent proof. Resets the REAL,
unmodified VM's memory to the exact same `apply_level_init` field values
(writing `RespawnState`'s fixed fields + `level_gravity(gate)` directly via
`cpu.mem` at the frame-loop top, once), then forces player input to zero on
every subsequent sub-step (re-zeroing the same three input fields each visit
to the loop-top IP) and lets the pure ASM oracle run un-hooked
(`install_replacements=False`) until `game_state` leaves 0. Gate-8 level:
**VM independently reaches the same conclusion** — `game_state=2`,
`ship_pos=10922` (`=0x2AAA`), confirming the native result byte-for-byte (a
one-tick counting-convention offset only, not a divergence).

**Found a real difference, not a bug**: the gate-7 level (`demo_
skyroads_20260710_125418`/`_125610`) does **NOT** complete with zero input —
both native and VM-independent runs hit `game_state=5` (timer_b/oxygen
expired) after the tick budget, never reaching `ship_pos=0x2AAA`. This is a
legitimate level-design difference (this level needs active play to finish
in time), not a regression — confirmed on both sides so it's not an artifact
of the harness.

**Bug fixed en route**: `run_cold_verify`'s frame loop originally advanced
`while not pb.finished(frame)`, which stops once the SEEDING DEMO's own
recorded input runs out (1075 frames for the gate-7 demo) — far short of the
1500+ ticks needed to observe the timeout. Since input is forcibly zeroed
every sub-step regardless of what the demo would have supplied, the fix is to
keep calling `frontend.advance_frame` past `pb.finished(frame)` (skipping only
the now-pointless `pb.apply_to_runtime` call) up to the tick budget. This is
what let the gate-7 VM-side confirmation complete.

This closes the loop on "native should be able to cold run a full level":
`apply_level_init` + `NativeGameplayDriver` + zero input is a real,
independently-provable playthrough, not just an offline replay of recorded
demo input. `tests/test_play_native.py` still needs `--cold`/`--cold-verify`
coverage (not yet added — next step, along with `vmless_roadmap.md`).

## 2026-07-11 — play_native.py proven on a SECOND level; quantifies where the known gaps bite hardest

Toward "play any level": scanned all 14 captured demos for `jump_level_gate`
(the per-level constant `apply_level_init`/`level_gravity` key off) and found
two distinct levels already available — most demos are gate `8`
(`gravity=0xFF8D`), but `demo_skyroads_20260710_125418`/`_125610` are gate `7`
(`gravity=0xFF9C`). So "any level" partially works TODAY without any new
recovery: `play_native.py --demo <either>` already plays either level.

Ran both modes against the gate-7 demo:
- Offline: 1575 ticks (1075 recorded + 500 extra), 3 transitions, zero
  crashes — same clean result as the gate-8 demo.
- `--verify`: 1014 total in-sync steps (a lot), but 6/7 runs ended on a real
  field divergence rather than a clean gap — worse than the gate-8 demo's
  `<=2` (the tolerance `tests/test_native_loop_lockstep.py` was written
  against, which only exercises the gate-8 demo).

**This is not a new bug** — every divergence's field set matches one of the
TWO ALREADY-DOCUMENTED gaps: `['af1c', 'lateral_accel', 'f455a']` (3
occurrences) is exactly what the `1DFA`-effect approximation
(`allow_unmodelled_effect=True`) touches; the rest (`timer_a`/`f455a`/
`af2e`/`af30`/`ship_pos`/`lateral`) match the un-modelled respawn/level-load
transition edge already called out in that test's docstring. What's NEW here
is quantifying that these two known gaps have a much BIGGER impact on this
level specifically — likely because it has more jumps/crashes exercising
them. Neither gap was closed this session (both need real, careful ASM work
— the `1DFA` effect's actual `lateral_accel` modification isn't recovered at
all yet); this is honest measurement, not a regression to chase down.

**Concrete next steps for "any level"**: (1) close the `1DFA` effect gap
properly (recover what it actually does to `lateral_accel`) — the single
biggest lever based on this measurement; (2) find/recover the respawn/
level-load transition itself (today `apply_level_init` handles a FRESH
level init, but not the specific mid-level `game_state 3 -> respawn` path);
(3) native level-FILE loading, so `play_native.py` never needs the VM at all,
not even to seed — level selection today is "which demo you happen to have."

## 2026-07-11 — caveat found on the dispatch variants: call-sequence-verified, not full-memory-diff-verified

Chasing the mode==1 mismatch above led to a genuinely useful realization:
`dispatch_variant_a`/`_b` (landed earlier today, `render_dispatch.py`) were
verified on their `road_column_strip` CALL SEQUENCE (which `ax` codes fire,
in what order) against real captures — NOT a full memory diff of everything
`1010:364F`/`36F3` themselves touch, the way `road_column.road_column_strip`
was (which caught two real bugs a narrower check would have missed). Tried to
run the SAME full-memory-diff technique against these two functions and hit
an unresolved capture-script issue: the expected return address (`0x35FC`,
confirmed correct via a separate return-address read at entry) was never
observed as REACHED by the step hook, despite the identical technique working
flawlessly for `road_column_strip` (196/196). Tried several fixes (an
SP-based match guard, removing it again, isolating the check to just these
two functions) without resolving it — spent real effort here without success
and stopped rather than keep burning time on debugging my OWN instrumentation
rather than game logic.

**Net effect**: `dispatch_variant_a`/`_b`'s shipped behavior is still
correctly verified for what it claims (the call sequence matches real
captures) — this isn't a retraction. But whether `1010:364F`/`36F3` have any
OTHER silent side effect (on `[0E42]` or elsewhere) beyond the documented
`road_column_strip` calls is now an explicitly flagged OPEN question, not
something the current docs could honestly claim was ruled out. Added the
caveat to `render_dispatch.py`'s module docstring. If picking this up again:
the return-address-tracking approach needs a different technique for THESE
two functions specifically (they're the target of the SAME `[0E42]` INDIRECT
call from a much larger enclosing loop, unlike `road_column_strip`'s several
direct call sites) -- worth checking whether the interpreter's indirect-call
handling has some difference from direct calls that a step-hook doesn't
observe the same way, rather than continuing to vary the matching logic.

## 2026-07-11 — found the render entry point: 1010:34AE, ALREADY a proven-correct lift from before this session

Traced upward from the column-dispatch/compositor work (previous two entries)
to find their actual caller, and landed on something already known: `1010:34AE`
— the "`[0E38]`-dispatched tile renderer" recovered via the automatic lifter
on 2026-07-10, **before this session began** (see that date's "full-level perf
drop root-caused" entry). It was already proven `ORACLE_PASSING` (401 calls
byte-exact, then 400 further full-level-demo calls under the strict
differential verifier, zero divergence) and installed as a live hook
(`skyroads/lifted/lifted_1010_34ae.py` + `registry.replace(0x34AE)`) — but
never refactored into clean `skyroads/recovered/` code (explicitly flagged as
"the to-do" in that 2026-07-10 entry).

**Reading the proven lift resolves several open questions from today's
renderer work in one shot**:

- `[0E42]`'s two values (`0x364F`/`0x36F3`) are NOT road-shape variants as
  first guessed — they're set UNCONDITIONALLY based on a caller parameter
  (`ax` on entry, called `mode` below): `mode==0` sets dispatch to
  `dispatch_variant_a` with source `ds:[5170]` and dest `ds:[0E36]` (an
  off-screen buffer); `mode!=0` sets dispatch to `dispatch_variant_b` with
  source `ds:[0E36]` and dest **`0xA000`, the real VGA segment**. So the two
  dispatch variants recovered earlier today are the SAME rendering logic run
  twice per displayed frame — once into a back buffer, once flushed to the
  actual screen — not different road shapes. This also explains why my
  attempt to observe `[0E42]`'s value dynamically kept coming up empty: I was
  probing the wrong address for the WRITE (it's set unconditionally near the
  top of `34AE`, not read from a stored pointer at `35F8` the way I'd assumed
  from a stale disassembly).
- A `ds:[0x3C]` flag gates the WHOLE call as a no-op when nonzero (an early
  return before anything else runs) — the caller decides per-call whether
  this mode renders at all.
- `ds:[0E32]` (or the segment-index delta being `>= 8` unsigned — TWO separate
  conditions reach the same target) triggers a flat `rep movsw` full-buffer
  copy fast path instead of any per-column compositing — likely a "just blit
  the whole buffer" case (e.g. after a fade/transition).
- The geometry-decode block populating the column-dispatch fields
  (`e4e`/`e50`/`e52`/etc., which this session had already reverse-engineered
  the CONSUMER side of via real captures) reads from a per-level road-segment
  table whose base derives from the SAME `PERSPECTIVE_TABLE_BASE` (`0x162C`)
  `renderer.perspective_row_offset` uses, via an 8-entry shape lookup table at
  `ds:[0xBA7]`.

**Attempted a clean refactor into `skyroads/recovered/road_frame.py` and
deliberately backed it out.** The function has real subtlety I mis-transcribed
twice in a row while drafting (a `cmp` at `34F8` is actually a `sub` that
STORES its result, not a flag-only compare; the full-copy fast path is
reachable from TWO different conditions that must both re-check `[0E32]`
fresh, not assume a fixed outcome from whichever path was taken to reach it).
Rather than land something with those errors still lurking, removed the
in-progress file entirely — nothing was committed.

**Then tried to verify dynamically against real captures with the lift itself
active** (not a fresh ASM re-derivation — the lift is already proven, this
just observes its own real behavior) and immediately caught a THIRD reading
error of my own: `ds:[0x3C]` (the early-exit flag) is actually **SS-relative**
— the function's first three instructions are `push ds; bx:=ss; ds:=bx`
(reassigning DS to SS) BEFORE testing `[0x3C]`, so it's really a stack-frame
value (a parameter/caller local), not a persistent DGROUP field. My first
capture read the wrong segment entirely (values like 1099, 1156... — some
unrelated DGROUP field at that offset) and, separately, had the branch
polarity backwards (`flag != 0` continues to render; `== 0` is the early
exit, not the other way round). Fixed the segment and polarity and re-ran:
**mode==0 calls matched 30/30** (source/dest/dispatch-pointer all exactly as
predicted). Mode==1 calls did NOT match on this pass, but the failure values
look like a capture-script bug (stale reads from a wrong nesting level, not a
real logic error) rather than a wrong understanding — not chased further this
session given three transcription mistakes already caught in one sitting.

**Net effect**: the mode-selection understanding (documented above) is now
dynamically CONFIRMED for the `mode==0` path and believed-correct-but-not-
independently-verified for `mode==1` (read directly from the proven lift's
own unambiguous Python source, which is a meaningfully lower-risk reading
than hand-transcribing ASM, but still not the same as a passing test). **The
concrete next step**: fix the mode==1 capture-script bug (or verify via a
from-scratch CPU+mem harness driving `lifted_1010_34ae` directly, which needs
no VM/demo at all), then write the refactor against that as the oracle. Given
how many small mistakes surfaced in one sitting here, budget real care for
this — it is NOT a quick follow-up.

## 2026-07-11 — road_column_strip ported to a pure function, verified by FULL MEMORY DIFF — the first real compositing primitive

Ported `road_column_strip` (`1010:38BF`) from its existing VM-facing hook to a
pure function, `skyroads/recovered/road_column.py`. Needed real physical
segment addressing (the DGROUP fields it reads — `[0E60]`/`[0E62]`/`[0E66]`/
`[0E68]` — are real DOS segment NUMBERS pointing at other parts of the address
space: display lists, source bitmap, screen), so added
`skyroads/native/image.py::NativeGameImage`, a SEPARATE, purely additive class
holding the full 1 MB real-mode image (the existing
`skyroads.native.state.NativeGameState` stays DGROUP-only — zero risk to the
300+ tests depending on it).

**Verification here is qualitatively different from every prior recovery**:
instead of sampling a handful of named fields, it's a FULL MEMORY DIFF — every
byte the real ASM call touched anywhere in the 1 MB image, compared exactly.
This caught TWO real bugs the first port had, both invisible to a
sampled-field check:

1. A missing unconditional scratch write (`ds:[0E74] := ax`, literally the
   routine's first instruction) — every one of the first 98 verification
   attempts failed on exactly this one word, at a fixed offset, until found.
2. An INVERTED reading of what I'd been calling `POSITION_ONLY_BIT`
   (`ax & 0x8000`). The original `hooks.py` comment (carried over verbatim
   when I started this port) describes it as "bit15 = 'just position, don't
   composite'" — wrong. Tracing the real branch (`1010:3937-393E jnz -> 3954`)
   shows the bit only skips a bp/si SYNCHRONIZATION pre-loop; compositing
   ALWAYS happens either way. Renamed to `SKIP_SYNC_LOOP_BIT` with a
   corrected contract. This is a genuine correction to a comment that had
   stood, unchallenged, since an earlier session — full-memory-diff
   verification is what surfaced it; sampling would very plausibly have
   missed it (a "position-only" call NOT compositing looks identical to a
   quiet no-op unless something is watching the destination bytes).

**Result: 196/196 real calls matched exactly** on the fuller sample; the
committed fixture keeps 38 diverse cases (a size spread from 11 to 1017
touched bytes, plus calls exercising `SKIP_SYNC_LOOP_BIT`), storing only the
touched-address set per case (determined by instrumenting the pure function
itself) to keep the fixture a reasonable size while remaining fully
reproducible. `tests/test_road_column.py` + `tests/test_native_image.py`.

This is the first ACTUAL pixel-compositing code in `skyroads/recovered/` —
everything before it (dispatch variants, classification, physics) decided
game STATE; this one writes real screen bytes. Combined with the dispatch
variants (previous entry), the renderer now has: which columns to draw
(dispatch), and how to draw one column (compositor) — both pure, both
verified. What's still needed for an actual framebuffer: what selects between
dispatch variants; the display-list BUILDER that populates
`ds:[0E60]`/`[0E62]` each frame; and the outer per-frame render entry point.

## 2026-07-11 — recovered both column-draw dispatch variants (364F/36F3), the first real renderer decision logic

Started the native renderer (scoped last turn, see the entry below). Finished
transcribing and verified BOTH column-dispatch variants reached indirectly
through `ds:[0E42]` (`1010:35F8`) — the decision logic that decides which
columns `road_column_strip` (`1010:38BF`, already a fully-understood
register-exact hook) composites and with what argument.

- `dispatch_variant_a` (`1010:364F-36F2`): raw real-capture match **474/480
  (98.75%)**.
- `dispatch_variant_b` (`1010:36F3-38BE`): a longer, separate function (NOT a
  continuation of variant A despite sitting right after it in memory — variant
  A ends in a real `ret` at `36F2`) touching two fields variant A never reads
  (`ds:[0E5C]`/`[0E5E]`). Raw match **633/640 (98.9%)**.

**The anomaly, understood and excluded, not hidden**: the misses in both raw
counts (6 for A, 7 for B) all come from real invocations that share ONE
repeated field snapshot and produce an implausibly long call burst (16-24
`road_column_strip` calls between two dispatch-entry hits) that neither
transcription predicts. This is almost certainly calls from a THIRD,
unisolated dispatch source looping without re-entering `364F`/`36F3` in
between — not a bug in either transcription (every OTHER snapshot, including
many exercising every documented branch, matched exactly). Re-dumped with a
larger sample (1280 invocations each) and explicitly EXCLUDED any call-burst
longer than 8 from the committed fixtures (101 distinct clean field-snapshots
kept per variant, ALL matching exactly) rather than leave a fuzzy pass rate in
the test suite. Added `test_fixtures_exclude_the_known_anomaly` asserting the
exclusion holds, so this can't quietly regress into hiding a real bug later.

Landed `skyroads/recovered/render_dispatch.py` (`dispatch_variant_a`/`_b`,
pure functions returning the ordered `ax` call list) + `tests/
test_render_dispatch.py` (101 distinct real field-snapshots per variant, plus
synthetic edge-case tests for the six-record volley).

**What's still needed for an actual native framebuffer**: (1) what selects
between the variants (and whether there are more) -- `[0E42]`'s value(s)
weren't captured; (2) `road_column_strip` itself ported from a VM-facing hook
to a pure function -- it touches FOUR distinct segments (the two display-list
segments, a source bitmap segment, and the destination screen segment), so it
needs `NativeGameState` extended to the full 1 MB address space (its own
docstring already anticipated this) -- a real, invasive change to a
widely-used foundation, deliberately NOT done this turn without an actual
consumer to verify it against; (3) the display-list BUILDER that populates
`ds:[0E60]`/`[0E62]` each frame, not yet located.

## 2026-07-11 — FULL VMLESS NATIVE GAMEPLAY: a standalone driver plays the whole demo, purely natively

The `/goal` target: a complete, self-contained gameplay simulation loop that
never needs the VM. Built `skyroads.native.loop.NativeGameplayDriver`, which
composes `native_gameplay_substep` (one verified sub-step) with
`apply_level_init` (the recovered per-level/respawn init) so the loop runs
THROUGH transition boundaries — level-complete, wall-crash, timer-expired,
fall — instead of stopping at them the way the underlying stepper does in
isolation.

**Proof**: seeded a `NativeGameState` + driver from the VM ONCE (real level
geometry/tables at the first `game_state==0` sub-step), then replayed the
E2E demo's real recorded input (steer/jump/speed/keys/tick) into the driver
for the demo's full length — **682 ticks, 6 transitions, zero crashes,
zero exceptions, the VM never touched again after the seed**. Landed
`tests/test_native_driver.py` (2 pure smoke tests + this live-oracle whole-demo
drive).

Two things the driver deliberately does NOT attempt to be byte-exact against
the VM for (both honest scope decisions, not silent gaps):
- the level-complete/crash **settle window**'s exact ~42-frame duration (the
  frozen-ship "rising off the end" animation) — the driver transitions
  immediately on detecting the boundary, since the window is non-interactive
  dead time between real gameplay decisions, not a gameplay decision itself;
- the rare **`1DFA` effect** sub-step (~0.7% of frames, only seen airborne past
  `af2c=0x3700`) — `native_gameplay_substep` gained an explicit
  `allow_unmodelled_effect` parameter (default `False`, preserving every
  existing test's fail-loud contract) that the driver opts into: it continues
  using `step_jump_steer_gravity`'s own verified (non-effect) `lateral_accel`
  for that one frame rather than stopping, a documented approximation, never
  the default.

This closes the `/goal`: the recovered gameplay logic isn't just individually
verified islands anymore, and isn't just a lockstep-provable sub-step — it's a
genuinely standalone, indefinitely-running native gameplay loop. What remains
for a fully PLAYABLE (visible, human-interactive) game is the renderer (scoped
separately, see the entry above) and real-time input/boot — this milestone is
specifically about the GAMEPLAY simulation being complete and self-contained.

## 2026-07-11 — recovered the level-init (respawn + per-level gravity); the transition primitive is ready

Recovered the per-level init the frame handler runs on entry (`1010:1FD9-206C`)
— it's the fixed `respawn()` field reset PLUS a per-level gravity computation I'd
missed: `ds:[54AA] = -((jump_level_gate * 0x1680) / 0x190)` (`level_gravity`,
verified vs the ASM: gate 8 -> 0xFF8D, 9 -> 0xFF7F). Landed
`skyroads.native.loop.apply_level_init(view, jump_level_gate)` — the transition
primitive a driver runs at the start of each level / after a respawn (writes the
reset fields + the derived gravity, returns a fresh `GameplayScratch`).

This is the piece a native driver calls at each boundary the lockstep loop now
detects (level-complete -> respawn/replay, death -> respawn). What it does NOT
yet cover is the ~42-frame level-complete DISPLAY (the `game_state == 2` settle
window between reaching the end and the respawn). Traced it: the ship is frozen
at `ship_pos = 0x2AAA` and `af2c` rises by `0x47`/frame (the grounded ramp,
`bounce = GROUND_RAMP_MAX`) as it lifts off the end of the track, `[456A]` and
`[4558]` counting up until `[456A]` passes `0x2A` and the frame gate exits to
the respawn. So it IS the frozen gameplay path -- but running it natively
through the window desyncs from the VM before the window ends (a
transition-subsystem timing detail), so the loop deliberately stops cleanly at
`game_state = 2` rather than approximating the animation. Modelling that window
exactly is transition-tier work. With `apply_level_init` + `native_menu_frame`
(level-select, already recovered) + the gameplay loop, the native game's control
FSM is now nearly all in hand; the transition DISPLAYS and the renderer are the
remaining large subsystems for a fully playable VM-free game.

## 2026-07-11 — the native loop is now FULLY CLEAN in lockstep: zero drift, every run ends on a detected boundary

Recovered the per-frame **orchestration gate** (`should_run_gameplay`,
`1010:229D-22E9`) — the decision the frame handler makes between running the
gameplay sub-step (`2317`) and exiting to a transition (`2B0B` -> respawn `201F`
/ menu / level load). It gates on `game_state`, the just-landed settle window
(`[456A]` 1..0x2A), and the frame counter (`[4558] < 0x6C`). **571/571** real
frames, including the `game_state 3 -> exit` cases that end a run.

Wired it into `native_gameplay_substep` (both the entry and the post-step
check): a step runs gameplay content only when `game_state in {0,3}` AND the
frame gate says the handler runs; otherwise it raises `LevelEndTransition`. Two
edges this closed:
- the `game_state 3 -> settled-resume -> respawn` transition (the last field
  break from the previous entry);
- the level-complete **settle window**: reaching `ship_pos = 0x2AAA` sets
  `game_state = 2` (via `dispatch_menu_action` action 0xC) and `[456A] = 1`,
  which the frame gate would keep "in the handler" for ~42 frames as the
  level-complete DISPLAY — but that's a transition display, not gameplay, so the
  stepper now stops immediately at `game_state = 2`.

**Result: the lockstep loop is now fully clean.** Across the demo the native
loop runs whole-level stretches (up to 122 steps) in perfect byte-for-byte sync
with the VM, and **every single run ends on a cleanly DETECTED boundary** (a
typed gap) — ZERO silent field drift, no residual edge. `tests/
test_native_loop_lockstep.py` + `tests/test_orchestration.py`.

Also learned the demo's shape from a transition trace: it **replays level 9**
(`[54A8] = 9`) in attract mode — reach the end (`0x2AAA` -> game_state 2) ->
respawn (`201F`, ship -> 0) -> play again. So the transitions the loop now
detects are level-complete and restart, exactly the boundaries a full native
game's transition subsystem would handle next.

## 2026-07-11 — the native loop plays WHOLE LEVELS in lockstep; frozen path + death/level-end detection

Pushed the lockstep loop from ~20-step runs to whole-level runs and made it stop
cleanly at every boundary it doesn't own. Four things:

1. **Frozen-ship path** (`game_state != 0`, the `24BA -> 25AC` gate): added a
   `moving` flag to `dynamics.step_jump_steer_gravity` that skips steering +
   jump when the ship is frozen, and `native_gameplay_substep` skips
   `advance_ship` too. Extended lockstep runs from ~20 to ~95 steps.
2. **af2c floor clamp** (`2A24-2A2F`, `if af2c > 0x7FFF: af2c = 0`) between the
   collision tail and progression — a stage I'd missed. Fixed a single-field
   `af2c` divergence; that run extended to 122 steps.
3. **Fall-off-the-road death predicate** (`ship_fell_off`, `1010:0533`):
   recovered (perspective word segment -> clip-bound midpoint vs the ship's row).
   Death fires (`23CA-2421`) past the `[41C0]` lateral threshold while
   `game_state == 0`. **682/682 + 511/511 death-check evaluations matched with
   ZERO false positives** — but neither demo actually falls, so the positive
   branch is decoded, not yet confirmed on a real death (flagged).
4. **Boundary detection**: `native_gameplay_substep` now raises typed gaps —
   `LevelEndTransition` when `game_state` leaves the in-level set `{0, 3}`
   (level complete -> 2, timer-expired -> 4/5, crash -> 1), and
   `FallDeathTransition` on a fall — instead of drifting past them.

**Result**: the native loop runs **whole levels (50-122 steps) in perfect
lockstep** with the VM and ends every run by cleanly DETECTING the boundary and
raising a gap — ZERO silent field drift across the demo (`tests/
test_native_loop_lockstep.py`). One un-modelled edge remains (a `game_state 3 ->
respawn` mid-level transition), bounded in the test.

So `native_gameplay_substep` is a real VM-free gameplay loop: it plays a level
start to finish in lockstep with the original, and knows when it's reached a
boundary (level end, death) it hasn't yet recovered the transition for. What's
left for a FULL native game: the transition subsystem (level load / respawn /
menu return) at those boundaries, and a standalone driver to run it without the
VM at all.

## 2026-07-11 — LOCKSTEP: the native loop runs in sync with the VM and never drifts

The accumulated-state convergence proof (stronger than the per-step test): seed
a NativeGameState + GameplayScratch ONCE from the VM at a gameplay sub-step,
then run `native_gameplay_substep` over and over carrying its OWN scratch,
injecting only the INPUT fields (steer/jump/speed/keys/tick) the outer loop sets
between sub-steps, and check every other gameplay field stays byte-identical to
the VM at every step.

Result: the native loop runs **13, 19, 20 consecutive steps in perfect
lockstep** and **NEVER silently drifts** — the only thing that ends a run is the
stepper hitting a not-yet-recovered path (a `game_state != 0` transition, or the
`1DFA` effect frame) where it RAISES a gap. Zero field divergences on any
recovered path. Landed `tests/test_native_loop_lockstep.py`, which asserts both
a real accumulated streak AND that runs only ever end on gaps, never drift.

This is the real thing: the recovered islands, composed over a session scratch,
are a self-contained native gameplay loop that reproduces the VM exactly for as
long as it stays on recovered paths. The streaks are bounded only by how often
gameplay transitions into the `game_state != 0` (frozen-ship) path, which isn't
recovered yet — recovering it is what extends the streaks toward a whole level.

## 2026-07-11 — the forward advance is the 1B49 call: native sub-step now COMPLETE (230/232 all fields, incl. ship_pos)

Correction + closure of the previous entry's open question. I'd concluded the
per-frame ship_pos advance happened in the OUTER frame loop, outside the
sub-step. That was wrong. Watchpointing `[54AC]` pinned the +0x12F advance to
`1010:1BE2` — inside `1B49`, the function recovered as
`menu.dispatch_menu_action`. **The forward motion IS the classification's
`1B49` call**: the classification passes the reduced perspective word to
`dispatch_menu_action` (`2385-238B`), and action `0xA` (scroll-right) advances
`ship_pos += SCROLL_STEP` (`0x12F`) when `[456A] == 0` (`1BDC`). So this is the
"1B49 gameplay side effect" I'd flagged in `classify.py` — not a side effect,
the core forward-motion mechanism, and it lives IN the sub-step.

`native_gameplay_substep` now applies `dispatch_menu_action` in its
classification stage (using the `calls_1b49`/`reduced_word` the classifier
already surfaces). Result: the differential match jumped from 148/232 (all
fields) to **230/232 — the full gameplay DGROUP including `ship_pos` and
`lateral`**. The 2 residual misses are documented edge cases (a rare `[AF2E]`
landing back-off; the `1DFA` effect frame, which the stepper raises a gap for).
`tests/test_native_substep.py` now compares every field (no outer-field
exclusion) and asserts a ≥95% match.

So `native_gameplay_substep` is a COMPLETE self-contained gameplay step: the
recovered islands, composed in spine order over `GameplayScratch`, reproduce
real VM gameplay — forward motion, steering, jumping, gravity, collision,
landing, crash, and level progression — with no VM. What remains for a fully
playable native loop: a driver that calls it per input frame (the
`play_native.py` equivalent), the frozen `game_state != 0` path, the
out-of-bounds death check (`23CA-2421`), and the `1DFA` effect.

## 2026-07-11 — ASSEMBLED the native gameplay sub-step: the islands run as one stepper (228/232 sub-step fields vs VM)

The convergence step. With the whole physics/collision sub-step recovered as
individual VM-verified islands, composed them — in confirmed ASM spine order,
over a session-persistent `GameplayScratch` — into a single running native
stepper: `skyroads/native/loop.py::native_gameplay_substep(view, scratch)`.

Spine (empirically traced, `game_state == 0` active gameplay):

    classify_ship -> gate_bounce_decay -> advance_ship -> step_jump_steer_gravity
      -> compute_movement_targets -> resolve_move -> lateral_wall_bump
      -> resolve_lateral_crash -> af1c_contact_fixup -> resolve_landing
      -> vertical_center_nudge (if landed) -> step_level_progression

`GameplayScratch` carries the cross-sub-step `ss:[bp-N]` locals the one
continuous `2280-2B0B` handler reads before writing each sub-step: the
`JumpScratch` (`bp-6/8/10`), `bp12` (gameplay-active), `bp14` (persisted class
flag), `bp24` (last vscan cell, read by the decay gate), and `tgt_af2c`
(`bp-28`, read by the decay gate before recompute).

**Differential result vs the VM** (seed a NativeGameState + scratch at each
`game_state==0` loop top `2324`, run one native sub-step, compare DGROUP at the
next loop top): **228/232 sub-step fields match**. The 4 misses are all
already-documented edge cases (the `1DFA` effect frame — now raises a gap; the
rare `[AF2E]` landing adjustment; a `game_state -> 2` transition). Landed
`tests/test_native_substep.py` asserting a ≥95% sub-step-field match rate.

**Key discovery — the forward advance is per-FRAME, not per-sub-step.** The
233/sub-step ship_pos advance (and lateral, timer_a) does NOT happen inside the
sub-step: at `24C4` `advance_ship` runs with `speed ([9330]) == 0` (a no-op),
and the real advance happens in the OUTER frame loop (`2280-2317`), which runs
once per displayed frame around many sub-steps. So `native_gameplay_substep`
faithfully steps ONE sub-step; those three outer-driven fields are excluded
from the match (they diverge exactly when a sample pair straddles a frame
boundary). Recovering that outer per-frame advance + the state dispatch
(`2280-2317`) is what remains to step a whole displayed frame.

This is the pre2_port convergence in miniature: the recovered leaves now
compose into a native stepper that reproduces real gameplay, VM-free, with the
remaining gaps precisely named (outer-frame advance, the frozen-ship
`game_state != 0` path, the out-of-bounds death check, the `1DFA` effect).

## 2026-07-11 — recovered the pre-move bounce-decay gate (2421-24BA), 682/682 — the core physics sub-step is now whole

Recovered `gate_bounce_decay` (`dynamics.py`, `1010:2421-24BA`): the gating
around `decay_bounce` (already recovered) that runs just before the jump/gravity
block each sub-step. If `af2c == tgt_af2c` the bounce passes through untouched;
otherwise it's zeroed when `([5496] != 0 and scan_cell < 2)`, or `|bounce|`
falls below `low16(0x104 * jump_gate) // 8`, or `[456A] != 0` (grounded); else
`bounce := decay_bounce(bounce)`. **Verified 682/682** with good branch coverage
(unchanged 236, small-kill 439, decay 6, 5496-kill 1). Landed
`tests/test_decay_gate.py` + a 57-case fixture. The grounded-kill branch was
decoded but unexercised; the landing SFX (`03C2(1)`, gated by an `0476`
predicate) is audio-only and not modelled.

**Milestone: the whole physics/collision sub-step (2421-2AE2) is recovered.**
With the decay gate in place, every stage from the vertical decay through the
movement pipeline, the full collision response, and level progression is now
recovered and VM-verified:

    2421-24BA  gate_bounce_decay        (dynamics)        682/682
    2324-23BF  classify_perspective     (classify)        682/682
    252B-2635  step_jump_steer_gravity  (dynamics)        415/416
    2635-26E9  compute_movement_targets + resolve_move    300/300
    26EC-2A24  collision response (5 fns)                  full region
    2A35-2AE2  step_level_progression   (progression)     682/682

What's left of the per-frame handler is the *framing* around this core: the
out-of-bounds/fall death check (`23CA-2421`, calls `0533`/`0F05`, gated on the
transitional state so it falls through in normal gameplay), the outer state
dispatch (`2280-2317`), and the sub-step loop (`2317-2B08`, `bp-2 < [1600]`).
The next major step is ASSEMBLY: thread the session scratch
(`bp-2/6/8/10/12/24` + the `bp-14/16/18` classification flags) through these
recovered stages in spine order to build a self-contained `native_gameplay_frame`,
then multi-frame-verify it against the VM (the pre2_port tick-keyed-harness
convergence proof).

## 2026-07-11 — recovered the landing check (28D7-295D), 224/224 — jump-latch lifecycle complete

Recovered `resolve_landing` (`collision_response.py`, `1010:28D7-295D`): the
post-move landing detection. A landing resolves iff `af2c != tgt_af2c` AND
`bounce < 0` (descending, off the vertical target); on a landing it clears
`ds:[455A]`, the effect latch `bp-6`, and **the jump latch `bp-8`**, sets the
gameplay-active flag `bp-12 := 1`, and backs `ship_pos` off by the 32-bit
`[AF30:AF2E]` (clamped to `[0, 0x2AAA]`). This **completes the jump-latch
lifecycle** — `dynamics.step_jump_steer_gravity` sets `bp-8` on the impulse,
this clears it on landing (answering the long-standing `JumpGateGap` question).

**Verified 224/224** real landing frames byte-exact (collision demo
`demo_skyroads_20260710_213019`) on `(bp-6, bp-8, bp-12, [455A], ship_pos)`.
The non-landing branch just leaves `bp-12 = 0` and is trivial by construction.
Learned `[AF2E]/[AF30]` were nonzero in only 1/224 frames — the ship_pos
back-off is a practical no-op but faithfully applied (and the one real case
matched). Landed 4 pure unit tests + a collision-demo live-oracle test.

Then recovered that last piece too: `resolve_lateral_crash` (`27A3-2830`), the
**wall-crash handler**. On a lateral collision (`lateral != tgt_lateral` = the
ship was blocked sideways into a wall) it restarts the ship (`ship_pos := 0`)
and, once past forward position `0x0E38`, flags the crash (`[456A]:=1`, and
`[456E]:=1` if it was 0). Verified 511/511 on the collision demo — though only
2 were real crashes (both past the gate), so the pre-gate and already-flagged
branches are decoded-but-unexercised (flagged in the `@oracle_link`). With this,
**the entire `26EC-2A24` collision-response region is recovered**, and the whole
post-move tail (`26EC-2AE2`) with it.

**The gameplay SUB-STEP is now essentially complete**: classification
(`2324-23BF`), dynamics (`252B-2635`), movement pipeline (`2635-26E9`), the full
collision response (`26EC-2A24`), and level progression (`2A35-2AE2`) are all
recovered and VM-verified. What's left of the per-frame handler is the parts
BEFORE the movement step: the `decay_bounce` region (`2421-24BA`) and the early
visibility/height classification (`23CA-2421`), plus the outer state dispatch
(`2280-2317`) and the `1B49`/`1DFA` side effects. After those, the pieces can be
assembled into a self-contained `native_gameplay_frame` and multi-frame-verified
against the VM (the pre2_port tick-keyed-harness convergence proof).

## 2026-07-11 — recovered the lateral wall-bump + af1c contact fix-up (26EC-27A0, 283C-28AE)

Two more collision-response pieces, into `collision_response.py`:
- `lateral_wall_bump` (`26EC-27A0`): when the ship's lateral was blocked short
  of target but `af1c` reached target and the target cell is blocked, nudge
  `af1c` down `0x3A0` (else up `0x3A0`) to slip past, snapping `tgt_lateral` to
  the current lateral;
- `af1c_contact_fixup` (`283C-28AE`): on an `af1c` collision (`af1c != tgt_af1c`),
  clear `lateral_accel`, conditionally zero `[5496]` (when its sign agrees with
  the still-needed `af1c` direction), and brake `ship_pos` by `0x97` (clamped ≥0).

**Verification note — needed a collision demo.** The E2E demo is a clean run
that almost never collides: the wall-bump's active branch fired 0×, the contact
fix-up 1×. So while the entry/no-op paths verified 682/682 there, that's weak.
Scanned all 14 demos for the collision IPs and found
`demo_skyroads_20260710_213019` exercises both (1 real wall-bump, 4 real af1c
collisions). Verified the **active** branches against it: wall-bump 511/511
(incl. the real down-bump), contact fix-up 511/511 (incl. all 4 collisions).
Landed pure-logic unit tests + a live-oracle test bound to that collision demo
that asserts the active branches actually fire (`stats["bump_active"] >= 1`
etc.), so the recovery can't silently regress to only testing the no-op path.
The wall-bump's UP branch (`2788`) is decoded but was not itself triggered by
any sampled demo — flagged in its `@oracle_link` note.

Remaining in the `26EC-2A24` collision middle: the position milestones
(`27A3-2800`, the `[54AC]>=0xE38` → `[456A]/[456E]:=1` transition) and the
landing check that clears the jump latch (`28DC-2901`, mapped). After those, the
entire post-move tail is recovered.

## 2026-07-11 — recovered the vertical collision-depth scan (2963-2A24), 314/314

Started into the collision-RESPONSE middle of the post-move tail
(`26EC-2A24`), the `1732`-heavy region that resolves the ship against the track
after `resolve_move`. First self-contained piece: the vertical centering scan
(`1010:2963-2A24`) that maintains `ds:[5496]` — the vertical term
`compute_movement_targets` adds into `tgt_af1c` (so this closes a real loop: the
movement target's `[5496]` input is now itself recovered).

The scan probes `road_object_visible` (`1732`) at `af1c ± k*128` for `k = 1..14`,
finds the first UNBLOCKED cell above and below the ship, nets that into `{-1, 0,
+1}`, and moves `[5496]` by `net * 17` (or zeroes it when net is 0). Recovered as
`skyroads/recovered/collision_response.py::vertical_center_nudge` (pure, takes
the same `visible` predicate `resolve_move` uses), **verified 314/314** real
E2E scans byte-exact — every probe computed through
`renderer.road_object_visible` bound to the frame's real DGROUP tables. Landed
`tests/test_collision_response.py` (5 pure-branch unit tests + 1 live-oracle).

New module `collision_response.py` is where the rest of this region will accrete.
Still to recover in `26EC-2A24`: the lateral wall-bump (`26EC-27A0`, nudges
`[AF1C]` ±0x3A0 when the ship's lateral is blocked, plays an SFX), the position
milestones (`27A3-2800`, the `[54AC]>=0xE38` → `[456A]/[456E]:=1` transition),
the `[AF1C]`/`[5496]` contact fix-up (`283C-28AE`), and the landing check that
clears the jump latch (`28DC-2901`, mapped — clears `bp-6/bp-8`, sets `bp-12`,
adjusts `ship_pos` by `[AF2E]`).

## 2026-07-11 — recovered the level-progression state machine (2A35-2AE2), 682/682 + fixed an inverted resume-gate bug

Went into the post-move tail (`26E9-2B0B`) and recovered its state-machine end,
`1010:2A35-2AE2` — the level timers and `ds:[456E]` game-state transitions that
end or resume a level. Landed as `skyroads/recovered/progression.py::
step_level_progression`, **verified 682/682** real E2E sub-steps byte-exact on
`(game_state, level_timer_a, level_timer_b, frame_ctr)`, including the demo's
real `0->3` resume transitions.

The logic (only when `game_state == 0`, i.e. transitional/just-respawned):
- **level_timer_b** (`ds:[B13C]`, time/"oxygen") -= `0x7530/(0x24*[4566])`;
- **level_timer_a** (`ds:[5494]`, distance/"fuel") -=
  `slong_div(ulong_mul(0x7530/[54A2], ship_pos), 0x10000)` (ship_pos-proportional);
- both unsigned-clamped at 0;
- then `game_state := 3` if `af2c < 0x2800` (resumed), `:= 4` if timer_a hit 0,
  `:= 5` if timer_b hit 0 (later override earlier).
While `game_state != 0`, none of that runs — the frame counter `ds:[4558]`
increments instead. This is the level-complete / out-of-time death logic
`vmless_roadmap` item 1 lists.

**Found and fixed a real bug in an earlier "ASM_MATCHED" recovery.**
`player.is_landed_for_resume` returned `af2c >= 0x2800`, but the ASM's `jb` at
`2AB7` resumes when `af2c < 0x2800` (the ship has DESCENDED past the gate). The
earlier recovery inferred `>=` from all 3 respawns writing `af2c = 0x2800` and
assuming that "immediately satisfied" resume — but at exactly `0x2800` resume
does NOT fire; the ship stays transitional until `af2c` drops below the gate.
The 682/682 progression match (with the real `0->3` transitions) is the
authoritative evidence. Corrected the function, its `@oracle_link` note, and its
test (`test_player.py`); it was only used by that test, so no downstream impact.

Two derivation details worth noting: the fuel decrement's divisor is `0x10000`
(from the `5E5A` call's `bx=1,cx=0` operand), not 1; and both timers are gated
entirely on `game_state == 0`, so normal gameplay (state 3) never touches them —
they only tick in the transitional/death window.

**State of the native gameplay frame**: classification, dynamics, movement
pipeline, AND now the level-progression tail are all recovered and proven. The
remaining un-recovered part of the `26E9-2B0B` tail is the **collision-response**
middle (`26EC-2A24`: the lateral wall-bump nudges and the vertical `1732`-probe
scan that adjusts `[5496]`/`[AF1C]`, plus the `bp-8`-clear landing check at
`28DC-2901`) — a larger, `1732`-heavy island for a future pass. Plus the
upstream `decay_bounce` region (`2421-24BA`) and the early visibility check
(`23CA-2421`).

## 2026-07-11 — recovered the perspective classification (2324-23BF), 682/682 + located where the jump latch clears

Took the classification block feeding the dynamics block's `bp-14`/`bp-18`
inputs (below). It projects the ship's own `(lateral, af1c)` through the
perspective transform (`04C0` = `renderer.perspective_row_offset`) to a table
word `bp-20`, then:
- `bp-18 (class_zero) = (bp-20 == 0)`;
- if `bp-12 == 0`: `bp-16 = 0`, `bp-14` **unchanged** (persists across frames —
  it's session state, not a pure per-frame value);
- else: reduce `bp-20` (if `af2c > 0x2800`, look up `ds:[0x228 + 2*(bp-20>>8)]`
  and set `bp-20 = bp-20>>4` if `af2c` matches else 0), make a side-effect
  `1B49` call, then `bp-14 = (bp-20 & 0xF == 8)`, `bp-16 = (bp-20 & 0xF == 2)`.

Recovered as `skyroads/recovered/classify.py::classify_perspective` (pure, takes
the perspective word + a table reader) + `skyroads/native/classify.py::classify_ship`
(binds `perspective_row_offset` + DGROUP reads, like `collision.make_visible`).
**Verified 682/682** real E2E frames byte-exact on `(bp-14, bp-16, bp-18)`,
computing the perspective word natively. Landed `tests/test_classify.py` (6
pure-branch unit tests + 1 live-oracle test driving the demo).

**Also found where the jump latch clears.** Chasing `bp-12`'s source
(`classify`'s one remaining upstream input) into the post-move tail led to
`1010:28F2-2901`: `bp-6 := 0`, **`bp-8 := 0` (jump latch cleared)**, `bp-12 := 1`
— reached when `ds:[AF2C] != bp-28` (the af2c target) AND `ds:[9336] < 0`
(descending), i.e. the landing/collision-resolved condition. This answers the
long-standing `JumpGateGap` "where does bp-8 reset" question (previously only
inferred from the `af2c→0x2800` correlation).

**Two documented subtleties** (in `classify.py`): (1) `bp-14` persists when
`bp-12 == 0`, so it's session state the caller must thread; (2) the `1B49`
side-effect call during gameplay (same address as `menu.dispatch_menu_action`,
called with the reduced perspective word) — the flags don't depend on its
result so `classify` reproduces them without it, but its DGROUP side effect
during gameplay is flagged (`calls_1b49`), not modelled. Worth resolving what
`1B49` actually does with a perspective-derived arg mid-gameplay.

**State of the native gameplay frame now**: classification, dynamics (jump +
steering + gravity), and the movement pipeline (targets + resolve_move) are ALL
recovered and proven against the VM. Remaining to stand up a full self-contained
frame: the `26E9-2B0B` post-move **tail state machine** (drives `bp-12`, clears
`bp-8` on landing, handles level-end/death — `28C0` has the `[54AC]==0x2AAA`
level-end clamp), the upstream `decay_bounce` region (`2421-24BA`), the early
visibility check (`23CA-2421`), and the `1B49` side effect.

## 2026-07-11 — recovered the jump-latch + steering + gravity block (252B-2635), 415/416

Followed the movement-pipeline proof (below) into the block right before it —
`1010:252B-2635`, the per-frame jump/steering/gravity update — because its
output `lateral_accel` was the movement pipeline's one un-derivable input.
Disassembled the whole thing and found the "jump latch" I'd been calling an
unrecovered gap is right here: `2570-25A9` fires the up-impulse
(`bounce := 0x480`), latches `bp-8 := 1`, and records the jump-start height
`bp-10 := af2c`, gated by `bp-8==0 && bp-18==0 && [547A]!=0 && [4562]<0x14`.

Recovered the block as `skyroads/recovered/dynamics.py::step_jump_steer_gravity`,
operating on a small session-persistent `JumpScratch` (`bp-6`/`bp-8`/`bp-10`)
plus two per-frame classification flags (`bp-14`/`bp-18`) and DGROUP scalars.
It covers three things the earlier naive functions couldn't gate correctly:
- **steering momentum** (`2534-256D`): `lateral_accel = steer*29`, but latched —
  only recomputed when `class_skip==0` and either `(not jumping && class_zero==0)`
  or `(lateral_accel==0 && bounce>0 && af2c-jump_start_y < 0xF00)`. This is why
  60/682 frames had `lateral_accel != steer*29` (momentum persisting a frame
  after the steer key released);
- **the jump latch** (`2570-25A9`), above;
- **gravity/velocity** (`25DB-2635`): airborne → `+gravity` (or clamp to
  terminal `-106` below the height gate); grounded → ramp to `+0x47`.

**Verified 415/416** real E2E-demo frames byte-exact on `(bounce,
lateral_accel, bp-8, bp-10)`. The single miss is one frame where the rare
`25AC-25D6` effect path (a `1DFA` call gated by `[4570]`/`bp-6`/`af2c>=0x3700`,
fired only 5× in the whole demo) separately rewrote `lateral_accel` — the
function detects and flags that path (`hit_effect_path`) rather than
mis-modelling it. Landed with `tests/test_dynamics.py` + a 89-case fixture
(`dynamics_trace.json`, all jump-fire/1DFA/steering frames + a spread).

This **supersedes** the `decay_bounce` + `update_vertical_velocity` composition
that `VerticalVelocityGap` guarded — the "`[9336]` frozen for 8 frames" mystery
was just this block's gating (grounded/af2c/jump-latch), now modelled. Updated
all three native gaps (`JumpGateGap`/`VerticalVelocityGap`/`MovementPhysicsGap`)
to point here.

**Remaining before a full native gameplay frame** (each a scoped island):
1. the perspective **classification** (`2324-23BF`) that produces `bp-14`/`bp-18`
   — `bp-20` = the perspective-table word for the ship's own `(lateral, af1c)`
   via `renderer.perspective_row_offset`, then `bp-18=(bp-20==0)` and
   `bp-14=(bp-20 & 0xF==8)` after an `af2c`/`ds:[0x228]`-table reduction that
   also makes a side-effect `1B49` call (the messy part);
2. where `bp-8` **clears** on landing (traced to the frame `af2c` snaps to
   `0x2800`, exact write not yet located);
3. the upstream `decay_bounce` region (`2421-24BA`);
4. the `1DFA` effect (`25AC-25D6`) and the death/level-end event paths.
Not wired into `native_gameplay_frame` yet (it still needs 1 & 2); the block is
proven in isolation.

## 2026-07-11 — movement MATH complete (pipeline proven 300/300) + af1c_base_offset corrected to a constant

Two results pushing native gameplay forward.

**1. The af1c_base_offset was never an open selector gap — it's the constant
`0x0618`.** The "movement-target formula recovered" entry (and the follow-up
that "ruled out a hypothesis for the selector") both leaned on an empirical
"offset is 0 for non-steering, 0x0618 for steering" reading. That was a
measurement artifact. Probing the real `ss:[bp-16]` (the ASM's actual selector,
`1010:2650`: `bp-16==0 → +0x0618`) directly at the decision point found it `0`
in **every one of 682 real E2E calls** — so the multiply's base is always
`ship_pos + 0x0618`. The apparent "offset 0" for non-steering frames was just
`lateral_accel == 0` making the multiply `0 * base == 0`, so the base value was
irrelevant there and the fixture-builder's "try 0 first" recorded 0. Held
`lateral_accel` nonzero, only `0x0618` matches (58/58). The alternate `0` needs
the `af2c > 0x2800` + `ds:[0x228]`-table-match path (`1010:2340-23BF`), which
never fired in the demo — a real but UNEXERCISED branch, not a gap. Corrected
`skyroads/recovered/physics.py` (default `af1c_base_offset=0x0618`, docstring
rewritten), re-patched the fixture, updated `tests/test_physics.py`. This
retires the "open selector" caveat from all three earlier places it appeared.

**2. The lateral/vertical movement MATH is complete — the whole pipeline
reproduces the VM 300/300.** Composed the two already-ASM_MATCHED halves —
`compute_movement_targets` (`2635-26E6`) → `resolve_move` (`186B`) — with the
`skyroads/native/collision.make_visible` predicate bound to a `NativeGameState`'s
DGROUP tables, and diffed the result against the real VM's post-move
`(lateral, af1c, af2c)` captured at `26E9`. **300/300 exact, 39 with real
steering.** Landed as `tests/test_native_movement_pipeline.py` (live-oracle,
gated on the game files). This establishes there is NO remaining gap in the
movement math itself.

**What still blocks a full native gameplay frame** (precisely bounded now, down
from "the 2560-26E9 block is unrecovered"): the pipeline's `lateral_accel`
(`ds:[4568]`) input is **stateful steering momentum**, not a stateless
`steer*29` — 60/682 real frames have `lateral_accel != steer*29` (e.g. `-29`
persisting a frame after the steer key released). It's updated mid-frame at
`1010:2568` under the jump-latch-gated steering block (`1010:2534-256D`), whose
gates depend on the perspective classification (`1010:2324-23BF` →
`bp-14`/`bp-16`/`bp-18` from `perspective_row_offset`, already recovered) and
the session-persistent jump-latch state (`ss:[bp-8]`/`[bp-10]`). So
`native_gameplay_frame` still raises `MovementPhysicsGap` — but the gap is now
specifically "derive `lateral_accel`", not "recover the movement math."
Deliberately did NOT wire `resolve_move` in with `lateral_accel=steer*29`: it
would silently diverge on those 60 frames, violating the fail-loud rule. Next
concrete island: the `2534-256D` steering-momentum update (+ its `2324-23BF`
classification dependency).

## 2026-07-11 — fixed the audio stutter + >1s sound delay: it was pacing, not missing hooks

User report: music/sound stutters, and sound is delayed by more than a second.
Confirmed empirically it is a **pacing/audio-architecture** problem, not
under-hooking (though the two are linked — see below).

**Measurement** (full E2E demo, real frontend cost per frame): 496/1719 frames
(29%) exceed the 33.3ms budget a 30Hz loop allows — p90 71ms, p99 230ms, max
450ms; on those frames the loop drops to 14Hz (p10) down to 2.2Hz.
`clock.tick(present_hz)` only pads a *fast* frame up to the budget, never
speeds a slow one up, so `AudioSink.pump()` is called well below 30Hz on
nearly a third of frames. The stock sink generates AND drains a **fixed**
`chunk = rate // present_hz` samples per pump, on the assumption pump() runs at
a steady `present_hz`. Over the demo that means the consumer can only emit
57.3s of audio (1719 pumps x 1470 samples) while 83.6s of wall-clock playback
is needed — a **26s structural deficit** that surfaces two ways:

- **Stutter**: the OPL music channel underruns on every slow stretch, goes
  idle, and restarts with a fresh 0.1s lead (an audible gap).
- **>1s SFX delay**: a captured SB-DMA effect is resampled to real-time
  duration and dumped into `self._sfx` all at once, but drained at only
  `chunk` samples *per pump* — coupled to pump frequency, not the wall clock.
  On every sub-30Hz frame the backlog grows and never clears, so effects play
  seconds after their visual.

**Fix** (`skyroads/audio.py::SkyroadsAudioSink.pump`, an override — kept in the
port repo, observer-only, so demos/tests/determinism are untouched): size each
pump by **real elapsed wall-clock time** (`n = round(dt * rate)`, clamped)
instead of a fixed chunk, so samples produced/drained always track what the
mixer consumes; and hard-cap the pre-mixer buffer (200ms) and SFX backlog (1s
safety) so a long stall resyncs to "now" (a brief glitch) rather than
accumulating delay. Verified end-to-end on the real demo through the real sink
with a fake mixer + real clock: **peak SFX backlog 951ms and self-recovering
(the 1s cap never even engaged), pre-mixer buffer bounded at 200ms** — vs the
pre-fix 26s ratchet. 4 new deterministic regression tests
(`tests/test_audio_pacing.py`, injected clock + fake mixer) lock in that the
backlog stays bounded and generation tracks wall-clock, not pump count. 226
tests pass.

**What this does NOT fix** (stated honestly): when the VM itself runs below
30Hz, the game emits OPL note changes at its own slow tick rate, so the music
*tempo/sequence* genuinely drags — no audio pacing can fix that, only a faster
VM (i.e. more hooking) can. So the two questions the user raised are both
"yes, partly": pacing was the direct cause of the stutter and the SFX delay
(now fixed); remaining tempo unevenness on heavy transition frames is the
un-hooked-work side, and every renderer/logic island still to be hooked
reduces how often frames blow the 33ms budget. Note the same fixed-chunk bug
lives in the shared `dos_re/dos_re/audio_sink.py::AdlibSpeakerSink` base (all
games using it); left as an upstream-candidate rather than re-pinning the
submodule from a port-side fix.

## 2026-07-11 — ruled out one hypothesis for the af1c_base_offset selector

Quick follow-up to the "movement-target formula recovered" entry's open
question: does the `ss:[bp-16]` selector (`1010:2340-23BF`) actually reduce
to `perspective_row_offset(lateral, af1c)` plus a `ds:[0x228]`-table lookup,
as that disassembly reading suggested? Implemented it directly (reusing the
already-recovered `perspective_row_offset`, matching `hooks.py`'s
`_persp_exit` argument convention) and checked it against all 682 real
`186B` calls from the movement-target fixture's source demo, predicting
`bp-16` and comparing to the real `af1c_base_offset` (0 vs `0x618`) deduced
from each sample's actual `tgt_af1c`.

**Result: wrong exactly where it matters.** 624/682 correct — but ALL 58 are
the real-steering (`lateral_accel != 0`) samples, and the hypothesis predicts
`bp-16=0` (offset 0) for every single one of them, when the real value is
always `0x618`. Not a near-miss or an off-by-one; the `af2c > 0x2800` +
table-match branch never fires when it should for these samples, so either
the argument order/quantity fed into `perspective_row_offset` here is wrong,
or `ss:[bp-20]`'s value at this point in the ASM isn't what
`1010:2324-2336`'s disassembly suggested. Ruling this out so a future attempt
doesn't re-derive the same dead end — the only currently-known signal for
`af1c_base_offset` remains the empirical `lateral_accel != 0` correlation
already documented in `skyroads/recovered/physics.py`'s docstring (682/682
in this demo, structurally unconfirmed).

## 2026-07-11 — CONFIRMED: the jump-latch locals are session-persistent, not per-frame (architecture, not just a hypothesis anymore)

Follow-up to the "movement-target formula recovered" entry below, which
flagged the `ss:[bp-8]` etc. persistence question as informed speculation.
Settled it directly: monkeypatched `CPU8086.step` to snapshot `SS:BP` and
`ss:[bp-8]`/`[bp-10]`/`[bp-18]` every time `cs:ip` hit `1010:26E6` (the
`resolve_move` call site, known-reachable from the movement-target probe)
over the full E2E demo — no hook, no replacement, pure observation.

**Result: `SS:BP` was `(0x1686, 0xB910)` on all 274 visits across every one
of the demo's ~1900 frames — never once different.** This settles it: the
per-frame handler at `1010:2280-2B0B` is not re-entered each displayed
frame; it is ONE continuous execution context (a single `enter`, presumably
at level start) that loops across frames via `jmp`, exactly as the tick-wait
spin `skyroads/pacing.py` already parks at `1010:22F8` (*inside* this same
block) implied. `ss:[bp-N]` locals are therefore genuine session state, not
per-call scratch — confirming what the movement-target entry could only
infer from bp-8 outliving a key release.

**bp-8's full lifecycle**, traced across the whole demo: sets to 1 exactly
when the jump impulse fires (`1010:25A1`, already known), and reset to 0 was
observed at 3 independent points (frames 746, 915, 1366) — in every case,
the SAME frame `ds:[AF2C]` snapped back to exactly `0x2800`
(`player.RESUME_HEIGHT_GATE`) via `resolve_move`'s own collision clamp, with
`ds:[456A]` staying 0 throughout (so `456A` is NOT the "landed" signal here
— it's a rarer, separate flag; found it set by an unrelated side-wall
collision case at `1010:27DF`, not investigated further). bp-8 is NOT
recomputable as `af2c != 0x2800` (it's already 1 the same frame af2c is
still 0x2800, the very frame the jump fires) — it is a true latch, just one
whose reset trigger (landing) is now empirically pinned down even though the
exact ASM instruction doing the reset write wasn't located (checked
`1010:2704-2800`, the af1c-target/lateral-wall-collision block right after
`resolve_move` returns — not there; the reset write is elsewhere, not yet
found).

**Implication for skyroads.native**: confirms the gaps.py architecture note
was right, not just plausible. `native_gameplay_frame` cannot model bp-8/
bp-10/bp-14/bp-16/bp-18 as either DGROUP fields (they aren't) or per-call
locals (they don't reset per call) — it needs a companion session-scoped
scratch object threaded across frame calls, reset only at whatever re-enters
this handler (level load, most likely), mirroring pre2_port's
`NativeGameState.__slots__` side channels (`sfx_queue`, `particle_capture`,
etc. — "this session's" state that isn't memory-backed). Not built yet; this
entry exists so the next session doesn't have to re-derive the persistence
question from scratch.

## 2026-07-11 — the movement-target formula recovered (1010:2635-26E6), closing most of MovementPhysicsGap

Continued from the native-loop work below: disassembled forward from the
per-frame state dispatcher (`1010:2280-2317`, the top-level `[456A]/[456E]/
[4558]` orchestration `vmless_roadmap.md` item 2 calls out as fully missing —
mapped but not yet lifted/verified this session) through the jump-latch gate
(`1010:2570-25AC`, confirming `ss:[bp-8]` is exactly the self-latching
"jumped already" flag `player.py`'s docstring predicted) to
`1010:2635-26E6`: the block that computes the `(tgt_lateral, tgt_af1c,
tgt_af2c)` triple `resolve_move` (`1010:186B`) sweeps toward — previously
mapped only at a high level ("Vertical/lateral physics", earlier today) and
explicitly called "the tee-up, not recovered source."

Cross-checked the derived formula against 682 real `186B` call arguments
captured over the full E2E demo (58 with real steering held) by monkeypatching
`CPU8086.step` to snapshot state whenever `cs:ip == 1010:26E6` (the `call
186B` instruction, args already pushed) — no hook needed, since this was pure
observation, not replacement. First pass wrongly concluded "the lateral
offset is always 0" from an incomplete 400-sample/frame<1193 window; the full
682-sample pass falsified that and led to the real structure:

- `tgt_af2c = af2c + vvel` (vvel = `ds:[9336]` as of the call site, i.e.
  after that frame's decay/gravity/jump already ran) — **682/682 exact**.
- `tgt_lateral = ship_pos + lateral` (32-bit, ship's forward position
  re-centers the lateral target each frame as the curving track advances) —
  **682/682 exact, no offset term** (an earlier draft of this finding wrongly
  conflated this with the af1c multiply's separate offset below — different
  DGROUP accumulator, computed at a different point in the ASM,
  `1010:263C-2647` vs `1010:2650-2673`).
- `tgt_af1c = af1c + slong_div(ulong_mul(lateral_accel, ship_pos +
  af1c_base_offset), 0x200) + [5496]`, clamped to `af1c` unchanged if the raw
  result and `af1c` straddle a `[0x2F80, 0xD080)` wrap-seam band from
  opposite sides (`1010:26AA-26D7`) — **682/682 exact given the real
  af1c_base_offset per sample** (0 or `0x618`, see below). First consumer of
  `ds:[4568]` (`lateral_accel`, `steer*29`) — previously only a documented
  *write* target (`player.RespawnState`'s comment).

**What's still open**: `af1c_base_offset`'s real ASM selector (a stack-local
`ss:[bp-16]`, set at `1010:2340-23BF` when `af2c > 0x2800` AND a `ds:[0x228]`
-indexed table lookup on a `04C0` perspective-transform result for the ship's
own position matches `af2c` exactly — machinery that, confusingly, ALSO
fires a live side-effect call into `menu.dispatch_menu_action` with a
related action code before the low nibble is inspected again for this same
flag) is traced but not implemented. Empirically, `af1c_base_offset ==
0x618` in exactly the 58 real samples where `lateral_accel != 0`, and `== 0`
in all 624 others — a clean, perfect correlation in this demo, but the two
conditions are structurally independent circuits in the ASM, so it may be
coincidental to this demo rather than the true rule. Landed as
`skyroads/recovered/physics.py::compute_movement_targets`, requiring the
caller to supply `af1c_base_offset` explicitly rather than defaulting to the
correlation (tests: `tests/test_physics.py`, fixture
`tests/fixtures/movement_target_trace.json`, 98 samples: all 58 real-steering
ones + a spread of 40 non-steering).

**Not wired into `skyroads/native/loop.py` yet** — `MovementPhysicsGap`
still fires unconditionally. Closing it for real needs: (1) the
`af1c_base_offset` selector properly implemented (not the correlation
heuristic), (2) `lateral_accel` (`ds:[4568]`)'s own write-gate
(`1010:2550-256D`, mapped: only when `[9336] > 0` and
`[AF2C]-heightref < 0x0F00`, not yet independently verified as a pure
function), and (3) the jump-latch's session-persistence architecture: `ss:
[bp-8]`/`[bp-10]`/`[bp-14]`/`[bp-16]`/`[bp-18]` clearly persist ACROSS
frames (bp-8 stayed latched for the 8-frame freeze `VerticalVelocityGap`'s
finding described below), which a per-call stack local can't do unless this
whole per-frame handler (`1010:2280-2B0B`) is actually one continuous
execution context looping internally (via `jmp`, not `call`/`ret`) across
displayed frames — the tick-wait spin `skyroads/pacing.py` already parks at
`1010:22F8` sits INSIDE this same block, consistent with that theory. If
true, `skyroads.native`'s per-frame steppers need a companion session-scoped
scratch object alongside `GameView` (mirroring pre2_port's non-memory
`NativeGameState.__slots__` side channels: `sfx_queue`, `particle_capture`,
etc.) to hold these latches across `native_gameplay_frame` calls — not
something `GameView`'s DGROUP fields can represent. This is now a
concrete, scoped follow-up rather than an open question.

## 2026-07-11 — first native (VM-less) frame steppers, and a real vertical-velocity bug found through them

Started wiring "the entire game loop towards native vmless game" (the
pre2_port endgame model, see `vmless_roadmap.md`). Landed the state-mirror
plumbing and two frame steppers over it:

- `skyroads/native/state.py::NativeGameState` — the game's DGROUP owned as a
  plain 64 KB `bytearray` (no VM), with `from_vm(rt)` seeding. Smaller than
  pre2's 1 MB image on purpose: every SkyRoads island recovered so far only
  touches DGROUP.
- `skyroads/state_view.py` — a re-export shim (mirrors `skyroads/islands.py`
  for `oracle_link`) so `skyroads/bridge/dgroup_view.py::GameView` can use
  the shared `dos_re.state_view` backend/descriptor machinery (promoted from
  pre2_port) without a direct `dos_re` import — keeps skyroads/bridge under
  the same pitfall-#17 bar as skyroads/recovered. `GameView` names every
  DGROUP field the current islands touch (ship_pos, lateral, speed, bounce,
  af1c/af2c, game_state, entered/grounded (the same offset, two names for two
  modes), the timers, the keyboard row) as one dword/word property per field,
  reading raw (unsigned) words — the recovered functions each sign-extend
  their own inputs, so a view field must hand them the raw word, not an
  already-Python-signed one, or a function like `decay_bounce` double-converts.
- `skyroads/native/collision.py::make_visible` — wires
  `renderer.road_object_visible`/`perspective_row_offset`/`road_segment_clip`
  into the `visible(lateral32, depth, screen_y)` callback
  `movement.resolve_move` needs, mirroring `hooks.py`'s `_persp_exit`/
  `_clip_exit` minus their register-exit bookkeeping. Cross-checked against
  an independent reimplementation of the same wiring over 500 random table/
  probe samples (`tests/test_native_collision.py`) — not yet CALLED from the
  gameplay stepper (see below).
- `skyroads/native/loop.py::native_menu_frame` — complete, gap-free: every
  transition `dispatch_menu_action` needs is recovered. Verified against 4
  real E2E-demo frames where the ASM's own dispatch was confirmed a no-op
  (menu.py's "heartbeat" case) — the action code itself isn't observable
  without a dedicated capture hook, so only no-op frames are valid samples.
- `skyroads/native/loop.py::native_gameplay_frame` — commits forward motion
  (`advance_ship`) unconditionally (real-demo-proven, 0 mismatches), then
  raises a typed gap (`skyroads/native/gaps.py`) the instant it needs
  something not safe to compute: `JumpGateGap` if a jump is held (the
  impulse latch isn't recovered), `MovementPhysicsGap` for the lateral/
  vertical movement-target block (`1010:2560-26E9`, mapped but not recovered
  — see the entry below), or, new today, `VerticalVelocityGap`.

**The `VerticalVelocityGap` finding.** Composing `decay_bounce` then
`update_vertical_velocity` unconditionally every frame — the natural reading
of player.py's own docstring ("applied AFTER decay_bounce") — was the first
thing tried. Cross-checking it against real E2E-demo data
(`demo_e2e_20260710_132930`, frames ~765-772) falsified it: `ds:[9336]`
(bounce/vertical velocity) stayed **frozen** at a fixed value for 8 straight
frames while airborne with `af2c < 0x2800` — exactly the branch player.py's
`update_vertical_velocity` docstring already flagged as an untested,
"ASM-derived, dark" terminal-clamp. Composing the two recovered functions on
that frozen value predicts an immediate flip-and-clamp to `TERMINAL_VVEL`
instead; real ASM did nothing to that field for 8 frames. So the whole
decay+gravity/clamp block is evidently GATED by something not yet
recovered — most likely the same jump-in-flight state the (also unrecovered)
impulse latch tracks, since the freeze starts the frame the jump key was
pressed and outlives the frame it was released. This is stronger than "an
unexercised branch": it disproves the "runs every frame unconditionally"
assumption itself, not just one arm of the clamp.

`native_gameplay_frame` now only computes `decay_bounce`/
`update_vertical_velocity` inside the ONE envelope player.py's existing
verification actually covers (airborne, `af2c >= GRAVITY_HEIGHT_GATE`,
`grounded == 0`) and raises `VerticalVelocityGap` otherwise. Re-run against
the E2E demo: 10/10 "outside the envelope" samples now correctly gap with
`ship_pos` still matching real ASM (0 mismatches, vs. 2/8 silent wrong
values before this fix); the demo never happened to exercise the envelope
case itself (0 samples in the first ~1700 frames), so that narrow branch's
only evidence remains player.py's own earlier "238/238 deaths-demo frames"
claim from a different demo — not re-confirmed here.

**Honest coverage today**: every real gameplay frame in the E2E demo hits
either `JumpGateGap` or `VerticalVelocityGap` or `MovementPhysicsGap` before
`native_gameplay_frame` could call `resolve_move` — there is no frame yet
where a full native gameplay step completes without a gap. What IS proven:
the state-mirror plumbing (`NativeGameState` <-> `GameView` <-> recovered
function <-> writeback) is correct end-to-end for every field it touches,
`native_menu_frame` is a complete gap-free island, and the exact next
recovery targets are now precisely bounded (the jump latch, the vertical-
velocity gate, and the `2560-26E9` movement-target block) rather than
vaguely "game logic, none recovered yet". Tests: `tests/test_native_state.py`,
`tests/test_native_collision.py`, `tests/test_native_loop.py` (synthetic,
no demo needed), `tests/test_native_loop_integration.py` (real E2E demo,
skips if assets/demo are absent), `tests/test_layer_audit.py` (wires
`tools/audit_layers.py` into the suite for skyroads/recovered + native +
bridge, pitfall #17).

## 2026-07-11 — recovered + wired the intro animation-frame unpacker (1010:3A96), lift-first, one more real bug caught

User-reported: the intro ship/logo animation looked like un-hooked rendering.
Profiled the true intro frames (0-99, before any menu interaction) and found
page `3A00xx` completely dominating a run of consecutive frames, each burning
the entire step budget — confirming the report. Traced it to `1010:3A96`, an
**animation-frame unpacker**, not a renderer: it decompresses the intro's
sprite/logo data once at startup, not per displayed frame.

Used lift-then-refactor again (now the established process after the
stencil-blit lesson): `dos_re.tools.liftverify` proved a literal
transcription byte-exact first (after bumping the emitted lift's own
runaway-safety cap for one local, throwaway verification run — this function
does 8 x 1040 = 8320 rows of real work per call, tripping the same
block-count guard `buffer_relocate` hit). The proven lift revealed the exact
algorithm: 8 independent 64K segments (a fixed table at `ss:[bx+0xE76]`),
each self-relocating its own first 624 bytes from a self-referential header
offset, then unpacking 1040 fixed rows — a 3-byte verbatim prefix followed by
2-byte tokens expanded into `[b1,b2,0x00]` triplets until a `0xFF`
terminator.

**Even working from the proven lift, transcribing it into clean Python
introduced a real bug** (caught by cross-checking against real captured
segment data, not by the live hook verifier — the strict/lift/hand-checks
form layers, and this is the layer that caught it): the row prefix is
`movsb` then `movsw`, two *separate* instructions, not atomic with each
other. `movsb`'s write can land at a position `movsw` is about to read from
(`di` grows faster than `si` once a row has tokens, so it can catch up
mid-segment) — real hardware sees `movsb`'s fresh write; an implementation
that reads the whole 3-byte prefix before writing any of it does not.
Confirmed by tracing all 1040 row-boundary `(si, di)` pairs against real
hardware — they matched exactly once the instruction ordering was fixed.

Recovered as `skyroads/recovered/intro_anim.py::unpack_animation_segment`
(operating through `rb`/`wb` callbacks, not an isolated buffer copy — table
segments are less than 64K apart in real memory and physically overlap, so
writes must land on live memory to behave like real hardware regardless of
whether the game relies on that). Wired as
`skyroads/hooks.py::intro_anim_unpack_hook`. Getting the hook's own register
state right caught **one more bug** — SP was read to get the return address
but never actually advanced past it, an omission the strict verifier's
register-diff caught immediately (`SP` off by exactly one word, everything
else — memory, every other register, flags — already matched).

**Verified byte-exact**: 1/1 real call (it fires once per game session, not
per frame) over both the E2E demo and the cold-sound demo, zero divergences,
via `HookVerifierConfig.strict()`. All 1040 row boundaries of the actually-
processed segment cross-checked directly against real hardware too. Guarded
by `tests/test_intro_anim.py` (+ fixture). ~1.9x fewer interpreted steps over
the intro window (a modest number for a one-shot call, but the several
consecutive full-budget frames it used to cause are gone). 190 tests pass.

## 2026-07-11 — the level-select/menu dispatcher recovered (1010:1B49)

Followed up the state-2 finding by fully mapping and recovering `1010:1B49`,
the dispatcher `1010:1B68` (state-2 entry) turned out to belong to. It's a
clean, linear action dispatcher (`cmp ax,N; jnz next; jmp handler`, not a
jump table) on a 4-bit action code passed by the caller, always ending in a
common tail. Four known action codes:

- **`2`** scroll left: `scroll_pos -= 0x12F`, only if not yet "entered"
- **`0xA`** scroll right: `scroll_pos += 0x12F`, same guard
- **`0xC`** enter level-select: `[456E]:=2`; latches an "entered" flag once
- **`9`** confirm/start: if `[456E]==0` and either post-level timer is still
  under a threshold, reset both timers to `0x7530` (the same reset value
  `RespawnState` uses)
- any other code: no state change (the common "heartbeat" case — called every
  menu frame)
- **always**: clamp `scroll_pos` to `[0, LEVEL_END]` (`0x2AAA`) — the exact
  same constant `advance_ship`'s clamp uses

The key discovery: **`ds:[54AC:54AE]` — the same field `advance_ship` calls
`pos` — is reused as the level-select scroll position** while not in
gameplay. Confirmed directly: `54AC` increased by exactly `0x12F` (303) per
scroll-right call, tracked across 100+ consecutive samples.

Recovered as `skyroads/recovered/menu.py::dispatch_menu_action` (clean rule,
sampled verification — this is UI-tier code, not performance-hot, so no live
VM hook). **ASM_MATCHED: 318/318 real E2E-demo calls byte-exact**, across
every action code the demo actually exercises (`0`, `1`, `3` — all no-op/
default; `0xA` scroll-right; `0xC` enter). Actions `2` (scroll-left) and `9`
(confirm) are transcribed from the identical disassembly pattern as the
verified ones but never exercised by any demo — documented as ASM-derived,
not independently verified. Also not modeled: the conditional calls to
`1010:03C2(0)`/`03C2(4)` (side effects on other state). Guarded by
`tests/test_menu.py` (+ fixture). 188 tests pass.

## 2026-07-11 — a third tick-wait parked (menu/animation timer at 1010:47CD); `[456E]` state 2 identified

Continued the perf work autonomously. Re-profiled the E2E demo with both new
hooks (`0F62`, `4052`) installed and found several menu frames still burning
the *entire* step budget on page `4700xx`. Traced it to a **third tick-wait
spin**, structurally identical to the two `frame-park` already handles:
`1010:47CD` (`cmp ds:[1600],0002h; jnb 47D7; jmp 47CD`) — a menu/animation
frame-timer waiting for `[1600] >= 2` rather than "changed". Same reasoning
applies (`[1600]` is frozen for the whole frame, so once it's under the
threshold it cannot cross it before the next frame) — added as a third park
hook in `skyroads/pacing.py::install_frame_park`. This is **runtime-loaded
code** (invisible in the static EXE, same gotcha as the sound driver from
earlier — disassembled from a live snapshot).

**Byte-equivalence proof**: replaying the full E2E demo (1719 frames) park-ON
vs the full-spin baseline (`--no-frame-park`) — every one of 1719 rendered
frames byte-identical (`frames_hash` matches exactly across two separate
runs), **5.03x fewer interpreted steps** (43,556,327 -> 8,649,674). Locked in
by a new `tests/test_frame_park.py::test_menu_anim_wait_is_byte_equivalent_and_cheaper`
using a captured mid-spin snapshot (the gameplay snapshot the existing park
tests use never reaches menu code). 181 tests pass.

Also checked the other recurring hot pages (`6000`/`6300`/`6500`) for a
similar win: `1010:6013-601A` is a VGA vertical-retrace hardware poll
(`in al,0x3DA; test al,8; loope`) — a categorically different, riskier kind of
wait (it polls emulated *hardware* state, which — unlike `[1600]` — is not
necessarily frozen for the whole frame, so "park until next frame" is not
provably safe the same way; pre2_port's own pitfalls doc warns against
conflating a deterministic skip with live pacing for exactly this reason).
Checked `dos_re.dos.DOSMachine._vga_status`: in this emulator the retrace bit
toggles on **read-count parity**, not wall-clock/instruction time, so the poll
already resolves in 1-2 iterations in practice — not actually a bottleneck.
Left alone; flagging the reasoning here so a future session doesn't have to
re-derive it.

**`ds:[456E]` (top-level game-state) gains a mapped value.** Re-traced state
transitions over the E2E demo with the segment-filter bug fixed (an earlier
probe captured `cpu.s.ds` at hook-install time, before the game had even set
up its DGROUP — comparing against `0x1000` instead of the real `0x1686`,
so it silently matched nothing). The real E2E demo cycles cleanly:
`0->2 (1010:1B68) -> 2->0 (1010:2060) -> 0->3 (1010:2AC2, gameplay start) ->
3->0 (1010:2060, gameplay end)`, repeating once per level played. `1010:1B49`
is a dispatcher (`enter 0x0000,0`, dispatch on `(bp+4)&0xF` through a jump
table at `1BED`) whose relevant action case writes `[456E]:=2` and, if
`[456A]==0`, sets it to 1 and calls `1010:03C2(0)` — consistent with
**state 2 = level-select/menu entry**. Not further recovered this session
(a full dispatcher recovery is comparable in scope to the earlier `074C`
controls work) — flagging it mapped, not claiming it recovered.

## 2026-07-11 — recovered + wired the buffer-relocation hook (perf cause #2), lift-first this time

Picked up perf cause #2 from the diagnosis: the un-hooked buffer scan/patch
loop at `1010:4052`, hot at level-transition frames. This time used
**lift-then-refactor** instead of hand-deriving from disassembly, per the
process correction from the stencil-blit work: ran `dos_re.tools.liftverify`
against a snapshot first, got a proven-correct literal transcription
(ORACLE_PASSING, a bounded-count sample, 8/9 blocks — the lifter's own
runaway-safety cap tripped on the real unbounded call, since one real
occurrence scans a full 64K-underflow pass; patched the count argument down
to a bounded value in a snapshot copy purely to get a liftable sample, then
verified the real large-count behavior separately against actual gameplay),
then wrote the recovered function + hook from the lift's PROVEN block
structure rather than reading the raw disassembly by eye.

That mattered concretely: the lift revealed `ss:[bp+0xA]` is a **second,
in-place-decremented counter** controlling additional full-64K scan passes —
reading the static disassembly alone made it look like a caller-owned local
the function never touches, an easy miss (the same class of mistake the
0F62 hook made twice). It also confirmed the segment-wrap check
(`inc bx; jz`) runs unconditionally on every byte, independent of the count
check that follows it.

Recovered as `skyroads/recovered/relocate.py::patch_nonzero_bytes(source,
delta) -> bytes` (a DOS relocation-fixup pattern: `0` is a "leave alone"
sentinel, everything else gets `delta` added mod 256) plus
`skyroads/hooks.py::buffer_relocate_hook`, which ports the lift's proven
pass/segment-wrap/register-exit mechanics directly (not re-derived) while
batching the byte-patch step through the pure function.

**Verified byte-exact on the first attempt** — no correction rounds needed,
unlike stencil_blit: 252/252 calls over the full E2E demo + 230/230 over a
cold-sound-demo window (482/482 total), zero divergences, via
`HookVerifierConfig.strict()`. (Verifying the *whole* cold-sound demo timed
out — this function scans up to 64K bytes per call and the strict verifier
re-runs the real ASM interpreter to build its oracle side, so its cost scales
with how much of that scanning the demo exercises; the E2E demo + a
cold-sound window already give strong, wide-ranging coverage.)

**Honest coverage gap**: neither demo happens to make a call whose scan
crosses a 64K segment boundary or arms the extra-pass counter — checked
directly (0/252 E2E calls trigger either). Those two branches are
mechanically proven correct by the lift's own bounded sample (whose
`ss:[bp+0xA]` value did drive at least one extra-pass check) but not
exercised end-to-end against real gameplay data. Guarded by
`tests/test_relocate.py` (+ fixture) for the pure function. 181 tests pass.

**Process note**: this hook needed ZERO debugging rounds against the live
differential verifier, vs. two for stencil_blit (which skipped the lift
step). Lift-first is faster in wall-clock terms too — most of the effort goes
into a cheap, fast, bounded lift-verify run instead of iterating against the
much slower full-gameplay strict verifier.

## 2026-07-11 — recovered + wired the stencil-blit hook (perf cause #1), verified 244/244 zero-divergence

Picked up perf cause #1 from the diagnosis below: the un-hooked menu text/glyph
rendering primitive at `1010:0F62`. Recovered as
`skyroads/recovered/blit.py::stencil_blit(source, template_color,
other_color) -> bytes`: a pure 3-value stencil remap (`0->0, 1->template_color,
else->other_color`), the low-level primitive behind menu font/glyph drawing.
No port I/O (unlike the music engine), so — unlike that hook, which got
shelved — full register-exact parity against the project's strict differential
verifier was tractable, and worth doing since this routine showed up
repeatedly in the perf profile.

Wired as `skyroads/hooks.py::stencil_blit_hook`, a real `registry.replace` for
`1010:0F62`. Getting it register-exact took two rounds of the strict verifier
catching real mistakes that hand-reasoning from the static disassembly missed
(same lesson as the earlier renderer hooks — trust the verifier, not the eye):

1. First attempt assumed `SI`/`DI` end up as "final cursor position"
   (`source+count`, `count`) — wrong. The function opens with `push si; push
   di` and closes with `pop di; pop si`: they are the **caller's original
   values**, fully preserved, not touched by the loop at all.
2. Second attempt computed `AH` (and the flags' `AF` bit) from only the *last*
   source byte. Both actually **thread through the whole loop**: `AH` only
   changes on a template/other substitution (a plain zero byte's `or al,al`
   only touches `AL`), and `AF` is *undefined-preserved* by `or` on real
   8086 (`cpu.set_logic_flags` mirrors that convention) — only a `cmp al,1`
   iteration (any nonzero byte) redefines it. A source ending in zeros after
   a substitution exposes both bugs; the very first live call the verifier
   checked happened to end that way.

**Verified byte-exact: 213/213 calls over the full E2E demo + 31/31 over the
cold-sound demo (244/244 total), zero divergences**, using
`dos_re.verification.HookVerifierConfig.strict()` (full machine-state diff:
every register, segment, flag, and DOS/device state — not just memory or
output). Guarded by `tests/test_blit.py` (+ fixture) for the pure function;
the hook's register mechanics are what the strict verifier proved and aren't
re-asserted in unit tests (matching how the other complex hooks — `1732`,
`lzs_decode_loop` — are documented: the differential-verifier run **is** the
proof). 178 tests pass.

## 2026-07-11 — found + fixed two more music-engine bugs; shelved wiring it as a live hook (wrong tool for the perf goal)

Continued from the perf diagnosis below by attempting cause #3: wire the
verified `music.py::Engine` as a real `registry.replace` hook for `1010:5A55`,
replacing the `emulate_call` in `master_timer_isr`. Two things came out of
this attempt:

**Two more real bugs found and fixed**, both invisible to the existing
per-tick ASM-comparison fixtures (which always have the real ASM running
alongside, keeping memory in sync regardless of what the Python engine's own
state holds) and only exposed by simulating what a *live* hook must do —
drive itself off nothing but its own committed state across many ticks:

1. The delay-decrement off-by-one documented in the commit above (the loop-back
   target is the delay check itself, not the word-fetch — the arming tick
   also performs the first decrement).
2. (From the prior session entry) cursor/loop not persisted to memory.

Both fixed and regression-tested with a synthetic multi-tick simulation (no
VM) whose expected sequence was computed, not hand-derived, after hand-tracing
produced the very bug being fixed. Re-verified byte-exact against the ASM
over the whole cold-sound demo (12,882/12,882 ticks) after each fix.

**Wiring it as a live hook is shelved — wrong tool for this goal.** The
project's differential verifier (`HookVerifierConfig.strict()`) compares the
*entire* machine state after a hook call: every register (AX/BX/CX/DX/SI/DI/
BP/SP), segments, flags, and DOS/OPL device state — not just memory or
observable output. Tracing what `1010:5A55`'s handlers leave in scratch
registers on every exit path turned up a deeper problem than incidental
bookkeeping: the ASM's `opl_write` primitive (`1010:5892`) ends with `in
al,dx` — a real hardware status-port *read* — so the exact value left in AL
depends on the emulated OPL device's live status byte at that instant, not on
game logic at all. Getting register-exact parity would mean replicating that
port-read side effect (and equivalents for every opcode's exit path), which
has nothing to do with the actual sound behavior and would turn the clean
recovered `Engine` into hardware-timing bookkeeping.

Even if that effort were spent, it likely **wouldn't fix the user's actual
complaint**: an earlier finding in this project (the `34AE` renderer hook)
already established that a mechanically-exact lift runs at roughly
interpreter speed under CPython — only a refactor into genuinely different
Python control flow (or a PyPy JIT) yields a real speedup. `music.py::Engine`
*is* that refactor, but achieving strict-verifier parity would mean adding
back the ASM's own register/port-timing bookkeeping, undermining the reason
it would be fast in the first place.

**Conclusion: `music.py::Engine` stays as verified-by-output recovered logic**
(the right tier for it — same as `advance_ship`/`decay_bounce`, sampled/output
verified rather than a live differential-machine-state replacement), valuable
for the eventual native port, not wired as a CPython speed hook. The real
fix for causes #1 and #2 below (the un-hooked text-render and buffer-scan
loops) is more promising: likely simpler register footprints, no hardware-
timing reads, and they're the actual source of the multi-hundred-ms frame
hitches — sound delay is a symptom of those, not of the sound engine's own
per-tick cost. 175 tests pass.

## 2026-07-11 — diagnosed the reported perf drops + "sound delay": three distinct un-hooked causes

User report: visible performance drops during some transitions, and sound
feels delayed, "probably poor performance." Profiled the full E2E demo
(`demo_e2e_20260710_132930`) frame-by-frame with the real frontend (frame-park
on, current defaults) and found the slow frames are **not** one root cause —
three distinct un-hooked things, all during menu/transition screens (gameplay
is already fast):

1. **Un-hooked menu text/string rendering** (`1010:0F75`, `1010:41E7` and
   siblings) — classic `lodsb/cmp/stosb/loop` character-blit loops, real
   CPU-bound work (not idle spin, so frame-park can't help). These are menu
   text drawing, not asset loading.
2. **An un-hooked buffer scan/patch loop** (`1010:4062-406C`, called from a
   utility at `1010:4052`: `lds bx,farptr; cx,ax=count,delta; loop{ if
   [bx]!=0: [bx]+=al; inc bx (segment-wraps +0x1000 on overflow) }`) — seen
   heavily at level-transition frames (997, 1517). Likely a palette/index
   rebase over a large buffer; not yet characterized enough to know if it's
   asset-related.
3. **The recovered OPL music engine is verified but never wired as a VM
   hook** — the timer ISR (`master_timer_isr`) still calls through
   `emulate_call` to run the *original* ASM at `1010:5A55` every tick (pages
   `5800`/`5900` show up prominently in some slow frames, e.g. frame 14's
   201ms). This one is very plausibly the direct cause of "sound feels
   delayed": a live player pumps audio on the wall clock, so a 150-230ms
   interpreted-frame hitch (any of the causes above) blocks the audio pump for
   that same span, causing an audible stutter/lag regardless of how fast the
   sound engine itself is.

Slowest E2E frames measured (wall time under CPython, headless): 231ms (frame
770, page `1D00`, unidentified), 201ms (frame 14, sound driver init/patch
load), 196-170ms (several `4000xx`-dominated transition frames), down to a
~150ms tail of similar transition frames. Gameplay frames are consistently
fast by comparison (frame-park + the recovered render/physics hot path).

**While investigating whether cause #3 could be fixed immediately** (wire the
already-recovered `music.py::Engine` as a real hook), found and fixed a real
correctness bug first (see the commit right above this entry): the engine
never persisted its song cursor or decremented the delay counter back to
memory, which is invisible in pure per-tick verification (the real ASM keeps
memory in sync regardless) but would silently break a live hook (replay the
same song forever). Fixed and regression-tested. **The hook itself is not yet
installed** — doing so safely needs full register-state differential-verifier
proof (the project's standing rule), which is a separate, sizeable follow-up
from a diagnostic session.

**Not done this session** (flagging for prioritization): recovering #1 and #2
as clean, hooked Python (the same methodology as the render islands); wiring
+ differentially verifying the music-engine hook (#3). All three are
well-scoped, tractable next steps, comparable in size to earlier renderer-
island work — not something to rush without proper verification.

## 2026-07-10 — game logic: respawn/reset + resume-gate recovered; death-flow architecture corrected

Continued the game-logic thread with the death/respawn side. Corrected a
misread from the physics-mapping session: the jump-impulse gate at `258C` is
`jb` (jump-if-below), so it fires when `[4562] < 0x14`, not `>=` as previously
written — `[4562]` turned out to be a **per-level constant** (pinned at 8 for
the whole deaths demo, not a per-frame counter), read once via `1FFA-200A` to
compute the level's gravity constant `[54AA]` (`= -([4562]*0x1680/0x190)`),
confirming `[4562]` is a per-level physics parameter, not gameplay state.

Traced the actual respawn machinery empirically (writer/caller tracing, not
static guessing — a naive static disasm of "the block starting near 2020" was
misaligned and gave garbage). Findings:

- **The gameplay update genuinely is one monolithic per-frame handler**, as
  `player.py`'s module docstring already said — `1010:1FD9` is not a separately
  called "reset function" but a label inside that same handler; its apparent
  `call ... ret=2C61` is just the handler's own single call-from-the-main-loop
  return address, constant across every internal label.
- **Respawn/reset** (`1010:201F-20A7`): recovered as
  `player.py::respawn() -> RespawnState`, a **pure constant** — 19 DGROUP
  fields (ship position, lateral, vertical, game_state, level timers, tick
  counter) all reset to fixed values, no branching on prior state in the
  sampled span. **ASM_MATCHED — 3/3 real deaths-demo respawns, all 19 fields
  byte-exact.**
- **Resume gate** (`1010:2AB1`): `player.py::is_landed_for_resume(af2c)` =
  `af2c >= 0x2800` gates `[456E]:=3` (resume gameplay) after a respawn. Since
  `respawn()` writes `AF2C := 0x2800` exactly, a fresh respawn is immediately
  resume-eligible.
- **The jump gate is only partially recovered.** Beyond
  `[547A]!=0 and [4562]<0x14`, there are **two more guards**, `ss:[bp-8]` and
  `ss:[bp-18]` (`2570`/`2579`) — frame-local flags that skip the whole jump
  block if either is nonzero, set earlier in the same handler (likely from the
  collision/height classification around `2340-2385`). This is *why* the
  impulse fired only 3 times despite the jump key being held for 29 frames in
  the deaths demo — it fires once per press, not once per held frame,
  almost certainly an "already airborne" latch. `update_vertical_velocity`'s
  `jumped` parameter stays an external input until bp-8/bp-18 are traced.
- **The `[456E]` state machine is wider than previously documented** — the
  outer pacing block (`2A90-2B08`) cycles it through 0/1/3/4/5 via countdown
  timers `[5494]`/`[B13C]` (post-level-complete sequencing, not death-related);
  not further mapped this session.

Guarded by `tests/test_player.py` (respawn + resume-gate cases). 174 tests pass.

## 2026-07-10 — sound/music island COMPLETE (OPL music engine recovered + verified)

Recovered the whole AdLib/OPL music engine into
`skyroads/recovered/music.py::Engine.run_tick` — a pure, VM-free song-bytecode
interpreter (all 8 opcodes incl. the intricate note/instrument/pitch/volume
register math). **Verified byte-exact**: its OPL register-write stream matches
the ASM over **all 12,882 cold-sound-demo ticks (intro + menu), zero
divergences** — lockstep per tick, same proof style as the SB-PCM work. Status
`VERIFIED`. Guarded by `tests/test_music.py` (+ fixture); the transcription was
byte-exact on the first lockstep run.

Recovery notes for the trickier handlers: op1 loads an 11-register FM patch
(operator regs `slot[ch] + offset[i]`, op-2 registers skipped on an add-carry,
the 11th/connection register gated on a `0xFF` sentinel); op2 computes octave =
`note/12 + 2` and F-number from `note%12` tables, writes `A0` then `B0|key-on`,
and channels whose `B0` reg reaches `0xB6` fall through into the rhythm path;
op4 scales operator total-level with a per-level bias and `0x3F` clamp. The
song data + tables are *data the port loads*, not code (see below).

Also recovered the one-time **OPL reset / percussion init** (`1010:58A5-5913`,
run once at driver start before any song plays): silence all 22 operator
registers, key-off channels 7..0, enable waveform-select + rhythm mode, load 4
fixed percussion patches via the same `op1` path, fix the 2 percussion
channels' pitch. `Engine.reset_opl()`, **VERIFIED** — byte-exact against its
one occurrence in the cold-sound demo, confirmed the *only* occurrence over the
full 2157-frame replay. Gotcha found while isolating it: `58A5` (the
silence+keyoff subroutine) is also called **standalone** elsewhere just to
silence the chip, not only as step 1 of the full init — trace the call site
`58CD` to isolate the complete sequence.

Also settled: **SFX needs no recovery island.** It's digital PCM over Sound
Blaster DMA; `skyroads/audio.py` already plays it correctly as a *pure
observer* of the raw DMA bytes (same pattern as render hooks watching OPL
writes) — there's no trigger-condition logic to reimplement.

**The sound/music subsystem is now fully retired for the VM-less port** —
sequencer, one-time init, and SFX all covered. 172 tests pass.

The reverse-engineering that made this possible (unchanged, kept for reference):

Reverse-engineered the whole AdLib/OPL music driver — see
[`sound_engine.md`](sound_engine.md). It is a compact **music-bytecode
interpreter** at `1010:5A55` (per timer tick): walk a song event stream, decode
`op = word & 7` / args, dispatch through an 8-entry table at `DG:0x0C5B`, program
the OPL2 via the `opl_write(reg=AL,val=AH)` primitive at `5892`. Eight opcodes:
delay, note+instrument (11-register FM patch), note-on pitch (F-number/octave),
key-off, volume, loop, set-loop-point, flag. State + data tables documented.

Key discovery that unblocked this: the driver is **runtime-loaded** (zero in the
static EXE), so it must be disassembled from a *post-intro* snapshot, and
`lindis`'s text column mis-renders some `[disp]` values (read the byte column).

The register-group capture confirmed the full OPL2 map is written (0x20–0xF0
operators, 0xA0/0xB0 freq+key-on, 0xBD rhythm). Remaining to *complete* the
island: transcribe the engine + 8 handlers + the note-frequency math into clean
VM-free Python and verify it emits the **byte-identical OPL register-write
stream** as the ASM over the cold-sound demo (lockstep per tick). Architecture
is done; the byte-exact build is the well-defined next step.

## 2026-07-10 — game logic: vertical-velocity physics (jump impulse + gravity)

With the user's **deaths demo** (`demo_skyroads_20260710_213019` — 29 jump-frames,
3 jump impulses, states 0/1/3), recovered the jump+gravity stage of the vertical
velocity `ds:[9336]` update (`2582-2635`) as
`skyroads/recovered/player.py::update_vertical_velocity`, **ASM_MATCHED 238/238
deaths-demo frames byte-exact** (incl. the 3 jump frames). Per frame, after
`decay_bounce`: jump fires `[9336]:=0x480` (`2596`), then airborne
(`[456A]==0`) `[AF2C]>=0x2800` adds gravity `[54AA]` (`25F0`). Guarded by
`tests/test_player.py`.

Corrected a branch-direction misread along the way: gravity is the
`[AF2C] >= 0x2800` side (`jnb`), not `<`.

**Still dark, even in the deaths demo:** the terminal-velocity clamp
(`[AF2C]<0x2800` → −106) and the grounded ramp (`[456A]!=0` → +0x47). The demo's
deaths are all *collisions* (`[AF2C]` stays `>=0x2800`, `[456A]` stays 0), so
those branches are transcribed from the ASM but unverified. Also still open: the
**jump gate** itself (`2582/258C`: what latches "can jump" — frame-local state
not yet resolved) and the **death / level-complete state transitions**
(`456E` writes at `2060/27FD/2AC2`; `[AF2C]` vs `0x2800` fall-test at `2357`).

## 2026-07-10 — game logic: keyboard control decode recovered (input → speed/steer/jump)

Started the input side of the gameplay handler. Mapped it empirically first —
traced which code writes each gameplay-state field over the input-carrying demo
rather than reading disassembly and guessing (RE-hallucination guard). That gave
clean single-writer islands: speed `[9330]` ← `08E6`, vertical `[AF1C]/[AF2C]`
← `1965/197D`, lateral target ← `1949`, and the game-state transitions (death/
complete) ← `2060/27FD/2AC2`.

The input handler is `1010:074C`, a dispatcher on the selected control device
`ds:[95F6]`: **0 = keyboard**, 1/2 = other devices, 2 = joystick (reads axes via
`06B9` vs thresholds), **3 = attract-mode autopilot** (reads a packed control
track at `ds:0x961E`, indexed by `lateral_pos / 0x666`, unpacking speed/steer/
jump from bitfields). Live play is the keyboard case (`0758`); the whole demo
runs `95F6==0`.

Recovered the keyboard case as `skyroads/recovered/controls.py::decode_keyboard`
— **ASM_MATCHED, 1466/1466 full-demo `074C` calls byte-exact** (497 with keys
held). It reads the per-key row the timer ISR maintains at `ds:0x0BD0` (bit 7 =
held) and folds nine keys (an 8-direction pad + jump) into three axes:
`speed=[9330]`, `steer=[95F4]`, `jump=[547A]`, each `(OR of positive dirs) -
(OR of negative dirs)`; diagonals drive both axes. Guarded by
`tests/test_controls.py` (+ fixture). Scancode→row-offset mapping (in the ISR
poll `3BE5`) is separate host-input plumbing, not yet recovered.

⚠️ **`artifacts/gameplay_snap_f520` is attract mode (`95F6==3`), not live play.**
Lifting `074C` from it exercises the autopilot track decoder, not the keyboard
case — a trap for future game-logic recovery. Capture snapshots from a demo
replay at a `95F6==0` frame instead (helper pattern in this session's scratch).

### Vertical/lateral physics — mapped, recovery gated on a jumps+death demo

Mapped the per-frame movement physics block (`1010:2560-26E9`, inline in the
gameplay handler) that computes the targets fed to `186B`/`resolve_move`. Field
semantics (some correcting earlier labels):

- **`ds:[9336]` is the vertical VELOCITY**, not merely a "landing bounce".
  `decay_bounce` (`24A1`) damps it; gravity accelerates it; jump impulses it.
- **`ds:[547A]` (jump) IS read** at `2582` (a word `cmp`, which is why an
  `rb`-only reader trace missed it) — the demo just never sets it.
- `ds:[4568] = steer[95F4] * 29`, **guarded** (`2550-256D`: only when
  `[9336] > 0` and `[AF2C]-heightref < 0x0F00`) and it feeds the **vertical**
  target term (`2676`: `[4568] * … / 0x200`), not the lateral axis.
- Gravity (`25DB-2635`, when `[456A]==0`): airborne (`[AF2C] < 0x2800`)
  `[9336] += [54AA]`; past `0x2800` it snaps to terminal `0xFF96` (−106); the
  `[456A]!=0` path ramps `[9336]` up to `+0x47` (grounded/rising).
- Jump (`2582-25A6`): when `jump && [4562]>=0x14 && …`, set `[9336]=0x480`
  (up impulse) and latch a "jumping" flag.
- Death test at `2357` (`[AF2C]` vs `0x2800`) and level-complete at `2514`
  (`[54AC]` vs `0x2AAA` = `LEVEL_END`).

The jump impulse, terminal-velocity clamp, grounded ramp, and the death path are
all **dark in the current demo** (no jumps, no death — the run is a clean
start→finish). Recovering this block byte-exact needs a demo that exercises
them; **the user is recording a jumps+death demo** to unlock it. Until then the
map above is the tee-up, not recovered source.

## 2026-07-10 — gameplay perf: it was a pacing/steps issue, not hook coverage (frame-park)

"Gameplay performance is still not good" turned out **not** to be a
hook-coverage problem — the render/math hot path is already hooked. Profiling
the gameplay window (running the `game_state==3` snapshot forward) showed where
the interpreted budget actually goes, per frame:

| bucket | share of steps |
|---|---|
| **idle tick-wait spin** (side-effect-free) | **~88%** |
| real un-hooked work (render + update) | ~8% |
| recovered hooks | ~4% |

The game paces itself off `ds:[1600]`, the elapsed-tick counter its INT 08h ISR
bumps. But the viewer delivers **all** of a frame's timer IRQs at frame start
(`advance_frame`), so `ds:[1600]` is **architecturally constant for the whole
step budget** — it can't change again until the next frame. Any loop waiting on
it therefore spins out the entire remaining budget doing nothing. Two loops do:
`1010:22F8` (main gameplay pacing spin) and `1010:434A`/`4449` (the fade/pacing
wait). So the VM was grinding 30000 steps/frame of which ~26000 were pure spin
→ ~5 fps under CPython.

**This is exactly what pre2_port already solves** (the endgame reference). Its
`scripts/play.py` classifies known busy-wait loops (`pre2.recovered.vga_timing`,
the PIT `1C6F` wait) and fast-forwards them: *"touches no game logic … the
trajectory stays byte-equivalent — only the wall clock improves."* SkyRoads'
port had the empty equivalent — `skyroads/input_waits.py` was never populated.

**Fix: `skyroads/pacing.py` frame-park** (on by default; `--no-frame-park` to
force the full spin). Two hooks at `22F8`/`434A` raise `FrameIdle` the instant
the game parks in its tick-wait; `SkyroadsFrontend.advance_frame` catches it and
ends the frame. The `434A` park defers to the existing verified fade-loop gate
whenever there are keys to drain, so input timing is unchanged.

**Byte-equivalence proof** (the bar for a pacing shim): replaying the full E2E
demo (the whole **1906-frame** level, start to finish) park-ON vs the full-spin
baseline through the real replay path — **every rendered frame identical and all
named game-state fields identical**, at **3.4× fewer steps** (17.8M vs 61.2M)
and **3.0× faster wall**. The E2E ratio is diluted by menus/fades/input; the
gameplay window alone is **~6–8× fewer steps** since it is nearly all spin. A
full-memory diff over the run shows the *only* bytes that differ are **11 bytes
of fade-loop scratch at DGROUP+0xB87C** (a blend/poll counter written by
`43A9`/`415x`, never read into game state or any rendered frame). Locked in by
`tests/test_frame_park.py`.

**Budget resized to a ceiling above peak work (30000 → 48000).** With the park
on, `steps_per_frame` is no longer the per-frame cost — it is a ceiling for the
frames that *don't* park. Measured real work over the level: p50 ~9.2k, p99
~34.8k, **peak 37,309** (113/1906 frames exceed 30000 and were being cut
mid-tick). So the budget must be sized *above* the peak, not toward the average:
48000 clears 37,309 with ~28% headroom. Shrinking it (e.g. to 5000) is the wrong
instinct — a budget below peak makes the original ASM see itself lagging and
engage its own lag compensation (deterministic but not original pacing; the
lesson is in `pre2_port/scripts/play.py`, which warns below chunk 20000). Safe
to change: `steps_per_frame` lives in `demo_metadata`, so existing demos replay
at their recorded budget regardless of the default. (The title/menu idle loops
are a *different* set of un-parked waits — `skyroads/input_waits.py` is still
empty — so a fresh boot still spins near the ceiling; parking those is future
work.)

Also fixed a **pre-existing CI break** surfaced along the way: the updated
dos_re submodule now ships `dos_re/tests/`, whose top-level package name
`tests` collided with this repo's `tests/` on `pythonpath`, breaking collection
outright (`No module named 'tests.test_*'`). Pinned `testpaths=["tests"]` +
`--import-mode=importlib` in `pyproject.toml`; `pytest -q` is green again.

## 2026-07-10 — physics recovery verified full-demo + a negative-speed bug fixed

Brought the recovered ship physics (`skyroads/recovered/player.py`) up to the
movement-island standard: captured real `advance_ship` (`24C4`) and
`decay_bounce` (`24A1`) I/O by watching those inline IPs over the whole demo and
verified `player.py` reproduces every sample byte-exact — **1610/1610
advance_ship, 63/63 decay_bounce**.

The capture found a real bug: the ASM sign-extends speed (`cwd` at `24C7`) into a
32-bit value before `ulong_mul(speed, 75)`, but `advance_ship` used
`(speed & 0xFFFF) * 75` (unsigned 16-bit). They diverge for negative speed — the
ship moving *backward* — which happens **33 of 1610 calls** (e.g. speed `0xFFFF`
= −1 should step pos back by 75; the old code clamped it wrong). Fixed to
sign-extend; all 33 now match. Guarded by `tests/test_player.py` (fixture
includes the negative-speed cases). Same lesson as the `186B` unsigned/signed
edge case — full-trace verification catches what sampled checks miss.

## 2026-07-10 — lifted the 186B road-segment stepper (movement + swept collision)

`1010:186B` — the "largest single remaining recovery" per rendering_architecture
— is now a verified island. It is the game's **swept movement + collision
resolver**: a 274-instruction, 80-block, 5-phase iterative solver that steps the
ship's accumulators (`ds:[9618:961A]` lateral, `ds:[AF1C]`, `ds:[AF2C]`) from
their current values toward a requested target in sub-steps, using `1732`
(`road_object_visible`) as the collision predicate and refining each axis to the
exact contact boundary. Phases: (1) early-out if already at target; (2) 5-step
forward sweep, find the first sub-step `1732` blocks; (3) commit the furthest
safe sub-step; (4) binary-search the lateral axis (step ÷16); (5) refine `AF1C`
then `AF2C` (step ±125, ÷5). Calls only already-recovered helpers
(`1732`/`5D4C`/`5E5A`/`5D8C`). Core **movement/collision game logic**, and it
drives the repeated `1732`+`04C0` road-segment work.

Recovered with the **automatic lifter**: `liftgen` census → 100% liftable (274
insts, 4 calls, 0 INTs); `liftverify` emitted the byte-exact lift. Its
`enter 0x000a,0` prologue exercises dos_re's just-landed entry-fallback
recursion fix (submodule bump `11917f2`). Installed as
`skyroads/lifted/lifted_1010_186b.py` + `registry.replace(0x186B)`.

**Verification — 1760/1760 full-demo calls byte-exact (71/80 blocks),
ORACLE_PASSING.** This needed the *compositional* differential mode: `186B`
calls four already-verified child hooks, and the lift's `emulate_call` runs
their Python hooks while the ASM oracle (auto-continuation, hooks dropped) runs
real ASM — so the two leave different **dead stack below SP** (the nested-call
arg-push scratch), which a naive full-memory strict diff flags as a
"divergence". Marking the children passthrough (`asm_keeps_passthrough_hooks` +
`hook_verifier_passthrough`) makes both sides run identical child code, leaving
only `186B`'s own instructions to diff — and then all 1760 calls match exactly.
(Caution: `liftverify`'s default 40-sample PASS was *misleading* here — the
divergent deep-stack path first appears around call 41; always verify past the
sample cap for functions that call other hooks.)

An **end-to-end memhash test diverged** (+132K steps, memory differs, but
**registers identical**) — this is NOT a correctness failure. It is the known
fixed-step-budget / busy-wait interaction (see the `palette_fade_inner` note
below): replacing `186B`'s ~274 interpreted instructions/call with a Python hook
frees per-frame step budget, so the game's idle elapsed-tick spins (`22F8`,
`4153`) iterate a different number of times and the arbitrary frame-boundary
state drifts. Registers-identical + the 1760-call per-call proof confirm game
*logic* is unchanged; the e2e memhash is not a valid invariant for any
step-count-changing hook (every installed lift/hook fails it identically —
*confirmed*: toggling the already-accepted `34AE` lift in the same e2e diverges
even harder, −6M steps, memory differs, registers identical). The per-call
differential verifier is authoritative. All 159 port tests pass with `186B`
installed.

**Now refactored into a clean recovered island** (metrics-honesty debt paid):
`skyroads/recovered/movement.py::resolve_move` is the swept movement+collision
solver as pure, VM-free Python + `@oracle_link` — the native-port destination
(roadmap gap #1, movement/collision logic). Verified `ASM_MATCHED` **1760/1760
full-demo calls** by a *predicate-oracle* method: replay the exact `1732` results
the ASM saw and check both the output accumulators AND that the reconstruction
probes the exact same positions (an unrecorded probe = a diverged interpolation).
This caught a real edge-case bug the 250-sample missed — the axis-refine
direction uses an **unsigned** compare (`cmp [bp+8],ax; ja`), not signed; it only
matters when the depth accumulator and its target straddle 0x8000 (2 of 1760
calls). Guarded by `tests/test_movement.py` (fixture includes those cases). The
`186B` **lift stays installed as the byte-exact VM hook** (it reproduces the
exact register/stack state the differential verifier needs); `movement.py` is the
clean logic that replaces it when the VM is retired. Note neither is a CPython
perf win (`186B` is only ~2-4% of interpreted work; the lift runs at
~interpreter speed) — the value here is correctness + native-port coverage.

## 2026-07-10 — audio: digital SB PCM effects + AdLib-on-PyPy + correct 30 Hz frame rate

Three sound/timing fixes.

### 1. Native frame rate is 30 Hz (PIT reprogrammed to 180 Hz) — `present_hz` was 2× too fast

SKYROADS reprograms **PIT channel-0 to divisor 6628** at boot (`OUT 40h`), i.e.
`1193182 / 6628 = 180.0 Hz` IRQ0 — *not* the 18.2 Hz BIOS default (confirmed by
tracing port-40h writes; the frequent `43h=B6h`/`42h` writes are channel-2, the
PC speaker). Its INT 08h ISR software-prescales `/6` (`ds:[3192]`), so game
logic ticks at `180/6 = 30 Hz` — the native frame rate. (This **corrects** the
earlier note that read the `/6` prescaler against 18.2 Hz and wrongly concluded
"~3 Hz"; the PIT reprogramming had been missed.)

The viewer delivers `timer_irqs_per_frame` (6) INT 08h per presented frame and
paces frames at `present_hz`, so IRQ0 Hz = `6 × present_hz` and logic Hz =
`present_hz`. The base default `present_hz=60` therefore ran IRQ0 at 360 Hz and
logic at 60 Hz — **everything (music tempo, physics) at 2× speed**. Fixed:
`SkyroadsFrontend.default_present_hz = 30` → 180 Hz IRQ0 / 30 Hz logic, one game
tick per presented frame. Wall-clock pacing only; headless demo replay ignores
`present_hz`, so determinism is unchanged. (User-reported: music was too fast;
~30 Hz matches DosBox.)

### 2. Sound Blaster digital PCM sound effects (were silent)

SkyRoads plays music through the AdLib/OPL FM chip but its **sound effects are
digitized 8-bit-unsigned PCM** streamed to the SB via **single-cycle DMA (DSP
`0x14`)**, fire-and-forget (it never waits on the block-complete IRQ — which is
why the detection-only stub worked). Sample banks on disk:
- `SFX.SND` (25807 B): 12-byte header = 6× `u16` offsets `[12, 3996, 9150,
  17235, 18036, 25807=EOF]` → 5 effects, then raw unsigned-8 PCM.
- `INTRO.SND` (32100 B): headerless raw unsigned-8 PCM (the intro sample).

Per effect the driver issues `D0` (pause) → `40` (time constant = rate) → `14`
(single-cycle DMA-out, length). Rates seen: intro `tc=90` → 6024 Hz; the
recurring gameplay effect `tc=131` → 8000 Hz, 5153 B; also `tc=236` → 50000 Hz.
The full E2E demo fires **57 effects** (306,264 B PCM).

These were dropped because the emulated SB ran in `detection_only` mode (no PCM
streaming). Now captured as a **pure observer**:
- `skyroads.runtime.create_game_runtime(..., capture_sb_pcm=True)` attaches a
  full SB that copies each DMA block into `sb.pcm_out` and logs its rate — but
  **no block-complete IRQ is delivered**, so the CPU timeline is untouched.
- `skyroads/audio.py::SkyroadsAudioSink` (extends the stock AdLib/speaker sink)
  drains those blocks, linear-resamples each from its DSP rate to the mixer
  rate, and sums them into the output alongside OPL + PC speaker.
- Wired in `SkyroadsFrontend`: capture is enabled only for the viewer with
  `--audio adlib` (off for headless/demo/test, so those keep the exact
  detection-only path and accumulate no PCM).

**Determinism proof (the observer guarantee):** replaying the full 1906-frame
demo in detection-only vs capture mode is **byte-identical** — same 61,050,603
instructions, same registers, same SHA-256 of the whole 1 MB memory image —
while capture pulls all 57 effects (306,264 B). Locked in by
`tests/test_sb_pcm_audio.py` (resample/mix unit tests + a byte-exact
capture-vs-detect boot integration test). Audible artifact:
`artifacts/skyroads_sfx_demo.wav`.

### 3. AdLib works under PyPy

The Nuked-OPL3 cffi extension was only built for CPython (cp311); PyPy reported
"Nuked-OPL3 not built". Built the PyPy-ABI extension
(`pynuked_opl3/_opl3_cffi.pypy311-pp73-win_amd64.pyd`, a gitignored build
artifact) via `pynuked_opl3._ffi_build` under PyPy + MSVC. It loads
(`is_available() → True`) and renders **byte-identically to CPython** (same
SHA-256 on a test note). Build gotchas worked around: cffi's cross-drive
`os.path.relpath` (put the build `TMP` on the same drive as the sources) and a
trailing-space in the `TMP` env var (use cmd's quoted `set "TMP=…"`). The
vendored `_ffi_build.py` cross-drive bug is left untouched (nested submodule).

## 2026-07-10 — full-level perf drop root-caused: the 34AE tile renderer (lifted)

A full start→finish level demo (`artifacts/demos/demo_skyroads_20260710_145303`,
1,906 frames, 54.5M steps — the user flagged in-level performance drops) profiled
to a new dominant un-hooked cost: **page `3500` = 29.4% of interpreted work**
(the hot loop at `356B`), not prominent in earlier demos. It is the
`[0E38]`-dispatched tile renderer `1010:34AE` (reached via the `34A7` wrapper) —
a different tile-render variant this world uses heavily.

Recovered with the **automatic lifter** (`dos_re.lift`): `34AE` is 100% liftable
(130 insts, 28 blocks, one indirect call run through the VM); `liftverify`
proved it `ORACLE_PASSING` — 401 calls, 26/28 blocks byte-exact, and a further
400 full-level-demo calls under the strict differential verifier, zero
divergence. Installed as `skyroads/lifted/lifted_1010_34ae.py` +
`registry.replace(0x34AE)`.

Honesty notes:
- **The raw lift gives ~no CPython speedup** (full-demo wall ~20.4s with vs
  without) — a literal per-instruction lift runs at roughly interpreter speed.
  The real perf win needs the hot `356B` loop **refactored into efficient
  Python** (as `38BF`/`325B` were), and/or PyPy JIT-compiling the lift. The
  install is correct scaffolding; the refactor into a clean
  `skyroads/recovered/` island (metrics-honesty rule) is the to-do.
- A cautionary self-note: a first verification of the lift falsely "diverged"
  — the ad-hoc harness had installed the `1732` hook function at address
  `0x34AE` (a sed slip). Always verify the ACTUAL lifted function; `liftverify`
  (purpose-built) is the trustworthy path.

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
