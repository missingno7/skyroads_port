"""TEMPLATE — the VM-less runtime. GROWS LATER (lifecycle stage 4+), not day 1.

The shipped product: native game state + boot constants + the fixed-step tick
driver, composing the SAME ``recovered/`` functions the hooks adapt — never a
second copy of any rule (pitfall #4: the parallel implementation drifts and
the wrong one wins silently).

Ground rules when this package starts growing:
- An unrecovered path raises a HybridGap-style loud error; it never falls
  back to the VM or a plausible guess.
- Preserve the game's tick cadence, never its waiting machinery (no busy
  waits, no retrace polls) — pacing is explicit.
- Boot from the original data files + extracted boot constants alone: no EXE,
  no snapshot, at runtime.
- Top-down pressure ("native needs X") is answered by recovering X at the
  source layer — never by inventing it here.
"""
