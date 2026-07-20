"""SkyRoads stencil blit — a 3-value template-to-screen byte copy.

Recovered from `1010:0F62-0F8B`. Copies a source byte run to a destination
byte run (typically screen/off-screen memory: mode 13h is one byte per
pixel), remapping each source byte through a tiny 3-entry stencil: `0`
(background/transparent) stays `0`; `1` becomes ``template_color``; any other
nonzero value becomes ``other_color``. This is the low-level primitive behind
menu text/glyph rendering — font glyphs are stored as 0/1/2 stencils and
recolored per-draw into the caller's chosen palette entries.
"""
from __future__ import annotations

from typing import NamedTuple



class StencilStep(NamedTuple):
    """What one source byte produces, for an adapter that needs the ABI too.

    ``value`` is the byte ``stosb`` writes and is the whole SEMANTIC answer;
    the rest is state the pure mapping discards and the original does not.

    ``ax`` is the FULL accumulator after this byte. It threads: ``lodsb`` writes
    only AL, and ``or al,al`` reads only AL, so a zero byte leaves AH alone --
    while a substitution loads a whole WORD over both halves. AX at exit is
    therefore the last SUBSTITUTED byte's colour word, which can be several
    bytes before the end when the source has trailing zeros. Deriving it from
    the final byte instead is wrong, and was wrong twice in the VM hook this
    implementation came from; the very first live call hit the case.

    ``compared`` says whether ``cmp al,1`` ran for this byte. It is what
    discriminates the three per-byte costs, and it also decides AF -- ``or``
    leaves AF alone, so the exit AF belongs to the last NONZERO byte, which
    again need not be the last one.
    """

    value: int
    ax: int
    compared: bool
    byte: int


def stencil_blit_steps(source, template_color: int, other_color: int, ax: int = 0):
    """Yield one :class:`StencilStep` per source byte, LAZILY.

    Laziness is the point, and it is the same idea as ``road_segment_clip``'s
    lazy bound reads: the original interleaves ``lodsb`` and ``stosb`` one byte
    at a time, and source and destination are two caller-chosen far pointers
    that this function cannot prove disjoint. An adapter that drained the source
    first and wrote afterwards would produce a different memory trace whenever
    they overlap. Consuming ``source`` as a generator makes the adapter's write
    for byte i land before the read for byte i+1, which is the ASM's own order.
    """
    tc = template_color & 0xFFFF
    oc = other_color & 0xFFFF
    for b in source:
        b &= 0xFF
        ax = (ax & 0xFF00) | b                  # 0F75 lodsb
        if b == 0:                              # 0F78 or al,al -> 0F84
            yield StencilStep(0, ax, False, b)
            continue
        ax = tc                                 # 0F7C mov ax,[bp+10], either way
        if b != 1:                              # 0F7A cmp al,1
            ax = oc                             # 0F81 mov ax,[bp+12]
        yield StencilStep(ax & 0xFF, ax, True, b)


def stencil_blit(source: bytes, template_color: int, other_color: int) -> bytes:
    """Remap each source byte through the 3-entry stencil (1010:0F76-0F84)."""
    return bytes(s.value for s in
                 stencil_blit_steps(source, template_color, other_color))
