"""Typed gaps used by focused authored recovery evidence.

A gap is raised immediately when an implementation reaches behavior outside
its declared evidence boundary. Silent fallback would hide missing coverage
and could desynchronize authored state from the oracle. This module remains
independent of CPU and machine-memory implementations.
"""
from __future__ import annotations


class SkyroadsGap(RuntimeError):
    """The native stepper reached something not yet recovered."""


class LevelEndTransition(SkyroadsGap):
    """The original ``1FD9`` continuation gate returned ``game_state``.

    The value is an inner handler result, not a reconstructed product-lifecycle
    label.  Outer ``2B3D``/``01B8`` control flow decides whether it retries,
    returns to selection, or takes another route.
    """


class RoadDepartureTransition(SkyroadsGap):
    """The ``1010:23CA-241E`` road-departure check fired.

    (``skyroads.native.collision.ship_fell_off`` is true past the `[41C0]`
    lateral threshold while ``game_state == 0``). The oracle calls ``0F05`` and
    returns its raw result.  The preserved candidate replay observes result
    zero followed by outer-loop level advancement; no death/respawn meaning is
    assigned at this inner seam.
    """
