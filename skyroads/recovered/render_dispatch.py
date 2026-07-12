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

**What selects between variants** is now understood (see run_status.md's
"found the render entry point" entry — `1010:34AE` sets `[0E42]`
unconditionally based on a caller mode: off-screen-buffer pass vs
direct-to-VGA pass, not a road-shape choice as first guessed). `34AE` itself
is not yet ported to clean code (an attempt was made and deliberately backed
out after catching transcription mistakes -- see the same entry).

**Open caveat**: these two functions were verified on their `road_column_strip`
CALL SEQUENCE (which `ax` codes fire, in what order) against real captures —
not a full memory diff of everything `1010:364F`/`36F3` themselves touch (the
way `road_column.road_column_strip` was, which caught two real bugs a
narrower check would have missed). An attempt to full-memory-diff these two
functions the same way hit an unresolved capture-script issue (the return
address never observed as reached, despite the identical technique working
for `road_column_strip`) — not chased down further; see run_status.md. So a
possible SILENT side effect on `[0E42]` or elsewhere, beyond the
`road_column_strip` calls these functions already document, is an open,
undischarged question, not one this docstring can currently rule out.

**Out-of-range caveat (2026-07-12)**: verified only for the NORMAL
classification range (fields 0..~4, the reduced nibbles the render loop
produces). On a `34AE` pass observed with LARGE out-of-range field values
(``e50=1568`` etc. — an atypical/non-road render), `dispatch_variant_b`'s
output diverged from the ASM (16/80). Those values never occur in normal
gameplay classification, so this doesn't affect the 633/640 result, but the
transcription is NOT guaranteed for out-of-range inputs. See run_status.md's
"mode-1 DEFINITIVE" entry.
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
