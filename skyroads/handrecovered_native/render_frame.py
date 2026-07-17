"""Native `1010:34AE` road render — assembling the recovered renderer pieces.

:func:`run_34ae` is the COMPLETE function (decoded 2026-07-13 from the proven
lift, all 28 blocks): both modes (``ax == 0`` composite bg->offscreen with
dispatch variant A; ``ax != 0`` PRESENT offscreen->VGA ``0xA000`` with variant
B), all four bodies (`[0E32]` full copy / delta==0 nothing / delta>=8 band
copy / the column loop), each ending in a `39D4` ship-sprite call. Verified
full-1MB byte-exact inside `compose_frame` on four captures plus a 6-frame
chain (see run_status.md). The remainder of this module is the older mode-0
subset (:func:`composite_mode0` etc.), kept for its tests and tooling:

The off-screen COMPOSITE pass of `1010:34AE` (``mode == 0``), assembled over a
:class:`~skyroads.handrecovered_native.image.NativeGameImage` from the individually-recovered
stages, in the order `34AE` itself runs them:

    34AE setup (blocks 0-12, verified 6/6) -- computes the source/dest/dispatch
      selection, the [0E60]/[0E62] display-list segments, [0E64], and the
      road-record base pointer, all from the frame's [0E2A]/[0E6A] positions.
        |
    render_classify (skyroads.handrecovered.render_classify, 80/80) -- walks the
      road records to produce the per-column classification fields.
        |
    dispatch_variant_a (skyroads.handrecovered.render_dispatch) -- turns each
      classification into the ordered road_column_strip ``ax`` call list.
        |
    road_column_strip (skyroads.handrecovered.road_column, full-mem-diff verified)
      -- composites one column into the destination buffer.

**Verified: this reproduces the VM's EXACT road_column_strip call sequence.**
For a real mode-0 pass (demo_e2e_20260710_132930), the VM made 24
road_column_strip calls; :func:`mode0_column_calls` produces the identical 24
``(ax, e44, e46, e48)`` tuples with identical per-pass ``e60/e62/e64/e66/e68``
(see ``tests/test_render_frame.py``). So the render DECISION pipeline
(setup -> classify -> dispatch) is proven correct end to end.

:func:`composite_mode0` additionally runs `road_column_strip` per column to
composite pixels into the destination buffer. Its inputs — the display-list
records at ``[0E60]``/``[0E62]`` and the source bitmap at ``[0E66]`` — were
confirmed byte-IDENTICAL between a pre-`34AE` seed and the actual
`road_column_strip` call (0/4096 changed in each), and `road_column_strip` is
full-memory-diff verified, so the compositing is correct by construction. (An
earlier note here wrongly blamed a pixel mismatch on an unrecovered
"display-list builder"; that was a comparison-reference error — the mismatch
was against the VM's post-`39D4`/mode-1 image, not the post-mode-0-columns dest
— now retracted. See run_status.md's 2026-07-12 correction.) A clean
independent full-pixel VM diff is still worth adding; the pieces are all
verified.
"""
from __future__ import annotations

from typing import Callable, List, NamedTuple, Tuple

from skyroads.handrecovered_native.image import NativeGameImage
from skyroads.handrecovered.render_classify import ColumnClass, render_classify
from skyroads.handrecovered.render_dispatch import (dispatch_variant_a,
                                                dispatch_variant_b)
from skyroads.handrecovered.road_column import road_column_strip

#: `1010:34AE` block-12: record base = ([0E2A]>>3)*0xE + PERSPECTIVE_TABLE_BASE
#: + 0x62.
PERSPECTIVE_TABLE_BASE = 0x162C
RECORD_BASE_BIAS = 0x62
RECORD_STEP = 0xE
#: `ds:[0E76]`, 8 word entries -- the rotating display-list buffer segments.
BUFFER_SEG_TABLE = 0x0E76
#: source/dest field addresses read/written by the setup.
SRC_FIELD = 0x5170        # -> [0E66] source (mode 0)
OFFSCREEN_FIELD = 0x0E36  # -> [0E68] dest (mode 0)


class FrameSetup(NamedTuple):
    """The mode-0 setup outputs (34AE blocks 2-12), all verified 6/6."""
    seg_src: int      # [0E66]
    seg_dst: int      # [0E68]
    seg_records_cur: int   # [0E60]
    seg_records_prev: int  # [0E62]
    e64: int          # [0E64]
    record_base: int  # [0E4C]


