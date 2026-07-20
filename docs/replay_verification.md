# Replay verification

`dos_re.replay.ReplayArtifact` is the sole persistent record/replay format.
SkyRoads owns only input and continuation adapters in `skyroads.replay`.
Artifact persistence, timing and reproduction boundaries belong exclusively to
`ReplayArtifact`.

Record only the untouched oracle:

```text
python scripts/play.py --composition oracle --record-demo smoke
```

Verify faithful replacements over an exact interval:

```text
python scripts/play.py --profile verification --composition faithful \
  --play-demo artifacts/demos/replay_smoke_TIMESTAMP \
  --verify-start 100 --verify-end 180
```

The dos_re player restores the nearest valid boundary, lazily caches the exact
start point, replays only the requested interval and compares complete
continuation state. Cached boundaries remain base-relative changed-page deltas
inside the original artifact.

Oracle recordings may also carry `ReplayExecutionEvidence`: actual observed
control transfers plus the nested function-visit index. The checked-in
`recovery/replays/oracle_atlas_smoke` pilot was produced by the explicit
one-shot `scripts/record_atlas_evidence.py` conversion tool and is ingested by
`scripts/build_atlas.py`. Normal runtime replay paths accept only the current
adapter channels and contain no legacy compatibility branch.

Current machine-state differential verification covers the interpreted oracle
and DOS-memory-backed faithful replacements. Generated detached providers
remain independently selectable in development, while strict detached/release
planning rejects the Atlas's unresolved control-flow frontiers. A canonical
semantic projection must be supplied before cross-representation differential
verification is enabled for a fully detached native state model.
