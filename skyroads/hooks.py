"""Replacement hooks: thin VM adapters over pure recovered rules.

A hook only: (1) reads relevant state from original memory/registers, (2)
calls a clean recovered function that knows nothing about the CPU, (3) writes
the result back, (4) reproduces the exact return mechanics. No logic
accumulates here — see docs/hooks_and_verification.md and pitfall #3.

NOT YET INSTALLED BY DEFAULT: see the bottom of this file. The palette-fade
hook is registered but left uninstalled pending verification against the
differential hook verifier (docs/skyroads/run_status.md task tracking,
2026-07-08) — do not flip it on without running that check first.
"""
from __future__ import annotations

from dos_re.cpu import CPU8086
from dos_re.hooks import registry

from skyroads.recovered.palette_fade import blend_palette

CODE_SEG = 0x1010  # SKYROADS.EXE's single code segment (from program.entry_cs)

# CS:IP 1010:4331 — the hot palette-fade interpolation loop (see
# docs/skyroads/symbol_ledger.md). Near call, caller-cleans-up convention
# (every exit is a plain `ret`, never `ret N`): at hook entry SP points at the
# return address, with args at [sp+2]=srcB struct ptr, [sp+4]=srcA struct ptr,
# [sp+6]=duration. Struct layout (both args): word+0 = source segment (fixed
# offset 0 within DS), word+4 = palette entry count (x3 = byte count).
def _palette_fade_hook(cpu: CPU8086) -> None:
    ss, sp = cpu.s.ss, cpu.s.sp
    ds = cpu.s.ds
    arg_b = cpu.mem.rw(ss, (sp + 2) & 0xFFFF)
    arg_a = cpu.mem.rw(ss, (sp + 4) & 0xFFFF)
    duration = cpu.mem.rw(ss, (sp + 6) & 0xFFFF)

    if cpu.mem.rw(ds, 0x003C) != 0:
        seg_b = cpu.mem.rw(ds, arg_b)
        count = cpu.mem.rw(ds, (arg_b + 4) & 0xFFFF)
        seg_a = cpu.mem.rw(ds, arg_a)
        count_bytes = 3 * count

        cpu.mem.ww(ds, 0x1600, 0)
        elapsed = cpu.mem.rw(ds, 0x1600)

        src_b = bytes(cpu.mem.rb(seg_b, i & 0xFFFF) for i in range(count_bytes))
        src_a = bytes(cpu.mem.rb(seg_a, i & 0xFFFF) for i in range(count_bytes))
        out = blend_palette(src_a, src_b, count_bytes, elapsed, duration)
        for i, value in enumerate(out):
            cpu.mem.wb(ds, (0x31A8 + i) & 0xFFFF, value)

    cpu.s.ip = cpu.mem.rw(ss, sp)
    cpu.s.sp = (sp + 2) & 0xFFFF


# Registered but NOT installed: registry.replace only records the mapping;
# dos_re.runtime.create_runtime installs whatever is registered. Uncomment the
# decorator once verified.
# @registry.replace(CODE_SEG, 0x4331, "palette_fade")
def palette_fade_hook(cpu: CPU8086) -> None:
    _palette_fade_hook(cpu)
