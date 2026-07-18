# The standalone CPUless port

The **standalone CPUless port** runs SkyRoads through a pure-Python recovered
corpus with **no CPU, no interpreter, no lifted graph** — the true hard wall.

## Source vs generated

| Kind | Path | Tracked? |
| --- | --- | --- |
| **Source** — census inputs | `artifacts/codemap/{recovery_ir,observed}.json`, `boundary_heads.txt`, dispatch tables | yes |
| **Source** — manual overrides | `skyroads/recovered_overrides/func_CCCC_IIII.py` (address-keyed) | yes |
| **Source** — runner + tools | `scripts/play_cpuless.py`, `scripts/build_recovered.py`, `tools/lint_cpuless.py` | yes |
| **Source** — shared frame model | `skyroads/cpuless_driver.py` (runner *and* verifier) | yes |
| **Committed record** — manifest | `artifacts/codemap/cpuless_manifest.json` | yes |
| **Committed** — recovered corpus | `skyroads/recovered/` (the recorded no-CPU port surface) | **yes** |
| **Generated** — scratch adapters | `artifacts/recovered_adapters/` (hybrid-only, unused here) | no |

The recovered corpus **is committed** — it is the recorded no-CPU port surface,
inspectable from a clean checkout. `scripts/build_recovered.py`
regenerates it **in place**, deterministically, so a rebuild that changes any
byte shows up as a diff (drift detection). Only the CPU-carrying scratch
adapters and the build intermediates stay ignored.

## Overrides

A hand-written CPUless function goes in `skyroads/recovered_overrides/` as an
address-keyed `func_CCCC_IIII.py`. The build layers it **over** the generated
body for that address, and the manifest records it as `manual-cpuless-override`.
An override must match the generated interface and import nothing outside the
recovered package (the purity lint enforces this). None are needed today — the
generator covers all runtime-reachable functions.

## Working on it: three commands

```sh
python scripts/rebuild_all.py      # the pipeline, in the one correct order
python scripts/check_all.py        # every gate, one verdict (--quick to skip diffs)
python scripts/coverage_audit.py   # find coverage gaps before a player does
```

**Order matters and used to be tribal knowledge.** The stages are
`build_codemap` → `close_vmless_wall` → `build_recovered`, each consuming the
previous one's output. Skipping the middle one is silent: functions the new
census discovered get no IR entry, every caller of one refuses `contains-call`,
and that cascaded to five refusals including `1010:61F3` — the C-startup root —
leaving the runner unable to import its entry point. The output said
"5 refused", not "you skipped a stage". `rebuild_all.py` owns the order, and
`build_recovered.py` independently refuses to build against an IR older than the
census, so the mistake is caught from both sides.

`check_all.py` runs the cheap gates first, then the two frame-exact
differentials — which are the ones that actually prove the port and therefore
the ones most likely to be skipped by hand. (It earned its place immediately by
catching a test that only passed when run from one directory.)

### The interpreter split (2026-07-18) — the differentials run under PyPy

The oracle-stepping gates are pure-Python instruction interpretation, PyPy's
best case; the test suites are fixture-bound and want CPython + `-n auto`
instead (every PyPy worker re-pays JIT warmup). `check_all.py` therefore picks
`pypy3` off PATH for the differentials automatically and prints the choice per
gate (`(48s, pypy)`), CPython for everything else. Nothing else changes: same
script, same demo, same comparison, same exit status. `--no-pypy` forces
CPython everywhere; `SKYROADS_PYPY=` (empty) opts out via the environment.

Run a differential by hand the same way — `pypy3` is a drop-in, and `-u` keeps
the progress heartbeat live:

```sh
pypy3 -u scripts/verify_cpuless.py artifacts/demos/demo_attract_20260718_135434
```

**This is an optimisation, never a gate change, and it is only allowed because
the two interpreters were proven to agree.** Measured 2026-07-18 on the full
5,109-frame attract differential, run end-to-end under each:

| | wall clock | verdict |
|---|---|---|
| CPython 3.11 | **500.8 s** (8m21) | PASS, 5109 frames |
| PyPy 3.11 v7.3.20 | **47.3 s** | PASS, 5109 frames |
| | **10.6x** | sha1 of output **identical** |

