"""Master timer-tick rule — recovered from the INT 08h ISR at 1010:3B17.

The game's IRQ0 handler is the master clock + music-tempo driver. This module
holds the *pure* decision rule the handler runs each tick (no CPU, no memory,
no ports — portable game logic); the VM-facing frame (pusha/popa/iret, the
sound-engine call, the PIT/PIC port writes) lives in the thin hook
`master_timer_isr` in skyroads/hooks.py.

Recovery trail: the routine was first lifted mechanically by `dos_re.lift`
(`liftverify` proved the literal lift byte-exact against the interpreted
original — 199 in-situ calls, then every branch), and this is the refactor of
that lift into the pure rule + adapter split. The end-to-end hook is verified
byte-exact across every prescaler value in tests/test_master_timer_isr.py.

The tick logic: a prescaler counts 9→0 and wraps. On the two ticks it reads
5 or 0, the handler emits the next music note — bump the elapsed-tick counter,
advance the song cursor unless the note stream ended (word 0), and set PIT
channel 2 to that note's period. Each tick the prescaler decrements; on wrap it
resets to 9 and the handler chains to the BIOS timer ISR (so BIOS timekeeping
still advances once per cycle), otherwise it sends the EOI and returns.
"""
from __future__ import annotations

from dataclasses import dataclass

from skyroads.islands import oracle_link


@dataclass(frozen=True)
class TimerTick:
    """What one timer tick decides, as a pure function of its inputs."""
    emit_note: bool          # this tick emits a music note (prescaler was 5 or 0)
    advance_cursor: bool     # the song stream continues (note word != 0)
    pit_divisor: int         # PIT channel-2 reload for the note (valid iff emit_note)
    next_prescaler: int      # value stored back to the prescaler after this tick
    chain_to_bios: bool      # the prescaler wrapped: reset to 9 and chain BIOS


PRESCALER_RELOAD = 0x09      # the prescaler is reset to this when it wraps


@oracle_link(
    boundary="1010:3B17",
    contract="advance_music_timer(prescaler, note_word) decides the per-tick music "
             "emission (on prescaler 5/0), the PIT-ch2 divisor (note_word+2), the "
             "prescaler countdown, and the wrap->reset+BIOS-chain. Pure; the hook "
             "supplies the elapsed-tick counter, cursor, ports and ISR frame.",
    status="ASM_MATCHED",
    merge_target="skyroads.native.timing (future)",
)
def advance_music_timer(prescaler: int, note_word: int) -> TimerTick:
    emit_note = prescaler in (0, 5)
    decremented = (prescaler - 1) & 0xFF
    wrapped = bool(decremented & 0x80)          # 0 -> 0xFF: the sign bit sets
    return TimerTick(
        emit_note=emit_note,
        advance_cursor=emit_note and note_word != 0,
        pit_divisor=(note_word + 2) & 0xFFFF,
        next_prescaler=PRESCALER_RELOAD if wrapped else decremented,
        chain_to_bios=wrapped,
    )
