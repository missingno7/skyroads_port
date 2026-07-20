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
- `generated-functions` mixes literal generated functions with interpreter
  fallback and is the current complete-state verification composition.
- `authored-candidates` overlays authored faithful candidates and generated
  functions on the interpreted baseline.
- `play` adds non-authoritative host pacing enhancements to the authored
  candidates; this is the default development composition.
- `behavioral` explicitly opts into intentional behavior changes.
- `generated-cpu` selects the generated VMless provider over the Atlas-known region.
- `generated-abi` selects the generated ABI-recovered provider over that region.
- `auto` chooses the conservative profile default.

These axes are intentionally independent. Build platform and replay
verification are separate concerns.

## Declared bootstrap

Composition also selects the plan's initial-state provider:

- interpreted compositions use `ExeBootstrapProvider`;
- generated CPU-backed and ABI-recovered region compositions use
  `BuildImageBootstrapProvider`;
- the build-image provider declares the memory image, continuation state, and
  provenance manifest under stable artifact IDs.

Creating the image requires the original EXE, but using the packaged image
does not. Its build-time EXE requirement is therefore reported separately from
the release runtime closure. Once Atlas coverage is closed, the selected
generated ABI-recovered release composition is EXE- and
interpreter-detached while retaining
DOS memory, DOS services, and the product-safe dos_re runtime.

`--profile release --plan-only` validates all three source artifacts. If they
are absent it fails before backend launch with:

```text
run: python scripts/build_boot_image.py
```

Export copies the declared artifacts automatically and records their IDs and
runtime paths in `dos_re_release.json`. The standalone launcher resolves that
index through `dos_re.bootstrap_runtime`; `cpuless_backend` no longer assumes a
project-relative `artifacts/boot_image` directory.

## Single catalog and identity model

`skyroads.execution` is the only implementation-selection authority.
`skyroads.identities` defines the content-addressed original image and stable
function, execution-point, and program-region identities shared by retained
Recovery IR, ReplayArtifact evidence, the Execution Atlas, catalogs, and plans.
Generated Python module names are never program identity.

The catalog declares:

- the interpreted EXE baseline;
- generated VMless and CPUless region providers;
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
- Behavioral modifications cannot enter generated or authored-faithful
  verification compositions.
- Detached and release plans fail before launch if any reachable identity
  requires the EXE or interpreter.
- A release plan must have a build target and complete closed-world Atlas
  coverage. The current retained IR exposes unresolved call and indirect
  frontiers, so release planning correctly fails until recovery evidence closes
  them; the previous small hand-maintained coverage set was not release proof.
- Release planning requires a materializable build-image bootstrap; export
  includes it automatically and rejects the original EXE, interpreter, replay,
  snapshot and planner services.
- Poisoning is optional destructive evidence, not bootstrap or release
  authority.
- Backend modules cannot be launched as independent players.

Examples:

```text
python scripts/play.py
python scripts/play.py --composition authored-candidates
python scripts/play.py --profile development --composition generated-abi --headless
python scripts/play.py --profile release --composition generated-abi --plan-only  # frontier report
python scripts/build_atlas.py --from-ir
python scripts/build_boot_image.py
python scripts/export_release.py dist/skyroads
```
