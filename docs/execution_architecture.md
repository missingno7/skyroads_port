# SkyRoads execution architecture

SkyRoads has one player, one persistent Atlas coverage source, one
implementation catalog, and one dos_re execution plan. Recovery level belongs
to each selected implementation. It is not a player or whole-game mode.

## Product compositions

`scripts/play.py --composition` supplies broad product intent:

| Composition | Selected graph |
|---|---|
| `oracle` | untouched EXE provider only |
| `workbench-auto` | authored faithful candidates, then literal generated functions, then interpreted frontier |
| `faithful-product` | generated VMless frontend plus faithful leaves and the long-lived authored gameplay region |
| `generated-detached` | generated CPUless/ABI-recovered whole-program provider |
| `auto` | `workbench-auto` for development/verification; `generated-detached` for detached/release |

These names do not create new runtimes. The selected program-root provider
determines the execution carrier:

- `baseline:interpreted-exe` -> `interpreted-cpu`;
- `baseline:generated-vmless` -> `generated-vmless-cpu`;
- `baseline:generated-cpuless` -> `generated-cpuless`.

Every authored faithful implementation is one ordinary semantic callable with
separate interpreted-CPU and generated-VMless adapters. Disabling authored
candidates from `faithful-product` falls directly back to the generated
provider; no source import order or hook flag participates.

## Hook-boundary collapse

The committed Atlas supplies conservative reachability, unresolved transfers,
and more than one thousand resolved or observed edges. Planning compares the
selected owner at each end of every edge. Different owners produce an explicit
`ExecutionBoundary` naming the carrier and adapter. Equal owners are counted as
collapsed.

The current `workbench-auto` plan is deliberately fragmented because literal
functions and authored bodies sit inside the interpreted provider. The
`faithful-product` plan removes the literal-function islands, selects the
long-lived `skyroads.gameplay` provider, and makes its ordinary inner bindings
dormant while gameplay owns control. Turning authored candidates off makes the
VMless provider own the complete known graph again.

A provider must honor selected inner bindings and region handoffs. The
generated VMless backend does so by binding the plan before execution and
exposing a generated-carrier region adapter. The current generated CPUless
provider is selected as one opaque region because its emitted direct-call graph
does not yet have a verified authored ABI binding surface; authored candidates
are therefore not falsely advertised for that carrier.

## Native gameplay execution region

The first long-lived region proves the complete control path through the one
player:

```text
generated frontend/menu
    -> 1010:2317 / start-level
authored skyroads.gameplay over the same DOS memory
    -> level-completed or player-died
generated 1010:20AD continuation
```

The region owns input decoding, gameplay physics, collision, rendering, HUD,
and transition detection across successive semantic frame boundaries. It does
not bounce through the historical per-function hooks it covers. The Atlas
retains those identities for evidence and navigation, and the plan report
lists their ordinary bindings as contextually dormant.

Shared DOS memory is authoritative in this first slice. Entry captures only
the original stack locals that are genuinely session state. Exit synchronizes
the live timing local and resumes the generated continuation. The same
`ReplayArtifact` timeline continues across the carrier change because both
sides yield `skyroads:main-loop-or-input-boundary:v1` points.

Differential verification constructs the generated VMless candidate through
the same planned-runtime factory as interactive play; it never substitutes an
interpreted candidate. A profile may deliberately remove optional captured
devices (the current pilot uses `--no-sound`), in which case its point-zero
continuation retains CPU, memory, DOS, files, and input state while dropping
only the absent device state under a distinct cache identity.

Guest-instruction coordinates remain diagnostics, not portable semantic
boundaries. An older replay that stops in the middle of an atomic lifted body
must be re-recorded with semantic coordinates or gain an explicit resumable
yield for that body before a generated or native provider can verify it. The
runtime fails on such an impossible mid-body restore instead of enabling
interpreter fallback.

Native gameplay SFX have not yet been bridged into the shared emulated Sound
Blaster continuation state. The mixed pilot therefore requires `--no-sound`
and fails before entry otherwise; it never silently drops or approximates a
device effect. This is the next external service adapter, not a reason to keep
an internal gameplay hook seam.

## Evidence and contracts

Each descriptor declares its stable targets, per-candidate `RecoveryLevel`,
content digest, dependencies, recovered call contract, and finite evidence
grade. Authored candidates currently declare focused oracle evidence. That is
enough for development/product selection under SkyRoads policy, but it is not a
claim about unobserved inputs. Every new replay-backed comparison remains a
scoped claim and can raise confidence without inventing a permanent
`verified=True` state.

The runtime report exposes:

- the selected carrier and owner count;
- every active implementation boundary and collapsed known-edge count;
- candidate fallback decisions and rejection reasons;
- unresolved Atlas transfers and retained dependency capabilities;
- independent EXE, interpreter, CPU-model, DOS-memory, DOS-service, and dos_re
  detachment milestones.

## Product features

Product features do not own recovered program identities. The first behavioral
slice is `skyroads:practice-level-position`, requested with:

```text
python scripts/play.py --practice-level-position 0x123 --record-replay practice
```

The request must be recorded. It becomes an immutable
`skyroads:feature/v1` event, is queued by the shared feature controller, and is
applied to authoritative DOS-memory state only at the SkyRoads main-loop
boundary. Playback feeds the same event through the project input adapter.
Faithful differential policy rejects behavioral features; intentional modified
behavior is tested under its own contract.

The generated-CPUless carrier currently rejects this feature because it has no
corresponding state adapter. That explicit failure is preferable to a feature
silently working in one provider and disappearing in another.

## Bootstrap and release

Oracle and workbench execution use `ExeBootstrapProvider`. `faithful-product`
and `generated-detached` use a declared `BuildImageBootstrapProvider` containing
`state.json`, `memory_1mb.bin`, and `manifest.json`. Generate it with:

```text
python scripts/build_boot_image.py
```

Build-time EXE use does not imply runtime EXE use. Missing images fail planning
with this instruction. Detached and release policy forbid the EXE and
interpreter at runtime.

Closed-world export packages only the selected file/capability/bootstrap
closure and now writes `execution_plan.json`: the final carrier, bindings,
implementation/adapter digests, features, and services. A standalone launcher
or code generator consumes that materialized selection; it does not import the
development planner and choose again. The current release preflight remains
honestly blocked by named unresolved Atlas transfers.

## Authored inventory

`skyroads.handrecovered` contains CPU-independent semantic algorithms.
`skyroads.native` contains state-backed subsystem assemblies, including the
catalogued gameplay region. `skyroads.authored_inventory` classifies every module as an
active runtime override, verification-only evidence, or experiment. Only
catalog entries selected by the plan execute. Tests that exercise a native
module do not make it an implicit provider.

The destination is progressive rather than binary: generated coverage can
make the game EXE-free first; recovered subsystems then replace generated
regions, state ownership can move away from DOS layouts independently, and
hook boundaries collapse until the exported product contains only the game.

## Commands

```text
python scripts/play.py --composition workbench-auto
python scripts/play.py --composition faithful-product --no-sound --headless --frames 12
python scripts/play.py --profile development --composition generated-detached --headless --frames 12
python scripts/play.py --profile release --composition generated-detached --plan-only
python scripts/build_atlas.py --from-ir
python scripts/check_all.py
```
