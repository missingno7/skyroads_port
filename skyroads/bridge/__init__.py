"""Typed views: VM memory <-> named fields. The ONE place raw offsets live.

May know memory layout; holds no gameplay decisions (docs/state_mirrors.md).
Audited alongside skyroads/recovered and skyroads/native for VM leakage
(tools/audit_layers.py, tests/test_layer_audit.py) -- imports the generic
backend machinery via skyroads.state_view, never dos_re directly.
"""
