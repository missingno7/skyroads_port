"""SkyRoads AdLib/OPL music engine — the song-bytecode interpreter.

Recovered from the driver at ``1010:5892-5A54`` (runtime-loaded; see
``docs/skyroads/sound_engine.md``). SkyRoads plays FM music by walking a song
event stream once per timer tick and programming the OPL2. This module is that
engine as pure, VM-free Python: :meth:`Engine.run_tick` reads the song stream,
data tables, and engine state through two DGROUP memory readers and returns the
exact list of ``(register, value)`` OPL writes the ASM would emit that tick.

Verified byte-exact against the ASM: the emitted OPL register-write stream
matches over the **whole cold-sound demo — 12,882 ticks across intro + menu,
zero divergences** (lockstep per tick, the same proof style as the SB-PCM work).

## The tick (``1010:5A55``)

If the delay counter ``[0C83]`` is nonzero, wait (emit nothing). Otherwise pull
16-bit words from the song cursor ``[3196]`` and dispatch on ``op = word & 7``
(handlers listed below), processing events until a ``delay`` op arms the counter.
Per word: ``al = (word >> 4) & 0x0F`` (channel), ``ah = (word >> 8) & 0xFF``
(note / value / delay).

## Opcodes (ASM dispatch table at ``DG:0x0C5B``)

===  =========  ===================================================
op   handler    effect
===  =========  ===================================================
0    ``5914``   delay: ``[0C83] = ah`` (wait ``ah`` ticks)
1    ``5919``   note+instrument: key-off, then load an 11-register FM patch
2    ``5971``   note-on pitch: F-number/octave -> ``A0``/``B0``, key-on (+rhythm)
3    ``59CF``   key-off: ``B0+ch := 0`` (rhythm channels mask ``BD``)
4    ``5A0D``   volume: operator total-level (``0x40``-group) registers
5    ``5A42``   loop: ``cursor = [3198]``
6    ``5A49``   set loop point: ``[3198] = cursor``
7    ``5A50``   flag: ``[31A6] = ah``
===  =========  ===================================================

The song stream and the data tables are *data the port loads*, not code; a
native port supplies ``rb``/``rw`` over its own copy of them.
"""
from __future__ import annotations

from typing import Callable

from skyroads.islands import oracle_link

# DGROUP offsets — state
DELAY = 0x0C83; CURSOR = 0x3196; LOOP = 0x3198; INSTR_BASE = 0x3194
RHYTHM = 0x319A; NOTES = 0x319B; FLAG = 0x31A6
# DGROUP offsets — data tables
OP_OFFS = 0x0C10           # [bx+..] 11 operator-register offsets
OP1_SLOT = 0x0C1B          # [di+..] per-channel operator-1 slot
OP2_SLOT = 0x0C26          # [di+..] per-channel operator-2 slot (0xFF = 1-op)
CONN_SLOT = 0x0C31         # [di+..] per-channel connection slot
VOL_BIAS = 0x0C3C          # [di+..] per-level total-level bias
FNUM_LO = 0x0C6B           # [note%12] F-number low
FNUM_HI = 0x0C77           # [note%12] F-number high / octave


def _ror8(v: int, c: int) -> int:
    c &= 7
    v &= 0xFF
    return ((v >> c) | (v << (8 - c))) & 0xFF if c else v


def _shr8(v: int, c: int) -> int:
    c &= 0xFF
    return (v & 0xFF) >> c if c < 8 else 0


