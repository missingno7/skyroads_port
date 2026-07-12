# Cookbook — proven techniques that live in the source repos

Not everything the two ports invented could be promoted into this framework:
some mechanisms are inseparably welded to their game's addresses and layouts.
But every one of them **solved a problem your game will likely also have**,
and the worked example is sitting on disk. This is the problem-indexed map.

The worked examples live in the sibling repositories: `pre2_port` (P2 —
github.com/missingno7/pre2_port) and `overkill_port` (OK), typically checked
out next to this repo. Paths below are relative to those repos. If neither is
available on your machine, the entries below still carry the essential shape
of each technique — enough to re-derive it against your own oracle; treat the
missing example as lost convenience, not lost method.
When you re-implement one of these for a new game, read the original first —
each encodes debugging that took days — and if your version comes out generic,
promote it (see `roadmap.md`, "parameterize-and-promote").

## Timing and speed

**Headless runs crawl because the game busy-waits (retrace/PIT spins).**
→ *Deterministic timing fast-forward*: collapse provably-identical poll
iterations in closed form, re-emitting every due IRQ at its emulated-time
point, never skipping across a pump boundary. The emulated timeline stays
byte-identical — demos don't re-record. Worked examples:
`pre2/bridge/timing_fastforward.py` (+ `tests/test_timing_fastforward.py`,
whose mock-CPU sweep is the template for proving skip arithmetic) and the
simpler `overkill/timing_fastforward.py`. Read pitfalls #12–13 first.

**The interactive viewer runs too fast once waits are hooked.**
→ *Wall-clock parking* (a different mechanism from fast-forward — pitfall #14):
sleep until the real retrace phase matches, keep servicing IRQs/audio/input,
let the VM's own poll exit naturally. Design doc:
`pre2_port/docs/pre2/live_view_timing_design.md`.

**Forward traces hang forever at a timer flag.**
→ The game's ISR increments the flag; free-running the VM never delivers it.
Deliver the *real installed IRQ0 ISR* at the wait points (never poke the flag
— OK measured ~314 bytes of lost music/BIOS state from the poke shortcut).
Worked example: `overkill/timing_fastforward.py::advance_frames_fast`.

**Every probe/investigation replays minutes of VM to reach its target.**
→ *Walk-shadow cache*: record a demo's per-frame states once (delta-encoded;
~10–20 MB for an 8000-frame demo), replay in seconds, keyed by the demo file's
hash so it can't go stale. Worked example: `overkill/probes/_shadow_cache.py`;
the probe scaffolding around it is `overkill/probes/_harness.py`.

## Bootstrap and cold start

**The packed EXE takes ages to boot; you want one canonical initialized state.**
→ *Static runtime bundle*: run the original bootstrap once to a declared
frontier, snapshot, and record a manifest (PSP tail, frontier address, memory
+ data-segment hashes). Worked examples: `overkill/static_runtime_bundle.py`,
`overkill/bootstrap_boundary.py`, doc
`overkill_port/docs/overkill/bootstrap_static_boundary.md`.

**The native port must cold-boot without the EXE (endgame).**
→ *Boot-data extraction*: trace which memory the game reads-before-writes
after bootstrap, extract those tables into native constants. Worked examples:
`pre2/native/boot_data.py` (the result), `pre2/probes/map_boot_reads.py` and
`pre2/probes/extract_boot_data.py` (how it was derived).

**The game arrived as a DOSBox save, not a clean boot.**
→ The core imports DOSBox-X saves (`dos_re/dosbox_savestate.py`); the adapter
locates the program inside the image by a code signature. Worked example:
`pre2/runtime.py::load_dosbox_savestate` (signature + segment derivation).

## Hard code shapes

**The game patches its own code at runtime (a routine has multiple live bodies).**
→ *Runtime-code staticization*: name every accepted variant, guard by
signature (the guards are already in `dos_re.hooks`), map each variant to an
explicit static Python owner; unknown bytes fail loud. Worked examples:
`overkill/runtime_code.py` (the slot/variant registry), doc
`overkill_port/docs/overkill/runtime_code_staticization.md`. To *find* the
patching: `overkill_port/scripts/trace_runtime_code_writes.py` and
`scripts/probe_ega_self_mod.py` (watch a code range for writes via
`mem.write_watchers`, report the patched spans).

**The game is procedural spaghetti (handler zoos, dispatch tables, choreography).**
→ The Overkill campaign is the worked example of decomposing it: handler
cross-referencing (`overkill/scripts/behavior_zoo_xref.py`), the generated
hook inventory (`overkill/scripts/gen_hook_inventory.py`), per-island truth
tables (`overkill_port/docs/overkill/island_truth_tables.md`), and the
actor-model recovery write-up (`docs/overkill/actor_model.md`). Goal: recover
the implicit actor/choreography model, not a rewrite of each handler.

