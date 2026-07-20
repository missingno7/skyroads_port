# SkyRoads dos_re 3.0 contributor guide

These instructions apply to the whole SkyRoads port. The generic framework is
the `dos_re/` submodule; SkyRoads-specific identities, adapters and generated
corpora remain in this repository.

## Architecture invariants

- `scripts/play.py` is the only player and launch entrypoint.
- `skyroads.execution` is the only implementation catalog, coverage source and
  composition authority.
- Execution profiles express dependency policy. Recovery level is a property
  of an implementation, never a separate player.
- `skyroads.runtime` constructs the untouched interpreted baseline. It has no
  hook-selection flags and importing handwritten code installs nothing.
- Authored overrides are selected explicitly and activated only through the
  resolved execution plan.
- `ReplayArtifact` is the only persistent record/replay format. SkyRoads may
  provide event and continuation adapters, not another manifest or clock.
- Detached/release compositions must fail planning if any reachable identity
  needs the EXE or interpreter.
- Generated code lives only in `skyroads/lifted/functions` and
  `skyroads/recovered`; generated files must not contain project-authored
  modifications.

## Working rules

- Correctness and oracle evidence beat speed.
- Fail loudly instead of silently falling back.
- Keep `dos_re/` game-agnostic.
- Preserve deterministic replay behavior and complete continuation-state
  comparisons.
- Add focused tests for planner selection, runtime activation and replay
  adapters whenever these boundaries change.

## Required gates

```text
python -m pytest -q
python tools/lint.py
python tools/check_undefined_names.py
python tools/lint_cpuless.py
python scripts/play.py --plan-only
python scripts/play.py --profile release --composition cpuless --plan-only
```

The release plan-only gate passes after `python scripts/build_boot_image.py`.
When the build image has not been generated, its required result is an
immediate `missing bootstrap artifacts` diagnostic naming that command; a
backend traceback or late file-open failure is never acceptable.

Current documentation starts at `docs/README.md`. Material under
`docs/history/` is evidence only and does not define supported commands or
APIs.
