"""Authored SkyRoads recovery evidence over DOS-backed or detached state.

This package contains independently testable implementation candidates:

- address-anchored faithful implementations such as rendering, HUD, SFX, and
  resource loading;
- ``NativeGameState``/``NativeGameImage`` views used to test those
  implementations without a CPU interpreter;
- experimental subsystem composition with explicit typed gaps where evidence is
  incomplete.

The package is not a parallel player or implementation registry. Its current
subsystem compositions are recovery evidence only. Nothing here becomes
executable in the unified player by import side effect; a candidate becomes a
runtime implementation only after ``skyroads.execution`` declares and selects
it under a stable identity.

The layer imports ``skyroads.handrecovered`` and ``skyroads.bridge`` but never
the CPU interpreter. ``tools/audit_layers.py`` and focused differential tests
enforce that boundary.
"""
