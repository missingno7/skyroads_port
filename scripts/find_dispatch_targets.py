"""Extract the music driver's dispatch-table targets — a BOUNDED indirect call.

``codemap.py``'s census keeps a call target only if a demo actually EXECUTED it.
That is the right rule (every entry is evidence-backed), but it under-covers a
DATA-DRIVEN dispatch: the music ISR at ``1010:5A55`` reads the next byte of the
song stream, masks it to an index, and calls through a table --

    1010:5A6F  and bx,0007h          ; index = command & 7   <- the BOUND
    1010:5A72  shl bx,1              ; word entries
    1010:5A77  call ds:[bx+0C5B]     ; the table

so which entries run depends on the SONG, and the boot picks a track at random
(skyroads/native/world_load.pick_gameplay_song). A census built from a few demos
gets whichever handlers those songs happened to use, and the strict-VMless wall
then fires on the first unseen command -- one crash at a time, days apart.

The mask makes the set PROVABLE rather than observed: ``and bx,7`` means exactly
eight entries, no more, and the table's bytes are ordinary DGROUP data. So this
enumerates all 8 and hands them to the census as entries. Evidence-backed, not
guessed: the bound comes from the instruction, the targets from the data.

Usage:
    python scripts/find_dispatch_targets.py            # -> artifacts/codemap/dispatch_extra.txt
    python scripts/find_dispatch_targets.py --print
"""
from __future__ import annotations

import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

CODE_SEG = 0x1010
DGROUP_SEG = 0x1686
#: 1010:5A77 `call ds:[bx+0C5B]`, indexed by `and bx,7; shl bx,1` at 5A6F/5A72.
MUSIC_TABLE_OFF = 0x0C5B
MUSIC_TABLE_MASK = 0x0007          # the `and bx,7` bound: 8 word entries


def read_table(image: bytes, *, seg: int, off: int, count: int) -> list[int]:
    base = (seg << 4) + off
    return [image[base + 2 * i] | (image[base + 2 * i + 1] << 8)
            for i in range(count)]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--image",
                    default=str(ROOT / "artifacts" / "boot_image" / "memory_1mb.bin"),
                    help="a booted memory image (the table is DGROUP data)")
    ap.add_argument("--out",
                    default=str(ROOT / "artifacts" / "codemap" / "dispatch_extra.txt"))
    ap.add_argument("--print", dest="show", action="store_true")
    args = ap.parse_args(argv)

    image = Path(args.image).read_bytes()
    n = MUSIC_TABLE_MASK + 1
    targets = read_table(image, seg=DGROUP_SEG, off=MUSIC_TABLE_OFF, count=n)

    lines = [
        "# Indirect dispatch targets -- DERIVED by scripts/find_dispatch_targets.py.",
        "# The music ISR (1010:5A55) calls ds:[bx+0C5B] with `and bx,7` -- so the",
        "# table has EXACTLY 8 entries and the set is provable, not merely observed.",
        "# Needed because which entries a demo exercises depends on the song, and",
        "# the boot picks one at random: a demo-derived census covers only some, and",
        "# the strict-VMless wall then fires on the first unseen music command.",
    ]
    for i, t in enumerate(targets):
        lines.append(f"# table[{i}] (ds:{MUSIC_TABLE_OFF + 2 * i:04X}) -> {CODE_SEG:04X}:{t:04X}")
    seen: list[str] = []
    for t in targets:
        key = f"{CODE_SEG:04X}:{t:04X}"
        if t and key not in seen:
            seen.append(key)
    lines += seen
    text = "\n".join(lines) + "\n"
    if args.show:
        print(text)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(text)
    print(f"[dispatch] {len(seen)} music-table targets -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
