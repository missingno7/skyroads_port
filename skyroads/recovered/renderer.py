"""SkyRoads renderer island — recovered, VM-agnostic rendering algorithms.

This module grows bottom-up into a clean reimplementation of the road/object
renderer (see docs/skyroads/run_status.md "the renderer-island plan"). Each
function here knows nothing about the CPU or memory layout; the VM-facing hooks
in skyroads/hooks.py adapt registers/memory to these pure calls and reproduce
the exact original register/flag state for the differential verifier.

First recovered layer: the fixed-point perspective transform at 1010:04C0 —
the keystone every render path calls. It maps a 32-bit horizontal coordinate
and a depth value to a word offset into the perspective table at ds:0x162C
(the caller then reads that word). The leaf rasterizers and the 32-bit
long-arithmetic helpers this sits on are already recovered/hooked.
"""
from __future__ import annotations

from typing import NamedTuple

from skyroads.islands import oracle_link

#: ds-relative base of the perspective word table read at 1010:0521.
PERSPECTIVE_TABLE_BASE = 0x162C


class PerspectiveResult(NamedTuple):
    """Everything a caller (and the exact-state hook) needs from 04C0.

    ``in_range`` False reproduces the ASM's early ``return 0`` (row index out of
    the 0..321 window). ``offset`` is the ds word-offset to read for the result.
    ``rem128``/``rem46`` are the two divide remainders the ASM leaves in DX on
    its two exit paths; ``add_lhs``/``add_rhs`` are the operands of the final
    ``add cx,ax`` whose result becomes the offset AND sets the exit flags.
    """
    in_range: bool
    idx: int
    rem128: int
    offset: int
    rem46: int
    add_lhs: int
    add_rhs: int


@oracle_link(
    boundary="1010:04C0",
    contract="perspective_row_offset(x_lo, x_hi, depth): row idx = "
             "((depth>>7) - 95) mod 2^16; if idx>=322 (unsigned) the ASM returns 0; "
             "else offset = 0x162C + low16(((x_hi:x_lo)/0x2000)/8*14) + 2*(idx/46), "
             "and the caller's AX becomes ds:[offset]. Two unsigned truncating "
             "divides (ulong_div) then an unsigned multiply (ulong_mul, by 14), "
             "in sequence; then a 16-bit /46. Only low words feed the offset.",
    status="VERIFIED",
    merge_target="skyroads.native.renderer (future)",
)
def perspective_row_offset(x_lo: int, x_hi: int, depth: int) -> PerspectiveResult:
    idx = (((depth & 0xFFFF) // 128) + 0xFFA1) & 0xFFFF
    rem128 = (depth & 0xFFFF) % 128
    if idx >= 0x142:  # unsigned compare `cmp si,0x142; jb` at 04D7 -> out of range
        return PerspectiveResult(False, idx, rem128, 0, 0, 0, 0)

    x = ((x_hi & 0xFFFF) << 16) | (x_lo & 0xFFFF)
    # Two truncating unsigned divides (04F0 /0x2000, 04FD /8) then an unsigned
    # MULTIPLY by 14 (050A calls ulong_mul at 5D4C, NOT ulong_div). Only the
    # low word of the product is consumed by the `add cx,ax` at 0510.
    q = (((x // 0x2000) // 8) * 14) & 0xFFFF
    add_lhs = (PERSPECTIVE_TABLE_BASE + q) & 0xFFFF
    add_rhs = (2 * (idx // 46)) & 0xFFFF  # `div bx(46)` then `shl ax,1` at 0519/051B
    offset = (add_lhs + add_rhs) & 0xFFFF
    return PerspectiveResult(True, idx, rem128, offset, idx % 46, add_lhs, add_rhs)
