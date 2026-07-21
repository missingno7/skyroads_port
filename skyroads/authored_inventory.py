"""Non-runtime census of retained authored recovery modules.

``skyroads.execution`` remains the sole executable implementation catalog.
This inventory has a narrower purpose: it makes otherwise non-selected Python
honest about why it remains in the repository.  It must never be consulted by
the player or planner.

The two authored packages are different layers:

* ``handrecovered`` contains CPU-independent semantic algorithms.  A module in
  this layer can become an override only when the execution catalog pairs a
  complete stable target with a backend adapter.
* ``native`` composes semantic algorithms over DOS-backed or detached state.
  These modules are test harnesses and subsystem experiments until a complete
  provider or override boundary is declared by the execution catalog.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class AuthoredUse(str, Enum):
    """Why an authored module is retained."""

    RUNTIME_OVERRIDE = "runtime-override"
    VERIFICATION_ONLY = "verification-only"
    EXPERIMENTAL = "experimental"


@dataclass(frozen=True)
class AuthoredModule:
    module: str
    use: AuthoredUse
    reason: str


AUTHORED_MODULES = (
    # Complete semantic implementations with CPU-carrier adapters declared in
    # skyroads.execution via skyroads.hooks.FAITHFUL_OVERRIDE_ADAPTERS.
    AuthoredModule("skyroads.handrecovered.blit", AuthoredUse.RUNTIME_OVERRIDE,
                   "faithful stencil-blit implementation"),
    AuthoredModule("skyroads.handrecovered.present", AuthoredUse.RUNTIME_OVERRIDE,
                   "faithful sprite-blit implementation"),
    AuthoredModule("skyroads.handrecovered.renderer", AuthoredUse.RUNTIME_OVERRIDE,
                   "faithful perspective and visibility implementations"),
    AuthoredModule("skyroads.handrecovered.rle_sprite", AuthoredUse.RUNTIME_OVERRIDE,
                   "faithful forward and backward RLE implementations"),
    AuthoredModule("skyroads.handrecovered.tile_raster", AuthoredUse.RUNTIME_OVERRIDE,
                   "faithful clip, shade, and raster implementations"),

    # CPU-independent recovery facts that are differentially exercised but do
    # not yet define a complete runtime target plus backend adapter contract.
    AuthoredModule("skyroads.handrecovered.classify", AuthoredUse.VERIFICATION_ONLY,
                   "verified semantic fragment used by native experiments"),
    AuthoredModule("skyroads.handrecovered.collision_response",
                   AuthoredUse.VERIFICATION_ONLY,
                   "verified semantic fragment used by the partial gameplay loop"),
    AuthoredModule("skyroads.handrecovered.controls", AuthoredUse.VERIFICATION_ONLY,
                   "verified keyboard branch without a complete function adapter"),
    AuthoredModule("skyroads.handrecovered.dynamics", AuthoredUse.VERIFICATION_ONLY,
                   "verified gameplay fragment used by the partial gameplay loop"),
    AuthoredModule("skyroads.handrecovered.intro_anim", AuthoredUse.VERIFICATION_ONLY,
                   "verified decoder used by native boot experiments"),
    AuthoredModule("skyroads.handrecovered.menu", AuthoredUse.VERIFICATION_ONLY,
                   "verified menu fragments without a complete function adapter"),
    AuthoredModule("skyroads.handrecovered.movement", AuthoredUse.VERIFICATION_ONLY,
                   "verified semantics lacking a complete ABI and timing adapter"),
    AuthoredModule("skyroads.handrecovered.music", AuthoredUse.VERIFICATION_ONLY,
                   "verified OPL event engine without continuation/timing adapter"),
    AuthoredModule("skyroads.handrecovered.orchestration",
                   AuthoredUse.VERIFICATION_ONLY,
                   "verified gameplay-dispatch fragment"),
    AuthoredModule("skyroads.handrecovered.physics", AuthoredUse.VERIFICATION_ONLY,
                   "verified movement-target fragment"),
    AuthoredModule("skyroads.handrecovered.player", AuthoredUse.VERIFICATION_ONLY,
                   "verified player-state fragments"),
    AuthoredModule("skyroads.handrecovered.progression",
                   AuthoredUse.VERIFICATION_ONLY,
                   "verified level-progression fragments"),
    AuthoredModule("skyroads.handrecovered.relocate", AuthoredUse.VERIFICATION_ONLY,
                   "verified relocation primitive"),
    AuthoredModule("skyroads.handrecovered.render_classify",
                   AuthoredUse.VERIFICATION_ONLY,
                   "verified renderer fragment used by native render experiments"),
    AuthoredModule("skyroads.handrecovered.render_dispatch",
                   AuthoredUse.VERIFICATION_ONLY,
                   "verified renderer fragment used by native render experiments"),
    AuthoredModule("skyroads.handrecovered.road_column", AuthoredUse.VERIFICATION_ONLY,
                   "verified renderer fragment used by native render experiments"),
    AuthoredModule("skyroads.handrecovered.roads_archive",
                   AuthoredUse.VERIFICATION_ONLY,
                   "verified asset decoder used by native loading experiments"),

    # Detached-state components are evidence and test infrastructure, not a
    # hidden player.  The three assemblies with known incomplete boundaries are
    # called experimental explicitly.
    AuthoredModule("skyroads.native.boot", AuthoredUse.EXPERIMENTAL,
                   "partial native bootstrap assembly"),
    AuthoredModule("skyroads.native.frame", AuthoredUse.EXPERIMENTAL,
                   "partial native frame assembly"),
    AuthoredModule("skyroads.native.loop", AuthoredUse.EXPERIMENTAL,
                   "partial gameplay loop with typed unsupported transitions"),
    AuthoredModule("skyroads.native.anim", AuthoredUse.VERIFICATION_ONLY,
                   "native animation candidate and focused tests"),
    AuthoredModule("skyroads.native.classify", AuthoredUse.VERIFICATION_ONLY,
                   "state-backed adapter for verified classification semantics"),
    AuthoredModule("skyroads.native.collision", AuthoredUse.VERIFICATION_ONLY,
                   "state-backed adapter for verified collision semantics"),
    AuthoredModule("skyroads.native.exe_image", AuthoredUse.VERIFICATION_ONLY,
                   "build-time image reconstruction evidence"),
    AuthoredModule("skyroads.native.gaps", AuthoredUse.VERIFICATION_ONLY,
                   "typed frontiers used by partial native assemblies"),
    AuthoredModule("skyroads.native.hud", AuthoredUse.VERIFICATION_ONLY,
                   "native HUD candidate and focused tests"),
    AuthoredModule("skyroads.native.image", AuthoredUse.VERIFICATION_ONLY,
                   "detached full-address-space test state"),
    AuthoredModule("skyroads.native.level_load", AuthoredUse.VERIFICATION_ONLY,
                   "native level-loading candidate and focused tests"),
    AuthoredModule("skyroads.native.pcx", AuthoredUse.VERIFICATION_ONLY,
                   "native PCX decoder candidate and focused tests"),
    AuthoredModule("skyroads.native.render_frame", AuthoredUse.VERIFICATION_ONLY,
                   "render-pipeline candidate used by the partial frame assembly"),
    AuthoredModule("skyroads.native.render_params", AuthoredUse.VERIFICATION_ONLY,
                   "render-parameter candidate and focused tests"),
    AuthoredModule("skyroads.native.sfx", AuthoredUse.VERIFICATION_ONLY,
                   "native sound-bank candidate and focused tests"),
    AuthoredModule("skyroads.native.state", AuthoredUse.VERIFICATION_ONLY,
                   "detached gameplay-state test model"),
    AuthoredModule("skyroads.native.tile_dispatch", AuthoredUse.VERIFICATION_ONLY,
                   "tile-dispatch candidate used by render experiments"),
    AuthoredModule("skyroads.native.world_load", AuthoredUse.VERIFICATION_ONLY,
                   "native world-loading candidate and focused tests"),
)


def authored_modules(use: AuthoredUse | None = None) -> tuple[AuthoredModule, ...]:
    """Return the immutable census, optionally filtered by disposition."""
    return tuple(
        item for item in AUTHORED_MODULES if use is None or item.use is use
    )
