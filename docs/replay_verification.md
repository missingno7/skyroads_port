# Replay verification

`dos_re.replay.ReplayArtifact` is the sole persistent record/replay format.
SkyRoads owns only input and continuation adapters in `skyroads.replay`.
Artifact persistence, timing and reproduction boundaries belong exclusively to
`ReplayArtifact`.

Record with whichever development composition is responsive enough to play:

```text
python scripts/play.py --composition generated-functions --record-replay smoke
```

The artifact records the exact capture-plan identity. A candidate capture is
provisional evidence, not an oracle claim. Validate its complete deterministic
input stream against the untouched oracle before treating it as trusted:

```text
python scripts/play.py --profile verification --composition generated-functions --play-replay artifacts/replays/replay_smoke_TIMESTAMP --verify-start 0 --verify-end END
```

Use the same candidate composition that captured the replay; the verification
runner constructs the untouched oracle side itself. `END` is the recording's
final ordinal, shown by `python dos_re/tools/replay_info.py REPLAY`.

Verify the current literal generated candidates over an exact interval:

```text
python scripts/play.py --profile verification --composition generated-functions --play-replay artifacts/replays/replay_smoke_TIMESTAMP --verify-start 100 --verify-end 180
```

The dos_re player restores the nearest valid boundary, lazily caches the exact
start point, replays only the requested interval and compares complete
continuation state. Cached boundaries remain base-relative changed-page deltas
inside the original artifact.

Any existing recording may later acquire `ReplayExecutionEvidence`: actual
observed control transfers plus the nested function-visit index. Enrichment is
an idempotent post-hoc oracle observation tied to the event-stream hash, exact
execution-plan identity, observer implementation, and observed interval:

```text
python scripts/enrich_replay.py artifacts/replays/replay_smoke_TIMESTAMP
python scripts/build_atlas.py --from-ir
```

Keeping this separate means normal capture does not pay instrumentation cost
and hooks cannot hide the oracle edges. Atlas ingestion accepts only trusted
artifacts with oracle-produced execution evidence and reports exactly which
functions, invocations, edges, and new corpus identities each replay adds.
Normal runtime replay paths accept only current adapter channels and contain no
legacy compatibility branch.

Current complete machine-state interval verification covers the interpreted
oracle and DOS-memory-backed literal generated functions. Authored faithful
candidates have focused semantic tests and explicit provenance but are not yet
instruction-clock transparent as a complete composition, so the interval gate
correctly refuses to promote them. Generated detached region providers remain
independently selectable in development, while strict detached/release planning
rejects the Atlas's unresolved control-flow frontiers. A canonical semantic
projection must be supplied before cross-representation differential
verification is enabled for a fully detached native state model.
