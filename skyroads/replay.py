"""SkyRoads adapters for dos_re's authoritative :class:`ReplayArtifact`.

This module owns no format, manifest, recorder, playback clock, or persistence.
It only adapts immutable real-mode input events and machine continuation state
to the SkyRoads frame boundary.
"""
from __future__ import annotations

from pathlib import Path

from dos_re.replay_input import RealModeInputAdapter
from dos_re.replay import (
    CanonicalState,
    ContinuationState,
    ReplayExecutionIdentity,
    ReplayArtifact,
    ReplayPoint,
    machine_projection,
)
from dos_re.snapshot import (
    apply_runtime_continuation,
    capture_runtime_continuation,
)

PROJECTION_SCHEMA = "dos-re-complete-machine-v1"


def recording_profile(artifact: ReplayArtifact) -> ReplayExecutionIdentity:
    """Return and validate the oracle identity that owns the artifact base."""
    profile_id = artifact.metadata.get("recording_profile_id")
    matches = [
        profile for profile, _ in artifact.profiles()
        if profile.profile_id == profile_id
    ]
    if len(matches) != 1:
        raise RuntimeError(
            f"ReplayArtifact {artifact.directory} has no unique untouched-oracle "
            "recording profile; discard it and record again with "
            "--composition oracle"
        )
    profile = matches[0]
    if profile.role != "oracle":
        raise RuntimeError(
            "ReplayArtifact was not recorded from the untouched oracle; "
            "discard it and record again with --composition oracle"
        )
    return profile


def recording_base(artifact: ReplayArtifact) -> ContinuationState:
    profile = recording_profile(artifact)
    base = artifact.cached_points(profile)[0]
    return artifact.restore(profile, base)


def recording_artifacts(directory: str | Path) -> tuple[ReplayArtifact, ...]:
    """Open every authoritative replay artifact directly below *directory*."""
    root = Path(directory)
    artifacts = tuple(
        ReplayArtifact.open(manifest.parent)
        for manifest in sorted(root.glob("*/replay.json"))
    )
    for artifact in artifacts:
        recording_profile(artifact)
    return artifacts


def recording_base_memories(
    directory: str | Path,
) -> tuple[tuple[str, bytes], ...]:
    """Return named memory images from authoritative replay recording bases."""
    return tuple(
        (artifact.directory.name, recording_base(artifact).regions["memory"])
        for artifact in recording_artifacts(directory)
    )


class SkyroadsReplayDriver:
    """Replay one interpreted or DOS-memory-backed override composition."""

    def __init__(
        self,
        frontend,
        args,
        runtime,
        artifact: ReplayArtifact,
        profile: ReplayExecutionIdentity,
    ):
        self.frontend = frontend
        self.args = args
        self.runtime = runtime
        self.artifact = artifact
        self._profile = profile
        self._point = ReplayPoint(0, artifact.timeline_id)
        self.input = RealModeInputAdapter(artifact.events)

    @property
    def profile(self) -> ReplayExecutionIdentity:
        return self._profile

    @property
    def current_point(self) -> ReplayPoint:
        return self._point

    def capture(self) -> ContinuationState:
        return capture_runtime_continuation(
            self.runtime, event_cursor=self.input.event_cursor)

    def restore(self, state: ContinuationState, point: ReplayPoint) -> None:
        apply_runtime_continuation(self.runtime, state)
        self.input.seek(state.event_cursor)
        self._point = point

    def replay_to(self, artifact: ReplayArtifact, target: ReplayPoint) -> None:
        if artifact is not self.artifact:
            raise ValueError("driver belongs to another ReplayArtifact")
        if target.timeline_id != artifact.timeline_id:
            raise ValueError("target belongs to another replay timeline")
        if target.ordinal < self._point.ordinal:
            raise ValueError("driver cannot replay backwards")
        while self._point.ordinal < target.ordinal:
            ordinal = self._point.ordinal
            self.input.apply_to_runtime(
                ordinal,
                self.runtime,
                deliver=lambda rt, scancode: self.frontend.deliver_input(
                    rt, scancode),
            )
            self.frontend.advance_frame(self.runtime, self.args, ordinal)
            self._point = ReplayPoint(ordinal + 1, artifact.timeline_id)

    def project(self) -> CanonicalState:
        return machine_projection(self.capture(), schema_id=PROJECTION_SCHEMA)
