"""SkyRoads road-column strip COMPOSITOR — `1010:38BF`.

Given a column descriptor (``ax``: low byte = which stride-3
display-list record to reach, skipping that many `0xFF`-terminated columns;
bit15 = :data:`SKIP_SYNC_LOOP_BIT`), it scans two stride-3 display-list
segments to locate the target column's records, then ALWAYS walks the second
list's records compositing horizontal pixel runs from a source bitmap segment
onto a destination (screen) segment, one scanline per record, until a `0xFF`
length marker ends the column — bit15 only skips a bp/si synchronization
pre-loop beforehand, it does not skip compositing.

Ported here as a PURE function reading/writing through ``(seg, offset)``
callbacks — the same shape `skyroads.native.image.NativeGameImage`'s own
``rb``/``rw``/``ww`` methods have, and the same shape a VM memory adapter
provides, so this function can run over either representation.

Unlike the hook (which must reproduce exact register/flag exit state for the
differential verifier), this pure port only returns whether it composited —
callers that need the hook's exact register bookkeeping still use the VM hook.
"""
from __future__ import annotations

from typing import Callable


#: Column-descriptor bit that SKIPS the bp/si synchronization pre-loop
#: (1010:3937-393E `jnz -> 3954`, jumping straight into composite prep) --
#: compositing still happens either way; this only skips the "advance until
#: bp>=si" wait.
SKIP_SYNC_LOOP_BIT = 0x8000
#: The stride-3 display-list record terminator (1010:38EA `al=0xFF`).
RECORD_TERMINATOR = 0xFF
#: Per-scanline stride within a column (1010:394E/3958 `add bp,0x140`).
SCANLINE_STRIDE = 0x140
#: The vertical origin bias applied before compositing (1010:3954 `add bp,0x2800`).
ORIGIN_BIAS = 0x2800
#: DGROUP scratch the column descriptor is unconditionally mirrored to
#: (1010:38C2 `mov [0E74],ax`) before any positioning/compositing happens.
COLUMN_DESCRIPTOR_SCRATCH = 0x0E74


def road_column_strip(
    rb: Callable[[int, int], int], rw: Callable[[int, int], int],
    ww: Callable[[int, int, int], None],
    ax: int, ds_seg: int, e44: int, e46: int, e48_down: int, e64: int,
    seg_records_a: int, seg_records_b: int, seg_src: int, seg_dst: int,
) -> bool:
    ax &= 0xFFFF
    ww(ds_seg, COLUMN_DESCRIPTOR_SCRATCH, ax)      # 38C2: unconditional mirror
    di = (ax & 0x7FFF) >> 7
    a = (0x0B - (e44 & 0xFFFF)) & 0xFFFF
    a = (a * 4 + 4) & 0xFFFF
    a = (a - (e46 & 0xFFFF)) & 0xFFFF
    a = (a * 0x0C) & 0xFFFF
    di = (di + a) & 0xFFFF
    di = (di + (e64 & 0xFFFF)) & 0xFFFF

    def scan(seg: int, bx: int, count: int) -> int:
        for _ in range(count & 0xFF):
            bx = (bx + 3) & 0xFFFF
            while rb(seg, bx) != RECORD_TERMINATOR:
                bx = (bx + 3) & 0xFFFF
            bx = (bx + 1) & 0xFFFF
        return bx

    col = ax & 0xFF
    bx_a = rw(seg_records_a, di)
    bx_a = scan(seg_records_a, bx_a, col)
    si = rw(seg_records_a, (bx_a + 1) & 0xFFFF)

    di2 = (di - (e64 & 0xFFFF)) & 0xFFFF
    bx_b = rw(seg_records_b, di2)
    bx_b = scan(seg_records_b, bx_b, col)
    bp = rw(seg_records_b, (bx_b + 1) & 0xFFFF)
    bx_b = (bx_b + 3) & 0xFFFF

    if not (ax & SKIP_SYNC_LOOP_BIT):
        while True:                               # 3940-3952 skip-loop
            if rb(seg_records_b, bx_b) == RECORD_TERMINATOR:
                return False                       # 3944: no columns to draw
            if bp >= si:
                break
            bx_b = (bx_b + 3) & 0xFFFF
            bp = (bp + SCANLINE_STRIDE) & 0xFFFF
    # else: SKIP_SYNC_LOOP_BIT set -> composite immediately from bx_b/bp as-is

    bp = (bp + ORIGIN_BIAS) & 0xFFFF
    down = e48_down != 0
    if down:
        bp = (bp - 1) & 0xFFFF

    composited = False
    while True:                                   # 3978/39A3 copy loop
        length0 = rb(seg_records_b, bx_b)
        if length0 == RECORD_TERMINATOR:
            break
        if not down:
            off0 = (bp - length0) & 0xFFFF
        else:
            off0 = (bp + length0) & 0xFFFF
        run = rb(seg_records_b, (bx_b + 1) & 0xFFFF)
        low = off0 & 1
        si_word = off0 & ~1
        cx = (run + low) if not down else ((run - low + 1) & 0xFFFF)
        words = ((cx >> 1) + (cx & 1)) & 0xFFFF
        step = -2 if down else 2
        sp = si_word
        dp = si_word
        for _ in range(words):
            ww(seg_dst, dp, rw(seg_src, sp))
            sp = (sp + step) & 0xFFFF
            dp = (dp + step) & 0xFFFF
            composited = True
        bp = (bp + SCANLINE_STRIDE) & 0xFFFF
        bx_b = (bx_b + 3) & 0xFFFF

    return composited
