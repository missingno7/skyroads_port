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

Current machine-state differential verification covers the interpreted oracle
and DOS-memory-backed faithful replacements. The generated detached providers
remain independently selectable and closed-world checked; a canonical semantic
projection must be supplied before cross-representation differential
verification is enabled for a fully detached native state model.
