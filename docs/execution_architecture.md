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
| `faithful-product` | authored faithful candidates over the generated VMless whole-program provider |
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
`faithful-product` plan removes the literal-function islands and retains only
the edges around authored bodies. Turning those bodies off makes the VMless
provider own the complete known graph and collapses all known implementation
boundaries. Future authored subsystem providers use the same mechanism: they
claim evidenced contained targets and only their real entry/exit edges remain.

A provider must honor selected inner bindings. The generated VMless backend
does so by binding the plan before execution. The current generated CPUless
provider is selected as one opaque region because its emitted direct-call graph
does not yet have a verified authored ABI binding surface; authored candidates
are therefore not falsely advertised for that carrier.

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
`skyroads.native` contains state-backed subsystem assemblies and experimental
larger islands. `skyroads.authored_inventory` classifies every module as an
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
python scripts/play.py --composition faithful-product --headless --frames 12
python scripts/play.py --profile development --composition generated-detached --headless --frames 12
python scripts/play.py --profile release --composition generated-detached --plan-only
python scripts/build_atlas.py --from-ir
python scripts/check_all.py
```
