"""Palette fade interpolation — recovered from CS:IP 1010:4331-4457.

See docs/skyroads/symbol_ledger.md "Palette-fade interpolation" for the full
trace evidence. Pure function: takes the two source palette byte arrays, the
elapsed/duration counters, and returns the blended byte array. The VM-facing
hook (skyroads/hooks.py) reads/writes memory; this function knows nothing
about the CPU.
"""
from __future__ import annotations

from skyroads.islands import oracle_link


@oracle_link(
    boundary="1010:4331",
    contract="blend[i] = srcB[i] + trunc((srcA[i]-srcB[i]) * percent / 100), "
             "percent = clamp(100*elapsed//duration, 100) if duration else 100",
    status="RECOVERED",
    merge_target="skyroads.native.palette (future)",
)
def blend_palette(src_a: bytes, src_b: bytes, count_bytes: int, elapsed: int, duration: int) -> bytes:
    if duration == 0:
        percent = 100
    else:
        percent = (100 * elapsed) // duration
        if percent > 100:
            percent = 100
    out = bytearray(count_bytes)
    for i in range(count_bytes):
        byte_b = src_b[i]
        byte_a = src_a[i]
        delta = byte_a - byte_b
        prod = delta * percent
        # x86 IDIV truncates toward zero; Python's // truncates toward -inf.
        blended = -(-prod // 100) if prod < 0 else prod // 100
        out[i] = (byte_b + blended) & 0xFF
    return bytes(out)
