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
- `play` selects the authored candidates plus the product-safe frame-parking
  runtime service; this is the default development composition.
- `generated-cpu` selects the generated VMless provider and composes every
  selected faithful replacement through its declared CPU-carrier adapter.
- `generated-abi` selects the generated ABI-recovered provider over that region.
  It currently selects no authored replacement: an ABI-recovered caller needs
  a separately evidenced CPUless adapter (outputs, flags, return ABI and
  virtual-time effects), and the planner must reject one rather than silently
  use the generated body.
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
- authored faithful replacements.

`skyroads.hooks` contains CPU-carrier adapter functions only. Importing it
installs nothing. The resolved plan activates the selected adapter after
runtime construction, snapshot restoration, or generated-graph boot. The
semantic implementation therefore has one identity and one category even when
different backend adapters are needed. A missing adapter is an explicit
frontier, not permission to use a different implementation.

Frame parking is a declared product-safe runtime service, not an implementation
override. It intercepts only the two empty timer-wait loops used by main-frame
and menu pacing; fade execution remains entirely in the emulated program.

## Actual launch path

There is no second native runner hidden behind the player. The executable path
is:

1. `scripts/play.py` maps `--profile` and `--composition` to one
   `ExecutionConfiguration`.
2. `dos_re.player` asks the planner to bind every reachable stable identity
   from the Atlas to one entry in `skyroads.execution.catalog()`.
3. The selected program-root provider chooses the carrier: the ordinary
   dos_re player for `baseline:interpreted-exe`, `skyroads.vmless_backend` for
   `baseline:generated-vmless`, or `skyroads.cpuless_backend` for
   `baseline:generated-cpuless`.
4. `GameFrontend.bind_execution_plan()` activates only the selected adapter for
   that carrier. On a CPU-model carrier, this populates the hook table; the
   semantic implementation itself never performs instruction interpretation.
5. Calls then enter the selected implementation through the stable function or
   execution-point binding. No import installs code and no backend has a second
   selection path.

`auto` in development resolves to `play`: the interpreted root provider, all
nine authored faithful overrides, seven literal generated-function bindings,
and interpreter execution for the remaining identities. `generated-cpu`
selects the generated VMless root and composes the same nine faithful
implementations through their CPU-model adapters. `generated-abi` selects the
generated CPUless root; it intentionally has no authored bindings until those
implementations have independently proven ABI, return-state, and virtual-time
adapters.

## Authored source layers

`handrecovered` and `native` are both authored code, but they do not own the
same responsibility:

- `skyroads.handrecovered` contains natural Python semantic algorithms over
  values, buffers, or narrow callbacks. It does not own a runtime, state model,
  or registration. Five modules (`blit`, `present`, `renderer`, `rle_sprite`,
  and `tile_raster`) currently provide the nine complete runtime overrides.
- `skyroads.native` contains state-backed adapters, detached-state test models,
  and subsystem assemblies built from the semantic layer. `boot`, `frame`, and
  `loop` are explicitly experimental because their boundaries still contain
  known gaps. No `native` module is currently a selectable provider or
  override.

`skyroads.authored_inventory` assigns every retained module exactly one status:
runtime override, verification-only evidence, or experiment. Repository tests
require that its census exactly matches both directories, that runtime-override
modules exactly match authored catalog callables, and that the semantic layer
never imports the native composition layer. This inventory documents dormant
recovery evidence; it is deliberately not another execution registry.

The partial native gameplay and renderer assemblies are therefore not exposed
as a misleading composition. They become selectable only when a coherent
subsystem identity has complete entry/exit state, side-effect, timing, and
verification contracts. At that point the implementation is added to the same
catalog and planner used by every other provider or override.

Terminology is strict:

- **generated** means reproducible lifting output with no authored game logic;
- **hand-recovered semantic code** means authored CPU-independent algorithms;
- **native code** means authored composition over an explicit non-CPU state
  boundary, not merely any Python function;
- an **override** is a catalogued alternative for a stable identity;
- a **hook** is only a CPU-carrier adapter that transfers control to a selected
  implementation;
- a **provider** implements a declared function or region;
- a **composition** is planner input selecting provider preferences and optional
  overrides;
- a **backend** is the execution carrier that runs the resulting plan.

## Invariants

- Oracle construction never installs authored or generated replacements.
- Authored code is never part of a generated baseline corpus.
- No behavioral override composition is exposed by the active player.
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
