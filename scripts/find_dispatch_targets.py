"""Recover the dispatch-table targets a census structurally cannot see.

``codemap.py`` keeps a call target only if a demo EXECUTED it and only if it can
SEE the call. Both rules are right, and together they are blind to every target
reached through a table: nothing in the instruction stream calls those
addresses, so the census is never told they exist. Behind the strict-VMless wall
that surfaces as a violation in a function nobody lifted because nobody knew it
was a function -- one crash at a time, days apart, each looking like a census
bug rather than the one missing fact it is.

Two shapes, one standard: take the bound from the PROGRAM, never from what a
demo happened to do. Observation is exactly what under-covers a dispatch --
which entries run depends on data (which song, which level, which video mode),
so a demo-derived set always misses the branch nobody took yet.

1. BOUNDED INDEXED DISPATCH  (``indexed_tables``)

       and bx,MASK        ; the BOUND -- bx can be nothing else
       shl bx,1           ; word entries
       call [bx+TABLE]    ; the dispatch

   The mask PROVES the entry count, so enumerating TABLE covers every branch the
   program can ever take. Skyroads has two: the music driver (5A6F/5A77, `and
   bx,7` -> 8 handlers, and the boot picks a track at random) and the per-object
   renderer (2DCF/2DD4, `and bx,0Fh` -> 16). The entries are ordinary DGROUP
   data, so they are read from images -- and from EVERY image available, boot
   plus every demo snapshot, because what a table holds depends on what the
   machine was doing when it was caught. The renderer's table is empty in a menu
   snapshot and full in a gameplay one.

2. TABLES BUILT AT RUNTIME  (``code_pointer_stores``)

       1010:2CD3  mov word [0E38],34A7   \  variant A
       1010:2CD9  mov word [0E3A],3153    |  (the column-draw routines)
       1010:2CDF  mov word [0E3C],3190    |
       1010:2CE5  mov word [0E3E],325B   /
       1010:2CEB  cmp word [003C],1      ; a mode flag picks...
       1010:2CF2  mov word [0E38],347E   \  ...variant B, over the top
       ...                               /

   Here an image is no good: it only ever shows the variant that boot chose. So
   read the fill instead -- but the VALUE proves nothing on its own. "An
   immediate that is an address some demo executed" also describes `mov word
   [AF2C],0` (1010:0000 is executed -- it is the entry) and every 1 and 2 in the
   program; that test alone returned 81 "targets", mostly integers. And it fails
   in the direction that costs most: it drops variant B, which no demo runs.

   The SHAPE is the proof. A table fill is a straight-line run of word stores
   over consecutive slots; if such a run covers a slot the program reaches with
   ``call [imm16]``, the run is a call table and every value in it is a code
   pointer -- executed or not. A run with no called slot (ds:0E44's
   000B/0001/0000) is data.

Usage:
    python scripts/find_dispatch_targets.py            # -> artifacts/codemap/dispatch_extra.txt
    python scripts/find_dispatch_targets.py --print
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

CODE_SEG = 0x1010
DGROUP_SEG = 0x1686

#: `call [bx+disp16]` -- FF /2 mod=10 rm=111: FF 97 lo hi (+ optional seg prefix).
_CALL_BX_DISP16 = "ff97"
#: `and bx,imm8sx` (83 E3 ii) and `and bx,imm16` (81 E3 lo hi) -- the BOUND.
_AND_BX_IMM8 = "83e3"
_AND_BX_IMM16 = "81e3"


#: `mov word ptr [imm16], imm16` -- opcode C7 /0 with a disp16 (mod=00, rm=110).
#: Six bytes: C7 06 off_lo off_hi imm_lo imm_hi.
_MOV_MEM_IMM16 = "c706"
#: `call [imm16]` -- opcode FF /2 with a disp16 (mod=00, rm=110): FF 16 lo hi.
#: May carry a segment-override prefix (26/2E/36/3E), which we skip past.
_CALL_MEM16 = "ff16"
_SEG_PREFIXES = ("26", "2e", "36", "3e")


def _strip_seg_prefix(b: str) -> str:
    return b[2:] if b[:2] in _SEG_PREFIXES else b


def called_slots(ir: dict) -> set[int]:
    """Table slots the program actually CALLS THROUGH (`call [imm16]`).

    This is the anchor that makes the rest evidence and not pattern-matching:
    an address stored into a word nobody ever calls is just data.
    """
    out: set[int] = set()
    for rec in ir["functions"].values():
        for blk in rec["blocks"]:
            for inst in blk["instructions"]:
                if inst["kind"] != "call_ind":
                    continue
                b = _strip_seg_prefix(inst["bytes"])
                if b.startswith(_CALL_MEM16) and len(b) == 8:
                    raw = bytes.fromhex(b)
                    out.add(raw[2] | (raw[3] << 8))
    return out


def read_table(image: bytes, *, seg: int, off: int, count: int) -> list[int]:
    base = (seg << 4) + off
    return [image[base + 2 * i] | (image[base + 2 * i + 1] << 8)
            for i in range(count)]


def indexed_tables(ir: dict) -> list[tuple]:
    """Bounded indexed dispatches: (call_ip, table_off, entry_count).

    The shape, and the reason the count is a PROOF rather than a guess:

        and bx,MASK       ; the bound -- bx can be nothing else
        shl bx,1          ; word entries
        call [bx+TABLE]   ; the dispatch

    ``and bx,MASK`` means exactly MASK+1 entries, no more, so enumerating the
    table covers every branch the program can ever take -- including the ones
    no demo has taken. That is the whole point: which entries run depends on
    data (which song, which level), and a census built from observation gets
    only the ones those runs happened to need. The wall then fires on the first
    unseen one, one crash at a time, days apart.

    Both of skyroads' indexed dispatches have it: the music driver
    (`and bx,7; call ds:[bx+0C5B]` at 5A6F/5A77 -- 8 handlers) and the
    per-object renderer (`and bx,0Fh; call ss:[bx+0BAF]` at 2DCF/2DD4 -- 16).
    """
    out: list[tuple] = []
    for rec in ir["functions"].values():
        for blk in rec["blocks"]:
            mask = None
            for inst in blk["instructions"]:
                b = _strip_seg_prefix(inst["bytes"])
                if b.startswith(_AND_BX_IMM8) and len(b) == 6:
                    mask = int(b[4:6], 16)
                elif b.startswith(_AND_BX_IMM16) and len(b) == 8:
                    raw = bytes.fromhex(b)
                    mask = raw[2] | (raw[3] << 8)
                elif b.startswith(_CALL_BX_DISP16) and len(b) == 8:
                    if mask is None:
                        continue          # unbounded: cannot enumerate, skip
                    raw = bytes.fromhex(b)
                    off = raw[2] | (raw[3] << 8)
                    out.append((int(inst["ip"], 16), off, mask + 1))
    return sorted(set(out), key=lambda t: t[1])


def _table_fills(ir: dict) -> list[list[tuple]]:
    """Straight-line runs of `mov word [imm16], imm16` over CONSECUTIVE slots.

    A table fill is a shape, and this is the shape: consecutive word stores to
    consecutive offsets, in one basic block, with nothing in between. Grouping
    by it is what separates a table from a scatter of unrelated initialisers.
    """
    runs: list[list[tuple]] = []
    for rec in ir["functions"].values():
        for blk in rec["blocks"]:
            run: list[tuple] = []
            for inst in blk["instructions"]:
                b = inst["bytes"]
                if len(b) == 12 and b.startswith(_MOV_MEM_IMM16):
                    raw = bytes.fromhex(b)
                    off = raw[2] | (raw[3] << 8)
                    val = raw[4] | (raw[5] << 8)
                    if run and off != run[-1][1] + 2:
                        runs.append(run)
                        run = []
                    run.append((int(inst["ip"], 16), off, val))
                elif run:
                    runs.append(run)
                    run = []
            if run:
                runs.append(run)
    return runs


def code_pointer_stores(ir: dict) -> list[tuple]:
    """Code addresses stored into a table the program CALLS THROUGH.

    Returns (store_ip, table_off, target) sorted by slot then target.

    THE CALL SITE IS THE EVIDENCE -- not the value. "An immediate that happens
    to be an executed address" proves nothing: 1010:0000-0005 are executed (they
    are the entry), so `mov word [AF2C],0` matches, and so does every 1 and 2 in
    the program. That test alone yielded 81 "targets", mostly integers.

    Worse, it is wrong in the direction that costs the most. Requiring the value
    to have EXECUTED drops the variant a demo never took: the render dispatch is
    filled twice, variant A at 2CD3 and variant B at 2CF2, chosen by the mode
    flag at ds:003C -- and no demo runs B, so the executed test silently drops
    exactly the four routines the wall fires on the day someone plays that mode.
    Under-covering an indirect dispatch from observation is the very failure the
    music table above exists to avoid; the answer there was a proof (`and bx,7`),
    and it is a proof here too.

    So: a run of word stores over consecutive slots (``_table_fills``) that
    includes a slot reached by ``call [imm16]`` IS a call table, and every value
    it stores IS a code pointer -- whether or not any demo has been there yet.
    A run with no called slot in it (ds:0E44's 000B/0001/0000) is just data.
    """
    anchors = called_slots(ir)
    out: list[tuple] = []
    for run in _table_fills(ir):
        if any(off in anchors for _ip, off, _v in run):
            out += run
    return sorted(out, key=lambda t: (t[1], t[2]))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--image", action="append", dest="images", default=None,
                    help="booted memory images to read the tables out of "
                         "(repeatable; defaults to the boot image + every demo "
                         "snapshot, because a table's contents depend on what "
                         "the machine was DOING)")
    ap.add_argument("--ir",
                    default=str(ROOT / "artifacts" / "codemap" / "recovery_ir.json"),
                    help="recovery IR -- both derivations read the program from it")
    ap.add_argument("--out",
                    default=str(ROOT / "artifacts" / "codemap" / "dispatch_extra.txt"))
    ap.add_argument("--print", dest="show", action="store_true")
    args = ap.parse_args(argv)

    images = args.images
    if images is None:
        images = [str(ROOT / "artifacts" / "boot_image" / "memory_1mb.bin")]
        images += [str(p) for p in sorted(
            (ROOT / "artifacts" / "demos").glob("*/snapshot/memory_1mb.bin"))]
    images = [p for p in images if Path(p).exists()]

    ir = json.loads(Path(args.ir).read_text(encoding="utf-8"))
    tables = indexed_tables(ir)
    stores = code_pointer_stores(ir)

    lines = [
        "# Indirect dispatch targets -- DERIVED by scripts/find_dispatch_targets.py.",
        "# Nothing here is hand-listed. Every address is one the PROGRAM proves is a",
        "# function pointer, reached through a table the census cannot see: it keeps",
        "# a call target only if it can SEE the call, and an indirect call through a",
        "# table shows it nothing.",
        "#",
        "# 1. BOUNDED INDEXED DISPATCH -- `and bx,MASK; shl bx,1; call [bx+TABLE]`.",
        "#    The mask is a PROOF of the entry count, so enumerating the table covers",
        "#    every branch the program can take, including the ones no demo took.",
        "#    That matters: which entries run depends on data (which song, which",
        "#    level), so a census built from observation gets only the ones those",
        "#    runs needed, and the wall fires on the first unseen one -- one crash at",
        "#    a time, days apart. Tables are read from every image below, because",
        "#    their contents depend on what the machine was doing when it was caught.",
    ]
    for img in images:
        lines.append(f"#      image: {Path(img).parent.parent.name}/{Path(img).name}")

    targets: list[int] = []
    for call_ip, off, count in tables:
        lines.append(f"#    {CODE_SEG:04X}:{call_ip:04X} calls [bx+{off:04X}], "
                     f"{count} entries:")
        for img in images:
            vals = read_table(Path(img).read_bytes(), seg=DGROUP_SEG,
                              off=off, count=count)
            for i, t in enumerate(vals):
                if t and t not in targets:
                    targets.append(t)
                    lines.append(f"#      [{i:2d}] (ds:{off + 2 * i:04X}) -> "
                                 f"{CODE_SEG:04X}:{t:04X}  ({Path(img).parent.parent.name})")

    lines += [
        "#",
        "# 2. TABLES BUILT AT RUNTIME -- a straight-line run of `mov word [imm16],",
        "#    imm16` over consecutive slots, covering a slot that `call [imm16]`",
        "#    reaches. The run IS the table and every value in it IS a code pointer.",
        "#    The render dispatch at ds:0E38 is filled this way, twice, and which",
        "#    fill wins is a mode flag (ds:003C) -- so reading it from an image can",
        "#    only ever show one variant. Taken from the instructions instead, both",
        "#    are covered.",
    ]
    for ip, off, t in stores:
        lines.append(f"#    {CODE_SEG:04X}:{ip:04X} stores ds:{off:04X} <- "
                     f"{CODE_SEG:04X}:{t:04X}")

    seen: list[str] = []
    for t in targets + [t for _ip, _off, t in stores]:
        key = f"{CODE_SEG:04X}:{t:04X}"
        if t and key not in seen:
            seen.append(key)
    lines += seen
    text = chr(10).join(lines) + chr(10)
    if args.show:
        print(text)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(text)
    print(f"[dispatch] {len(seen)} targets -> {args.out} "
          f"({len(tables)} indexed table(s) over {len(images)} image(s) -> "
          f"{len(targets)}; {len(stores)} runtime-stored)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
