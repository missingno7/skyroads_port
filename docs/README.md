# SkyRoads dos_re 3.0 documentation

Current architecture:

- [Execution architecture](execution_architecture.md) — the one source-tree
  player, execution-region handoff, composition catalog, profiles, override
  policy, coverage and release rules.
- [Replay verification](replay_verification.md) — authoritative
  `ReplayArtifact` recording and interval verification.
- [Execution Atlas](execution_atlas.md) — retained static and oracle replay
  evidence, stable identities, navigation, coverage, and honest release
  frontiers.

[Native gameplay presentation](native_gameplay_presentation.md) defines the
fixed semantic tick, read-only scene, optional native renderer/faithful audio,
and explicit replay-corpus coverage report.

[Audio recovery](audio_recovery.md) records the original OPL sequencer, PCM
bank and call-site evidence, the closed native-faithful playback contract, and
the explicit mono/stereo and replay-verification claims.

[Rendering recovery](rendering_recovery.md) traces the original road data to
pixels, defines the source-mapped seven-lane world representation, documents
the exact TREKDAT reference and recovered continuous lens, and keeps raster
fidelity separate from high-resolution geometric intent.

[Verification contracts](verification_contracts.md) defines the native
gameplay semantic authority and strict generated-shell return seams.

Everything under `history/` predates the dos_re 3.0 migration. It is retained
only as design evidence; its commands, imports and APIs are not supported.
