# SkyRoads recovery port

This repository recovers SkyRoads against the original DOS executable using
the dos_re 3.0 execution and replay architecture.

The port has one player:

```text
python scripts/play.py
```

The default development composition uses verified faithful replacements plus
the non-authoritative frame-pacing enhancement. Use `--composition oracle`
when an untouched, intentionally slow interpreter run is required.
The window defaults to a desktop-safe 2× scale; use `--scale 3` or resize it
after launch on a larger display.

Execution policy and implementation composition are separate:

```text
# untouched interpreted oracle
python scripts/play.py --profile development --composition oracle

# interpreted baseline with selected faithful replacements
python scripts/play.py --profile development --composition faithful

# differential verification over a ReplayArtifact
python scripts/play.py --profile verification --composition faithful \
  --play-demo artifacts/demos/replay_name

# generated CPUless implementation while recovery frontiers remain visible
python scripts/play.py --profile development --composition cpuless --headless

# strict readiness report (currently rejects named Atlas frontiers)
python scripts/play.py --profile release --composition cpuless --plan-only

# rebuild/query the persistent evidence map
python scripts/build_atlas.py --from-ir
python dos_re/tools/atlas.py unresolved recovery/atlas --json

# materialize the declared bootstrap; export remains blocked until closure
python scripts/build_boot_image.py
```

`skyroads.execution` is the single implementation catalog and composition
authority; `recovery/atlas` is the persistent coverage model.
Generated VMless and CPUless code are baseline providers; authored faithful
replacements, presentation enhancements and behavioral modifications are
separate override categories. Importing an adapter never installs it.

The selected `BuildImageBootstrapProvider` declares `state.json`,
`memory_1mb.bin`, and `manifest.json`, including their packaged paths and the
command that generates them. Release planning reports missing bootstrap inputs
and unresolved Atlas control-flow sites before launch. Once both are closed,
export materializes the provider, rejects original executables and
interpreter/development imports, and publishes only the audited runtime,
bootstrap, and data closure. Code poisoning remains optional additional
evidence, not release authority.

`ReplayArtifact` is the only persistent demo/replay format. Recording is
restricted to the untouched oracle; selected faithful replacements are checked
over exact replay intervals with complete continuation-state comparison.

See [current documentation](docs/README.md). Pre-3.0 recovery notes are kept
under `docs/history/` as evidence only.

## Development

Python 3.11 or newer is required. The original game files are not included.

```text
python -m pytest -q
python tools/lint.py
python tools/check_undefined_names.py
python tools/lint_cpuless.py
```

The reusable framework is the `dos_re/` submodule. SkyRoads-specific addresses,
formats, implementations and generated corpora stay in this repository.

## License

MIT for this repository. Game assets and executables remain the property of
their rights holders and must be supplied separately.
