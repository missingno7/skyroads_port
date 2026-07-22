"""Offline OPL-write analysis into human-readable semantic events.

Consumes the exact `(register, value)` writes the RECOVERED music engine emits
(`skyroads.handrecovered.music.Engine.run_tick`, byte-exact over 12,882 verified
ticks) and tracks just enough OPL2 channel state to say what the music *means*:
key-on transitions become :class:`NoteOn` (with the channel's current
:class:`FmPatch` timbre and the fnum/block decoded to Hz), key-offs become
:class:`NoteOff`, in-key frequency/volume changes become
:class:`PitchBend`/:class:`SetVolume`, and rhythm-mode drum-bit rises become
:class:`DrumHit`s. Pure and stateful-but-tiny; no VM, no pygame.

This decoder is diagnostic only. It must never feed faithful playback because
that would discard register-level behavior. OPL2 frequency:
``freq = fnum * 49716 / 2**(20 - block)``.
"""
from __future__ import annotations

from typing import Iterable, List

from skyroads.audio.events import (DRUM_NAMES, AudioEvent, DrumHit, FmPatch,
                                   NoteOn, NoteOff, PitchBend, SetVolume)

#: OPL2 operator slot offset for each melodic channel's (modulator, carrier).
_CH_OPS = [(0x00, 0x03), (0x01, 0x04), (0x02, 0x05),
           (0x08, 0x0B), (0x09, 0x0C), (0x0A, 0x0D),
           (0x10, 0x13), (0x11, 0x14), (0x12, 0x15)]
_OP_TO_CH = {}
for _ch, (_m, _c) in enumerate(_CH_OPS):
    _OP_TO_CH[_m] = (_ch, "mod")
    _OP_TO_CH[_c] = (_ch, "car")

_MASTER_CLOCK = 49716.0


def _op_offset(reg: int) -> int:
    return reg & 0x1F


class OplEventDecoder:
    """Feed it each tick's OPL writes; collect the semantic events."""

    def __init__(self) -> None:
        self.regs = [0] * 0x100
        self.keyed = [False] * 9
        self.rhythm_bits = 0

    # -- channel state helpers ------------------------------------------------
    def _freq(self, ch: int) -> float:
        fnum = self.regs[0xA0 + ch] | ((self.regs[0xB0 + ch] & 0x03) << 8)
        block = (self.regs[0xB0 + ch] >> 2) & 0x07
        return fnum * _MASTER_CLOCK / float(1 << (20 - block))

    def _volume(self, ch: int) -> float:
        car = _CH_OPS[ch][1]
        return 1.0 - ((self.regs[0x40 + car] & 0x3F) / 63.0)

    def _patch(self, ch: int) -> FmPatch:
        m, c = _CH_OPS[ch]
        r = self.regs
        return FmPatch(mod_char=r[0x20 + m], car_char=r[0x20 + c],
                       mod_level=r[0x40 + m], car_level=r[0x40 + c],
                       mod_attack=r[0x60 + m], car_attack=r[0x60 + c],
                       mod_sustain=r[0x80 + m], car_sustain=r[0x80 + c],
                       mod_wave=r[0xE0 + m], car_wave=r[0xE0 + c],
                       feedback=r[0xC0 + ch])

    # -- the decoder ----------------------------------------------------------
    def feed(self, writes: Iterable[tuple[int, int]]) -> List[AudioEvent]:
        out: List[AudioEvent] = []
        for reg, val in writes:
            reg &= 0xFF
            val &= 0xFF
            old = self.regs[reg]
            self.regs[reg] = val

            if 0xB0 <= reg <= 0xB8:                      # key-on / block / fnum-hi
                ch = reg - 0xB0
                keyon = bool(val & 0x20)
                if keyon and not self.keyed[ch]:
                    self.keyed[ch] = True
                    out.append(NoteOn(ch, self._freq(ch), self._patch(ch),
                                      self._volume(ch)))
                elif not keyon and self.keyed[ch]:
                    self.keyed[ch] = False
                    out.append(NoteOff(ch))
                elif keyon and (val & 0x1F) != (old & 0x1F):
                    out.append(PitchBend(ch, self._freq(ch)))
            elif 0xA0 <= reg <= 0xA8:                    # fnum-lo
                ch = reg - 0xA0
                if self.keyed[ch] and val != old:
                    out.append(PitchBend(ch, self._freq(ch)))
            elif 0x40 <= reg <= 0x55:                    # total level
                info = _OP_TO_CH.get(_op_offset(reg))
                if info is not None:
                    ch, role = info
                    if role == "car" and self.keyed[ch] and (val & 0x3F) != (old & 0x3F):
                        out.append(SetVolume(ch, self._volume(ch)))
            elif reg == 0xBD:                            # rhythm mode + drum keyons
                rising = val & ~self.rhythm_bits & 0x1F
                self.rhythm_bits = val & 0x1F
                for bit, name in DRUM_NAMES.items():
                    if rising & bit:
                        out.append(DrumHit(name))
        return out
