"""SkyRoads adapters for dos_re's authoritative :class:`ReplayArtifact`.

This module owns no format, manifest, recorder, playback clock, or persistence.
It only adapts immutable real-mode input events and machine continuation state
to the SkyRoads frame boundary.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from dos_re.input_demo import RealModeInputAdapter
from dos_re.replay import (
    CanonicalState,
    ContinuationState,
    ExecutionProfile,
    ReplayArtifact,
    ReplayPoint,
    machine_projection,
)
from dos_re.snapshot import (
    apply_runtime_continuation,
    capture_runtime_continuation,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROJECTION_SCHEMA = "dos-re-complete-machine-v1"


def _hash_files(paths: tuple[Path, ...]) -> str:
    digest = hashlib.sha256()
    files: list[Path] = []
    for path in paths:
        files.extend(path.rglob("*.py") if path.is_dir() else (path,))
    for path in sorted((item for item in files if item.exists()), key=str):
        digest.update(path.relative_to(PROJECT_ROOT).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def execution_profile(rt, *, role: str) -> ExecutionProfile:
    """Describe one concrete oracle or selected-implementation runtime."""
    if role not in {"oracle", "candidate"}:
        raise ValueError("role must be oracle or candidate")
    hooks = tuple(
        f"{cs:04x}:{ip:04x}:{rt.cpu.hook_names.get((cs, ip), 'unnamed')}"
        for cs, ip in sorted(rt.cpu.replacement_hooks)
    )
    exe = Path(rt.program.exe.path)
    implementation = hashlib.sha256(
        ("\n".join((role, *hooks))).encode("utf-8")
    ).hexdigest()
    runtime_hash = _hash_files((
        PROJECT_ROOT / "dos_re" / "dos_re",
        PROJECT_ROOT / "skyroads",
        PROJECT_ROOT / "scripts" / "play.py",
    ))
    profile_key = hashlib.sha256(
        f"{implementation}:{runtime_hash}".encode("utf-8")
    ).hexdigest()[:12]
    return ExecutionProfile(
        profile_id=f"skyroads-{role}-{profile_key}",
        role=role,
        implementation=implementation,
        image=f"sha256:{hashlib.sha256(exe.read_bytes()).hexdigest()}",
        runtime=f"sha256:{runtime_hash}",
        devices="skyroads-dos-devices-v1",
        continuation_schema="dos-re-real-mode-continuation-v1",
        projection_schema=PROJECTION_SCHEMA,
        overrides=hooks if role == "candidate" else (),
    )


def recording_profile(artifact: ReplayArtifact) -> ExecutionProfile:
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
        profile: ExecutionProfile,
    ):
        self.frontend = frontend
        self.args = args
        self.runtime = runtime
        self.artifact = artifact
        self._profile = profile
        self._point = ReplayPoint(0, artifact.timeline_id)
        self.input = RealModeInputAdapter(artifact.events)

    @property
    def profile(self) -> ExecutionProfile:
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
