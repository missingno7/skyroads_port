"""SkyRoads adapters for dos_re's authoritative :class:`ReplayArtifact`.

This module owns no format, manifest, recorder, playback clock, or persistence.
It only adapts immutable real-mode input events and machine continuation state
to the SkyRoads frame boundary.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from dos_re.dos import ConsoleInputWouldBlock
from dos_re.observable import (
    PRESENTATION,
    REPLAY_INPUT,
    SEMANTIC_BOUNDARY,
    RollingEffectDigest,
)
from dos_re.replay_input import MOUSE_CHANNEL, SCAN_CHANNEL, RealModeInputAdapter
from dos_re.replay import (
    CanonicalState,
    ContinuationState,
    ReplayExecutionIdentity,
    ReplayArtifact,
    ReplayPoint,
    machine_projection,
)
from dos_re.snapshot import (
    capture_runtime_continuation,
    runtime_machine_projection_digest,
)

PROJECTION_SCHEMA = "dos-re-complete-machine-v1"


def capture_profile(artifact: ReplayArtifact) -> ReplayExecutionIdentity:
    """Return the plan identity that captured the immutable input stream."""
    return artifact.capture_profile()


def capture_base(artifact: ReplayArtifact) -> ContinuationState:
    profile = capture_profile(artifact)
    base = artifact.cached_points(profile)[0]
    return artifact.restore(profile, base)


def project_base_to_runtime_devices(
    runtime,
    state: ContinuationState,
) -> ContinuationState:
    """Remove optional captured devices absent from a selected profile.

    A replay's input timeline is immutable, but a candidate may deliberately
    select a stricter device topology such as ``--no-sound``.  Its profile
    needs the same CPU, memory, DOS, file, and input state at point zero while
    omitting devices that do not exist in that runtime.  Adding a device whose
    initial state was never recorded remains unsafe and fails explicitly.
    """
    state = state.normalized()
    metadata = deepcopy(dict(state.metadata))
    dos_state = metadata.get("dos")
    if not isinstance(dos_state, dict):
        raise ValueError("SkyRoads replay base has no DOS continuation state")
    for state_key, runtime_attribute in (
        ("pic", "pic"),
        ("sound_blaster", "sound_blaster"),
    ):
        runtime_has_device = getattr(
            runtime.dos, runtime_attribute, None,
        ) is not None
        state_has_device = dos_state.get(state_key) is not None
        if runtime_has_device and not state_has_device:
            raise ValueError(
                f"cannot add {state_key} to a replay base that did not "
                "capture its deterministic state"
            )
        if not runtime_has_device:
            dos_state.pop(state_key, None)
    return ContinuationState(
        schema_id=state.schema_id,
        metadata=metadata,
        regions=state.regions,
        event_cursor=state.event_cursor,
    ).normalized()


def replay_artifacts(directory: str | Path) -> tuple[ReplayArtifact, ...]:
    """Open every authoritative replay artifact directly below *directory*."""
    root = Path(directory)
    artifacts = tuple(
        ReplayArtifact.open(manifest.parent)
        for manifest in sorted(root.glob("*/replay.json"))
    )
    for artifact in artifacts:
        capture_profile(artifact)
    return artifacts


def capture_base_memories(
    directory: str | Path,
) -> tuple[tuple[str, bytes], ...]:
    """Return named memory images from authoritative replay recording bases."""
    return tuple(
        (artifact.directory.name, capture_base(artifact).regions["memory"])
        for artifact in replay_artifacts(directory)
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
        self.frontend.apply_replay_state(self.runtime, state)
        self.input.seek(state.event_cursor)
        self._point = point

    def begin_observable_interval(self):
        previous = getattr(self.runtime.dos, "observable_effect_sink", None)
        if previous is not None:
            raise RuntimeError("nested SkyRoads observable intervals are unsupported")
        sink = RollingEffectDigest()
        self.runtime.dos.observable_effect_sink = sink
        return sink, previous

    def end_observable_interval(self, token):
        sink, previous = token
        if self.runtime.dos.observable_effect_sink is not sink:
            raise RuntimeError("SkyRoads observable interval sink was replaced")
        self.runtime.dos.observable_effect_sink = previous
        return sink.finish()

    def replay_to(self, artifact: ReplayArtifact, target: ReplayPoint) -> None:
        if artifact is not self.artifact:
            raise ValueError("driver belongs to another ReplayArtifact")
        if target.timeline_id != artifact.timeline_id:
            raise ValueError("target belongs to another replay timeline")
        if target.ordinal < self._point.ordinal:
            raise ValueError("driver cannot replay backwards")
        while self._point.ordinal < target.ordinal:
            ordinal = self._point.ordinal
            cursor_before = self.input.event_cursor
            self.input.apply_to_runtime(
                ordinal,
                self.runtime,
                deliver=lambda rt, scancode: self.frontend.deliver_input(
                    rt, scancode),
            )
            try:
                self.frontend.advance_replay_frame(
                    self.runtime,
                    self.args,
                    ordinal,
                    artifact.timeline_coordinate(ReplayPoint(
                        ordinal + 1, artifact.timeline_id)),
                )
            except ConsoleInputWouldBlock:
                # The interactive player treats a blocking DOS console read as
                # a stable, resumable frame state. Replays must advance the
                # same timeline so a later recorded key can satisfy the read.
                pass
            sink = getattr(self.runtime.dos, "observable_effect_sink", None)
            if sink is not None:
                # Input application is externally scheduled by ReplayArtifact.
                # Record exactly which immutable events were consumed, then the
                # semantic handoff/presentation fence.  Port I/O and interrupts
                # were recorded directly by dos_re's platform adapters.
                for event in artifact.events[cursor_before:self.input.event_cursor]:
                    channel = (
                        1 if event.channel == SCAN_CHANNEL
                        else 2 if event.channel == MOUSE_CHANNEL
                        else 0)
                    sink.record(
                        REPLAY_INPUT, event.sequence, ordinal, channel)
                coordinate = artifact.timeline_coordinate(ReplayPoint(
                    ordinal + 1, artifact.timeline_id))
                kind = coordinate.value.get("kind") if isinstance(
                    coordinate.value, dict) else "guest-coordinate"
                kind_id = {
                    "frame-park": 1,
                    "input-block": 2,
                    "guest-coordinate": 3,
                    "guest-fallback": 4,
                }.get(kind, 0)
                sink.record(SEMANTIC_BOUNDARY, ordinal + 1, kind_id)
                sink.record(PRESENTATION, ordinal + 1)
            self._point = ReplayPoint(ordinal + 1, artifact.timeline_id)

    def project(self) -> CanonicalState:
        return machine_projection(self.capture(), schema_id=PROJECTION_SCHEMA)

    def point_digest(self) -> str:
        return runtime_machine_projection_digest(
            self.runtime,
            event_cursor=self.input.event_cursor,
            projection_schema=PROJECTION_SCHEMA,
        )
