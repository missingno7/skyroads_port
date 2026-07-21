"""Census of retained authored recovery modules.

``skyroads.execution`` remains the sole executable implementation catalog.
This inventory has a narrower purpose: it states whether each authored module
is selected runtime code, verification evidence, or an experiment. It must
never be consulted by the player or planner.

The two authored packages are different layers:

* ``handrecovered`` contains CPU-independent semantic algorithms.  A module in
  this layer can become an override only when the execution catalog pairs a
  complete stable target with a backend adapter.
* ``native`` composes semantic algorithms over DOS-backed or detached state.
  The gameplay-region dependencies are runtime code; remaining modules stay
  verification evidence until a catalog contract selects them.
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

    # CPU-independent semantics owned by the selected gameplay region.  Their
    # runtime boundary is the region contract, not a set of internal hooks.
    AuthoredModule("skyroads.handrecovered.classify", AuthoredUse.RUNTIME_OVERRIDE,
                   "classification semantics used by the gameplay region"),
    AuthoredModule("skyroads.handrecovered.collision_response",
                   AuthoredUse.RUNTIME_OVERRIDE,
                   "collision response used by the gameplay region"),
    AuthoredModule("skyroads.handrecovered.controls", AuthoredUse.RUNTIME_OVERRIDE,
                   "keyboard and attract input used by the gameplay region"),
    AuthoredModule("skyroads.handrecovered.dynamics", AuthoredUse.RUNTIME_OVERRIDE,
                   "jump, steering, and gravity used by the gameplay region"),
    AuthoredModule("skyroads.handrecovered.effect_avoidance",
                   AuthoredUse.RUNTIME_OVERRIDE,
                   "projected-arc avoidance used by the gameplay region"),
    AuthoredModule("skyroads.handrecovered.intro_anim", AuthoredUse.VERIFICATION_ONLY,
                   "verified decoder used by native boot experiments"),
    AuthoredModule("skyroads.handrecovered.menu", AuthoredUse.RUNTIME_OVERRIDE,
                   "level-transition semantics used by the gameplay region"),
    AuthoredModule("skyroads.handrecovered.movement", AuthoredUse.RUNTIME_OVERRIDE,
                   "movement arithmetic used by the gameplay region"),
    AuthoredModule("skyroads.handrecovered.music", AuthoredUse.VERIFICATION_ONLY,
                   "verified OPL event engine without continuation/timing adapter"),
    AuthoredModule("skyroads.handrecovered.orchestration",
                   AuthoredUse.RUNTIME_OVERRIDE,
                   "gameplay dispatch used by the gameplay region"),
    AuthoredModule("skyroads.handrecovered.physics", AuthoredUse.RUNTIME_OVERRIDE,
                   "movement targets used by the gameplay region"),
    AuthoredModule("skyroads.handrecovered.player", AuthoredUse.RUNTIME_OVERRIDE,
                   "player-state semantics used by the gameplay region"),
    AuthoredModule("skyroads.handrecovered.progression",
                   AuthoredUse.RUNTIME_OVERRIDE,
                   "level progression used by the gameplay region"),
    AuthoredModule("skyroads.handrecovered.relocate", AuthoredUse.VERIFICATION_ONLY,
                   "verified relocation primitive"),
    AuthoredModule("skyroads.handrecovered.render_classify",
                   AuthoredUse.RUNTIME_OVERRIDE,
                   "render classification used by the gameplay region"),
    AuthoredModule("skyroads.handrecovered.render_dispatch",
                   AuthoredUse.RUNTIME_OVERRIDE,
                   "render dispatch used by the gameplay region"),
    AuthoredModule("skyroads.handrecovered.road_column", AuthoredUse.RUNTIME_OVERRIDE,
                   "road-column rendering used by the gameplay region"),
    AuthoredModule("skyroads.handrecovered.roads_archive",
                   AuthoredUse.RUNTIME_OVERRIDE,
                   "road archive decoder retained by the bootstrap dependency"),

    # State-backed assemblies reachable from the selected gameplay-region
    # provider.  They belong to one canonical player, not a hidden native one.
    AuthoredModule("skyroads.native.boot", AuthoredUse.RUNTIME_OVERRIDE,
                   "dashboard rendering used by the gameplay region"),
    AuthoredModule("skyroads.native.frame", AuthoredUse.RUNTIME_OVERRIDE,
                   "native frame assembly used by the gameplay region"),
    AuthoredModule("skyroads.native.loop", AuthoredUse.RUNTIME_OVERRIDE,
                   "semantic root of the selected gameplay region"),
    AuthoredModule("skyroads.native.anim", AuthoredUse.VERIFICATION_ONLY,
                   "native animation candidate and focused tests"),
    AuthoredModule("skyroads.native.classify", AuthoredUse.RUNTIME_OVERRIDE,
                   "state-backed classification used by the gameplay region"),
    AuthoredModule("skyroads.native.collision", AuthoredUse.RUNTIME_OVERRIDE,
                   "state-backed collision used by the gameplay region"),
    AuthoredModule("skyroads.native.exe_image", AuthoredUse.RUNTIME_OVERRIDE,
                   "dependency of the selected dashboard/bootstrap module"),
    AuthoredModule("skyroads.native.gaps", AuthoredUse.RUNTIME_OVERRIDE,
                   "typed external transitions of the gameplay region"),
    AuthoredModule("skyroads.native.hud", AuthoredUse.RUNTIME_OVERRIDE,
                   "HUD rendering used by the gameplay region"),
    AuthoredModule("skyroads.native.image", AuthoredUse.RUNTIME_OVERRIDE,
                   "shared DOS-memory image adapter for the gameplay region"),
    AuthoredModule("skyroads.native.level_load", AuthoredUse.RUNTIME_OVERRIDE,
                   "dependency of the selected dashboard/bootstrap module"),
    AuthoredModule("skyroads.native.pcx", AuthoredUse.VERIFICATION_ONLY,
                   "native PCX decoder candidate and focused tests"),
    AuthoredModule("skyroads.native.render_frame", AuthoredUse.RUNTIME_OVERRIDE,
                   "render pipeline used by the gameplay region"),
    AuthoredModule("skyroads.native.render_params", AuthoredUse.RUNTIME_OVERRIDE,
                   "render parameters used by the gameplay region"),
    AuthoredModule("skyroads.native.sfx", AuthoredUse.VERIFICATION_ONLY,
                   "native sound-bank candidate and focused tests"),
    AuthoredModule("skyroads.native.state", AuthoredUse.VERIFICATION_ONLY,
                   "detached gameplay-state test model"),
    AuthoredModule("skyroads.native.tile_dispatch", AuthoredUse.RUNTIME_OVERRIDE,
                   "tile dispatch used by the gameplay region"),
    AuthoredModule("skyroads.native.world_load", AuthoredUse.VERIFICATION_ONLY,
                   "native world-loading candidate and focused tests"),
)


def authored_modules(use: AuthoredUse | None = None) -> tuple[AuthoredModule, ...]:
    """Return the immutable census, optionally filtered by disposition."""
    return tuple(
        item for item in AUTHORED_MODULES if use is None or item.use is use
    )
