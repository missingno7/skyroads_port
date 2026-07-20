# Unified SkyRoads execution architecture

SkyRoads has one executable lifecycle: `scripts/play.py` resolves a dos_re
`ExecutionPlan`, then launches the selected implementation composition. VMless,
CPUless and native are properties of implementations, not player modes.

## Configuration axes

`--profile` is execution policy:

- `development` permits the EXE, interpreter, recording and diagnostics.
- `verification` permits the oracle and requires differential verification.
- `detached` forbids the EXE and interpreter and requires complete coverage.
- `release` adds closed-world packaging restrictions.

`--composition` selects implementation providers:

- `oracle` is the untouched interpreted EXE.
- `faithful` overlays verified authored replacements and generated function
  lifts on the interpreted baseline.
- `behavioral` explicitly opts into intentional behavior changes.
- `vmless` selects the complete generated VMless provider.
- `cpuless` selects the complete generated ABI-recovered provider.
- `auto` chooses the conservative profile default.

These axes are intentionally independent. Build platform and replay
verification are separate concerns.

## Single catalog and identity model

`skyroads.execution` is the only implementation-selection authority. Stable
functions use `skyroads:1.0:function:1010:xxxx`; the whole program uses
`skyroads:1.0:program`. These identities are also the interface consumed by
ReplayArtifact function visits and the future Execution Atlas.

The catalog declares:

- the interpreted EXE baseline;
- complete generated VMless and CPUless providers;
- generated per-function implementations;
- authored faithful replacements;
- explicitly selected behavioral modifications.

`skyroads.hooks` contains CPU adapter functions only. Importing it installs
nothing. The resolved plan activates the selected adapters after runtime
construction or snapshot restoration. The semantic implementation therefore
has one identity and one category even when different backend adapters are
needed.

## Invariants

- Oracle construction never installs authored or generated replacements.
- Authored code is never part of a generated baseline corpus.
- Behavioral modifications cannot enter the faithful verification composition.
- Detached and release plans fail before launch if any reachable identity
  requires the EXE or interpreter.
- A release plan must have a build target and complete closed-world coverage.
- Release export requires a poisoned, code-free boot image and rejects the
  original EXE, interpreter, replay, snapshot and planner services.
- Backend modules cannot be launched as independent players.

Examples:

```text
python scripts/play.py
python scripts/play.py --composition faithful
python scripts/play.py --profile detached --composition cpuless --headless
python scripts/play.py --profile release --composition cpuless --plan-only
python scripts/build_boot_image.py
python scripts/export_release.py dist/skyroads
```
