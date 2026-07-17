"""SkyRoads render-CLASSIFICATION loop — `1010:356B-3627` (inside `34AE`).

The producer of the per-column dispatch inputs. `34AE`'s render pass walks the
road-segment records and, for each column it will draw, extracts the
classification fields (``e44``..``e5e``) that `render_dispatch.dispatch_variant_a`/
`_b` then turn into the ordered ``road_column_strip`` call list. This module is
that walk — the missing middle between the raw road records and the already-
recovered dispatch/compositor.

Decoded from `34AE`'s proven lift (`skyroads/lifted/lifted_1010_34ae.py`, blocks
15-22), not re-derived from raw ASM (the same code an earlier hand-derivation
mis-transcribed three times — see run_status.md's 34AE entries). It is a
TRIPLE-nested loop, one dispatch call per innermost iteration:

    e4c = record_base
    for e44 in 11 down to 2:          # outer; e4c -= 0xE each pass
      for e46 in 1..4:               # middle
        for e48 in (0, 1):           # inner toggle
          <extract the 13 fields, then call [0E42] (dispatch)>

Confirmed subtleties (each a real trap): the `[0E48]-1`/`[0E44]-1`/`[0E46]-4`
ops are `cmp` (flag-only), not stores; the two `neg` ops give ``col`` and ``si``
their signed forms below; and the shape-reduction table at ``ds:[0BA7]`` is the
genuine compile-time constant ``[1,2,3,3,4,4,1,1]`` (:data:`SHAPE_TABLE`),
whereas ``ds:[0E76]``'s "8 buffer segments" are runtime-allocated (NOT baked
here).

Verified 80/80 against a real VM capture: one full `34AE` render invocation
(variant A, `record_base=0x16B8`) from `demo_e2e_20260710_132930` produced
exactly 80 dispatch calls (= 10 outer x 4 middle x 2 inner) and this function
reproduces every field of every call byte-exact. The fields it reads come from
the `ds:[0x162C]` road-record region already established as the per-level
projected road (see run_status.md).
"""
from __future__ import annotations

from typing import Callable, List, NamedTuple

from skyroads.islands import oracle_link

#: `ds:[0BA7]`, an 8-byte compile-time shape-reduction lookup (verified constant
#: across levels). Indexed by a road-record nibble & 7.
SHAPE_TABLE = (1, 2, 3, 3, 4, 4, 1, 1)

#: outer loop: e44 counts 11 down to 2 (10 iterations); e4c steps back by 0xE.
OUTER_START = 0xB
RECORD_STRIDE = 0xE


class ColumnClass(NamedTuple):
    """One innermost-iteration's classification fields — the exact inputs
    `render_dispatch.dispatch_variant_a`/`_b` consume (plus the loop indices
    ``e44``/``e46``/``e48``/``e4c`` for traceability)."""
    e44: int
    e46: int
    e48: int
    e4c: int
    e4e: int
    e50: int
    e52: int
    e54: int
    e56: int
    e58: int
    e5a: int
    e5c: int
    e5e: int


@oracle_link(
    boundary="1010:356B-3627",
    contract="render_classify(rb, record_base): walk 34AE's road-record "
             "classification loop and return the per-column ColumnClass list "
             "(one per dispatch call). Triple-nested: e44 11..2 (e4c -= 0xE "
             "per pass), e46 1..4, e48 0/1. Per iter: col = (4-e46) if e48==0 "
             "else e46+2; bx = e4c + 2*col; si = 2 - 4*e48; then e56=[bx]&0xF, "
             "e5c=[bx]>>4, e4e=SHAPE[[bx+1]&7], e58=[bx-0xE]&0xF, "
             "e5e=[bx-0xE]>>4, e50=SHAPE[[bx-0xD]&7], e5a=[bx+si]&0xF, "
             "e52=SHAPE[[bx+si+1]&7], e54=SHAPE[[bx+si-0xD]&7].",
    status="ASM_MATCHED",  # 80/80 fields of a real 34AE invocation (variant A,
    # record_base 0x16B8, demo_e2e_20260710_132930) reproduced byte-exact.
    merge_target="skyroads.native.render (future)",
)
def render_classify(rb: Callable[[int], int], record_base: int) -> List[ColumnClass]:
    """Reproduce 34AE's classification loop over the road records read via
    ``rb`` (a DGROUP byte reader, offset -> 0..255), starting at
    ``record_base`` (``ds:[0E4C]`` before the loop). Returns one
    :class:`ColumnClass` per dispatch call, in call order."""
    out: List[ColumnClass] = []
    e4c = record_base & 0xFFFF
    for outer in range(10):
        e44 = OUTER_START - outer
        for e46 in range(1, 5):
            for e48 in (0, 1):
                col = (4 - e46) if e48 == 0 else (e46 + 2)
                bx = (e4c + 2 * col) & 0xFFFF
                si = (2 - 4 * e48) & 0xFFFF
                b_bx = rb(bx)
                b_bxm = rb((bx - 0xE) & 0xFFFF)
                out.append(ColumnClass(
                    e44=e44, e46=e46, e48=e48, e4c=e4c,
                    e56=b_bx & 0xF,
                    e5c=b_bx >> 4,
                    e4e=SHAPE_TABLE[rb((bx + 1) & 0xFFFF) & 7],
                    e58=b_bxm & 0xF,
                    e5e=b_bxm >> 4,
                    e50=SHAPE_TABLE[rb((bx - 0xD) & 0xFFFF) & 7],
                    e5a=rb((bx + si) & 0xFFFF) & 0xF,
                    e52=SHAPE_TABLE[rb((bx + si + 1) & 0xFFFF) & 7],
                    e54=SHAPE_TABLE[rb((bx + si - 0xD) & 0xFFFF) & 7],
                ))
        e4c = (e4c - RECORD_STRIDE) & 0xFFFF
    return out
