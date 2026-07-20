# Porting a new DOS game — the bring-up checklist

This is the concrete path from "an original EXE + its data files" to "the
oracle-driven recovery loop is running". The full method is in
[`ai_porting_charter.md`](ai_porting_charter.md); this guide is the ordered
to-do list with the framework touchpoints named.
[`START_HERE.md`](../START_HERE.md) is your boot sequence (it routes here) and
[`pitfalls.md`](pitfalls.md) is the list of mistakes already made for you.

## Know your game's style first

The two source projects span the spectrum, and the recovery emphasis differs:

- **Code-heavy procedural games** (Overkill): behaviour lives in handler zoos,
  dispatch tables, runtime-patched routines, hardcoded choreography. Expect to
  invest in handler classification, shared-primitive detection, routine-family
  grouping, and staticizing patched code — the goal is recovering the implicit
  actor/choreography model hiding inside the spaghetti, not rewriting each
  handler forever. The Overkill repo is the worked example of these tools.
- **Data-driven games** (script/bytecode-interpreter engines): behaviour lives
  in data the engine interprets. Expect to invest in loaders, format decoders,
  and verifying the interpreter's opcodes one by one — round-trip decode tests
  carry more of the proof there.

Most games mix both. Classify early (a few hours of tracing tells you), and
let it steer where the first islands go.

## How the numbering systems line up

Three docs count differently; they describe one process:

| This guide (steps) | [`lifecycle.md`](lifecycle.md) (stages) | [`ai_porting_charter.md`](ai_porting_charter.md) (phases) |
|---|---|---|
| 0–6 (bring-up: adapter, boot, output, boundaries, verifier, input waits, first demo) | Stage 0 | charter §9 (bootstrapping checklist) |
| 7 (the lifting loop) | Stages 1–2 | Phase 1 |
| 8 (coverage telemetry) | ongoing | §10 (metrics) |
| Endgame steps 1–5 | Stages 3–5 | Phases 2–6 |
| Endgame step 6 (enhanced layer) | Stage 6 | after Phase 6 |

## 0. Set up the adapter

Copy the shape of [`examples/adapter_skeleton/`](../examples/adapter_skeleton/README.md)
into your own package (`mygame/`). From day one, enforce the boundaries:
`dos_re` never learns your game; your `recovered/` layer never imports `dos_re`.
Extend `dos_re/tools/lint.py`'s pattern with those rules.

## 1. Load & run

```python
from dos_re.runtime import create_runtime
rt = create_runtime("assets/MYGAME.EXE", command_tail=b"")
rt.cpu.run(1_000_000)
print(rt.cpu.addr(), rt.cpu.instruction_count)
```

- If the EXE is packed (LZEXE etc.): the unpacker is *bootstrap, not gameplay*.
  Run it once (`dos_re/bootstrap_lzexe.py` accelerates the LZEXE 0.91 loop; a
  different packer needs its own accelerator or patience), then
  `write_snapshot` past it and work from the unpacked image.
- When the interpreter hits an unsupported opcode or DOS/BIOS call: decode the
  exact instruction, implement only the required behaviour, match flags for the
  observed use, add a focused test in `tests/test_core.py` style. Don't
  generalize beyond what the executable proves it needs.
- Add a snapshot point after init/first playable state.

## 2. See output, wire input

- See the screen: `python tools/render_frame.py <snapshot_dir>` renders a
  snapshot's video memory to PNG (VGA mode 13h and EGA/VGA planar, using the
  saved DAC palette and display start). If your game uses a mode it doesn't
  cover (CGA, Tandy, text), your adapter grows a rasterizer — the tool is the
  template.
- Watch it live: copy this repo's [`scripts/play.py`](../scripts/play.py)
  (a thin `GameFrontend` over `dos_re.player`, the unified play runner) and
  fill in its GAME-SPECIFIC blocks — that IS your port's runner and the
  human owner's window from day one: viewer by default (`--headless` to
  disable), snapshot save/resume, demo record/replay, F10/F11/F12 hotkeys,
  paced with `--present-hz` / `--steps-per-frame` / `--timer-irqs-per-frame`.
  Keep the standard flag names; the cookbook's play-runner entry maps the
  game-specific ideas (pacing models, `--safe-hooks` tiers, verify modes) to
  worked examples. (`python dos_re/tools/view.py --exe assets/GAME.EXE` is
  the zero-setup fallback — the same runner with no adapter at all.)
- Deliver keys via `dos_re.interrupts.deliver_scancode` and confirm the game's
  key-state table updates (most action games poll their own INT 09h ISR state,
  not BIOS).

## 3. Find the frame boundaries

