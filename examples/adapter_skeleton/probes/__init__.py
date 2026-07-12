"""TEMPLATE — throwaway observation and diagnostic scripts.

The one package with no purity rules: probes may import anything, poke the
VM, and dump whatever helps. They are evidence-gathering tools, not product —
never imported by ``recovered/``, ``bridge/``, ``codecs/``, or ``native/``,
and never load-bearing for a test.

Keep each probe's finding in the ledger (symbol_ledger / run_status) — the
probe itself is disposable, the evidence is not. If a probe grows into a
reusable mechanism, promote it: game-agnostic → ``dos_re/tools/``, game-bound
→ a real adapter module.
"""
