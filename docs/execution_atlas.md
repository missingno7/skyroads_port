# SkyRoads Execution Atlas

`recovery/atlas` is SkyRoads' persistent, deterministic program map and the
sole `CoverageSource` supplied to the dos_re execution planner. It combines:

- `recovery/recovery_ir.json`, retained Recovery IR for 180 recovered entry
  candidates, including three refused records;
- `recovery/replays/oracle_atlas_smoke`, a fresh oracle-owned
  `ReplayArtifact` with actual observed transfers and function intervals;
- the explicit `skyroads-retained-entry-census-v1` fact connecting the product
  region to the observed unpacked-program hand-off at `1010:61F3` and
  preserving the prior recovered corpus's evidence-backed entry census.

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
Frame-pacing enhancements therefore target execution points, while semantic
function replacements target functions.

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

The bootstrap census is seeded mechanically from the already generated
CPUless corpus. Once emitted, the retained IR—not generated module contents—is
the Atlas's static source authority.

The checked-in oracle pilot can be recreated from a deterministic source
recording with the explicit one-shot converter:

```text
python scripts/record_atlas_evidence.py \
  --source-replay artifacts/replays/REPLAY --frames 3 --replace
python scripts/build_atlas.py --from-ir
```

This conversion command is not a runtime compatibility path.

## Current honest frontier

The committed Atlas contains and conservatively retains all 180 IR function
candidates plus five manually recovered hook identities absent from that
retained IR, along with stable execution points inside them, and reports
unresolved direct or indirect transfer sites. The oracle pilot
covers five functions over stable points 0→3 and records their aggregate
invocation counts and complete intervals.

Those frontiers intentionally make detached/release planning fail. Several
correspond to the fail-loud unrecovered-call witnesses already emitted in the
CPUless corpus; hiding them behind the former manually selected hook set would
restate incomplete coverage as release readiness. Each future static,
observed, or manually recovered fact should enrich this same Atlas until the
closed-world release proof becomes true.
