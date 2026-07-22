"""Offline semantic analysis of an exact SkyRoads OPL register stream.

The diagnostic decoder (:mod:`skyroads.audio.opl_events`) describes notes,
pitch slides, volume changes, and drum hits for inspection. This projection is
lossy and no faithful runtime backend consumes it. Faithful playback always
uses the complete original register stream.
"""
from __future__ import annotations

from dataclasses import dataclass

__all__ = ["FmPatch", "AudioEvent", "NoteOn", "NoteOff", "PitchBend",
           "SetVolume", "DrumHit", "DRUM_NAMES"]


@dataclass(frozen=True)
class FmPatch:
    """One channel's 2-operator FM timbre, as the song's patch load programmed
    it (the 11 bytes of an op1 instrument, register-order but semantically
    named). Values are diagnostic descriptions, not playback instructions."""
    mod_char: int      # 0x20+op: tremolo/vibrato/sustain/KSR/multiple
    car_char: int      # 0x23+op
    mod_level: int     # 0x40+op: key-scale + total level (63 = silent)
    car_level: int     # 0x43+op
    mod_attack: int    # 0x60+op: attack/decay
    car_attack: int    # 0x63+op
    mod_sustain: int   # 0x80+op: sustain/release
    car_sustain: int   # 0x83+op
    mod_wave: int      # 0xE0+op: waveform select
    car_wave: int      # 0xE3+op
    feedback: int      # 0xC0+ch: feedback/connection

    @property
    def car_mult(self) -> int:
        return self.car_char & 0x0F

    @property
    def mod_mult(self) -> int:
        return self.mod_char & 0x0F

    @property
    def additive(self) -> bool:
        """Connection bit: 1 = both operators sound (additive), 0 = FM."""
        return bool(self.feedback & 1)


class AudioEvent:
    """Base for all semantic audio events."""
    __slots__ = ()


@dataclass(frozen=True)
class NoteOn(AudioEvent):
    """Channel ``channel`` starts sounding ``freq_hz`` with timbre ``patch``.
    ``volume`` is 0.0..1.0 (derived from the carrier total-level)."""
    channel: int
    freq_hz: float
    patch: FmPatch
    volume: float


@dataclass(frozen=True)
class NoteOff(AudioEvent):
    channel: int


@dataclass(frozen=True)
class PitchBend(AudioEvent):
    """The keyed channel's frequency changed (portamento / vibrato step)."""
    channel: int
    freq_hz: float


@dataclass(frozen=True)
class SetVolume(AudioEvent):
    channel: int
    volume: float      # 0.0..1.0


#: OPL rhythm-mode drum bits (reg 0xBD bits 4..0).
DRUM_NAMES = {0x10: "bass", 0x08: "snare", 0x04: "tom", 0x02: "cymbal", 0x01: "hihat"}


@dataclass(frozen=True)
class DrumHit(AudioEvent):
    drum: str          # one of DRUM_NAMES' values
