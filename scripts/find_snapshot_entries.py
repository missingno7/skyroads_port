"""Every address a snapshot was CAUGHT at is a re-entry point into its function.

A recorded demo resumes from a snapshot, and a snapshot is taken wherever the
machine happened to be -- which is almost never a function entry. It is usually
whatever the game spends its wall clock in: a tick-wait, a render inner loop,
mid-blit. Skyroads' demos start at 1010:22F8 (the gameplay pacing spin, five of
them), 1010:3199 (inside the lifted 3190), 1010:2301, 1010:434E.

Behind the strict-VMless wall, resuming there is a violation at frame 0. The
containing function IS lifted and DOES contain the address -- there is simply no
hook at it, because the census exports entries and re-entry points, and a
mid-function address is neither. So it reads like a census gap and is not one,
which is what makes it expensive: the answer is not to lift anything, only to
say that this address is reachable from outside.

The evidence is as direct as evidence gets: the snapshot IS the machine at that
address. Not inferred, not observed once -- recorded. So declare each demo's
start CS:IP as a dynamic dispatch entry and the emitter makes it a block leader
with a re-entry hook, sharing the recovered blocks rather than cloning them.

Cheap when it is redundant: an address that is already an entry or a resume
point just resolves to the same block.

Usage:
    python scripts/find_snapshot_entries.py          # -> artifacts/codemap/snapshot_entries.txt
    python scripts/find_snapshot_entries.py --print
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CODE_SEG = 0x1010


def snapshot_entries(demos_dir: Path) -> list[tuple[str, int, int]]:
    """(demo_name, cs, ip) for every demo snapshot, sorted by address."""
    out: list[tuple[str, int, int]] = []
    for state in sorted(demos_dir.glob("*/snapshot/state.json")):
        try:
            cpu = json.loads(state.read_text(encoding="utf-8"))["cpu"]
        except (KeyError, ValueError):
            continue
        out.append((state.parent.parent.name, cpu["cs"] & 0xFFFF,
                    cpu["ip"] & 0xFFFF))
    return sorted(out, key=lambda t: (t[1], t[2]))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--demos", default=str(ROOT / "artifacts" / "demos"))
    ap.add_argument("--out",
                    default=str(ROOT / "artifacts" / "codemap" / "snapshot_entries.txt"))
    ap.add_argument("--seg", default=f"{CODE_SEG:04X}",
                    help="only export entries in this segment")
    ap.add_argument("--print", dest="show", action="store_true")
    args = ap.parse_args(argv)

    seg = int(args.seg, 16)
    found = snapshot_entries(Path(args.demos))
    lines = [
        "# Snapshot re-entry points -- DERIVED by scripts/find_snapshot_entries.py.",
        "# A demo resumes from a snapshot, and a snapshot catches the machine",
        "# wherever it was -- almost never at a function entry, usually in whatever",
        "# loop the game spends its time in. Resuming at a mid-function address",
        "# needs a hook there, or the VMless wall fires at frame 0 inside a function",
        "# that IS lifted. The evidence is the snapshot itself: the machine was",
        "# recorded at that address.",
    ]
    keep: list[str] = []
    for name, cs, ip in found:
        mark = "" if cs == seg else "   (other segment -- skipped)"
        lines.append(f"#   {name}: {cs:04X}:{ip:04X}{mark}")
        key = f"{cs:04X}:{ip:04X}"
        if cs == seg and key not in keep:
            keep.append(key)
    lines += keep
    text = chr(10).join(lines) + chr(10)
    if args.show:
        print(text)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(text)
    print(f"[snapshots] {len(keep)} distinct re-entry addresses from "
          f"{len(found)} snapshots -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
