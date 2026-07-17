# The standalone CPUless port

The **standalone CPUless port** runs SkyRoads through a pure-Python recovered
corpus with **no CPU, no interpreter, no lifted graph** — the true hard wall.
It is distinct from the *hybrid* surface (`scripts/play_cpuless_hybrid.py`),
which overlays the recovered corpus on the interpreter driver.

## Source vs generated

| Kind | Path | Tracked? |
| --- | --- | --- |
| **Source** — census inputs | `artifacts/codemap/{recovery_ir,observed}.json`, `boundary_heads.txt`, dispatch tables | yes |
| **Source** — manual overrides | `skyroads/cpuless_overrides/func_CCCC_IIII.py` (address-keyed) | yes |
| **Source** — runner + tools | `scripts/play_cpuless.py`, `scripts/build_cpuless_standalone.py`, `tools/lint_cpuless.py` | yes |
| **Committed record** — manifest | `artifacts/codemap/cpuless_manifest.json` | yes |
| **Committed** — recovered corpus | `skyroads/cpuless_standalone/` (the recorded no-CPU port surface) | **yes** |
| **Generated** — scratch adapters | `artifacts/cpuless_standalone_adapters/` (hybrid-only, unused here) | no |

The recovered corpus **is committed** — it is the recorded no-CPU port surface,
inspectable from a clean checkout. `scripts/build_cpuless_standalone.py`
regenerates it **in place**, deterministically, so a rebuild that changes any
byte shows up as a diff (drift detection). Only the CPU-carrying scratch
adapters and the build intermediates stay ignored.

## Overrides

A hand-written CPUless function goes in `skyroads/cpuless_overrides/` as an
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
python scripts/build_cpuless_standalone.py

# 2. prove the hard wall: no import path reaches a CPU (static AST proof)
python tools/lint_cpuless.py

# 3. boot the no-CPU runner from 1010:61F3 (fails loud at the recorded frontier)
python scripts/play_cpuless.py --headless

# one-shot: all of the above + count/manifest/frontier checks
python -m pytest tests/test_cpuless_standalone_smoke.py -q
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
182 `generated-cpuless`, frontier **0**. The runner boots from `1010:61F3`
through `CPUlessPlatformRuntime` with the interpreter import guard armed and
runs the recovered corpus with no CPU.

**Known frontier (fail-loud, by design):** a *cold boot from 61F3* reaches
early C-startup code the `--observed` probe never covered (e.g. the runtime-dead
exit at `1010:5FEA`), so the hard wall fires rather than guessing. The runner
reports the exact stop point and exits non-zero. Closing it is the next step —
the cold-boot **capture → close → promote** loop: extend `observed.json` to
cover the full startup from `61F3`, rebuild, and the classification matches what
the standalone actually executes.
