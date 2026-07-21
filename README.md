# SkyRoads recovery port

This repository recovers SkyRoads against the original DOS executable using
the dos_re 3.0 execution and replay architecture.

The port has one player:

```text
python scripts/play.py
```

The default development composition is the recovered product: the generated
VMless frontend and level-selection flow surrounding one authored native
gameplay region. Use `--composition oracle` for the untouched original, or
`--composition workbench-auto` for the deliberately fragmented per-function
recovery workbench.
The window defaults to a desktop-safe 2× scale; use `--scale 3` or resize it
after launch on a larger display.

Execution policy and implementation composition are separate:

```text
# untouched interpreted oracle
python scripts/play.py --profile development --composition oracle

# automatic workbench mix: authored, generated functions, interpreted frontier
python scripts/play.py --profile development --composition workbench-auto

# differential verification over a ReplayArtifact
python scripts/play.py --profile verification --composition workbench-auto --play-replay artifacts/replays/replay_name --verify-start 100 --verify-end 180

# canonical generated-shell/native-gameplay product (also the default)
python scripts/play.py --profile development --composition faithful-product

# direct launch through the same generated loader and gameplay region
python scripts/play.py --level 14

# generated CPUless implementation while recovery frontiers remain visible
python scripts/play.py --profile development --composition generated-detached --headless

# strict readiness report (currently rejects named Atlas frontiers)
python scripts/play.py --profile release --composition generated-detached --plan-only

# rebuild/query the persistent evidence map
python scripts/build_atlas.py --from-ir
python dos_re/tools/atlas.py unresolved recovery/atlas --json

# materialize the declared bootstrap; export remains blocked until closure
python scripts/build_boot_image.py
```

`skyroads.execution` is the single implementation catalog and composition
authority; `recovery/atlas` is the persistent coverage model.
Generated VMless and CPUless code are baseline providers; authored faithful
replacements are explicit semantic-plus-adapter pairs. Each authored body has
distinct interpreted-CPU and generated-VMless carrier adapters, and the plan
reports the exact remaining cross-owner boundaries. Selecting a larger owner
collapses its internal hook edges. In `faithful-product`, the generated menu
hands `1010:2317` to the long-lived authored `skyroads.gameplay` region; it
owns the recovered `2324-2AF8` body plus the oracle-derived `1FD9` pacing and
presentation loop over shared DOS memory. It batches bodies until the original
stack-local tick catches `DS:[1600]`, parks at `1010:22FB`, and returns the
original raw handler result without assigning product-lifecycle meaning at the
region seam. The separate `23CA-241E` road-departure path resumes generated
`1010:0F05`; abort returns raw result seven. Native SFX cross one explicit
external adapter into
the selected generated `1010:03C2` implementation, retaining its DOS-memory
and device effects while preserving the region's CPU-independent context.
The ABI-recovered provider
will select an authored body only once that body has a separately verified
CPUless ABI adapter. Frame parking is a product-safe runtime service rather
than an implementation override. Importing an adapter never installs it.

Normal play enters through the generated level-selection function. `--level N`
supplies the first confirmed selection at that same stable seam; all generated
loading and gameplay setup still run normally. The adapter removes itself
immediately. The generated `2B3D/01B8` callers alone decide whether a raw
gameplay result retries the level or returns to selection, and alone advance
campaign state. The native region never starts level N+1.

Product features are separate from implementations. For example,
`--practice-level-position 0x123 --record-replay practice` records an explicit
behavioral event and applies it only at SkyRoads' main-loop boundary. Faithful
oracle verification does not silently treat that intentional divergence as a
replacement failure.

Authored source has two enforced layers. `skyroads.handrecovered` contains
CPU-independent semantic algorithms; `skyroads.native` contains state-backed
subsystem assemblies, renderers, carrier-facing views, and detached-state
experiments. Every module has an explicit role and use classification in
`skyroads.authored_inventory`. Tests walk imports from selected implementations
and reject production runtime modules that are silently disconnected. Evidence,
experiments, and partial products do not become providers merely because a test
imports them.

The selected `BuildImageBootstrapProvider` declares `state.json`,
`memory_1mb.bin`, and `manifest.json`, including their packaged paths and the
command that generates them. Release planning reports missing bootstrap inputs
and unresolved Atlas control-flow sites before launch. Once both are closed,
export materializes the provider, rejects original executables and
interpreter/development imports, and publishes only the audited runtime,
bootstrap, and data closure. Code poisoning remains optional additional
evidence, not release authority.

`ReplayArtifact` is the only persistent record/replay format. Interactive
capture may use any responsive development composition, including already
replay-backed faithful overrides. Such a capture is provisional until the
immutable
input stream replays completely against the untouched oracle without
divergence. Post-hoc oracle replay also attaches function visits and observed
control-flow evidence, independently of capture. Literal generated functions
are green over the committed exact interval with complete continuation-state
comparison. Authored faithful candidates retain focused semantic tests and
explicit provenance; they become replay-verifiable by exposing the same game
boundaries and canonical state/effects, not by imitating assembler instruction
counts.
Every green result is scoped to its exact replay and interval. It is useful
evidence for continued development, not a claim that the function is correct
for inputs the corpus has never exercised.

See [current documentation](docs/README.md). Pre-3.0 recovery notes are kept
under `docs/history/` as evidence only.

## Development

Python 3.11 or newer is required. The original game files are not included.

Run the complete repository gate:

```text
python scripts/check_all.py
```

`python scripts/check_all.py --quick` is available for inner-loop work. The
complete gate also validates the retained replay differential and strict
release preflight.

The reusable framework is the `dos_re/` submodule. SkyRoads-specific addresses,
formats, implementations and generated corpora stay in this repository.

## License

MIT for this repository. Game assets and executables remain the property of
their rights holders and must be supplied separately.
