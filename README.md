# SkyRoads recovery port

This repository recovers SkyRoads against the original DOS executable using
the dos_re 3.0 execution and replay architecture.

The port has one player:

```text
python scripts/play.py
```

The default development composition uses authored faithful candidates plus
the product-safe frame-parking runtime service. Use `--composition oracle`
when an untouched, intentionally slow interpreter run is required.
The window defaults to a desktop-safe 2× scale; use `--scale 3` or resize it
after launch on a larger display.

Execution policy and implementation composition are separate:

```text
# untouched interpreted oracle
python scripts/play.py --profile development --composition oracle

# interpreted baseline with authored faithful candidates
python scripts/play.py --profile development --composition authored-candidates

# differential verification over a ReplayArtifact
python scripts/play.py --profile verification --composition generated-functions --play-replay artifacts/replays/replay_name --verify-start 100 --verify-end 180

# generated CPUless implementation while recovery frontiers remain visible
python scripts/play.py --profile development --composition generated-abi --headless

# strict readiness report (currently rejects named Atlas frontiers)
python scripts/play.py --profile release --composition generated-abi --plan-only

# rebuild/query the persistent evidence map
python scripts/build_atlas.py --from-ir
python dos_re/tools/atlas.py unresolved recovery/atlas --json

# materialize the declared bootstrap; export remains blocked until closure
python scripts/build_boot_image.py
```

`skyroads.execution` is the single implementation catalog and composition
authority; `recovery/atlas` is the persistent coverage model.
Generated VMless and CPUless code are baseline providers; authored faithful
replacements are explicit semantic-plus-adapter pairs. Frame parking is a
product-safe runtime service rather than an implementation override. Importing
an adapter never installs it.

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
replay-backed faithful overrides. Such a capture is provisional until the immutable
input stream replays completely against the untouched oracle without
divergence. Post-hoc oracle replay also attaches function visits and observed
control-flow evidence, independently of capture. Literal generated functions
are green over the committed exact interval with complete continuation-state
comparison. Authored faithful candidates retain focused semantic tests and
explicit provenance, but the complete authored composition must become
instruction-clock transparent before the same interval proof can promote it.
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
