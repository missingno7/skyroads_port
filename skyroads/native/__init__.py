"""Authored SkyRoads recovery evidence over DOS-backed or detached state.

This package contains independently testable authored implementations:

- address-anchored faithful implementations such as rendering, HUD, SFX, and
  resource loading;
- ``NativeGameState``/``NativeGameImage`` views used to test those
  implementations without a CPU interpreter;
- the selected long-lived gameplay-region assembly and its declared external
  transition boundaries;
- additional candidates that remain evidence until separately catalogued.

The package is not a parallel player or implementation registry. Nothing here
becomes executable by import side effect. The gameplay assembly runs only when
``skyroads.execution`` selects its stable region identity and activates the
planned carrier adapter; other candidates remain inert evidence.

The layer imports ``skyroads.handrecovered`` and ``skyroads.bridge`` but never
the CPU interpreter. ``tools/audit_layers.py`` and focused differential tests
enforce that boundary.
"""
