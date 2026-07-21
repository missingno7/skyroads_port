"""Declared verification authorities for SkyRoads execution regions.

The native gameplay island is faithful only under the two projections below:
semantic state while it owns gameplay, and a reconstructed continuation when
it gives control back to generated code.  The declarations are consumed by the
execution plan, replay driver, diagnostics, and focused seam tests.
"""
from __future__ import annotations

from dos_re.execution import (
    RegionExitVerificationContract,
    RegionVerificationContract,
    VerificationProjectionContract,
    VerificationRepresentation,
)

from skyroads.identities import (
    GAMEPLAY_CALLER_CONTINUATION,
    GAMEPLAY_ROAD_DEPARTURE_CONTINUATION,
)


PROJECTION_SCHEMA = "skyroads-authoritative-semantic-v2"

GAMEPLAY_INTERIOR_PROJECTION = VerificationProjectionContract(
    projection_id="skyroads:gameplay:interior/v1",
    representation=VerificationRepresentation.SEMANTIC_STATE,
    schema_id=PROJECTION_SCHEMA,
    required_fields=(
        "verification.contract",
        "verification.surface",
        "boundary",
        "gameplay",
        "input",
        "timing",
        "audio.claim",
        "presentation",
    ),
    required_regions=("vga-aperture",),
    observable_effects=(
        "replay-input",
        "semantic-boundary",
        "presentation",
        "audio:opl-command-stream",
        "filesystem",
    ),
    excluded_internal_state=(
        "cpu.registers",
        "cpu.flags",
        "cpu.instruction-count",
        "cpu.call-stack",
        "guest.stack-scratch",
        "sound-blaster.device-state",
        "vga.programming-order",
    ),
)


def _exit_projection(exit_id: str) -> VerificationProjectionContract:
    return VerificationProjectionContract(
        projection_id=f"skyroads:gameplay:exit:{exit_id}/v1",
        representation=VerificationRepresentation.CONTINUATION_SEAM,
        schema_id=PROJECTION_SCHEMA,
        required_fields=(
            "verification.contract",
            "verification.surface",
            "exit.id",
            "exit.continuation",
            "continuation.identity",
            "continuation.registers",
            "continuation.stack",
            "continuation.timing",
            "gameplay",
        ),
        required_regions=("shared-dos-memory",),
        observable_effects=(
            "replay-input",
            "semantic-boundary",
            "presentation",
            "audio:opl-command-stream",
            "filesystem",
        ),
        excluded_internal_state=(
            "native.python-objects",
            "native.renderer-scratch",
            "native.audio-backend-state",
        ),
    )


GAMEPLAY_REGION_VERIFICATION = RegionVerificationContract(
    contract_id="skyroads:gameplay-region-faithful/v1",
    interior=GAMEPLAY_INTERIOR_PROJECTION,
    exits=(
        RegionExitVerificationContract(
            "gameplay-result", GAMEPLAY_CALLER_CONTINUATION,
            _exit_projection("gameplay-result"),
        ),
        RegionExitVerificationContract(
            "road-departure-transition",
            GAMEPLAY_ROAD_DEPARTURE_CONTINUATION,
            _exit_projection("road-departure-transition"),
        ),
        RegionExitVerificationContract(
            "gameplay-aborted", GAMEPLAY_CALLER_CONTINUATION,
            _exit_projection("gameplay-aborted"),
        ),
    ),
)


MACHINE_FALLBACK_PROJECTION = VerificationProjectionContract(
    projection_id="skyroads:complete-machine-fallback/v1",
    representation=VerificationRepresentation.COMPLETE_CONTINUATION,
    schema_id=PROJECTION_SCHEMA,
    excluded_internal_state=(),
)


def exit_projection(exit_id: str) -> VerificationProjectionContract:
    """Return the declared seam projection for one named gameplay outcome."""

    for item in GAMEPLAY_REGION_VERIFICATION.exits:
        if item.exit_id == exit_id:
            return item.projection
    raise KeyError(f"unknown SkyRoads gameplay exit {exit_id!r}")
