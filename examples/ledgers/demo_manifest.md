# <GAME> — demo manifest

<!-- Every PROMOTED demo (artifacts/test_oracles/) gets a row. The corpus is a
     measured artifact: track what it covers AND what it doesn't — blind spots
     are open risks in every status report (pitfall #22). Record deaths,
     game-overs, and full playthroughs early; happy-path demos flatter the
     code. Who recorded it matters for provenance: scripted (agent) vs played
     (human, via scripts/play.py --record-demo). -->

| Demo | Frames | Source | Covers | Recorded over |
|---|---|---|---|---|
| test_oracles/<name> | ~N | agent-scripted / human-played | <levels, transitions, behaviours reached> | <hook tier / --safe-hooks / oracle-only> |

## Corpus blind spots (open risks)

<!-- Levels/behaviours/RNG paths NO demo reaches. Each is a standing request:
     either script it or ask the human to play it. -->

- <e.g. "no demo reaches the level-3 boss's second phase">
