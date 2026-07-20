"""SkyRoads per-column road-draw DISPATCH — `1010:364F-38BE`.

The decision layer above ``road_column_strip`` determines which columns to
composite and with what ``ax`` code. The low byte selects a stride-3
display-list record; high bits carry edge-composite behavior.

This dispatch is reached through an INDIRECT function pointer at
``ds:[0E42]`` (`1010:35F8`) — the game switches between at least two
dispatch VARIANTS depending on road/track shape. Both recovered here as pure
functions returning the list of ``ax`` codes `road_column_strip` should be
called with, in order:

* :func:`dispatch_variant_a` (`1010:364F-36F2`) — the shorter variant.
* :func:`dispatch_variant_b` (`1010:36F3-38BE`) — the longer variant, whose
  tail (`380E-38BE`) subsumes most of variant A's own decision tree (same
  `0x0200`/`0x0201`/`0x0400..0x0405`/`0x0500`/`0x0501` calls, differently
  gated) plus extra codes (`0x8300`, `0x8502`) and two fields
  (``ds:[0E5C]``/``[0E5E]``) variant A never reads.

``1010:34AE`` selects the variant through ``[0E42]`` according to the caller
mode: off-screen-buffer pass or direct-to-VGA pass.

**Evidence boundary**: the variants are verified on their ordered
``road_column_strip`` call sequences, not on every possible side effect of
the original functions.

**Out-of-range caveat**: verified only for the normal
classification range (fields 0..~4, the reduced nibbles the render loop
produces). On a `34AE` pass observed with LARGE out-of-range field values
(``e50=1568`` etc. — an atypical/non-road render), `dispatch_variant_b`'s
output diverged from the oracle. The transcription is therefore not guaranteed
for out-of-range inputs.
"""
from __future__ import annotations

from typing import List



def dispatch_variant_a(
    e44: int, e46: int, e4e: int, e50: int, e52: int, e54: int,
    e56: int, e58: int, e5a: int,
) -> List[int]:
    calls: List[int] = []
    if e56 == 0 and e58 != 0:
        if e50 == 1:
            calls.append(0x0000)
        if e5a == 0:
            calls.append(0x0001)
    if e4e < 3:
        if e50 == 3:
            calls.append(0x0200)
        if e50 >= 3 and e52 < 3:
            calls.append(0x0201)
    if e4e == 1 and e50 == 2:
        calls += [0x0400, 0x0401, 0x0402, 0x0403, 0x0404, 0x0405]
    if e4e >= 3:
        return calls
    if e50 != 4:
        return calls
    calls.append(0x0500)
    if e52 >= 4:
        return calls
    calls.append(0x0501)
    return calls


def dispatch_variant_b(
    e44: int, e46: int, e4e: int, e50: int, e52: int, e54: int,
    e56: int, e58: int, e5a: int, e5c: int, e5e: int,
) -> List[int]:
    calls: List[int] = []
    if e4e == 1 and e58 != e56 and e50 == 1:                       # (A)
        calls.append(0x0000)
    if e56 != 0 and e58 == 0 and (e46 <= 2 or e54 == 1):            # (B)
        calls.append(0x8002)
    if e5a == 0 and e58 != e56 and e54 == 1:                        # (C)
        calls.append(0x0001)
    if e4e in (2, 3):                                                # (D)
        if e50 != e4e or e5e != e5c:
            calls.append(0x0200)
    if e4e >= 2 and e50 < 3 and not (e4e == 2 and e50 == 2):        # (E)
        calls.append(0x8300)
    if (e4e == 2 and e50 != 2) or (e4e >= 3 and e50 < 3 and e52 < 3):  # (F)
        calls.append(0x0201)
    if e4e == 4:                                                     # (G)
        if e50 != e4e or e5e != e5c:
            calls.append(0x0500)
    if e4e == 4 and e50 < 4:                                        # (H)
        calls.append(0x8502)
    if e4e == 4 and e50 < 4 and e52 < 4:                            # (I)
        calls.append(0x0501)
    if e50 <= e4e:                                                   # (J)
        return calls
    if e4e <= 2:
        if e50 == 4:
            calls.append(0x0500)
            if not (e52 == 4 or e54 == 4):
                calls.append(0x0501)
            if e52 < 3 and e54 < 3:
                calls.append(0x0201)
        elif e50 == 2:
            calls += [0x0400, 0x0401, 0x0402, 0x0403, 0x0404, 0x0405]
        else:
            calls.append(0x0200)
            if e52 < 3 and e54 < 3:
                calls.append(0x0201)
    else:
        calls.append(0x0500)
        if not (e52 == 4 or e54 == 4):
            calls.append(0x0501)
    return calls
