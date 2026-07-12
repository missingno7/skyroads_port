"""TEMPLATE — typed views over VM memory: the ONE place raw offsets live.

The bridge quarantines the game's memory layout: ``dos_re.state_view``
descriptors that map named fields (``player.x``, ``slot.sprite``) onto the
original byte layout. Hooks and the native runtime read state through these
views; ``recovered/`` receives plain values and never sees an offset.

Rules:
- Layout knowledge only — no gameplay decisions in this package.
- A different read WIDTH is a different semantic: give each width its own
  descriptor (``facing`` vs ``facing_lo``), never a width parameter at the
  call site (pitfall #2).
- One canonical capture per state; derived/semantic models are built FROM a
  view, never maintained beside it (pitfall #18).
"""
