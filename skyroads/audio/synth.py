"""Modern presentation backend for semantic audio events.

It renders through a clean float synth and pygame.mixer. Deliberately not an
OPL emulation: each :class:`NoteOn` is rendered as a
2-operator FM-flavoured voice in float64 at the mixer rate, using the event's
:class:`FmPatch` as *timbre hints* (operator multiples -> modulation ratio,
modulator level -> modulation index, connection bit -> additive vs FM,
attack/sustain nibbles -> envelope shape). Voices are cached by
(freq, timbre, volume) and looped; a NoteOff fades the channel out. Drum hits
are short shaped-noise one-shots. FRONTEND RING: numpy/pygame imports are
lazy; the framework core never imports this module.
"""
from __future__ import annotations

from typing import Dict, Iterable, Tuple

from skyroads.audio.events import (AudioEvent, DrumHit, FmPatch, NoteOn,
                                   NoteOff, PitchBend, SetVolume)

_RATE = 44100
_LOOP_SECONDS = 1.0
_RELEASE_MS = 90
_MELODIC_CHANNELS = 9


def _mult(v: int) -> float:
    return 0.5 if (v & 0x0F) == 0 else float(v & 0x0F)


class ModernSynth:
    """Consume semantic events; keep one pygame channel per music channel."""

    def __init__(self) -> None:
        import numpy as np
        import pygame
        self._np = np
        self._pg = pygame
        if pygame.mixer.get_init() is None:
            pygame.mixer.init(frequency=_RATE, size=-16, channels=2, buffer=512)
        pygame.mixer.set_num_channels(_MELODIC_CHANNELS + 6)
        self._voices: Dict[Tuple, object] = {}
        self._drums: Dict[str, object] = {}
        self._next_drum_ch = _MELODIC_CHANNELS

    # -- voice synthesis ------------------------------------------------------
    def _render_voice(self, freq: float, patch: FmPatch, volume: float):
        np = self._np
        n = int(_RATE * _LOOP_SECONDS)
        t = np.arange(n, dtype=np.float64) / _RATE
        ratio = _mult(patch.mod_mult) / max(_mult(patch.car_mult), 0.5)
        index = (1.0 - (patch.mod_level & 0x3F) / 63.0) * 3.5
        mod = np.sin(2 * np.pi * freq * ratio * t)
        if patch.additive:
            wave = 0.6 * np.sin(2 * np.pi * freq * t) + 0.4 * mod
        else:
            wave = np.sin(2 * np.pi * freq * t + index * mod)
        # gentle attack from the patch's attack nibble (15 = instant)
        attack = max(1, int(_RATE * (16 - ((patch.car_attack >> 4) & 0x0F)) * 0.004))
        env = np.ones(n)
        env[:attack] = np.linspace(0.0, 1.0, attack)
        wave = wave * env * (0.28 * max(volume, 0.05))
        pcm = (wave * 32767).astype(np.int16)
        stereo = np.repeat(pcm[:, None], 2, axis=1)
        return self._pg.sndarray.make_sound(np.ascontiguousarray(stereo))

    def _voice(self, freq: float, patch: FmPatch, volume: float):
        key = (round(freq, 1), patch, round(volume, 2))
        snd = self._voices.get(key)
        if snd is None:
            snd = self._render_voice(freq, patch, volume)
            self._voices[key] = snd
        return snd

    def _render_drum(self, name: str):
        np = self._np
        dur = {"bass": 0.14, "snare": 0.12, "tom": 0.16,
               "cymbal": 0.25, "hihat": 0.06}[name]
        n = int(_RATE * dur)
        t = np.arange(n, dtype=np.float64) / _RATE
        rng = np.random.default_rng(hash(name) & 0xFFFF)
        noise = rng.uniform(-1, 1, n)
        if name == "bass":
            wave = np.sin(2 * np.pi * (110 - 60 * t / dur) * t) * 0.9 + noise * 0.05
        elif name == "tom":
            wave = np.sin(2 * np.pi * (180 - 80 * t / dur) * t) * 0.8 + noise * 0.1
        else:
            wave = noise
        env = np.exp(-t * (5.0 / dur))
        pcm = (wave * env * 0.30 * 32767).astype(np.int16)
        stereo = np.repeat(pcm[:, None], 2, axis=1)
        return self._pg.sndarray.make_sound(np.ascontiguousarray(stereo))

    # -- the event sink -------------------------------------------------------
    def handle(self, events: Iterable[AudioEvent]) -> None:
        pg = self._pg
        for ev in events:
            if isinstance(ev, NoteOn):
                ch = pg.mixer.Channel(ev.channel)
                ch.play(self._voice(ev.freq_hz, ev.patch, ev.volume), loops=-1)
            elif isinstance(ev, NoteOff):
                pg.mixer.Channel(ev.channel).fadeout(_RELEASE_MS)
            elif isinstance(ev, (PitchBend, SetVolume)):
                # v1: volume adjusts the live channel; bends re-trigger cheaply
                ch = pg.mixer.Channel(ev.channel)
                if isinstance(ev, SetVolume):
                    ch.set_volume(max(ev.volume, 0.0))
            elif isinstance(ev, DrumHit):
                snd = self._drums.get(ev.drum)
                if snd is None:
                    snd = self._render_drum(ev.drum)
                    self._drums[ev.drum] = snd
                ch = pg.mixer.Channel(self._next_drum_ch)
                self._next_drum_ch = (_MELODIC_CHANNELS
                                      + ((self._next_drum_ch - _MELODIC_CHANNELS + 1) % 6))
                ch.play(snd)

    def stop(self) -> None:
        self._pg.mixer.stop()
