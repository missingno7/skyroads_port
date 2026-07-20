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
    """The level ended: ``game_state`` left the in-level set ``{0, 3}`` (active /
    resume-frozen) for a transition/post-level state -- ``2`` (level-select, the
    ship reached the end and `dispatch_menu_action` action 0xC set it), ``4``
    (distance/"fuel" timer expired), ``5`` (time/"oxygen" timer expired), or
    ``1`` (the wall-crash flag). The gameplay stepper stops here; the transition
    itself (level load / menu return / respawn) is a separate subsystem. A
    fail-loud boundary, not a silent continuation into a non-gameplay state."""


class FallDeathTransition(SkyroadsGap):
    """The ship fell off the road: the `1010:23CA-2421` out-of-bounds check fired
    (``skyroads.native.collision.ship_fell_off`` is true past the `[41C0]`
    lateral threshold while ``game_state == 0``), which in the VM calls the death
    handler `0F05` and exits the frame. The gameplay stepper stops here; the
    death consequence (respawn) is a separate subsystem."""


class MovementPhysicsGap(SkyroadsGap):
    """The rare ``1010:25AC-25D6`` effect path was reached.

    That path calls ``1010:1DFA`` and rewrites ``lateral_accel``. The authored
    gameplay substep raises here unless its caller explicitly opts into the
    documented approximation used by the experimental subsystem driver.
    """