def compute_mode0_setup(img: NativeGameImage, ds: int) -> FrameSetup:
    """Reproduce `34AE`'s mode-0 setup (blocks 2-12) from the image's DGROUP
    position fields. Verified 6/6 against real captures (see run_status.md)."""
    e2a = img.rw(ds, 0x0E2A)
    e6a = img.rw(ds, 0x0E6A)
    seg_src = img.rw(ds, SRC_FIELD)
    seg_dst = img.rw(ds, OFFSCREEN_FIELD)
    e64 = 0x30 if (e2a >> 3) == (e6a >> 3) else 0
    seg_records_prev = img.rw(ds, (BUFFER_SEG_TABLE + 2 * (e6a & 7)) & 0xFFFF)
    seg_records_cur = img.rw(ds, (BUFFER_SEG_TABLE + 2 * (e2a & 7)) & 0xFFFF)
    record_base = ((e2a >> 3) * RECORD_STEP + PERSPECTIVE_TABLE_BASE + RECORD_BASE_BIAS) & 0xFFFF
    return FrameSetup(seg_src, seg_dst, seg_records_cur, seg_records_prev, e64, record_base)


class ColumnCall(NamedTuple):
    """One resolved road_column_strip call the mode-0 pass will make."""
    ax: int
    e44: int
    e46: int
    e48: int


def mode0_column_calls(img: NativeGameImage, ds: int) -> List[ColumnCall]:
    """The ordered road_column_strip call list the mode-0 pass makes, from
    setup -> render_classify -> dispatch_variant_a. This is the render DECISION
    pipeline; it needs only the road records (not the display-list buffers).
    Reproduces the VM's exact call sequence (verified 24/24)."""
    setup = compute_mode0_setup(img, ds)
    rb_dgroup: Callable[[int], int] = lambda off: img.rb(ds, off)
    calls: List[ColumnCall] = []
    for c in render_classify(rb_dgroup, setup.record_base):
        for ax in dispatch_variant_a(c.e44, c.e46, c.e4e, c.e50, c.e52, c.e54,
                                     c.e56, c.e58, c.e5a):
            calls.append(ColumnCall(ax, c.e44, c.e46, c.e48))
    return calls


