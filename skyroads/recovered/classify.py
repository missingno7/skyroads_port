"""SkyRoads per-frame perspective classification — `1010:2324-23BF`.

Runs at the top of the gameplay body, right after the state dispatch. It
projects the ship's OWN `(lateral, af1c)` through the perspective transform
(`04C0`, `renderer.perspective_row_offset`) to a table word, then derives the
three per-frame classification flags the rest of the frame gates on:

* `class_zero` (`ss:[bp-18]`) = the perspective word is 0 (ship off the
  projected row window);
* `class_skip` (`ss:[bp-14]`) = the reduced word's low nibble is 8;
* `bp-16` = the reduced word's low nibble is 2 (0 in all observed gameplay —
  the flag `physics.compute_movement_targets`'s `af1c_base_offset` selector
  reads; kept for faithfulness).

`class_skip`/`class_zero` are exactly the inputs
`dynamics.step_jump_steer_gravity` needs. Verified 682/682 against the real
ASM over the full E2E demo (computing the perspective word natively via
`perspective_row_offset` + a DGROUP read, and the table lookup via the same
reader).

## Two documented subtleties

1. `class_skip` (`bp-14`) is only RECOMPUTED when `bp12 != 0`; when `bp12 == 0`
   the ASM leaves `ss:[bp-14]` untouched (`1010:2354 -> 23C5` skips the write),
   so it PERSISTS from the previous frame. It is therefore session state, not a
   pure per-frame function — the caller must pass the prior value
   (`class_skip_prev`). `bp12` itself is the gameplay-active latch set at
   `1010:206C`/`2901` and cleared at `28D7` (the tail state machine — a
   separate island).
2. In the `bp12 != 0` path the ASM makes a side-effect call to `1010:1B49`
   (the same address recovered as `menu.dispatch_menu_action`) with the reduced
   word as its argument (`1010:2385-238B`), BEFORE reading the nibble for the
   flags. The flags do NOT depend on that call's result (they read the local,
   not the return), so this function reproduces them without it — but whatever
   DGROUP side effect that call has DURING gameplay is NOT modelled here (see
   the module TODO / run_status.md). It is flagged so a native frame stepper
   knows to treat it as an unresolved effect, not silently drop it.
"""
from __future__ import annotations

from typing import Callable, NamedTuple

from skyroads.islands import oracle_link

#: ds-relative base of the per-segment class table indexed by (persp_word>>8)
#: (word entries; 1010:236A `ds:[bx+0x228]`).
SEG_CLASS_TABLE = 0x228
#: Height gate above which the reduced-word path runs (1010:235D `af2c > 0x2800`).
CLASS_HEIGHT_GATE = 0x2800


class ClassifyResult(NamedTuple):
    class_skip: int   # bp-14  (steering-skip / "on a class-8 cell")
    bp16: int         # bp-16  (0 in all observed gameplay)
    class_zero: int   # bp-18  (perspective word == 0)
    reduced_word: int  # the post-reduction bp-20 (for chaining/debug)
    calls_1b49: bool  # whether the ASM's 1010:1B49 side-effect call fired --
    #                   its DGROUP effect during gameplay is NOT modelled here.


@oracle_link(
    boundary="1010:2324",
    contract="classify_perspective(persp_word, af2c, bp12, class_skip_prev, "
             "read_seg_table): perspective classification. class_zero = "
             "(persp_word == 0). If bp12 == 0: bp16 = 0, class_skip = "
             "class_skip_prev (UNCHANGED, persists), no 1B49 call. Else: reduce "
             "the word -- if af2c > 0x2800, look up read_seg_table(persp_word>>8) "
             "and set word = (persp_word>>4) if af2c==that else 0; otherwise "
             "leave it; a side-effect 1B49(word) call fires; then class_skip = "
             "(word & 0xF == 8), bp16 = (word & 0xF == 2). Returns the flags "
             "plus the reduced word and whether 1B49 was called.",
    status="ASM_MATCHED",  # 682/682 real E2E-demo frames byte-exact on
    # (class_skip, bp16, class_zero), computing persp_word natively via
    # renderer.perspective_row_offset + a DGROUP read and the table lookup via
    # the same reader. See tests/test_classify.py + run_status.md.
    merge_target="skyroads.native.classify (future)",
)
def classify_perspective(
    persp_word: int, af2c: int, bp12: int, class_skip_prev: int,
    read_seg_table: Callable[[int], int],
) -> ClassifyResult:
    class_zero = 1 if (persp_word & 0xFFFF) == 0 else 0

    if bp12 == 0:  # 1010:2354 -> 23C5: bp-14 untouched, bp-16 := 0, no 1B49
        return ClassifyResult(class_skip_prev, 0, class_zero, persp_word & 0xFFFF, False)

    word = persp_word & 0xFFFF
    if (af2c & 0xFFFF) > CLASS_HEIGHT_GATE:          # 1010:235D `ja`
        tbl = read_seg_table((word >> 8) & 0xFFFF) & 0xFFFF
        word = (word >> 4) if (af2c & 0xFFFF) == tbl else 0
    # 1010:2385-238B: push word; call 1B49; add sp,2  (side effect, not modelled)
    class_skip = 1 if (word & 0xF) == 8 else 0
    bp16 = 1 if (word & 0xF) == 2 else 0
    return ClassifyResult(class_skip, bp16, class_zero, word, True)
