"""TEMPLATE — pure recovered game logic. THE LAYERING RULE IS ABSOLUTE HERE.

Nothing in this package may import ``dos_re``, touch CPU/memory/segments, or
know that a VM exists — that is what makes the logic portable to the native
runtime unchanged (one leaf, many adapters). Enforced mechanically from day
one: run ``python dos_re/tools/audit_layers.py <game>/recovered`` with the
test suite (pitfall #17 — the imports WILL creep in during refactors
otherwise).

Every function here carries ``@oracle_link(boundary, contract, status,
merge_target)`` from ``dos_re.islands``; the island manifest is generated from
those tags. Status climbs the ladder on evidence only (GUESS → OBSERVED →
RECOVERED → ASM_MATCHED → VERIFIED → CANONICAL).

Names are earned, not invented: address-level names (``scan_4537``) until
multiple independent evidence paths agree on a semantic one (pitfall #1).
"""
