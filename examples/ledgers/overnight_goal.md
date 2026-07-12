# <GAME> — overnight goal brief (the standing `/goal` for unattended runs)

<!-- The binding document for scripts/overnight_loop.sh: a fresh agent is launched against
     this brief every time the previous one stops, so it must be self-contained and STABLE —
     the live "what next" belongs in run_status.md (see §2), not here. Copy into
     docs/<game>/ and fill in every <...>. -->

## 0. Preconditions (do NOT run unattended before these hold)

Unattended safety rests entirely on the mechanical gates — "never commit red"
only protects the run if red is *detectable*. The harness pays off in the
**middle** of a port: the massive hook/lift grind after bring-up's
judgment-heavy work (seams, wait loops, VM gaps) is done and before the flip's
design decisions. Check every box before the first overnight run:

- [ ] The game is **fully runnable** in the VM: bring-up steps 0–6 complete,
      no-op frame verify passes, the input-wait registry is stable (demos
      replay identically under every driver — no freezes).
- [ ] The **demo corpus spans gameplay** — ideally e2e COLD-START demos, and
      it reaches death, respawn, level-end, game-over (pitfall #22). The
      corpus IS the commit gate; its blind spots are where an unattended
      agent silently regresses.
- [ ] The demo suite runs **headless and fast enough** to gate every slice
      (cookbook "Timing and speed" if replays crawl).
- [ ] A `liftgen` census exists, so the work queue is measured, not guessed.

Before these hold, run attended sessions instead — bring-up needs judgment
per step, and each mistake there (a mis-set boundary, a missed wait loop)
silently voids the very demos the overnight gates depend on.

## 1. Done-condition (the loop stops when this holds)

<!-- One measurable statement, checkable by command. For the lifting-phase
     campaign this harness is built for:
       "native % >= <N> over the corpus with zero demo-suite divergence"
       "every liftable census entry ORACLE_PASSING or blocked-with-entry"
     (A flip-phase brief is possible but needs attended design first:
       "verify_ticks green on every corpus recording".)                        -->

<done-condition + the exact command(s) that prove it>

When it holds, print exactly: `ENDGAME REACHED`

## 2. Precedence

The invariants and gates here are timeless; the work queue (§5) goes stale. **The single
authoritative "what next" is the frontier statement at the top of `run_status.md`** — when
it and §5 disagree, run_status wins. Read run_status's standing-mechanisms notes before
building any tooling (it probably exists; check the cookbook and `dos_re/tools/` too).

## 3. Unattended safety (non-negotiable — START_HERE.md loop protocol)

- Never commit red: every commit passes <lint/test/demo-gate commands for this port>.
- Never weaken an oracle, test, or digest definition to make a change pass.
- On any failure: revert the attempt COMPLETELY, append a `blockers.md` entry
  (evidence, attempts, hypothesis), move on. A failed slice never stops the run.
- One verified slice = one focused commit + push; keep `run_status.md` current.

## 4. Verification gates (what "verified" means per slice)

<!-- The commands a slice must pass before its commit, e.g.:
     python dos_re/tools/lint.py && python -m pytest tests -q
     python dos_re/tools/audit_layers.py <game>/recovered
     <the demo-replay / hook-verify / tick-verify command for this port>          -->

<the gate commands>

## 5. Work queue (background buckets — run_status's frontier overrides)

<!-- Ordered buckets of demand-driven targets, coarse enough to stay valid for weeks:
     1. <e.g. remaining render-state leaves the native frame still reads from the VM>
     2. <e.g. audio sequencer layer N>
     3. <e.g. frontier addresses from the coverage report's 'unknown' island>      -->

1. <bucket>
2. <bucket>