Identify, in the original code: the PIT/timer wait, the CRT retrace wait, and
the present/blit routine(s). `dos_re/tools/profile_hotspots.py` (tight backward
edges = wait loops) and `dos_re/tools/lindis.py` (read the code at a snapshot)
are the workhorses here. These addresses become your frame-verify boundary hooks and
`reference_env_hooks`.

## 4. Stand up the frame verifier

Adapter `frame_verify.py`: boundaries + a `sample_builder` (framebuffer +
visible VRAM first). Confirm a no-op candidate (no hooks) matches the oracle
frame-for-frame before trusting anything else.

## 5. Build the input-wait registry (before any demo)

Find the boundary-less poll loops (title/menu/"press fire") and register their
canonical head addresses in the adapter's `input_waits.py`, consumed by every
driver. Read [`demos_and_snapshots.md`](../dos_re/docs/demos_and_snapshots.md) — recording
demos before this step produces proofs that freeze or lie.

## 6. Record the first demo

Drive menus into gameplay; confirm the demo replays identically under every
driver (interactive, headless, frame verifier). This demo is your first
regression asset: record into `artifacts/` (scratch), then **promote** it to
`artifacts/test_oracles/` with a `docs/<game>/demo_manifest.md` entry — the
storage convention is in
[`demos_and_snapshots.md`](../dos_re/docs/demos_and_snapshots.md#where-evidence-lives-git-convention).

You can usually script this first demo yourself (a few keys through the menus
into gameplay). For demos that need real *play* — clearing a level, reaching a
boss, dying in a specific way — **ask the human**: give them the exact command
(`python scripts/play.py --record-demo NAME`), what to do in-game, and where
the artifact lands. The human records; you promote it into the corpus and it
becomes a proof. (Corpus breadth matters more than skill — deaths, game-overs
and full playthroughs are the proof spine's spine; pitfall #22.)

## 7. Start the lifting loop

Start with the hot, well-bounded **leaf** routines — asset decompression and
decoders, blitters, tile/sprite drawing, palette handling. They have clean
verifiable boundaries, they make the interpreted VM dramatically faster, and
each one makes the system more observable (both source ports started exactly
here). Then move inward to the densest gameplay routines the profiler shows.

For each slice: trace → snapshot fixture → thin hook over a pure recovered
rule → declare its `HookStop` (or use strict mode) → verify against the ASM
oracle → document. One routine, one verification, per slice. See
[`hooks_and_verification.md`](../dos_re/docs/hooks_and_verification.md), and
[`lifecycle.md`](lifecycle.md) for where this stage sits in the whole arc.

**Optional accelerator — don't hand-translate the first draft.** The
automatic lifter (`dos_re/docs/lifting_design.md`) turns a known function
entry into a literal, verified Python hook mechanically, so you refactor from
a proven artifact instead of decompiling from scratch. Two commands:

```bash
# 1. Census: which of these entries can be lifted, and why not?
python dos_re/tools/liftgen.py --exe assets/GAME.EXE --snapshot <snap> \
    --entries-file docs/<game>/candidates.txt

# 2. Lift + prove in situ against the ASM oracle (writes a proof ledger):
python dos_re/tools/liftverify.py --exe assets/GAME.EXE --snapshot <snap> \
    --entry CS:IP --steps 5000000 --emit-dir mygame/lifted
```

`liftverify` runs each lifted function against the interpreted original every
time it executes and reports `ORACLE_PASSING` / `DIVERGED` / `NOT_REACHED`
(pick a snapshot where the target actually runs). A passing lift is a correct
replacement island *for free* — then the AI's job is to rename/simplify it
into clean recovered Python, with the same oracle keeping it honest. Lifted
functions live in their own `mygame/lifted/` tier and their own proof ledger;
they count as recovered **only after** you refactor them and tag them
`@oracle_link` (see the cookbook's "Automatic lifting" entry). ~95% of a
lifted function is literal per-instruction Python; the rest is exact
interpreter-fallback lines that mark what to look at first.

Tag every recovered function with `@oracle_link(boundary, contract, status,
merge_target)` from `dos_re.islands`, and generate your island manifest from
the code (`python tools/gen_island_manifest.py mygame.codecs mygame.recovered
-o docs/recovered_islands.md`) with a drift test. The ledger — not vibes — is
what tells you how far the port is.

## 8. Stand up coverage telemetry

The collector is a **framework engine now** — `dos_re.coverage.CoverageCollector`
implements the `cpu.coverage_telemetry` hook points; your adapter supplies
only the address→island **classifier**:

```python
from dos_re.coverage import CoverageCollector
cov = CoverageCollector(classifier=my_addr_to_island,
                        cache_path=Path("artifacts/coverage_cache.json"))
rt.cpu.coverage_telemetry = cov
# ... replay a demo ...
print(cov.format_summary()); cov.save_cache()
```

The headline metric, defined precisely so reports are comparable: **native %
= hook-covered ASM-equivalent instructions / all measured work**, accumulated
over a full demo replay. Hooked work is measured by the verifier (the
replaced-instruction count per verified call), estimated from the JSON cache
on unverified runs, and reported *unmeasured* — outside the percentage —
when neither exists; wrap oracle reference runs in `cov.bounded_original()`.
Report it overall and per island. The richer game-side build-out (regions,
category rollups, a live dashboard) is `overkill/coverage.py` (see
[`cookbook.md`](cookbook.md) "Progress and process machinery").

## Then: the phased roadmap

Phase 1 lift rules → Phase 2 collapse understood chains → Phase 3 decode all
game data natively → Phase 4 earn the native world model → Phase 5 native
backends → Phase 6 flip the engine, keep the VM as oracle. Details and exit
criteria per phase: [`ai_porting_charter.md`](ai_porting_charter.md) §7.

## The endgame — what "flip the engine" concretely takes

These are the pieces the Prehistorik 2 port needed to go from "hybrid plays"
to "a standalone native game ships" (full rationale in
[`lifecycle.md`](lifecycle.md) stages 4–5):

1. **Boot constants.** Extract the post-bootstrap initialized state (the
   tables the EXE builds before the first frame) into native data, so the
   native game cold-boots from the data files alone — no EXE, no snapshot at
   runtime.
2. **A native state + tick driver.** A byte-backed game state (the state
   mirror) plus a fixed-step frame driver at the original tick cadence, with
   explicit pacing. Do **not** port the busy-wait/retrace machinery; preserve
   the heartbeat, not the spin. **From day one, the native runner writes a
   resumable snapshot + prints the exact repro command on every gap/crash**
   (state image + a short state summary; `--snapshot <dir>` re-seeds from
   it). `dos_re.player` gives VM runners this for free; the VM-less runner
   implements the same — it is the endgame's biggest debug accelerator
   (worked example: the completed port's `dump_gap_snapshot`).
3. **Per-subsystem equivalence contracts.** Gameplay byte-exact; rendering
   pixel-exact but mechanism-flexible; audio event-exact but mixer-flexible;
   input semantic-exact. Write these down for your game before flipping, or
   you will argue every divergence twice.
4. **The tick-equivalence harness.** Replay a recorded demo through the ASM
   oracle and the native core tick by tick and compare the data-segment image
   byte-exact. This — over a demo corpus that reaches death, respawn,
   level-end, and game-over — is the proof the flip changed nothing.
   **The engine is a framework tool now**: `dos_re.tick_demo`
   (`record_ticks`/`verify_ticks`/`masked_digest`; usage skeleton in dos_re's
   `docs/agent_toolbox.md` §12). Your adapter supplies the tick seams, the
   consumed-key capture points, the ownership mask, any sidebands
   (instruction-count-derived state like an idle-fidget timer must be
   *recorded and injected* — the native port has no instruction count), and
   the tick function with its transition outcomes. Worked example of the whole
   pipeline: pre2_port's `game_tick_demo.py` + `verify_native_tick_demo.py`.
5. **A verification switch.** ON: the oracle runs beside the native game and
   diffs at boundaries. OFF: no VM starts. The shipped build contains no VM,
   no EXE, no fallback.
6. **Only now, the enhanced layer.** Widescreen, interpolation, scaling and
   friends are lifecycle Stage 6 — built on top of the *complete* faithful
   game, never during recovery ([`enhancements.md`](enhancements.md),
   pitfall #24). The audio-style exception (small, separable, fixes something
   that disrupts the recovery workflow itself) needs explicit justification in
   your run_status ledger.

**Audio deserves its own plan** (usually the longest pole, charter §8).
Recover it in layers, with the emulated Sound Blaster/OPL + the original ASM
driver as the *oracle path*, never the final architecture: asset decode → a
typed data model → the sequencer/tracker → the mixer (verify: same state +
events + timing → same PCM block against the emulated device's output) → then
detach the native game from the ASM audio path entirely.

## What is game-specific (yours to write)

Boot constants and EXE identity/signatures; command-tail policy; asset codecs;
DGROUP layout + state views ([`state_mirrors.md`](../dos_re/docs/state_mirrors.md)); hook
registrations + continuation metadata; frame boundaries + sample builder;
input-wait registry; island/coverage classification; the recovered logic
itself. The framework gives you the machine, the proof engines, and the
method — the knowledge of *your* game is earned from *your* oracle.
