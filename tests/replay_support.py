"""Test-only convenience around the authoritative ReplayArtifact APIs."""
from __future__ import annotations

from dos_re.replay_input import RealModeInputAdapter
from dos_re.replay import ReplayArtifact
from dos_re.snapshot import apply_runtime_continuation
from skyroads.replay import recording_base


class OracleReplaySession:
    def __init__(self, artifact: ReplayArtifact):
        self.artifact = artifact
        self.inputs = RealModeInputAdapter(artifact.events)
        self.manifest = {"metadata": artifact.metadata}
        self.end_boundary = artifact.end_point.ordinal

    def finished(self, ordinal: int) -> bool:
        return int(ordinal) >= self.artifact.end_point.ordinal

    def apply_to_runtime(self, ordinal, runtime, *, deliver):
        return self.inputs.apply_to_runtime(
            ordinal, runtime, deliver=deliver)


def open_oracle_replay(frontend, args, path):
    artifact = ReplayArtifact.open(path)
    frontend.apply_replay_metadata(args, artifact.metadata)
    runtime = frontend.create_runtime(args)
    apply_runtime_continuation(runtime, recording_base(artifact))
    runtime.dos.console_input_fallback = None
    return OracleReplaySession(artifact), runtime
