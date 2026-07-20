from __future__ import annotations

from types import SimpleNamespace

from dos_re.replay_input import SCAN_CHANNEL, scan_payload
from dos_re.replay import (
    ContinuationState,
    ReplayExecutionIdentity,
    ReplayArtifact,
    ReplayEvent,
    ReplayPoint,
)
from skyroads import replay
from scripts.find_replay_base_entries import replay_base_entries


class FakeDOS:
    def __init__(self):
        self.key_queue = []
        self.mouse = None

    def set_mouse_norm(self, u, v, buttons):
        self.mouse = (u, v, buttons)


def test_recording_base_discovery_uses_replayartifact(tmp_path):
    timeline = "real-mode-frame-boundaries:skyroads:v1"
    profile = ReplayExecutionIdentity(
        "oracle", "oracle", "implementation", "image", "runtime",
        "devices", "continuation", replay.PROJECTION_SCHEMA)
    directory = tmp_path / "replays" / "recording"
    artifact = ReplayArtifact.create(
        directory,
        timeline_id=timeline,
        events=(),
        metadata={"recording_profile_id": profile.profile_id},
    )
    base = ContinuationState(
        "continuation",
        {"cpu": {"cs": 0x1010, "ip": 0x3199}},
        {"memory": b"base-memory"},
        0,
    )
    artifact.register_profile(
        profile,
        base_point=ReplayPoint(0, timeline),
        base_state=base,
    )

    found = replay.recording_artifacts(directory.parent)
    assert tuple(item.directory for item in found) == (directory,)
    assert replay.recording_base_memories(directory.parent) == (
        ("recording", b"base-memory"),
    )
    assert replay_base_entries(directory.parent) == [
        ("recording", 0x1010, 0x3199),
    ]


def test_driver_consumes_replayartifact_events_and_tracks_cursor(monkeypatch):
    timeline = "real-mode-frame-boundaries:skyroads:v1"
    event = ReplayEvent(
        ReplayPoint(0, timeline), 0, SCAN_CHANNEL, scan_payload(0x4D))
    artifact = SimpleNamespace(timeline_id=timeline, events=(event,))
    runtime = SimpleNamespace(dos=FakeDOS(), marker=bytearray(b"base"))
    delivered = []
    frontend = SimpleNamespace(
        deliver_input=lambda rt, sc: delivered.append(sc),
        advance_frame=lambda rt, args, frame: rt.marker.__setitem__(
            slice(None), b"done"),
    )
    profile = ReplayExecutionIdentity(
        "candidate", "candidate", "implementation", "image", "runtime",
        "devices", "continuation", replay.PROJECTION_SCHEMA)

    monkeypatch.setattr(
        replay,
        "capture_runtime_continuation",
        lambda rt, event_cursor: ContinuationState(
            "continuation", {}, {"memory": bytes(rt.marker)}, event_cursor),
    )
    driver = replay.SkyroadsReplayDriver(
        frontend, SimpleNamespace(), runtime, artifact, profile)
    driver.replay_to(artifact, ReplayPoint(1, timeline))

    assert delivered == [0x4D]
    assert driver.capture().event_cursor == 1
    assert driver.current_point == ReplayPoint(1, timeline)
    assert driver.project().regions["memory"] == b"done"