Excluding only the timing heartbeats (which carry elapsed seconds by
construction), the two logs hash the same: `1da10d27…` — every per-frame line,
the `oracle peak 17126327 steps/frame` figure, and the verdict. **Re-run that
comparison after a PyPy upgrade before trusting the fast path.**

Two cautions on the number. **10.6x is the whole-harness figure, not the
~13–17x `dos_re/docs/performance.md` quotes** for steady-state raw
interpretation; the harness also builds 5,109 64 KB frame snapshots and
compares them, work that does not speed up as much. And **short runs gain far
less**: a 60-frame run measured only 5.4x (40.4 s → 7.5 s), because process
start plus JIT warmup is then most of the run. PyPy pays off on the long gates,
which is exactly where the cost is.

### Measured gate timings (2026-07-18, 24-thread Windows box)

Each run serially, so the numbers are not skewed by contention.

| gate | CPython | PyPy | note |
|---|---|---|---|
| `check_all.py` (all 7) | ~6 min † | **1 m 50 s** | 7/7, the differentials on PyPy |
| `check_all.py --quick` | ~70 s ‡ | — | suites; stays on CPython |
| port suite (`-n auto`) | 46 s | — | 464 passed / 1 skipped |
| dos_re suite | 23 s serial → **13 s** `-n auto` | — | was serial inside `check_all` |
| `verify_cpuless` (672 f) | 134 s | **18 s** | 7.4x |
| `verify_vmless` (672 f) | 142 s | **14 s** | 10.1x |
| `verify_cpuless --shadow-only` | 17 s | 8 s | oracle-free rung |
| `verify_cpuless` attract (5109 f) | 500.8 s | **47.3 s** | 10.6x |

Everything unmarked was timed end-to-end this session. † is the previously
recorded figure for the all-CPython run, carried forward, NOT re-measured here.
‡ is the SUM of the four cheap gates' measured times (2+46+14+8), not a timed
`--quick` run. Both are marked because an unmarked estimate becomes a quoted
fact one handoff later.

**What dominates**: the oracle. The three differential gates cost 293 s of the
all-CPython run against 70 s for the four cheap ones, and PyPy takes those 293 s
to 40 s. The cheap gates were never the problem, which is why the interpreter
split targets exactly those three and leaves the suites alone.

`coverage_audit.py` reads the game's own **dispatch tables** out of the boot
image and reports any entry the census never executed — each one a fail-loud
stop waiting to happen. For the block-type table it goes further and decodes
`ROADS.LZS` to name the levels carrying an uncovered block type, so a closing
demo can be aimed rather than guessed. This is the generalisation of how
`1010:2F57` was diagnosed, turned from a session of detective work into a report.

## Reproduction (clean checkout)

Requires your own game files under `assets/` and a built boot image
(`python scripts/build_boot_image.py`), like every replay in this repo.

```sh
# 1. regenerate the complete standalone corpus + manifest from tracked inputs
python scripts/build_recovered.py

# 2. prove the hard wall: no import path reaches a CPU (static AST proof)
python tools/lint_cpuless.py

# 3. PLAY it -- interactive window, live keyboard, no CPU anywhere
python scripts/play_cpuless.py

# one-shot: all of the above + count/manifest/frontier checks
python -m pytest tests/test_cpuless_smoke.py -q
```

## Playing it

`scripts/play_cpuless.py` is a playable game, not a boot probe. The default is an
interactive pygame window with live keyboard, running until you close it:

```sh
python scripts/play_cpuless.py                 # play (320x200, 3x, 30 Hz)
python scripts/play_cpuless.py --scale 4 --square-pixels
python scripts/play_cpuless.py --headless --frames 30   # agents/CI: no window
```

Everything on screen is produced by the recovered corpus. The runner imports no
CPU: an `__import__` guard is the runtime backstop and `tools/lint_cpuless.py` is
the static proof. Presentation borrows only CPU-free dos_re leaves
(`display`, `framebuffer`, `keyboard`).

Two details the window path depends on:

