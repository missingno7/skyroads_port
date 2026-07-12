"""The native audio boundary (`skyroads.audio`): OPL writes -> semantic events.

Pure decoder unit tests + a live test running the RECOVERED music engine
(byte-exact over 12,882 verified ticks) over a real gameplay snapshot's song
and checking the decoded stream is musically sane.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from skyroads.audio.events import DrumHit, NoteOff, NoteOn
from skyroads.audio.opl_events import OplEventDecoder

SNAP = Path(__file__).resolve().parents[1] / "artifacts" / "frame_2d1f" / "snap92"


def test_keyon_decodes_to_note_on_with_correct_frequency() -> None:
    dec = OplEventDecoder()
    # fnum=0x244 (580), block=4 -> 580 * 49716 / 2^16 = 439.9 Hz (concert A)
    evs = dec.feed([(0x20, 0x01), (0x23, 0x01), (0x40, 0x10), (0x43, 0x08),
                    (0xA0, 0x44), (0xB0, 0x20 | (4 << 2) | 0x02)])
    ons = [e for e in evs if isinstance(e, NoteOn)]
    assert len(ons) == 1
    assert abs(ons[0].freq_hz - 440.0) < 1.0
    assert 0.0 < ons[0].volume <= 1.0
    # key-off -> NoteOff; repeated key-off is not re-emitted
    evs = dec.feed([(0xB0, (4 << 2) | 0x02), (0xB0, (4 << 2) | 0x02)])
    assert [type(e) for e in evs] == [NoteOff]


def test_drum_bit_rises_emit_drum_hits() -> None:
    dec = OplEventDecoder()
    evs = dec.feed([(0xBD, 0x20 | 0x10)])          # rhythm on + bass drum
    assert [e.drum for e in evs if isinstance(e, DrumHit)] == ["bass"]
    evs = dec.feed([(0xBD, 0x20 | 0x10)])          # held, no rise
    assert not [e for e in evs if isinstance(e, DrumHit)]
    evs = dec.feed([(0xBD, 0x20)])                 # released
    assert not [e for e in evs if isinstance(e, DrumHit)]
    evs = dec.feed([(0xBD, 0x20 | 0x18)])          # bass + snare rise together
    assert sorted(e.drum for e in evs if isinstance(e, DrumHit)) == ["bass", "snare"]


@pytest.mark.skipif(not (SNAP / "memory_1mb.bin").exists(),
                    reason="snap92 snapshot not present (gitignored)")
def test_real_song_decodes_to_musical_events() -> None:
    """Run the recovered engine over the level-14 gameplay song for 1200 ticks
    (~17s at 70Hz) and decode: dozens of notes, all inside a sane musical
    range (the observed run: 43 NoteOns, 195.7..880 Hz, median 220 Hz)."""
    from skyroads.recovered.music import Engine

    mem = (SNAP / "memory_1mb.bin").read_bytes()
    base = 0x1686 << 4
    overlay: dict[int, int] = {}

    def rb(o: int) -> int:
        o &= 0xFFFF
        return overlay.get(o, mem[base + o])

    def rw(o: int) -> int:
        return rb(o) | (rb(o + 1) << 8)

    eng = Engine(rb, rw)
    dec = OplEventDecoder()
    ons, offs = [], 0
    for _ in range(1200):
        writes = eng.run_tick()
        overlay.update(eng.ovl)
        for ev in dec.feed(writes):
            if isinstance(ev, NoteOn):
                ons.append(ev)
            elif isinstance(ev, NoteOff):
                offs += 1
    assert len(ons) >= 20, f"only {len(ons)} NoteOns decoded"
    assert offs >= 10
    for ev in ons:
        assert 50.0 < ev.freq_hz < 4000.0, f"unmusical frequency {ev.freq_hz}"
