# Roadmap

The framework is a living organism: it grows when a real game needs it to,
under the rules in [`AGENTS.md`](../dos_re/AGENTS.md) (evidence-driven, stdlib-only
core, no invention without an oracle). This file separates "next", "when a
port needs it", and "long-term shape" so agents don't re-litigate scope every
session. Items graduate off this list into `MIGRATION.md` when they land.

## Short-term (framework-side, no game required)

- ~~tiny_frame_game example~~ **Done** (`examples/tiny_frame_game/`): a
  synthetic frame-loop game driven through oracle boot, cold-start demo
  record/replay, snapshot restore, hook oracle (wrong + correct), frame
  verifier lockstep, and a state mirror — also serving as the repo's only
  full-stack integration test.
- ~~Opt-in strict-ports mode~~ **Done**: unmodeled port reads are always
  recorded (`dos.unmodeled_port_reads`) and `dos.strict_ports = True` makes
  them fail loud (`UnmodeledPortRead`); the proven return-0 default is
  unchanged. A broader `dos_re/tools/` audit-fallbacks scan remains a nice-to-have
  (the manual ritual is `prompts/audit_no_silent_fallbacks.md`).
- **Diagram pass** — the oracle loop and recovery-geography diagrams exist in
  ASCII; consider rendered versions if the docs ever get a site.

## When the next port needs it (parameterize-and-promote candidates)

These exist as proven, game-entangled code in the source repos and should be
promoted the moment a second game wants them — not before. **The
problem-indexed guide to all of them (symptom → technique → worked-example
path) is [`cookbook.md`](cookbook.md)**; MIGRATION.md §"documented-as-pattern"
is the provenance record. The shortlist:

- **Timing fast-forward** engine (closed-form wait collapsing; needs a
  per-game loop classification + clock model).
- ~~Tick-demo equivalence harness~~ **Done** (`dos_re/tick_demo.py`):
  `TickDemo` (seed + per-tick consumed keys + gameplay digests + named u16
  sidebands), `masked_digest`, `record_ticks` (seam-address recorder with the
  consumption-point refine pattern), `verify_ticks` — generalized from
  pre2_port's `game_tick_demo.py`. The adapter supplies the seams, the
  ownership mask, and the tick function; dos_re's `agent_toolbox.md` §12 is
  the usage skeleton.
- ~~Coverage-telemetry classifier~~ **Done** (`dos_re/coverage.py`): the
  generic collector engine from `overkill/coverage.py` — verifier-measured
  ASM-equivalent accounting, cache-estimated unverified runs, loud
  UNMEASURED bucket, `bounded_original` oracle spans, per-island report; the
  adapter supplies only the address→island classifier. Overkill's regions /
  category rollups / Tk dashboard stay game-side.
- **Headless verification driver** (pluggable runtime factory).
- **Probe harness + walk-shadow cache** (delta-encoded per-frame state cache).
- ~~Runtime-code staticization registry~~ **Done** (`dos_re/runtime_code.py`):
  `RuntimeCodeSlot`/`RuntimeCodeVariant`/`RuntimeCodeStaticization`, variant
  identification against caller-supplied slot tables, the staticization-ready
  gate, and an opt-in write tracer for discovering installers — generalized
  from Overkill's own `overkill/runtime_code.py` (a materially richer
  mechanism than the flat `self_disable_if_patched`/`code_matches` guards
  already in `dos_re/hooks.py`, which stay as the simpler single-variant case).
- **Report generator / progress dashboard** (hooks by status, demo pass rate,
  interpreted-vs-native %, maturity counts, divergence list — the
  `source_port_status.py` pattern).
- **State-view extensions** — U32/pointer/bitfield/fixed-point descriptors,
  native↔byte sync helpers: add when a real game's layout demands them.
- **Boundary-clock abstraction** — today the boundary/clock contract is
  documented discipline (charter §6) + the adapter's input-wait registry;
  promote a shared `BoundaryClock` type if a second game's drivers start
  drifting despite the docs.
- ~~In-game overlay settings menu~~ **Done** (`dos_re/overlay_menu.py`):
  promoted from pre2_port — pygame-injected, items-as-data closures, modal
  determinism firewall, and the tab accuracy taxonomy (presentation /
  Experimental quarantine / debug-gated cheats) in its docstring. Playbook:
  [`post_endgame.md`](post_endgame.md).
- **Two-snapshot interpolation presenter** (`pre2/bridge/frame_capture.py`
  pattern) — gated on a SECOND port reaching Stage 6.

## Longer-term shape

- **CLI** (`dos-re init/run/record-demo/replay/verify-hook/verify-frames/
  report ...`) — a standard ritual so every port stops reinventing runner
  scripts. Design it *after* the tiny_frame_game example exists, so the CLI
  wraps a demonstrated workflow instead of speculation.
- **Two recovery styles as first-class guidance** — code-heavy procedural
  games (Overkill-like: handler zoos, dispatch tables, runtime-patched
  choreography) need handler-classification and routine-family tooling;
  data-driven games (script/bytecode interpreters) need loader/format/opcode
  verification tooling. The framework should serve both; today the docs note
  the distinction ([`porting_new_game.md`](porting_new_game.md)) and the
  Overkill repo holds the worked procedural example.
- **Issue templates / contribution scaffolding** — worth adding when a second
  contributor (human or agent fleet) actually shows up.
- **Paper/talk material** — the methodology docs are written to be
  distillable into a blog post / talk / paper; keep them that way.

## Non-goals (decided, don't reopen without new evidence)

- **General-purpose emulation** (DOSBox replacement, broad hardware matrices
  beyond what oracles demand).
- **Deep package restructure** of the flat `dos_re/` core into
  `cpu/ memory/ video/...` subpackages — the flat, tightly-coupled core is
  the proven shape from both source projects; cosmetic nesting adds churn
  and import risk without capability. Revisit only if the core grows well
  beyond its current ~25 modules.
- **Silently-degrading compatibility modes** of any kind.