- **Keys** go through `dos_re.keyboard.KeyDispatcher`, which holds each make for
  at least one frame before delivering its break — SkyRoads polls its key state
  once per frame, so an un-deferred tap would be set and cleared unseen.
- **Window size** comes from the game's framebuffer (320x200), not the first
  decoded frame: the runner boots at the C-startup root while the machine is
  still in *text* mode, so decoding then would size the window to the boot
  console. `Display.draw_game` letterboxes whatever arrives, so the brief
  text-mode phase still displays.

The runner and the `verify_cpuless` differential share one frame model
(`skyroads/cpuless_driver.py`). That is deliberate: a differential that proved a
verification-only lookalike would prove nothing about the shipped runner.

## The manifest

`artifacts/codemap/cpuless_manifest.json` records every discovered function and
its status:

- `generated-cpuless` — a generated recovered implementation;
- `manual-cpuless-override` — an address-keyed hand-written override;
- `native-platform-replacement` — replaced by a platform/native effect;
- `fail-loud-unsupported` — runtime-reachable but not promoted (a live frontier);
- `dead-unreachable` — reached only via an untaken call, no runtime evidence;
- `data-non-function` — an IR entry that is data, not code.

It also carries `runtime_frontier`, `runtime_closure_complete`, and
`static_only_addresses` (non-function resume/external targets reached only via
untaken calls — reported, never dropped).

## Current status

Against the committed `observed.json` trace the runtime closure is **complete**:
182 `generated-cpuless`, frontier **0**. `play_cpuless.py` cold-boots from
`1010:61F3` through `CPUlessPlatformRuntime` with the interpreter import guard
armed, runs the **entire** Borland C-runtime startup + intro `LZS` decompression
to the frame loop, and **renders frames** — all with **no CPU, no interpreter,
no lifted graph**. A 30-frame run reports `VGA nonzero px=48891`, matching the
byte-exact `play_vmless` output.

Two pieces make the cold boot work:
- **DOS arena restore** (`snapshot_headless._restore_dos_state`, CPU-free): the
  C heap `int 21h/48h` allocation must run against the post-load arena, or the
  startup takes its out-of-memory path (`5FEA`).
- **Frame scheduler**: each boundary delivers the frame's 6 timer IRQs through
  the game's own recovered INT 08h ISR (`func_1010_3b17`), advancing `ds:[1600]`
  so the tick-wait exits and the recovered body renders the next frame.

## Frame-exact verification (`verify_cpuless.py`)

`scripts/verify_cpuless.py` is the CPUless analogue of `verify_vmless_demo.py`:
it drives the committed `skyroads/recovered/` corpus through
`CPUlessPlatformRuntime` (NO CPU) and diffs every frame's VGA plane + DAC palette
against the interpreted ASM oracle over a cold demo.

```sh
python scripts/verify_cpuless.py artifacts/demos/demo_cold_20260718_003412
```

It **PASSES all 672 frames** of the full cold playthrough (intro → menu → level
select → play → die → leave → intro), byte-exact, with no CPU / no interpreter /
no lifted graph. Three mechanisms make an interactive playthrough reproduce
frame-exact:

- **Input timing**: each frame's demo input is applied to the upcoming frame at
  the boundary that captured the previous one, so input *N* affects frame *N*'s
  render (matching the oracle's apply-before-run).
- **Blocking reads**: a press-any-key `INT 21h AH=07` on an empty buffer must
  *wait*. The harness clears `console_input_fallback` (no phantom Esc) and
  installs `CPUlessPlatformRuntime.blocking_read_cb`, which advances a frame in
  place (frozen screen + IRQ-driven palette fade) until the awaited key arrives,
  then the read retries — the CPUless equivalent of a flat CPU rewinding its IP.
- **Boundary-model coverage**: the cold demo is timed to the 2nd-pass boundary
  cut, so `build_codemap.py` observes `BOUNDARY_DEMOS` through the same
  `run_to_cut` model the runtime reproduces (not the steps-per-frame front-end,
  which diverges). Without it the census misses the real end-game code and the
  recovered build fail-loud-stubs reachable exits (`1010:2EFC`, …) as
  runtime-dead.
