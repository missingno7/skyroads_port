# Replay verification

`dos_re.replay.ReplayArtifact` is the sole persistent record/replay format.
SkyRoads owns only input and continuation adapters in `skyroads.replay`.
Artifact persistence, timing and reproduction boundaries belong exclusively to
`ReplayArtifact`.

Record with whichever development composition is responsive enough to play:

```text
python scripts/play.py --composition generated-functions --record-replay smoke
```

New recordings primarily stop at SkyRoads' own blocked main-loop wait or
blocking-input seam. Interpreter, generated functions, and a future semantic
driver therefore share a game boundary without sharing assembler instruction
counts. Bootstrap or a long region that has not exposed a yield is labelled as
an exact guest-instruction fallback; host `CPU.step()` dispatch counts are never
replay timing. An older coordinate-less recording may be upgraded once before
normal playback:

```text
python scripts/materialize_replay_timeline.py artifacts/replays/REPLAY
```

The artifact records the exact capture-plan identity. A candidate capture is
provisional evidence, not an oracle claim. Validate its complete deterministic
input stream against the untouched oracle before treating it as trusted:

```text
python scripts/play.py --profile verification --composition generated-functions --play-replay artifacts/replays/replay_smoke_TIMESTAMP --verify-start 0 --verify-end END
```

The default checkpointed verifier rolls the exact complete canonical state of
every replay point, performs rich comparisons every 64 points, and automatically
replays only the failed chunk point by point. To include ordered interrupt,
input, presentation, OPL/audio, console, and other canonical external effects:

```text
python scripts/play.py --profile verification --composition generated-functions --play-replay REPLAY --verify-start 0 --verify-end END --verify-observables
```

SkyRoads keeps that stronger observer explicit because its instrumentation is
measurable rather than negligible on the 2,377-point PyPy replay. `--verify-mode
endpoint` remains the cheapest continuation-only check, but it can miss an
intermediate divergence that reconverges before the endpoint and is not the
corpus-promotion gate.

## Measured verification cost

PyPy 3.11 v7.3.20, the retained 2,377-point `candidate_smoke` replay, and the
literal generated-function candidate produced these end-to-end results on the
development machine (2026-07-21):

| mode | time | guarantee |
|---|---:|---|
| endpoint | 98.23 s | final continuation only |
| checkpointed, span 64 | 109.07 s | complete canonical digest at every replay point; rich final/mismatch diagnostics |
| checkpointed + observable effects | 194.69 s | point state plus ordered canonical interrupt/input/presentation/audio/console/device effects |

The default therefore costs about 11% over the endpoint-only run while closing
its demonstrated reconvergence false negative. The full effect observer costs
about 79% over the default on this audio-heavy recording (120,572 canonical
effects in its first 500 points), so the measured policy keeps it explicit.
Buffering uses fixed 64 KiB primitive-record blocks and allocates no event
objects in the hot path.

All three successful runs wrote only the normal two profile bases plus start/end
boundaries: about 2.212 MB total from the 1.541 MB retained artifact, within 100
bytes of one another. Coarse checkpoints are rolling digests, not persistent
snapshot directories. Runtime digest memory is bounded (one 64 KiB accumulator
per observed side plus the ordinary endpoint projections).

The generic tests include both failure shapes the endpoint mode misses: state
that becomes wrong and later heals, and an external effect that differs while
final state remains equal. A failed coarse chunk is replayed point by point and
returns the exact first divergent semantic transition. Instruction tracing is
then a focused diagnostic around that transition, not a global correctness
requirement.

Remaining risk is explicit. Without `--verify-observables`, an external effect
may differ and reconverge between semantic points. With it, false negatives are
limited to an adapter omitting a correctness-relevant effect or state field (and
the theoretical digest collision). A replacement that crosses an interrupt or
scanline-visible device event must expose a yield/effect; otherwise the semantic
boundary is insufficient and the low-level fallback fails on overshoot rather
than pretending equivalence.

Normally use the candidate composition that captured the replay; the
verification runner constructs the untouched oracle side itself. A corrected
successor composition may validate a provisional capture: trust certifies the
complete immutable event timeline, not the code that happened to record it.
`END` is the recording's final ordinal, shown by
`python dos_re/tools/replay_info.py REPLAY`.

Trust here is only an oracle-backed claim about that finite recording. A
function may be used during development after one relevant passing interval
and focused tests; SkyRoads does not wait for a global replay count or coverage
percentage. Every additional replay that visits it adds another scoped claim,
and every later divergence becomes a permanent focused regression.

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
Those coverage counts guide which scenarios to record next; they do not turn
into an unqualified “verified function” bit.
Normal runtime replay paths accept only current adapter channels and contain no
legacy compatibility branch.

Current complete machine-state interval verification covers the interpreted
oracle and DOS-memory-backed literal generated functions. Authored or detached
semantic implementations do not need instruction-clock transparency; they need
an adapter for the same semantic points, explicit yields where interrupts or
device effects may intervene, and the shared canonical state/effect projection.
Generated detached region providers remain independently selectable in
development, while strict detached/release planning rejects the Atlas's
unresolved control-flow frontiers.
