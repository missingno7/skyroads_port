"""Unit regression for the ulong_div hook's simple-path arithmetic.

Self-contained (no game files): it drives skyroads.hooks._ulong_div_hook
directly with a synthetic stack frame, so it locks in the exact register/
flag/stack contract the in-game differential verifier confirmed. The rare
32/32 "complex" path is deliberately NOT covered here — the hook delegates it
to the original ASM (interpret_current_instruction_without_hook), so there is
no recovered arithmetic of ours to regress; that path is exercised by the
full-game differential verification instead.
"""
from __future__ import annotations

from dos_re.cpu import CPU8086, CPUState, CF, PF, ZF, SF, OF
from dos_re.memory import Memory

from skyroads.hooks import _ulong_div_hook

# divisor high word must be 0 to stay on the simple (32/16) path.
_VECTORS = [
    (1000, 7),
    (0xFFFFFFFF, 3),
    (0x12345678, 0x100),
    (5, 5),
    (0, 9),
    (0xFFFF, 1),
    (0x10000, 2),
    (0xFFFFFFFF, 0xFFFF),
    (0xDEADBEEF, 0x000A),
]


def _run(dividend: int, divisor: int) -> CPU8086:
    mem = Memory()
    ss, sp, ds = 0x2000, 0x0100, 0x3000
    cpu = CPU8086(mem, CPUState(cs=0x1010, ip=0x5D8C, ss=ss, ds=ds, sp=sp))
    mem.ww(ss, sp, 0xBEEF)                       # return IP
    mem.ww(ss, (sp + 2) & 0xFFFF, dividend & 0xFFFF)
    mem.ww(ss, (sp + 4) & 0xFFFF, (dividend >> 16) & 0xFFFF)
    mem.ww(ss, (sp + 6) & 0xFFFF, divisor & 0xFFFF)
    mem.ww(ss, (sp + 8) & 0xFFFF, (divisor >> 16) & 0xFFFF)
    cpu.s.bx, cpu.s.si, cpu.s.bp = 0xAAAA, 0xBBBB, 0xCCCC
    _ulong_div_hook(cpu)
    return cpu


def test_ulong_div_simple_path_quotient_and_contract():
    for dividend, divisor in _VECTORS:
        assert (divisor >> 16) == 0 and divisor != 0, "vector must be simple-path"
        cpu = _run(dividend, divisor)
        q = dividend // divisor
        assert cpu.s.ax == (q & 0xFFFF)
        assert cpu.s.dx == ((q >> 16) & 0xFFFF)
        assert cpu.s.cx == (divisor & 0xFFFF)      # divisor_lo, left in CX by 5D98
        assert cpu.s.ip == 0xBEEF                   # returned to caller
        assert cpu.s.sp == (0x0100 + 10) & 0xFFFF   # ret 8: +2 ret IP +8 args
        # bx/si/bp are push/pop-preserved by the routine.
        assert (cpu.s.bx, cpu.s.si, cpu.s.bp) == (0xAAAA, 0xBBBB, 0xCCCC)


def test_ulong_div_simple_path_flags_are_xor_dx_dx():
    # The path's last flag-setting instruction is `xor dx,dx` (result 0):
    # ZF=1, PF=1, SF=0, CF=0, OF=0.
    cpu = _run(1234, 7)
    assert cpu.get_flag(ZF) is True
    assert cpu.get_flag(PF) is True
    assert cpu.get_flag(SF) is False
    assert cpu.get_flag(CF) is False
    assert cpu.get_flag(OF) is False
