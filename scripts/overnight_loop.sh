#!/usr/bin/env bash
# Unattended recovery loop — the relaunch harness that ran the source ports for months.
#
# A dumb restart-on-exit safety net around an autonomous agent: the agent recovers slices
# continuously (one verified slice = one commit) against a standing GOAL BRIEF; if it ever
# stops (context got heavy, a crash, a limit), this loop relaunches a fresh agent that picks
# up exactly where the last one left off — because ALL state lives in git + the ledgers
# (run_status.md, blockers.md). Killing it never loses work; a committed slice is always safe.
#
#   Usage:  bash scripts/overnight_loop.sh docs/<game>/overnight_goal.md [max_restarts]
#           AGENT_CMD="claude -p" CLAUDE_MODEL=opus bash scripts/overnight_loop.sh docs/mygame/overnight_goal.md
#   Stop:   Ctrl-C, or it stops itself when the agent prints "ENDGAME REACHED"
#           (the brief's §1 done-condition already holds).
#
# The goal brief is the binding document — copy the template from
# examples/ledgers/overnight_goal.md into docs/<game>/ and fill it in first.
#
# WHEN TO DEPLOY: the middle of the port — the massive hook/lift grind AFTER the game is
# fully runnable in the VM and a demo corpus spans gameplay (ideally e2e cold-start demos,
# reaching death/level-end/game-over). The corpus is what makes unattended commits safe;
# the brief's §0 preconditions checklist is the gate. Bring-up and the flip's design
# decisions stay attended.
#
# WARNING: the default agent command skips permission prompts so the agent can edit, run,
# and commit unattended. The safety is the loop protocol the brief binds it to (never commit
# red, never weaken an oracle, revert + log any failed attempt). Run it on a branch you are
# happy to let it push to, and review the commit log in the morning.
set -u
cd "$(cd "$(dirname "$0")/.." && pwd)"

BRIEF="${1:?usage: overnight_loop.sh docs/<game>/overnight_goal.md [max_restarts]}"
[ -f "$BRIEF" ] || { echo "goal brief not found: $BRIEF" >&2; exit 2; }
MAX="${2:-1000}"                            # max RESTARTS (each run is continuous, many slices)
MODEL="${CLAUDE_MODEL:-}"
AGENT="${AGENT_CMD:-claude -p}"             # the agent CLI; receives the prompt as one argument
ITER_TIMEOUT="${ITER_TIMEOUT:-0}"           # per-run wall-clock cap (s); 0 = none. A cap only ever
                                            # loses the one in-progress slice — commits are safe.
TIMEOUT_BIN="$(command -v timeout || true)"
LOG="artifacts/overnight_loop.log"
mkdir -p artifacts

GOAL="$(cat <<PROMPT
Run the unattended recovery loop defined in ${BRIEF}. Read that brief first — it is the
binding document — and follow it exactly, together with this repo's START_HERE.md loop
protocol.

CONTINUE recovering slices one after the next — flow into the next step and the next,
committing each verified slice — and do NOT stop after a single slice. Keep going,
autonomously, until the brief's done-condition is reached. The only reason to exit early is
that your context has grown heavy after a good batch of slices: commit cleanly and stop; a
fresh run picks up exactly where you left off from git + the ledgers.

Choose targets demand-driven per the brief's work queue, skipping anything in the blockers
ledger and anything git log shows is already done. Where a target needs something missing
underneath, GO DOWN, recover that leaf, VERIFY it through the brief's gates, then lift it
upward and close the island. NEVER fake a missing piece to keep something running.

Unattended safety, non-negotiable: never commit red; never weaken an oracle or test to
pass; on ANY failure REVERT that attempt completely (leave the tree exactly as before),
append a short entry to the blockers ledger, and MOVE ON — a single failed slice never
stops the run. On each success: ONE focused commit + push of the verified slice, and keep
run_status.md current.

If the brief's done-condition already holds, do nothing and print exactly:
ENDGAME REACHED
PROMPT
)"

run_agent() {
  # shellcheck disable=SC2086 — AGENT_CMD is intentionally word-split ("claude -p")
  local cmd=($AGENT "$GOAL" --dangerously-skip-permissions)
  [ -n "$MODEL" ] && cmd+=(--model "$MODEL")
  if [ -n "$TIMEOUT_BIN" ] && [ "$ITER_TIMEOUT" != "0" ]; then
    "$TIMEOUT_BIN" "$ITER_TIMEOUT" "${cmd[@]}"
  else
    "${cmd[@]}"
  fi
}

echo "overnight loop starting $(date -Is); brief=$BRIEF; max_restarts=$MAX; model=${MODEL:-default}; per_run_cap=${ITER_TIMEOUT}s" | tee -a "$LOG"
i=0
while [ "$i" -lt "$MAX" ]; do
  i=$((i + 1))
  echo "=== run $i (fresh agent, runs continuously) $(date -Is) ===" | tee -a "$LOG"
  run_agent 2>&1 | tee -a "$LOG"
  if tail -n 80 "$LOG" | grep -q "ENDGAME REACHED"; then
    echo "ENDGAME REACHED on run $i; stopping." | tee -a "$LOG"
    break
  fi
  echo "agent exited (context refresh / crash / limit); relaunching..." | tee -a "$LOG"
  sleep 10
done
echo "overnight loop ended $(date -Is) after $i run(s)" | tee -a "$LOG"
