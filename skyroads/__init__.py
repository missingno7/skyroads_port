"""SkyRoads-specific evidence, adapters, and implementation representations.

Origin and execution properties are independent:

``lifted/``
    Generated literal instruction-level functions.
``recovered/``
    Generated ABI-recovered functions over DOS-layout memory.
``handrecovered/``
    Authored semantic implementations over plain values.
``native/``
    Authored DOS-memory-backed and detached-state implementation candidates.

These representations may coexist, but merely existing in the repository does
not make one executable. Generated files are regenerated in place and never
contain authored changes. The current implementation catalog selects generated
baselines and a focused set of authored semantic replacements; other authored
and native modules remain independently tested recovery evidence until
``skyroads.execution.ImplementationCatalog`` explicitly declares them.
"""