def run_34ae(img: NativeGameImage, ds: int, ax: int,
             ship_sprites: Callable[[NativeGameImage, int], None]) -> int:
    """The COMPLETE `1010:34AE` (all 28 blocks of the proven lift, decoded
    2026-07-13 -- see run_status.md), both modes:

    * ``ax == 0`` (composite): src = background `[5170]`, dst = off-screen
      `[0E36]`, dispatch = variant A (`364F`).
    * ``ax != 0`` (PRESENT): src = off-screen `[0E36]`, dst = VGA ``0xA000``,
      dispatch = variant B (`36F3`). This pass is what puts the road band on
      the actual screen -- the stage the earlier "mode-1" entries chased.

    Then ONE of four bodies, keyed on `[0E32]` and ``delta = [0E2A]-[0E6A]``:
    full 44160-byte copy (`[0E32]` != 0, the rebuild/full-present path);
    nothing (``delta == 0``); rows-32..137 copy (``delta >= 8``, unsigned);
    or the 10-row x 4-col x 2-pass column loop (``1 <= delta < 8``).
    Every path ends by calling `39D4` (``ship_sprites``) against the segments
    this mode selected -- EXCEPT the `[003C] == 0` bail-out, which skips it.

    Returns the number of road_column_strip calls made (0 on non-loop paths).
    """
    if img.rw(ds, 0x003C) == 0:                       # bb0->bb24: plain ret
        return 0
    if ax == 0:                                       # bb2: composite mode
        img.ww(ds, 0x0E66, img.rw(ds, 0x5170))
        img.ww(ds, 0x0E68, img.rw(ds, 0x0E36))
        img.ww(ds, 0x0E42, 0x364F)
        variant_b = False
    else:                                             # bb3: present mode
        img.ww(ds, 0x0E66, img.rw(ds, 0x0E36))
        img.ww(ds, 0x0E68, 0xA000)
        img.ww(ds, 0x0E42, 0x36F3)
        variant_b = True
    src = img.rw(ds, 0x0E66)
    dst = img.rw(ds, 0x0E68)

    def _copy(byte_off: int, nbytes: int) -> None:    # bb25/27: rep movsw
        s0 = (src << 4) + byte_off
        d0 = (dst << 4) + byte_off
        img.data[d0:d0 + nbytes] = img.data[s0:s0 + nbytes]

    if img.rw(ds, 0x0E32) != 0:                       # bb4->bb25->bb26: full
        _copy(0x0000, 0x5640 * 2)                     # 44160 B from offset 0
        ship_sprites(img, ds)                         # bb23
        return 0
    delta = (img.rw(ds, 0x0E2A) - img.rw(ds, 0x0E6A)) & 0xFFFF
    if delta == 0:                                    # bb6->bb23: nothing new
        ship_sprites(img, ds)
        return 0
    if delta >= 8:                                    # bb8->bb25: band copy
        _copy(0x2800, 0x4240 * 2)                     # rows 32..137
        ship_sprites(img, ds)
        return 0

    # bb10-22: the column loop (1 <= delta < 8).
    e2a = img.rw(ds, 0x0E2A)
    e6a = img.rw(ds, 0x0E6A)
    e64 = 0x30 if (e2a >> 3) == (e6a >> 3) else 0
    img.ww(ds, 0x0E64, e64)
    seg_prev = img.rw(ds, (BUFFER_SEG_TABLE + 2 * (e6a & 7)) & 0xFFFF)
    seg_cur = img.rw(ds, (BUFFER_SEG_TABLE + 2 * (e2a & 7)) & 0xFFFF)
    img.ww(ds, 0x0E62, seg_prev)
    img.ww(ds, 0x0E60, seg_cur)
    record_base = ((e2a >> 3) * RECORD_STEP + PERSPECTIVE_TABLE_BASE
                   + RECORD_BASE_BIAS) & 0xFFFF
    rb_dgroup: Callable[[int], int] = lambda off: img.rb(ds, off)
    n = 0
    for c in render_classify(rb_dgroup, record_base):
        # The ASM stores every loop/classification field to DGROUP as it goes;
        # persist them so the post-frame DGROUP is byte-identical.
        img.ww(ds, 0x0E44, c.e44); img.ww(ds, 0x0E46, c.e46)
        img.ww(ds, 0x0E48, c.e48); img.ww(ds, 0x0E4C, c.e4c)
        img.ww(ds, 0x0E4E, c.e4e); img.ww(ds, 0x0E50, c.e50)
        img.ww(ds, 0x0E52, c.e52); img.ww(ds, 0x0E54, c.e54)
        img.ww(ds, 0x0E56, c.e56); img.ww(ds, 0x0E58, c.e58)
        img.ww(ds, 0x0E5A, c.e5a); img.ww(ds, 0x0E5C, c.e5c)
        img.ww(ds, 0x0E5E, c.e5e)
        if variant_b:
            codes = dispatch_variant_b(c.e44, c.e46, c.e4e, c.e50, c.e52,
                                       c.e54, c.e56, c.e58, c.e5a, c.e5c, c.e5e)
        else:
            codes = dispatch_variant_a(c.e44, c.e46, c.e4e, c.e50, c.e52,
                                       c.e54, c.e56, c.e58, c.e5a)
        for code in codes:
            road_column_strip(img.rb, img.rw, img.ww, code, ds, c.e44, c.e46,
                              c.e48, e64, seg_prev, seg_cur, src, dst)
            n += 1
    # ASM loop-exit values: [0E48] xors back to 0, [0E46] increments past 4,
    # [0E44]/[0E4C] step once more before the exit compare.
    img.ww(ds, 0x0E48, 0x0)
    img.ww(ds, 0x0E46, 0x5)
    img.ww(ds, 0x0E44, 0x1)
    img.ww(ds, 0x0E4C, (record_base - 10 * RECORD_STEP) & 0xFFFF)
    ship_sprites(img, ds)                             # bb23
    return n


def composite_mode0(img: NativeGameImage, ds: int) -> Tuple[FrameSetup, int]:
    """Run the full mode-0 compositing pass over ``img`` in place: setup ->
    classify -> dispatch -> road_column_strip per column. Byte-exact against
    the VM ONLY when ``img``'s display-list records (at the ``[0E60]``/
    ``[0E62]`` segments) are already populated -- see the module docstring.
    Returns (setup, number of road_column_strip calls made)."""
    setup = compute_mode0_setup(img, ds)
    rb_dgroup: Callable[[int], int] = lambda off: img.rb(ds, off)
    n = 0
    for c in render_classify(rb_dgroup, setup.record_base):
        for ax in dispatch_variant_a(c.e44, c.e46, c.e4e, c.e50, c.e52, c.e54,
                                     c.e56, c.e58, c.e5a):
            road_column_strip(img.rb, img.rw, img.ww, ax, ds, c.e44, c.e46, c.e48,
                              setup.e64, setup.seg_records_prev, setup.seg_records_cur,
                              setup.seg_src, setup.seg_dst)
            n += 1
    return setup, n
