from __future__ import annotations

from types import SimpleNamespace

from dos_re.replay import ContinuationState, ExecutionProfile, ReplayArtifact
from dos_re.dos import DOSMachine
from dos_re.memory import Memory
from skyroads import replay


PROFILE = ExecutionProfile(
    profile_id="skyroads-hooked",
    role="candidate",
    implementation="test-candidate",
    image="image",
    runtime="runtime",
    devices="devices",
    continuation_schema="dos-re-real-mode-continuation-v1",
    projection_schema=replay.PROJECTION_SCHEMA,
    overrides=("1010:1234:test",),
)

CPULESS_PROFILE = ExecutionProfile(
    profile_id="skyroads-cpuless",
    role="candidate",
    implementation="test-cpuless",
    image="image",
    runtime="runtime",
    devices="devices",
    continuation_schema="skyroads-cpuless-continuation-v1",
    projection_schema="skyroads-semantic-state-v1",
    overrides=("skyroads:recovered-corpus",),
)


class FakeDOS:
    def __init__(self):
        self.key_queue = []
        self.mouse = None

    def set_mouse_norm(self, u, v, buttons):
        self.mouse = (u, v, buttons)


def test_player_recorder_publishes_self_contained_replay_artifact(tmp_path, monkeypatch):
    rt = SimpleNamespace(dos=FakeDOS(), marker=bytearray(b"base"))

    def capture(runtime, *, event_cursor):
        return ContinuationState(
            "dos-re-real-mode-continuation-v1",
            {"marker": bytes(runtime.marker).hex()},
            {"memory": bytes(runtime.marker)},
            event_cursor,
        )

    monkeypatch.setattr(
        replay, "execution_profile", lambda runtime, role=None: PROFILE)
    monkeypatch.setattr(replay, "capture_runtime_continuation", capture)

    recorder = replay.SkyroadsReplayRecorder(
        root=tmp_path, name="smoke", metadata={"mouse_present": True})
    out = recorder.start(rt, boundary=17)
    recorder.record_scan(boundary=17, scancode=0x39)
    sample = recorder.record_mouse(boundary=18, u=0.25, v=0.75, buttons=1)
    rt.dos.set_mouse_norm(*sample)
    rt.marker[:] = b"done"
    saved = recorder.stop(boundary=19)

    assert saved == out
    assert (out / "replay.json").is_file()
    assert not (out / "input_demo.json").exists()
    artifact = ReplayArtifact.open(out)
    assert artifact.metadata["artifact_kind"] == "oracle-verifiable-replay"
    assert artifact.metadata["end_ordinal"] == 2
    assert artifact.cached_points(PROFILE) == (replay.point(0), replay.point(2))
    assert artifact.restore(PROFILE, replay.point(2)).regions["memory"] == b"done"


def test_replay_playback_restores_events_and_mouse_reapplication(tmp_path, monkeypatch):
    rt = SimpleNamespace(dos=FakeDOS(), marker=bytearray(b"base"))

    def capture(runtime, *, event_cursor):
        return ContinuationState(
            "dos-re-real-mode-continuation-v1", {},
            {"memory": bytes(runtime.marker)}, event_cursor)

    monkeypatch.setattr(
        replay, "execution_profile", lambda runtime, role=None: PROFILE)
    monkeypatch.setattr(replay, "capture_runtime_continuation", capture)
    recorder = replay.SkyroadsReplayRecorder(
        root=tmp_path, name="events", metadata={"mouse_present": True})
    out = recorder.start(rt, boundary=0)
    recorder.record_scan(boundary=0, scancode=0x4D)
    recorder.record_mouse(boundary=1, u=0.1, v=0.2, buttons=3)
    recorder.stop(boundary=3)

    playback = replay.SkyroadsReplayPlayback.load(out)
    delivered = []
    target = SimpleNamespace(dos=FakeDOS())
    assert playback.apply_to_runtime(
        0, target, deliver=lambda _rt, scan: delivered.append(scan)) == 1
    assert delivered == [0x4D]
    assert playback.apply_to_runtime(1, target) == 1
    assert target.dos.mouse == (0.1, 0.2, 3)
    target.dos.mouse = None
    assert playback.apply_to_runtime(2, target) == 0
    assert target.dos.mouse == (0.1, 0.2, 3)
    assert playback.finished(3)


def test_cpuless_session_recorder_uses_native_continuation_schema(tmp_path, monkeypatch):
    mem = Memory()
    dos = DOSMachine(tmp_path)
    rt = SimpleNamespace(mem=mem, dos=dos, clock=41)
    regs = {"ax": 1, "sp": 0x8000, "_flags": 0x202}
    monkeypatch.setattr(
        replay, "cpuless_execution_profile", lambda: CPULESS_PROFILE)
    recorder = replay.SkyroadsReplayRecorder(
        root=tmp_path, name="cpuless", metadata={"runner": "play_cpuless"})
    out = recorder.start_cpuless(rt, lambda: regs)
    mem.data[0x1234] = 0xA5
    rt.clock = 99
    recorder.stop(boundary=2)

    artifact = ReplayArtifact.open(out)
    end = artifact.restore(CPULESS_PROFILE, replay.point(2))
    assert end.schema_id == "skyroads-cpuless-continuation-v1"
    assert end.metadata["clock"] == 99
    assert end.metadata["regs"]["ax"] == 1
    assert end.regions["memory"][0x1234] == 0xA5

    target = SimpleNamespace(mem=Memory(), dos=DOSMachine(tmp_path), clock=0)
    playback = replay.SkyroadsReplayPlayback.load(out)
    restored_regs = replay.apply_cpuless_recording_base(target, playback)
    assert restored_regs["sp"] == 0x8000
    assert target.clock == 41  # playback restoration uses the artifact base
    assert target.mem.data[0x1234] == 0
