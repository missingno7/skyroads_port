"""Pure recovered game logic — NEVER imports dos_re/cpu/memory/hooks/offsets.

Every function here is tagged with @dos_re.islands.oracle_link. Audited
alongside skyroads/native and skyroads/bridge (pitfall #17):
``python tools/audit_layers.py skyroads/handrecovered skyroads/native skyroads/bridge``
(see tests/test_layer_audit.py).
"""
