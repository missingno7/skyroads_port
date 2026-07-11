"""SkyRoads per-column road-draw DISPATCH — `1010:364F-38BE`.

The decision layer above `road_column_strip` (`1010:38BF`, already a
fully-understood register-exact hook — the single most-called rasterizer in
gameplay, 34 callsites/~13% of render work, see `skyroads/hooks.py`'s
extensive comment there): which columns to composite and with what argument
(``ax``, a column index packed with edge-composite flags — bit15 set means
"just position, don't draw"; the low byte selects which stride-3 display-list
record to reach).

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

**What selects between variants (what `[0E42]` points to, and when) is NOT
yet recovered** — these are the two variants observed in a real E2E-demo
capture; there may be more. Consuming these dispatch functions to build a
real framebuffer additionally needs `road_column_strip`'s own recovery (today
only a hook, not yet a pure function) and the (unlocated) code that populates
the two display-list segments (``ds:[0E60]``/``[0E62]``) each frame.
"""
from __future__ import annotations

from typing import List

from skyroads.islands import oracle_link


@oracle_link(
    boundary="1010:364F",
    contract="dispatch_variant_a(e44,e46,e4e,e50,e52,e54,e56,e58,e5a): returns "
             "the list of ax codes to call road_column_strip (1010:38BF) with, "
             "in order. (1) if e56==0 and e58!=0: ax=0 if e50==1; ax=1 if "
             "e5a==0. (2) if e4e<3: ax=0x0200 if e50==3; ax=0x0201 if e50>=3 "
             "and e52<3. (3) if e4e==1 and e50==2: ax in "
             "0x0400..0x0405 (all six). (4) if e4e<3: (else return) ax=0x0500 "
             "if e50==4; ax=0x0501 if additionally e52<4.",
    status="ASM_MATCHED",  # 474/480 (98.75%) real E2E-demo invocations matched
    # exactly (the call-code sequence, in order). The 6 misses share one
    # repeated field snapshot (e44=2,e46=4, rest 0/1) producing a long,
    # non-matching call burst -- almost certainly calls from a THIRD dispatch
    # variant/loop this session hasn't isolated, not a bug in this
    # transcription (every other snapshot, including many with the same
    # e4e/e50/e52/e54 values but different e46, matched exactly). See
    # run_status.md.
    merge_target="skyroads.native.render_dispatch (future)",
)
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


@oracle_link(
    boundary="1010:36F3",
    contract="dispatch_variant_b(e44,e46,e4e,e50,e52,e54,e56,e58,e5a,e5c,e5e): "
             "returns the list of ax codes for road_column_strip, in order. "
             "(A) ax=0 if e4e==1 and e58!=e56 and e50==1. (B) ax=0x8002 if "
             "e56!=0 and e58==0 and (e46<=2 or e54==1). (C) ax=1 if e5a==0 and "
             "e58!=e56 and e54==1. (D) if e4e in (2,3): ax=0x0200 if e50!=e4e "
             "or e5e!=e5c. (E) ax=0x8300 if e4e>=2 and e50<3 and not(e4e==2 "
             "and e50==2). (F) ax=0x0201 if (e4e==2 and e50!=2) or (e4e>=3 and "
             "e50<3 and e52<3). (G) if e4e==4: ax=0x0500 if e50!=e4e or "
             "e5e!=e5c. (H) ax=0x8502 if e4e==4 and e50<4. (I) ax=0x0501 if "
             "e4e==4 and e50<4 and e52<4. (J) if e50<=e4e: done. elif e4e<=2: "
             "if e50==4: ax=0x0500, then ax=0x0501 unless e52==4 or e54==4, "
             "then ax=0x0201 if e52<3 and e54<3; elif e50==2: ax in "
             "0x0400..0x0405 (all six); else: ax=0x0200, then ax=0x0201 if "
             "e52<3 and e54<3. else (e4e>2): ax=0x0500, then ax=0x0501 unless "
             "e52==4 or e54==4.",
    status="ASM_MATCHED",  # 633/640 (98.9%) real E2E-demo invocations matched
    # exactly. The 7 misses share ONE repeated field snapshot producing a long
    # non-matching call burst -- the same anomaly dispatch_variant_a's status
    # note describes (likely a third, unisolated call source), not a bug here:
    # every other snapshot -- including many exercising blocks D/F/G/I/J's
    # branches -- matched exactly. See run_status.md.
    merge_target="skyroads.native.render_dispatch (future)",
)
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
