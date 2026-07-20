# SkyRoads replay artifacts (dos_re 3.0)

SkyRoads records one proof-corpus artifact: `dos_re.replay.ReplayArtifact`.
The old `input_demo.json` bundle is not produced or accepted by the player on
`feature/demo-replay-cache`.

## Record and replay

Install the viewer extra, then record normally:

```text
python scripts/play.py --record-demo smoke
```

F11 still starts and stops capture. The resulting
`artifacts/demos/replay_<name>_<timestamp>/` contains:

- `replay.json`: immutable frame-point event stream and metadata;
- an embedded complete base continuation state;
- a content-hashed execution profile covering the EXE, dos_re/SkyRoads runtime
  sources, device model, and installed override identities;
- a persistent recording-end boundary stored as metadata plus pages changed
  from the base.

Replay uses the embedded base; it does not load a sibling snapshot:

```text
python scripts/play.py --play-demo artifacts/demos/replay_<...> --headless
```

## Check exact interval restoration

The recording-end state is the deterministic oracle for the recording profile.
This command restores the closest cached boundary at or before X, lazily caches
X, runs only X to the recorded endpoint, and compares complete continuation
state:

```text
python scripts/check_replay.py artifacts/demos/replay_<...> --start 100
```

The comparison includes memory, CPU, DOS/device state, mutable DOS files,
timers, interrupts, and the replay event cursor. It fails if runtime, image,
device, override, base-snapshot, event-stream, or format identities are stale.

`scripts/verify_vmless_demo.py` now reads this artifact and supplies the same
event stream and embedded base to the pure interpreted oracle and lifted
candidate. Its existing stable boundary-head frame cut remains the SkyRoads
adapter policy; artifact storage and restoration are owned by `dos_re.replay`.

The interactive input layer is only an acquisition adapter. It does not define
another demo format, create suffix recordings, or own verification caches.