class Engine:
    """The per-tick OPL music sequencer.

    ``rb(off)`` / ``rw(off)`` read a byte / little-endian word from the game's
    data segment (DGROUP). The engine never writes memory back — within a tick it
    keeps an overlay so an event's stores are visible to later events, then
    discards it; the caller advances the real state (or re-reads it each tick).
    """

    def __init__(self, rb: Callable[[int], int], rw: Callable[[int], int]) -> None:
        self.rb = rb
        self.rw = rw
        self.ovl: dict[int, int] = {}
        self.writes: list[tuple[int, int]] = []
        self.cursor = self.loop = self.instr_base = 0

    def _rb(self, off: int) -> int:
        off &= 0xFFFF
        return self.ovl.get(off, self.rb(off))

    def _wb(self, off: int, v: int) -> None:
        self.ovl[off & 0xFFFF] = v & 0xFF

    def _ww(self, off: int, v: int) -> None:
        self._wb(off, v & 0xFF)
        self._wb((off + 1) & 0xFFFF, (v >> 8) & 0xFF)

    def _opl(self, reg: int, val: int) -> None:
        self.writes.append((reg & 0xFF, val & 0xFF))

    # --- opcode handlers (al = channel, ah = note/value/delay) ---
    def _op0(self, al: int, ah: int) -> None:
        self._wb(DELAY, ah)

    def _op7(self, al: int, ah: int) -> None:
        self._wb(FLAG, ah)

    def _op5(self, al: int, ah: int) -> None:               # loop: cursor := [3198] (5A42)
        self.cursor = self.loop

    def _op6(self, al: int, ah: int) -> None:               # set loop point: [3198] := cursor (5A49)
        self.loop = self.cursor
        self._ww(LOOP, self.loop)

    def _op3(self, al: int, ah: int) -> None:            # key-off (5919->59CF)
        if al < 6:
            self._opl((al + 0xB0) & 0xFF, 0)
        else:
            v = _ror8(0xEF, (al - 6) & 0xFF) & self._rb(RHYTHM)
            self._wb(RHYTHM, v)
            self._opl(0xBD, v)

    def _op1(self, al: int, ah: int) -> None:            # note + instrument (5919)
        self._op3(al, ah)
        si = ((ah << 4) + self.instr_base) & 0xFFFF
        di = al
        self._wb(NOTES + di, ah)
        for bx in range(5):                              # operator 1 registers
            self._opl(self._rb(OP1_SLOT + di) + self._rb(OP_OFFS + bx), self._rb(si + bx))
        for bx in range(5, 10):                          # operator 2 registers (skip on carry)
            reg = self._rb(OP2_SLOT + di) + self._rb(OP_OFFS + bx)
            if reg <= 0xFF:
                self._opl(reg, self._rb(si + bx))
        base = self._rb(CONN_SLOT + di)                  # connection register
        if base != 0xFF:
            self._opl((base + self._rb(OP_OFFS + 10)) & 0xFF, self._rb(si + 10))

    def _op2(self, al: int, ah: int) -> None:            # note-on pitch (5971)
        channel = al
        self._op3(al, ah)
        connslot = self._rb(CONN_SLOT + channel)
        if channel >= 7:
            self._rhythm(channel)
            return
        rem = ah % 12
        octave = (ah // 12 + 2) & 0xFF
        self._opl((connslot + 0xA0) & 0xFF, self._rb(FNUM_LO + rem))         # A0: F-number low
        hi = self._rb(FNUM_HI + rem) | ((octave << 2) & 0xFF)                # B0: F-number high|octave
        b0 = (connslot + 0xB0) & 0xFF
        if b0 < 0xB6:
            self._opl(b0, (hi | 0x20) & 0xFF)                                # + key-on
        else:
            self._opl(b0, hi & 0xFF)                                         # rhythm channel: no key-on
            self._rhythm(channel)

    def _rhythm(self, channel: int) -> None:             # 59B7
        v = _shr8(0x10, (channel - 6) & 0xFF) | self._rb(RHYTHM)
        self._wb(RHYTHM, v)
        self._opl(0xBD, v)

    def _op4(self, al: int, ah: int) -> None:            # volume (5A0D)
        di = ah
        bx = al
        note = self._rb(NOTES + bx) & 0xFF
        si = ((note << 4) + self.instr_base) & 0xFFFF
        if self._rb(OP2_SLOT + bx) == 0xFF:
            self._vol(si, bx, di)
            return
        self._vol((si + 5) & 0xFFFF, (bx + 11) & 0xFFFF, di)
        if self._rb(si + 10) & 1:
            self._vol(si, bx, di)

    def _vol(self, si: int, bx: int, di: int) -> None:   # 59EF
        tl = self._rb(si + 1)
        level = (tl & 0x3F) + self._rb(VOL_BIAS + di)
        if level > 0x3F:
            level = 0x3F
        self._opl((self._rb(OP1_SLOT + bx) + 0x40) & 0xFF, level | (tl & 0xC0))

    _DISPATCH = (_op0, _op1, _op2, _op3, _op4, _op5, _op6, _op7)

    @oracle_link(
        boundary="1010:5A55",
        contract="run_tick(): one music-engine tick. If [0C83]!=0 wait (no OPL "
                 "writes); else walk song words from cursor [3196], dispatch "
                 "op=word&7 (al=channel, ah=note/val) through the 8 handlers, "
                 "programming the OPL2, until a delay op arms [0C83]. Returns the "
                 "ordered (reg,val) OPL register writes for the tick.",
        status="VERIFIED",  # OPL write stream byte-exact vs ASM: 12,882/12,882 cold-sound-demo ticks
        merge_target="skyroads.recovered_native.music (future)",
    )
    def run_tick(self) -> list[tuple[int, int]]:
        """Run one tick against the current memory; return its OPL ``(reg, val)`` writes.

        ``self.ovl`` (readable after the call) holds every DGROUP byte this
        tick wrote — including the advanced song cursor (``ds:[3196]``, which
        the ASM stores unconditionally each word via `mov ds:[3196],si`
        (1010:5A69), and which must be committed for the *next* tick to resume
        from the right position rather than replaying this one forever).

        Faithfully mirrors the ASM's loop shape (1010:5A58 is the top of the
        loop, not just an entry check): every iteration checks the delay
        counter FIRST and decrements+exits if it's nonzero, THEN (only if it
        was zero) fetches and dispatches one word, then loops back to that same
        check. So when an ``op0`` (delay) fires mid-tick, the very next
        iteration immediately sees the freshly-armed nonzero delay and
        decrements it once *before* returning — the delay actually stored
        after a tick that arms it is one less than the value the song data
        specifies. This has no effect on the OPL write stream (arming and
        decrementing both emit nothing), so per-tick output verification alone
        cannot catch getting it wrong -- only matters for how many *subsequent*
        ticks wait, i.e. music timing once this drives OPL writes on its own
        rather than merely being cross-checked against ASM that is still
        running (see the multi-tick self-consistency regression test).
        """
        self.ovl = {}
        self.writes = []
        self.cursor = self.rw(CURSOR)
        self.loop = self.rw(LOOP)
        self.instr_base = self.rw(INSTR_BASE)
        while True:
            delay = self._rb(DELAY)                        # 5A58 (overlay-aware: sees
            if delay != 0:                                  # an op0 armed just this tick)
                self._wb(DELAY, delay - 1)                  # 5A5F `dec ds:[0C83]`
                return self.writes
            word = self.rw(self.cursor)
            self.cursor = (self.cursor + 2) & 0xFFFF
            self._ww(CURSOR, self.cursor)
            op = word & 7
            self._DISPATCH[op](self, (word & 0xFF) >> 4, (word >> 8) & 0xFF)

    #: Fixed instrument-table base the reset routine loads percussion patches
    #: from (1010:58E2 `mov ds:[3194],0x0C84`) -- distinct from the live song's
    #: own `[3194]`, which run_tick reads fresh each tick.
    RESET_INSTR_BASE = 0x0C84

    @oracle_link(
        boundary="1010:58A5",
        contract="reset_opl(): the one-time OPL reset + percussion-patch init "
                 "(1010:58A5-5913), run at driver start / song load. Silences all "
                 "operators (0x40-0x55 := 0x3F), key-offs channels 7..0 (melodic "
                 "B0+ch:=0, channels 6/7 via the rhythm mask), enables waveform "
                 "select (reg 0x01) and rhythm mode (0xBD:=0xE0), loads 4 fixed "
                 "percussion instrument patches (channel slots 7..10 from a fixed "
                 "table at 0x0C84, +0x0B/slot) via the same op1 patch-load path as "
                 "run_tick, then fixes the two percussion channels' pitch (A7/B7, "
                 "A8/B8). Returns the ordered (reg,val) OPL writes.",
        status="VERIFIED",  # byte-exact vs ASM: every occurrence in the cold-sound demo
        merge_target="skyroads.recovered_native.music (future)",
    )
    def reset_opl(self) -> list[tuple[int, int]]:
        """Run the one-time OPL reset/init; return its ordered OPL writes."""
        self.ovl = {}
        self.writes = []
        for reg in range(0x40, 0x56):                  # 58B0-58BF: silence all operators
            self._opl(reg, 0x3F)
        self._wb(RHYTHM, 0xE0)                          # 58AB: [319A] = 0xE0
        al = 7
        while al >= 0:                                  # 58C1-58CA: key-off channels 7..0
            self._op3(al, 0)
            al -= 1
        self._opl(0x01, 0x20)                           # 58D0: waveform-select enable
        self._opl(0x08, 0x00)                           # 58D6: CSM/keysplit off
        self._opl(0xBD, 0xE0)                           # 58DC: rhythm mode enable
        self.instr_base = self.RESET_INSTR_BASE         # 58E2
        al = 7
        while al < 0x0B:                                # 58E8-58F9: load percussion patches 7..10
            self._op1(al, 0)
            self.instr_base = (self.instr_base + 0x0B) & 0xFFFF
            al += 1
        self._opl(0xA8, 0xAC)                           # 58FB-5913: percussion fixed pitch
        self._opl(0xB8, 0x0C)
        self._opl(0xA7, 0x02)
        self._opl(0xB7, 0x0D)
        return self.writes
