# SkyRoads recovery port

This repository recovers SkyRoads against the original DOS executable using
the dos_re 3.0 execution and replay architecture.

The port has one player:

```text
python scripts/play.py
```

Execution policy and implementation composition are separate:

```text
# untouched interpreted oracle
python scripts/play.py --profile development --composition oracle

# interpreted baseline with selected faithful replacements
python scripts/play.py --profile development --composition faithful

# differential verification over a ReplayArtifact
python scripts/play.py --profile verification --composition faithful \
  --play-demo artifacts/demos/replay_name

# EXE-detached generated implementation
python scripts/play.py --profile detached --composition cpuless --headless

# closed-world release readiness
python scripts/play.py --profile release --composition cpuless --plan-only

# export the audited standalone product after generating a poisoned boot image
python scripts/build_boot_image.py
python scripts/export_release.py dist/skyroads
```

`skyroads.execution` is the single implementation catalog and coverage model.
Generated VMless and CPUless code are baseline providers; authored faithful
replacements, presentation enhancements and behavioral modifications are
separate override categories. Importing an adapter never installs it.

Release export requires a code-poisoned boot image, rejects original
executables and interpreter/development imports, and publishes only the
statically audited runtime and data closure.

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
