"""Palette fade interpolation — recovered from CS:IP 1010:4331-4457.

See docs/skyroads/symbol_ledger.md "Palette-fade interpolation" for the full
trace evidence. Pure function: blends one byte. The VM-facing hook
(skyroads/hooks.py) drives the per-byte loop and knows the memory layout;
this function knows nothing about the CPU.

The 1010:43A9-442D inner loop — not the 4331 outer function — is the hook
target: 4331's own 434A-4452 loop re-runs this inner pass ~duration times in
real elapsed ticks (each pass recomputing percent from ds:1600 and pushing to
the DAC) to animate the fade over real time. Hooking 4331 wholesale would
either skip that real-time animation or require reimplementing the wait/DAC
loop; hooking just the hot per-byte body preserves the original pacing
exactly while removing its ~20-instruction-per-byte interpreted cost.
"""
from __future__ import annotations

from skyroads.islands import oracle_link


@oracle_link(
    boundary="1010:43A9",
    contract="blend_byte(a, b, percent) = b + trunc((a-b) * percent / 100), "
             "x86 IDIV truncates toward zero",
    status="VERIFIED",
    merge_target="skyroads.handrecovered_native.palette (future)",
)
def blend_byte(byte_a: int, byte_b: int, percent: int) -> int:
    delta = byte_a - byte_b
    prod = delta * percent
    blended = -(-prod // 100) if prod < 0 else prod // 100
    return (byte_b + blended) & 0xFF
