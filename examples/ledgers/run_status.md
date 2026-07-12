# <GAME> — recovery status

<!-- The one ledger read at every session start. Two audiences, one file:
     the SUMMARY is for the human owner (no jargon, no addresses);
     everything below it is for the agent. Update via
     prompts/write_recovery_status.md at the end of every session. -->

## Summary (for the human)

<!-- 3-6 plain-language sentences. What works now, what changed lately, what
     is next. Example: "The game boots and the first level is playable in the
     hybrid runtime. Music is still original-emulated. Next: the enemy
     movement routines." -->

## Where we are

- **Phase:** <bring-up step N / lifting loop / subsystems / flip / enhancements>
- **Native %:** <measured over demo replay, or "not yet measured" — never estimate>
- **Islands:** <count by status: GUESS/OBSERVED/RECOVERED/ASM_MATCHED/VERIFIED/CANONICAL>
- **Demo corpus:** <N demos, pass rate, biggest blind spot>
- **Open blockers:** <count, oldest — see blockers.md>

## Recent findings (newest first)

<!-- Dated bullets. Each: what landed, WHICH VERIFIER PROVED IT, or what was
     reverted and why. Framework-core changes note the oracle evidence. -->

- YYYY-MM-DD — <finding> (proven by: <hook oracle / frame verify / demo replay + command>)

## Risks / unknowns

<!-- The largest unverified surface, corpus blind spots, approximate hardware
     models in play, single-case status claims. Explicit beats optimistic. -->

## Next targets

<!-- Ordered, evidence-based (profiler numbers, divergence reports) — not
     "looks interesting". -->
