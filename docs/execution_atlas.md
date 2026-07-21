# SkyRoads Execution Atlas

`recovery/atlas` is SkyRoads' persistent, deterministic program map and the
sole `CoverageSource` supplied to the dos_re execution planner. It combines:

- `recovery/recovery_ir.json`, retained Recovery IR for 180 recovered entry
  candidates, including three refused records;
- `recovery/replays/oracle_atlas_smoke`, a fresh oracle-owned
  `ReplayArtifact` with actual observed transfers and function intervals;
- the explicit `skyroads-retained-entry-census-v1` fact connecting the product
  region to the observed unpacked-program hand-off at `1010:61F3` and
  preserving its evidence-backed entry census.

The packed MZ entry at `1010:0000` remains visible. Product reachability starts
at `1010:61F3` because that is the generated program's evidence-backed
post-unpack entry.

## Authority boundaries

The Atlas imports retained IR and never invokes a decoder. ReplayArtifact owns
events, snapshots, stable points, visits, and observed transfers. The
implementation catalog owns executable providers and authored overrides. The
planner owns selection and release policy. Atlas indexes only join their stable
identities.

`skyroads.identities` defines one content-addressed EXE image. A function and an
interior execution point at the same `CS:IP` remain different identities.
The frame-parking runtime service therefore names its empty wait-loop execution
points, while semantic function replacements target functions.

## Regeneration

Rebuild normalized sources and indexes from committed evidence:

```text
python scripts/build_atlas.py --from-ir
python dos_re/tools/atlas.py validate recovery/atlas --json
python dos_re/tools/atlas.py coverage recovery/atlas game/play --json
python dos_re/tools/atlas.py unresolved recovery/atlas --json
```

Regenerate the retained IR from a complete oracle snapshot:

```text
python scripts/build_atlas.py --snapshot artifacts/SNAPSHOT_DIR
```

The retained Recovery IR seeds the static census. Generated ABI module names
are only a first-bootstrap fallback for a workspace that does not yet have
retained IR; generated module contents are never the Atlas's source authority.

The checked-in oracle pilot can be recreated or any other deterministic replay
can be enriched in place through post-hoc oracle observation:

```text
python scripts/enrich_replay.py artifacts/replays/REPLAY --frames 3
python scripts/build_atlas.py --from-ir
```

Enrichment never creates a suffix replay or parallel artifact. It leaves the
immutable event stream and capture base untouched, and idempotently attaches
evidence with its exact plan and observer provenance. `build_atlas.py` reports
the intrinsic contribution of each replay and its exact new node/edge delta
against the current corpus. For the complete machine-readable identity lists,
use:

```text
python dos_re/tools/atlas.py ingest-replay recovery/atlas artifacts/replays/REPLAY --json
```

## Current honest frontier

The committed Atlas contains and conservatively retains all 180 IR function
candidates, along with stable execution points inside them, and reports
unresolved direct or indirect transfer sites. The active authored replacements
target identities already present in that retained census. The oracle pilot
covers five functions over stable points 0→3, records aggregate invocation
counts and intervals, and explicitly marks functions whose outer invocation is
still active at the final boundary as incomplete.

Those frontiers intentionally make detached/release planning fail. Several
correspond to the fail-loud unrecovered-call witnesses already emitted in the
generated ABI implementation; hiding them through selective registration would
restate incomplete coverage as release readiness. Each future static, observed,
or manually recovered fact should enrich this same Atlas until the closed-world
release proof becomes true.
