"""Native music playback via REAL Nuked-OPL3 emulation (`pynuked_opl3`),
zero VM.

`skyroads.handrecovered.music.Engine.run_tick()` already returns the EXACT
`(register, value)` OPL writes the real ASM would emit that tick (VERIFIED
byte-exact, 12,882 ticks — see `music.py`'s docstring). This class feeds
those writes DIRECTLY into a real Nuked-OPL3 chip instance, skipping
`skyroads.audio.opl_events.OplEventDecoder` + `skyroads.audio.synth.
ModernSynth`'s decode-to-semantic-events-then-resynthesize path entirely —
the same rendering pipeline `dos_re`'s own `--audio adlib` VM viewer uses
(`dos_re/dos_re/audio_sink.py::AdlibSpeakerSink`), just fed register writes
from the recovered sequencer instead of a live VM's I/O-port trap.

``pynuked_opl3`` is a standalone package (no dos_re/VM imports at all --
see its own docstring) available here as skyroads_port's OWN top-level git
submodule (``pynuked_opl3/``, https://github.com/missingno7/pynuked_opl3),
not merely reached through ``dos_re``'s nested copy.

The wall-clock pump pacing (why a fixed per-frame chunk stutters under
CPython, and the wall-clock-sized fix) is the exact algorithm
``skyroads.audio.sink.SkyroadsAudioSink.pump`` already uses and documents at
length; this class is that same algorithm minus the VM-bound Sound-Blaster/
PC-speaker pieces (native SFX play as one-shot `pygame.Sound`s elsewhere --
see ``skyroads/native/sfx.py`` -- not through this continuous OPL channel).
"""
from __future__ import annotations

import time


class NativeOplSynth:
    """A real Nuked-OPL3 chip, fed raw register writes, paced to the wall
    clock so a slow (sub-``present_hz``) viewer frame neither stutters the
    mixer nor drifts the music out of sync."""

    #: Never let the pre-mixer buffer hold more than this many seconds.
    MAX_BUFFER_S = 0.20
    #: Clamp on how many samples a single pump may synthesize.
    MAX_PUMP_S = 0.25

    def __init__(self, pygame, present_hz: int = 35, *, now=time.perf_counter) -> None:
        import numpy as np

        self._np = np
        self._pygame = pygame
        self.available = False
        self.backend_label = "off"
        self._opl = None

        if not pygame.mixer.get_init():
            try:
                pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=512)
            except Exception as exc:                  # noqa: BLE001
                print(f"[audio] mixer unavailable ({exc}); music off")
                return
        rate, _size, channels = pygame.mixer.get_init()
        self._rate, self._channels = int(rate), int(channels)
        self._chunk = max(256, self._rate // max(1, present_hz))
        self._min_chunk = 256
        self._max_pump = max(self._min_chunk, int(self._rate * self.MAX_PUMP_S))
        self._buf_cap = int(self._rate * self.MAX_BUFFER_S)
        self._lead = int(self._rate * 0.10)
        self._buf = np.zeros((0, self._channels), dtype=np.int16)
        self._started = False
        self._now = now
        self._last_pump = None

        if pygame.mixer.get_num_channels() < 3:
            pygame.mixer.set_num_channels(3)
        self._channel = pygame.mixer.Channel(2)        # dedicated music channel

        try:
            from pynuked_opl3 import OPL3
            self._opl = OPL3(sample_rate=self._rate)
            self.backend_label = "pynuked-opl3 (real Nuked-OPL3)"
        except Exception as exc:                       # noqa: BLE001
            self.backend_label = "unavailable"
            print(f"[audio] Nuked-OPL3 not built ({exc}); music off. Build "
                  f"once: python -m pynuked_opl3._ffi_build")
            return
        self.available = True

    def write(self, reg: int, value: int) -> None:
        """Queue one raw OPL register write (from Engine.run_tick())."""
        if self._opl is not None:
            self._opl.write(reg, value)

    def _synthesize(self, n: int):
        np = self._np
        pcm = np.frombuffer(self._opl.generate_stereo(n), dtype="<i2").reshape(-1, 2)
        out = pcm.astype(np.int16)
        if self._channels == 1:
            out = out[:, :1]
        return out

    def _pop_sound(self, k: int):
        chunk, self._buf = self._buf[:k], self._buf[k:]
        arr = chunk if self._channels > 1 else chunk.reshape(-1)
        return self._pygame.sndarray.make_sound(self._np.ascontiguousarray(arr))

    def pump(self) -> None:
        """Feed audio sized by REAL elapsed wall-clock time. Call once per
        rendered frame, after that frame's OPL writes have been queued."""
        if not self.available:
            return
        now = self._now()
        if self._last_pump is None:
            n = self._chunk
        else:
            n = int(round((now - self._last_pump) * self._rate))
            n = max(self._min_chunk, min(n, self._max_pump))
        self._last_pump = now

        self._buf = self._np.concatenate([self._buf, self._synthesize(n)])
        if len(self._buf) > self._buf_cap:
            self._buf = self._buf[-self._buf_cap:]

        if not self._started:
            if len(self._buf) >= self._lead:
                self._channel.play(self._pop_sound(len(self._buf)))
                self._started = True
            return
        if not self._channel.get_busy():
            self._started = False
            return
        if self._channel.get_queue() is None and len(self._buf) >= self._min_chunk:
            self._channel.queue(self._pop_sound(len(self._buf)))
