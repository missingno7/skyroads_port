from __future__ import annotations

from types import SimpleNamespace

from dos_re.input_demo import SCAN_CHANNEL, scan_payload
from dos_re.replay import (
    ContinuationState,
    ExecutionProfile,
    ReplayEvent,
    ReplayPoint,
)
from skyroads import replay


class FakeDOS:
    def __init__(self):
        self.key_queue = []
        self.mouse = None

    def set_mouse_norm(self, u, v, buttons):
        self.mouse = (u, v, buttons)


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
    profile = ExecutionProfile(
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
