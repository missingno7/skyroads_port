"""Regression: SkyroadsAudioSink pacing must be wall-clock-driven, so a viewer
loop running below present_hz (the CPython reality — ~29% of E2E frames exceed
the 33ms budget) neither underruns the mixer (stutter) nor lets the SB-PCM SFX
backlog ratchet into seconds of delay.

Driven with an INJECTED clock and a fake pygame mixer, so it is deterministic
and needs no audio device. The pre-fix design generated/drained a fixed
`rate//present_hz` samples per pump regardless of elapsed time; these tests
would fail against it (SFX backlog grows without bound when pumps arrive slower
than real time).
"""
from __future__ import annotations

import numpy as np
import pytest

from skyroads.audio import SkyroadsAudioSink

RATE = 44100
PRESENT_HZ = 30


# ---- minimal fakes -----------------------------------------------------------
class _FakeSound:
    def __init__(self, arr):
        self.n = len(arr)


class _FakeChannel:
    """Models pygame's play + single-slot queue, draining at real (fake) time."""

    def __init__(self, clock):
        self._clock = clock          # returns current fake seconds
        self._playing = None         # (start_t, dur_s)
        self._queued = None

    def _advance(self):
        # promote the queued sound once the playing one has finished
        if self._playing is not None:
            start, dur = self._playing
            if self._clock() >= start + dur:
                if self._queued is not None:
                    qdur = self._queued.n / RATE
                    self._playing = (start + dur, qdur)
                    self._queued = None
                else:
                    self._playing = None
        # skip past any fully-elapsed promoted sounds
        while self._playing is not None and self._clock() >= self._playing[0] + self._playing[1]:
            if self._queued is not None:
                self._playing = (self._playing[0] + self._playing[1], self._queued.n / RATE)
                self._queued = None
            else:
                self._playing = None

    def play(self, sound):
        self._playing = (self._clock(), sound.n / RATE)
        self._queued = None

    def queue(self, sound):
        self._queued = sound

    def get_busy(self):
        self._advance()
        return self._playing is not None

    def get_queue(self):
        self._advance()
        return _FakeSound(np.zeros(self._queued.n)) if self._queued is not None else None


class _FakeMixer:
    def __init__(self, clock):
        self._clock = clock
        self._inited = True
        self._nchan = 2

    def get_init(self):
        return (RATE, -16, 2)

    def init(self, **kw):
        self._inited = True

    def get_num_channels(self):
        return self._nchan

    def set_num_channels(self, n):
        self._nchan = n

    def Channel(self, i):
        return _FakeChannel(self._clock)


class _FakeSndArray:
    @staticmethod
    def make_sound(arr):
        return _FakeSound(arr)


class _FakePygame:
    def __init__(self, clock):
        self.mixer = _FakeMixer(clock)
        self.sndarray = _FakeSndArray()


class _FakeSB:
    """Capture-mode Sound Blaster stub: append (block, rate) to fire an effect."""

    def __init__(self):
        self.detection_only = False
        self.log = []
        self.pcm_out = bytearray()

    def fire(self, n_bytes: int, rate: int = 8000):
        payload = {"len": n_bytes, "rate": rate}
        self.pcm_out.extend(bytes([200]) * n_bytes)   # arbitrary loud-ish PCM
        self.log.append(("dma_start", payload))


class _FakeDos:
    def __init__(self, sb):
        self.sound_blaster = sb

    def set_adlib_callback(self, cb, emit_current=False):
        pass

    def set_speaker_callback(self, cb, emit_current=False):
        pass


class _FakeRT:
    def __init__(self, sb):
        self.dos = _FakeDos(sb)


class _Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def tick(self, dt):
        self.t += dt


def _make_sink(clock, sb):
    pg = _FakePygame(clock)
    return SkyroadsAudioSink(pg, _FakeRT(sb), PRESENT_HZ, now=clock)


# ---- tests -------------------------------------------------------------------
def test_sfx_backlog_stays_bounded_when_pumps_run_slow():
    """The core delay bug: with pumps arriving at ~15Hz (half of present_hz) and
    a long effect fired, the SFX buffer must NOT keep growing pump after pump."""
    clock = _Clock()
    sb = _FakeSB()
    sink = _make_sink(clock, sb)
    assert sink.available

    # Fire a 1.0s effect (8000 bytes @ 8kHz), then pump slowly (66ms/pump = 15Hz)
    # for 3 seconds of fake wall-clock. A fixed-chunk drain would need ~90 pumps
    # to clear 1s of audio but only gets ~45 in 3s -> permanent backlog.
    sb.fire(8000, 8000)
    lengths = []
    for _ in range(45):                    # 45 * 66ms = ~3.0s wall clock
        clock.tick(1.0 / 15)
        sink.pump()
        lengths.append(len(sink._sfx))

    # Backlog must be bounded by the SFX cap, and must actually be draining
    # (end well below its early peak), not ratcheting up.
    assert max(lengths) <= sink._sfx_cap + sink._max_pump
    assert lengths[-1] < max(lengths), "SFX buffer never drained — still ratcheting"
    # After ~3s of real time a 1s effect must be long gone.
    assert lengths[-1] == 0


def test_pre_mixer_buffer_resyncs_after_a_long_stall():
    """A single 500ms 'frame' (a bad transition hitch) must not leave half a
    second of stale audio queued ahead — the buffer caps to MAX_BUFFER_S."""
    clock = _Clock()
    sb = _FakeSB()
    sink = _make_sink(clock, sb)

    clock.tick(1.0 / PRESENT_HZ)
    sink.pump()                            # prime
    clock.tick(0.5)                        # 500ms stall
    sink.pump()

    assert len(sink._buf) <= sink._buf_cap
    assert sink._buf_cap == int(RATE * SkyroadsAudioSink.MAX_BUFFER_S)


def test_samples_generated_track_wall_clock_not_pump_count():
    """Over a fixed span of fake wall-clock, total audio synthesized should track
    elapsed TIME regardless of how many (irregular) pumps happened in it."""
    clock = _Clock()
    sb = _FakeSB()
    sink = _make_sink(clock, sb)

    produced = 0
    orig = sink._synthesize
    def _counting(n):
        nonlocal produced
        produced += n
        return orig(n)
    sink._synthesize = _counting

    # irregular cadence totalling exactly 2.0s of wall clock
    for dt in [0.033, 0.2, 0.033, 0.033, 0.45, 0.033, 0.033, 0.1, 0.033, 1.0]:
        clock.tick(dt)
        sink.pump()

    # first pump is one nominal frame; the rest are wall-clock-sized, minus the
    # MAX_PUMP_S clamp on the two big gaps (0.45s and 1.0s both clamp to 0.25s).
    clamped_loss = (0.45 - 0.25) + (1.0 - 0.25)
    expected = int(round((2.0 - clamped_loss) * RATE))
    # allow one nominal chunk of slack for the first-pump seeding + rounding
    assert abs(produced - expected) <= sink._chunk * 2


def test_no_sb_still_pumps_music_without_error():
    """A runtime without a Sound Blaster (music-only) must still pump cleanly."""
    clock = _Clock()
    sink = _make_sink(clock, None)
    for _ in range(5):
        clock.tick(1.0 / PRESENT_HZ)
        sink.pump()                        # no exception, no SFX path
    assert sink._sb is None
