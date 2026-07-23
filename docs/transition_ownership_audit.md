# Replay transition ownership audit

This audit records the execution and presentation ownership observed in
`artifacts/replays/replay_skyroads_20260723_104537` on 2026-07-23. The replay
contains 706 semantic points, 69 input events, and four gameplay visits
(selected levels 4, 6, 7, and 8).

Reproduce the ownership census and latency measurements with:

```console
pypy scripts/audit_replay_ownership.py artifacts/replays/replay_skyroads_20260723_104537
pypy scripts/profile_replay_latency.py artifacts/replays/replay_skyroads_20260723_104537
```

The audit wrappers count selected implementation dispatches and therefore are
not the latency authority. `profile_replay_latency.py` is the unwrapped,
headless CPU-side latency measurement. Neither command mutates the replay.

## Observed ownership

The selected whole-program carrier is `baseline:generated-vmless`; this run has
zero interpreted fallbacks. The native gameplay region suppresses 22 internal
function hook seams while active. The native 3D renderer is a selected
presentation feature, not the gameplay region's execution provider.

| Replay points | Scene phase | Execution owner | Presentation owner |
|---|---|---|---|
| 1–3 | level selector input | generated carrier | original |
| 4–33 | selector fade out | generated carrier | original |
| 34–63 | gameplay start fade | generated carrier | native 3D |
| 64–67 | gameplay level 4 | authored gameplay region | native 3D |
| 68–97 | gameplay exit fade | generated carrier | native 3D |
| 98–127 | selector fade in | generated carrier | original |
| 128–166 | selector input and fade out | generated carrier | original |
| 167–196 | gameplay start fade | generated carrier | native 3D |
| 197–251 | gameplay level 6 | authored gameplay region | native 3D |
| 252–311 | exit fade and selector fade in | generated carrier | native, then original |
| 312–350 | selector input and fade out | generated carrier | original |
| 351–380 | gameplay start fade | generated carrier | native 3D |
| 381–396 | gameplay level 7 | authored gameplay region | native 3D |
| 397–456 | exit fade and selector fade in | generated carrier | native, then original |
| 457–494 | selector input and fade out | generated carrier | original |
| 495–524 | gameplay start fade | generated carrier | native 3D |
| 525–635 | gameplay level 8 | authored gameplay region | native 3D |
| 636–695 | exit fade and selector fade in | generated carrier | native, then original |
| 696–706 | level selector input | generated carrier | original |

There are exactly four execution-region entries and four exits. Presentation
also changes owner exactly eight times: one original-to-native acquisition at
each gameplay start fade and one native-to-original release at each selector
fade in. No per-frame renderer switching or interpreted/native ownership
ping-pong occurs.

## Boundary census

The replay executes 20,820,147 guest-equivalent instructions and 24,189
selected implementation dispatches. Of those dispatches, 21,091 occur while
the generated carrier owns execution and 3,098 occur while the gameplay region
is active.

The active-region calls expose an intentional high-frequency compatibility
boundary:

| Selected boundary | Calls | Meaning |
|---|---:|---|
| `1010:3B17` | 1,116 | original timer ISR, six per gameplay point |
| `1010:5A55` | 1,116 | music tick, six per gameplay point |
| `1010:5892` | 281 | OPL register write primitive |
| `1010:03C2` | 6 | gameplay sound-effect service |

The timer and music counts are exactly `186 gameplay points × 6`. They are not
accidental scene callbacks: device/timing ownership deliberately remains
outside the gameplay region. They are nevertheless the hottest remaining
cross-island seam and the natural boundary for a future recovered
timer-plus-audio service region. It must be collapsed only with oracle evidence
for interrupt delivery, music tempo, OPL command order, and replay coordinates.

The six sound-effect calls are necessary low-frequency service boundaries.
The four region entries/exits and eight presentation handoffs are coarse
lifecycle boundaries. Generated-to-generated function dispatches in fades and
the selector are implementation-graph edges, not interpreter transitions.

## Duplicate work and the corrected invalidation

The original `4331` palette fade owns only DAC state. Native geometry and
indexed assets are immutable across the fade. The renderer previously fitted
the fade after VGA's nonlinear 6-bit-to-8-bit expansion and treated two valid
integer fade steps as nonuniform palette mutations. That invalidated and
rebuilt the entire level mesh three times in one transition.

The renderer now recognizes the transform in the original 6-bit DAC domain.
Unused live DAC slots no longer invalidate geometry, while nonuniform changes
to owned colours still select the exact rebuild path.

Measured before and after:

| Measurement | Before | After |
|---|---:|---:|
| point 364 presentation preparation | 68.543 ms, mesh rebuilt | 0.263 ms, mesh retained |
| points 350–370 presentation p95 | 37.369 ms | 0.494 ms |
| full replay presentation p99 | 20.368 ms | 5.899 ms |
| full replay total p99 | 39.511 ms | 25.554 ms |
| full replay median | 0.999 ms | 0.795 ms |

The remaining maximum is not renderer ping-pong. Point 34 performs the first
generated level setup (214,520 guest-equivalent instructions) and cold native
presentation acquisition. Each later start has about 211,000 generated
instructions. Selector input points 130, 135, 320, and 464 materialize a
selected level's immutable native mesh before gameplay, which moves work out
of the audible gameplay interval but can still make rapid menu navigation
pause.

Native gameplay also continues to materialize the faithful VGA presentation
state before the enhanced renderer consumes semantic scene state. That is
deliberate verification/continuation work today, not competing display
ownership. Removing it safely requires a narrower cross-carrier verification
projection plus explicit VGA reconstruction at every generated return seam.

## Architectural conclusion

Scene ownership is coherent at runtime. The supported next collapses are
larger islands, not more leaf hooks:

1. A level-selection/loading/transition region could absorb selector commit,
   level assets, start/exit fades, and teardown. This is the route to removing
   the roughly 211,000-instruction setup burst and selector prewarm pauses.
2. A timer-and-audio service region could absorb the 3,098 active-gameplay
   generated dispatches, provided its virtual-time and effect stream are
   verified exactly.
3. Faithful VGA rendering inside the gameplay region can be retired only after
   semantic interior verification and generated-seam continuation
   reconstruction no longer depend on it.

Until those contracts exist, the current low-frequency lifecycle seams are
preferable to speculative merging. The one proven accidental invalidation in
this replay has been removed.

## Evidence status

This replay is valid for profiling and ownership observation, but it is not
currently trusted differential evidence. The standard
`scripts/check_all.py --replay` gate localized an oracle/candidate mismatch to
the audio projection: several final OPL registers differ. The renderer change
does not write audio or authoritative machine state, and the repository's
trusted `recovery/replays/oracle_atlas_smoke` artifact remains equivalent.
Do not promote the 706-point replay into the trusted Atlas corpus until its
pre-existing audio divergence is investigated and the full gate reports
`EQUIVALENT`.