## Audio

**The music driver is a resident segment doing millions of interpreted instructions.**
→ *Layered audio recovery* (never big-bang): asset decode → typed data model →
sequencer/tracker → mixer (verify: same state + events + timing → same PCM
block against the emulated device's `pcm_out`) → detach the ASM path. Worked
examples: `pre2/codecs/audio.py`, `pre2/recovered/tracker.py`,
`pre2/recovered/mixer.py`, plan in `pre2_port/docs/pre2/audio_architecture.md`
and the layered section of `docs/pre2/source_port_plan.md`.

**Audio makes bring-up unbearably slow before you care about it.**
→ *Fast-AdLib service*: replace the driver's delay/write loops with an
instant-return service during bring-up — reaches graphics fastest, mutes
music, and is recorded in demo manifests so replays stay compatible. Worked
example: `pre2/bootstrap_hooks.py` (`install_fast_adlib_service`).

**You need to hear/verify FM music without the game running.**
→ Capture the OPL register stream via `dos.adlib_callback`, render offline
through `pynuked_opl3`. Worked example: `pre2_port/scripts/render_music.py`.

## Automatic lifting (the first-draft accelerator)

**Not wanting to hand-translate a routine's first Python version from ASM.**
→ *The lifter* (`dos_re/docs/lifting_design.md`, framework-level): point it at
a function entry and it generates a literal, per-instruction Python hook —
ugly but faithful — then proves it byte-exact against the interpreted original
so you refactor from a verified artifact instead of decompiling from scratch.
`liftgen --report` censuses which entries are liftable and why not (indirect
jumps and x87 are the usual refusals); `liftgen --emit` writes the hooks;
`liftverify` installs each one and, every time it runs, diffs the full machine
state against the ASM oracle, reporting `ORACLE_PASSING` / `DIVERGED` /
`NOT_REACHED` into a proof ledger. ~95% of a lifted function is real
per-instruction Python (ALU/mov/flags/shifts/string ops calling the
interpreter's own helpers); the remainder are exact interpreter-fallback lines
that mark the to-do list. Calls and INTs run through the VM, so callees never
need lifting and lifted/hand-written hooks compose. Measured: 269/335 overkill
functions liftable, the lifted `4537` passes its hand-hook's 300-case fuzz
byte-exact (and was correct on a flag tail the *hand* version got wrong).

**Keeping "lifted" honest against "recovered".** → Lifted functions are
coverage of the *verification* frontier, not the *understanding* frontier, so
they live in their own `<game>/lifted/` tier with their own JSON proof ledger
(`dos_re.lift.manifest`), disjoint from `@oracle_link` islands. A lift becomes
recovered source only after the agent renames and simplifies it into clean
Python and tags it `@oracle_link` — with the same oracle test unchanged. Never
let a wall of unread-but-verified lifted code inflate the campaign's
"recovered %".

## Verification depth (the endgame)

**Proving the native port equals the VM, tick by tick, over whole playthroughs.**
→ *Tick-demo harness* — **now a framework engine**: `dos_re/tick_demo.py`
(`TickDemo` + `masked_digest` + `record_ticks` + `verify_ticks`; usage
skeleton in dos_re's `docs/agent_toolbox.md` §12). Record seed + per-tick
consumed input + a gameplay-state digest (render-only ranges masked out — ONE
digest definition shared with the forward oracle), replay through the VM-less
core, compare per tick. Includes the non-obvious lesson, now a first-class
*sideband* channel: state derived from instruction count (P2's idle-fidget
timer) must be *recorded and injected*, since the native port has no
instruction count. Worked examples of the full pipeline (pre-framework, still
the richest reference): `pre2/native/game_tick_demo.py`,
`pre2_port/scripts/verify_finish_demo.py`, `scripts/verify_native_tick_demo.py`.

**Proving the native FRONT END equals the VM — the screens with no game tick.**
→ *Front-end timeline harness* — **a framework engine**: `dos_re/frontend_timeline.py`
(`capture` + `collapse` + `diff_sequence` + `diff_pixels`; usage skeleton in
dos_re's `docs/agent_toolbox.md` §12b). The tick demo captures none of the intro
/ title / menu / attract / map / tally — they run with no tick — so those flows
ship verified against nothing and drift (a screen shown in the wrong ORDER, a
dropped fade, a screen before/after the wrong transition). Capture a per-present-frame
`(logical-screen, RGB-digest)` timeline from the VM and from the native front end,
then diff by **sequence** (screen order + per-run frame count — catches the wrong-order
class) and opt-in **pixels** (per-frame RGB, byte-exact — catches cadence bugs a
single golden-frame test misses, e.g. a native screen that jumps to the settled image
and drops the VM's multi-frame fade-in). Same oracle trick as the tick demo: capture
the VM's per-frame sampled input and *inject* it into native (no synthetic keystrokes);
needs a **cold-start demo** so the native cold-boot entry aligns. Worked adapters:
`pre2_port/scripts/probe_frontend_timeline.py` (VM ground-truth prober — run on any
demo), `scripts/frontend_capture.py`, `scripts/verify_native_frontend.py`.

**A divergence appears 10 minutes into a demo.**
→ *Suffix repro*: `InputDemoPlayback.write_suffix` (already in the core)
carves a snapshot + rebased event tail at the divergence boundary — "resume
here, run 4 frames" instead of "replay everything". The frame verifier's
repro-artifact capture (`dos_re/repro_artifacts.py`) pairs with it.

**Tracking what the demo corpus actually covers.**
→ Corpus census: enumerate demos, their length, and which
levels/scenes/behaviours each reaches; blind spots are reported risks. Worked
examples: `pre2/probes/demo_census.py`, `pre2_port/docs/pre2/demo_manifest.md`.

## The play.py entry point (the human's window into the port)

The runner itself is UNIFIED now: `dos_re/dos_re/player.py` owns the standard
CLI (viewer by default / `--headless`; `--snapshot`/`--save-snapshot`;
`--record-demo`/`--play-demo`/`--demo-continue`; the four hook-mode flags;
pacing knobs), the viewer loop with the standard hotkeys (F10 screenshot,
F11 demo-record toggle, F12 snapshot), headless replay and crash snapshots.
Your port subclasses `GameFrontend` — start from this repo's
[`scripts/play.py`](../scripts/play.py) and keep the flag names; the worked
examples below are the GAME-SPECIFIC ideas you graduate into as the port
matures.

**Which pacing model? (the main thing ports actually differ in).**
→ Start with the library default: a fixed `--steps-per-frame` budget +
`--timer-irqs-per-frame` INT 08h ticks — no wall clock, the frame index IS
the demo clock, record/replay trivially deterministic. Graduate only when the
game demands it, cheapest first:
- **Deterministic tick-wait park** (simplest, no wall clock): the game paces
  off a timer-tick counter its INT 08h ISR bumps, but the driver delivers all
  of a frame's IRQs at frame start — so that counter is *constant for the whole
  step budget*, and any loop waiting on it spins out the rest of the budget for
  nothing. Classify those wait heads and end the frame the instant the game
  parks in one (byte-equivalent trajectory: the spins have no side effects).
  Worked example: `skyroads_port/skyroads/pacing.py` (`--frame-park`) — ~6×
  fewer interpreted steps on gameplay-heavy frames, keeping the deterministic
  fixed-budget clock.
- **Wall-clock model** (P2): PIT ch0 + the 70 Hz retrace on the WALL clock for
  live play, while demos keep a deterministic instruction-count clock
  (`pre2_port/scripts/play.py`, `docs/pre2/timing_hook_design.md` — read its §7
  before touching live pacing).
- **Modelled wait boundaries** (Overkill): present at the game's timer/retrace
  wait addresses from an emulator thread with a producer/consumer handoff
  (`overkill_port/scripts/play.py`).

Whatever you pick: every knob a replay must match goes into
`demo_metadata`/`apply_demo_metadata`, or old demos lie.

**How big should `--steps-per-frame` be?** Size it *above* the game's peak
per-frame work, never toward the average. A budget below the real per-frame cost
isn't just a mid-work cut: the original ASM notices it isn't completing a logic
tick per frame and engages *its own* lag compensation, so the game plays
differently — still deterministic, but not original pacing (`pre2_port` warns
below chunk 20000, reaching natural pacing only from ~40000). This matters
*more* once you add a tick-wait park: the park makes the budget a **ceiling** the
common frame never reaches, so its value is set entirely by the rare heavy
frames — size it to peak + headroom (SkyRoads: measured peak 37.3k steps →
budget 48k). Because `steps_per_frame` lives in `demo_metadata`, raising the
default never disturbs existing recordings.

**A hook tier safe enough to record demos over (`--safe-hooks`).**
→ Classify hooks by WRITE-SET: render/audio-owned hooks (their writes cannot
touch the gameplay state a recording certifies) plus input-closed asset
decoders proven byte-identical over the whole asset set. Running only that
tier keeps the wall-clock playable for fluent human demo recording while
every gameplay byte still comes from original ASM. Worked example:
`pre2.checkpoints.SAFE_ORACLE_HOOKS` + the `--safe-hooks` help text in
`pre2_port/scripts/play.py`.

**In-viewer verify/trace modes (`--verify-hooks`, `--trace-hooks`).**
→ P2 runs the ASM as oracle and diffs each recovered replacement at its
contract boundary live, with a periodic per-hook summary (`--verify-verbose`,
`--full-verify` = whole-machine diff); Overkill adds a strict headless hook
verifier and a differential frame verifier behind the same flag family
(`--verify-frames`, budgets, PNG dumps). Worked examples:
`pre2_port/scripts/play.py`, `overkill_port/scripts/play.py` +
`overkill/verification.py`, `overkill/headless_verification.py`.

**Non-VGA video (CGA / Tandy / EGA pages / text) in the viewer.**
→ The library's default decoder covers VGA mode 13h + the 320x200 planar
path. Overkill's viewer decodes CGA `B800h` (palette-selectable) and Tandy,
publishing immutable VRAM snapshots to the UI thread. Worked example:
`overkill_port/scripts/play.py` (`--video cga|ega|tandy`, `--palette`).

**Viewer audio.**
→ Faithful OPL/AdLib: forward the VM's register stream to the vendored
Nuked-OPL3 backend (`dos_re.dos.set_adlib_callback` + `pynuked_opl3`; worked
examples: `overkill_port/scripts/play.py --adlib-audio`,
`ancient_port/scripts/play.py`). Digital SB-DMA games: pump the emulated DMA
blocks to the host mixer (worked example: P2 `--audio adlib`, and
`docs/pre2/audio_architecture.md` for the enhanced SDL_mixer path).

**Convenience fast-paths behind flags (never silent).**
→ Recovered accelerators the human toggles: P2's `--fast-song-load`
(byte-exact MOD-loader fast-forward, default ON live / OFF for replays so old
demos still verify) and `--fast-adlib` (mute the hot AdLib thunk to reach
graphics fastest). The pattern: default them by MODE, record them in demo
metadata, document the byte-exactness status in the help text. Worked
example: `pre2_port/scripts/play.py`.

## Progress and process machinery

**Measured progress reporting (interpreted vs native %, per-island).**
→ **Now a framework engine**: `dos_re/coverage.py` (`CoverageCollector` on
`cpu.coverage_telemetry`; the adapter supplies only the address→island
classifier — wire-up snippet in dos_re's `docs/agent_toolbox.md` §13). The
richer game-side build-out — region grouping, category rollups, dashboards,
oversized-file flags — remains the worked example: `overkill/coverage.py`,
`overkill/scripts/source_port_status.py`, `scripts/audit_islands.py`.

**Documenting a subsystem campaign so the next session can continue it.**
→ The *island doc* pattern: per-subsystem markdown with a truth table (facts /
guesses / frontiers), gap list, and merge plan. Worked examples:
`pre2_port/docs/pre2/renderer_island.md`, `player_fsm_island.md`,
`object_system_island.md`; the evidence-table format is
`docs/pre2/symbol_ledger.md`.

**Running the recovery loop unattended overnight.**
→ **Shipped in this repo**: `scripts/overnight_loop.sh` (the relaunch
harness — a fresh agent against a standing goal brief whenever one stops;
all state in git + the ledgers, so nothing is lost) + the goal-brief
template `examples/ledgers/overnight_goal.md` (preconditions checklist,
done-condition, gates, work-queue buckets; run_status's frontier statement
overrides the queue). Deploy it in the MIDDLE of the port — the hook/lift
grind after the game is fully runnable and the corpus spans gameplay
(ideally e2e cold-start demos); the corpus is what makes unattended commits
safe. Bring-up and the flip's design decisions stay attended.
Worked examples of a long campaign's brief evolving:
`overkill_port/scripts/overnight_loop.sh`,
`docs/overkill/overnight_endgame_execution.md`, `loop_blockers.md`. The
invariants are already in `START_HERE.md`; this is the harness that
enforced them for months.

**Shipping the finished native port.**
→ Deployment pattern: copy the import-closure of the native entry point into a
standalone folder, prove every import resolves *inside* that tree, smoke-run
it, optionally wrap with PyInstaller. Worked example:
`pre2_port/scripts/deploy_native.py`.

## Presentation (Stage 6 — only after the faithful game is complete)

**Making it look/feel modern without touching gameplay.**
→ [`enhancements.md`](enhancements.md) has the rules; the worked examples are
P2's enhanced layer: render-intent model (`pre2_port/docs/pre2/render_model.md`,
`enhanced_renderer_design.md`), frame interpolation over a two-snapshot rolling
window (`pre2/bridge/frame_capture.py`), smooth transitions
(`pre2/enhanced/transition_controller.py`), and the F10 overlay menu
(`pre2_port/scripts/overlay_menu.py`, pure pygame-surface UI).
