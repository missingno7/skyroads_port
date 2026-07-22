from __future__ import annotations

from dos_re.cpu import CPU8086, CPUState
from dos_re.memory import Memory

from skyroads.lifted.functions.lifted_1010_4331 import _run_palette_byte_loop


def _palette_loop_runtime(*, watched: bool):
    memory = Memory()
    state = CPUState(
        ax=0x1111, bx=0x2222, cx=0x3333, dx=0x4444,
        cs=0x1010, ds=0x1000, es=0x5555, ss=0x1000,
        sp=0x7F00, bp=0x8000, si=0x6666, di=0x7777,
        flags=0x0202,
    )
    cpu = CPU8086(memory, state)
    bp = state.bp
    structure = 0x2000
    destination = 0x3000
    source_a = 0x4000
    source_b = 0x5000
    source_a_segment = 0x1100
    source_b_segment = 0x1200

    memory.ww(state.ss, bp - 2, 0xFFFF)
    memory.ww(state.ss, bp + 4, structure)
    memory.ww(state.ds, structure + 4, 4)  # twelve palette bytes
    memory.ww(state.ss, bp - 14, destination)
    memory.ww(state.ss, bp - 12, source_a)
    memory.ww(state.ss, bp - 10, source_a_segment)
    memory.ww(state.ss, bp - 8, source_b)
    memory.ww(state.ss, bp - 6, source_b_segment)
    memory.ww(state.ss, bp - 4, 37)
    for index in range(1, 13):
        memory.wb(source_a_segment, source_a + index, 63 - index)
        memory.wb(source_b_segment, source_b + index, index)
    if watched:
        memory.write_watchers.append(lambda address, old, new: None)
    return cpu


def test_palette_fade_bulk_loop_preserves_literal_machine_continuation():
    bulk = _palette_loop_runtime(watched=False)
    literal = _palette_loop_runtime(watched=True)

    assert _run_palette_byte_loop(bulk, bulk.s, bulk.mem) == 20
    assert _run_palette_byte_loop(literal, literal.s, literal.mem) == 20

    assert bulk.s == literal.s
    assert bulk.instruction_count == literal.instruction_count
    assert bulk.mem.data == literal.mem.data
