"""Architectural census of retained authored recovery modules.

``skyroads.execution`` is the sole executable catalog.  This inventory is an
audit assertion: every module in the two authored packages has one explicit
role and disposition, and every runtime module must be reachable from a
catalogued semantic implementation or its declared carrier adapter.

``handrecovered`` owns CPU-independent algorithms.  ``native`` owns assemblies
of those algorithms over DOS-backed or detached state.  Neither package is a
player, registry, or implicit activation mechanism.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class AuthoredUse(str, Enum):
    """Whether normal planned execution may import the module."""

    RUNTIME = "runtime"
    EVIDENCE = "evidence"
    EXPERIMENTAL = "experimental"


class AuthoredRole(str, Enum):
    """The single architectural responsibility of an authored module."""

    FAITHFUL_FUNCTION = "faithful-function-replacement"
    GAMEPLAY_REGION = "gameplay-region-member"
    RENDERER = "renderer-or-platform-subsystem"
    NATIVE_STATE = "native-state-abstraction"
    CARRIER_ADAPTER = "carrier-adapter"
    VERIFICATION = "verification-or-test-infrastructure"
    PARTIAL_PRODUCT = "partial-native-product-component"
    OBSOLETE_DUPLICATE = "obsolete-duplicate"


@dataclass(frozen=True)
class AuthoredModule:
    module: str
    use: AuthoredUse
    role: AuthoredRole
    reason: str


RUNTIME = AuthoredUse.RUNTIME
EVIDENCE = AuthoredUse.EVIDENCE

AUTHORED_MODULES = (
    AuthoredModule("skyroads.handrecovered.blit", RUNTIME,
                   AuthoredRole.FAITHFUL_FUNCTION,
                   "catalogued 1010:3153 stencil blit semantics"),
    AuthoredModule("skyroads.handrecovered.present", RUNTIME,
                   AuthoredRole.FAITHFUL_FUNCTION,
                   "catalogued 1010:3190 sprite presentation semantics"),
    AuthoredModule("skyroads.handrecovered.renderer", RUNTIME,
                   AuthoredRole.RENDERER,
                   "catalogued perspective helpers and gameplay renderer dependency"),
    AuthoredModule("skyroads.handrecovered.rle_sprite", RUNTIME,
                   AuthoredRole.FAITHFUL_FUNCTION,
                   "catalogued forward and reverse RLE replacements"),
    AuthoredModule("skyroads.handrecovered.tile_raster", RUNTIME,
                   AuthoredRole.RENDERER,
                   "catalogued clip/shade/raster functions and region dependency"),

    AuthoredModule("skyroads.handrecovered.classify", RUNTIME,
                   AuthoredRole.GAMEPLAY_REGION, "ship/road classification"),
    AuthoredModule("skyroads.handrecovered.collision_response", RUNTIME,
                   AuthoredRole.GAMEPLAY_REGION, "collision response"),
    AuthoredModule("skyroads.handrecovered.controls", RUNTIME,
                   AuthoredRole.GAMEPLAY_REGION, "keyboard and attract input decode"),
    AuthoredModule("skyroads.handrecovered.dynamics", RUNTIME,
                   AuthoredRole.GAMEPLAY_REGION, "jump, steering, and gravity"),
    AuthoredModule("skyroads.handrecovered.effect_avoidance", RUNTIME,
                   AuthoredRole.GAMEPLAY_REGION, "projected-arc avoidance"),
    AuthoredModule("skyroads.handrecovered.menu", RUNTIME,
                   AuthoredRole.GAMEPLAY_REGION, "in-game action/progression dispatch"),
    AuthoredModule("skyroads.handrecovered.movement", RUNTIME,
                   AuthoredRole.GAMEPLAY_REGION, "movement arithmetic"),
    AuthoredModule("skyroads.handrecovered.orchestration", RUNTIME,
                   AuthoredRole.GAMEPLAY_REGION, "gameplay gate and settle window"),
    AuthoredModule("skyroads.handrecovered.physics", RUNTIME,
                   AuthoredRole.GAMEPLAY_REGION, "movement targets"),
    AuthoredModule("skyroads.handrecovered.player", RUNTIME,
                   AuthoredRole.GAMEPLAY_REGION, "player-state semantics"),
    AuthoredModule("skyroads.handrecovered.progression", RUNTIME,
                   AuthoredRole.GAMEPLAY_REGION, "fuel, oxygen, and finish state"),
    AuthoredModule("skyroads.handrecovered.render_classify", RUNTIME,
                   AuthoredRole.RENDERER, "road-render classification"),
    AuthoredModule("skyroads.handrecovered.render_dispatch", RUNTIME,
                   AuthoredRole.RENDERER, "road-render dispatch"),
    AuthoredModule("skyroads.handrecovered.road_column", RUNTIME,
                   AuthoredRole.RENDERER, "road-column raster semantics"),

    AuthoredModule("skyroads.handrecovered.intro_anim", RUNTIME,
                   AuthoredRole.PARTIAL_PRODUCT,
                   "asset decoder reachable through native presentation boot data"),
    AuthoredModule("skyroads.handrecovered.music", EVIDENCE,
                   AuthoredRole.PARTIAL_PRODUCT,
                   "verified OPL engine without a selected continuation adapter"),
    AuthoredModule("skyroads.handrecovered.relocate", EVIDENCE,
                   AuthoredRole.VERIFICATION, "verified relocation primitive"),
    AuthoredModule("skyroads.handrecovered.roads_archive", EVIDENCE,
                   AuthoredRole.PARTIAL_PRODUCT,
                   "native level-file decoder used only by recovery experiments"),

    AuthoredModule("skyroads.native.classify", RUNTIME,
                   AuthoredRole.GAMEPLAY_REGION, "DOS-state classification assembly"),
    AuthoredModule("skyroads.native.collision", RUNTIME,
                   AuthoredRole.GAMEPLAY_REGION, "DOS-state collision assembly"),
    AuthoredModule("skyroads.native.dashboard", RUNTIME,
                   AuthoredRole.RENDERER, "cockpit overlay used by the selected region"),
    AuthoredModule("skyroads.native.frame", RUNTIME,
                   AuthoredRole.RENDERER, "selected gameplay frame assembly"),
    AuthoredModule("skyroads.native.gaps", RUNTIME,
                   AuthoredRole.CARRIER_ADAPTER, "typed region exit conditions"),
    AuthoredModule("skyroads.native.hud", RUNTIME,
                   AuthoredRole.RENDERER, "selected DOS-backed HUD renderer"),
    AuthoredModule("skyroads.native.image", RUNTIME,
                   AuthoredRole.NATIVE_STATE, "shared 1 MiB DOS-memory image view"),
    AuthoredModule("skyroads.native.loop", RUNTIME,
                   AuthoredRole.GAMEPLAY_REGION, "semantic root of the gameplay region"),
    AuthoredModule("skyroads.native.render_frame", RUNTIME,
                   AuthoredRole.RENDERER, "gameplay render pipeline"),
    AuthoredModule("skyroads.native.render_params", RUNTIME,
                   AuthoredRole.RENDERER, "gameplay projection parameters"),
    AuthoredModule("skyroads.native.tile_dispatch", RUNTIME,
                   AuthoredRole.RENDERER, "gameplay tile dispatch"),

    AuthoredModule("skyroads.native.anim", EVIDENCE,
                   AuthoredRole.PARTIAL_PRODUCT, "animation candidate with focused tests"),
    AuthoredModule("skyroads.native.boot", RUNTIME,
                   AuthoredRole.PARTIAL_PRODUCT,
                   "native presentation asset and display-list decoding"),
    AuthoredModule("skyroads.native.exe_image", RUNTIME,
                   AuthoredRole.PARTIAL_PRODUCT,
                   "dependency of the selected native presentation asset loader"),
    AuthoredModule("skyroads.native.level_load", RUNTIME,
                   AuthoredRole.PARTIAL_PRODUCT,
                   "selected native presentation road and palette decoding"),
    AuthoredModule("skyroads.native.pcx", EVIDENCE,
                   AuthoredRole.PARTIAL_PRODUCT, "PCX decoder candidate"),
    AuthoredModule("skyroads.native.sfx", EVIDENCE,
                   AuthoredRole.PARTIAL_PRODUCT, "detached SFX-bank candidate"),
    AuthoredModule("skyroads.native.state", EVIDENCE,
                   AuthoredRole.NATIVE_STATE, "detached test-state abstraction"),
    AuthoredModule("skyroads.native.world_load", RUNTIME,
                   AuthoredRole.PARTIAL_PRODUCT,
                   "selected native presentation background and palette decoding"),
)


def authored_modules(use: AuthoredUse | None = None) -> tuple[AuthoredModule, ...]:
    return tuple(
        item for item in AUTHORED_MODULES if use is None or item.use is use
    )


def runtime_source_paths(repository_root: Path) -> tuple[Path, ...]:
    """Content-address only authored code reachable in normal execution."""
    return tuple(
        repository_root / Path(*item.module.split(".")).with_suffix(".py")
        for item in authored_modules(AuthoredUse.RUNTIME)
    )
