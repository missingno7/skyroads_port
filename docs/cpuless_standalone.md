# The standalone CPUless port

The **standalone CPUless port** runs SkyRoads through a pure-Python recovered
corpus with **no CPU, no interpreter, no lifted graph** — the true hard wall.

## Source vs generated

| Kind | Path | Tracked? |
| --- | --- | --- |
| **Source** — census inputs | `artifacts/codemap/{recovery_ir,observed}.json`, `boundary_heads.txt`, dispatch tables | yes |
| **Source** — manual overrides | `skyroads/recovered_overrides/func_CCCC_IIII.py` (address-keyed) | yes |
| **Source** — runner + tools | `scripts/play_cpuless.py`, `scripts/build_recovered.py`, `tools/lint_cpuless.py` | yes |
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

## Reproduction (clean checkout)

Requires your own game files under `assets/` and a built boot image
(`python scripts/build_boot_image.py`), like every replay in this repo.

```sh
# 1. regenerate the complete standalone corpus + manifest from tracked inputs
python scripts/build_recovered.py

# 2. prove the hard wall: no import path reaches a CPU (static AST proof)
python tools/lint_cpuless.py

# 3. boot the no-CPU runner from 1010:61F3 (fails loud at the recorded frontier)
python scripts/play_cpuless.py --headless

# one-shot: all of the above + count/manifest/frontier checks
python -m pytest tests/test_cpuless_smoke.py -q
```

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
