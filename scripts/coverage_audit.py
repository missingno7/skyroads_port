"""coverage_audit.py -- find the coverage gaps BEFORE a player falls into one.

A fail-loud stop in the recovered corpus is almost always a coverage bug: some
function the replays never executed, whose exits were therefore stubbed. We found
the last two the expensive way -- a player hit ``1010:2F57`` in live play, and
the diagnosis took a session. Both were sitting in plain sight the whole time.

The trick that found them generalises, and it is what this script does: a
DISPATCH TABLE is data in the game image, so its entries can simply be READ.
Every entry is a function the game can reach; any entry the census never
executed is a gap, and it is knowable now rather than after a crash.

    1010:2DD4 dispatches `call [bx+0x0BAF]` with bx = (cell & 0x0F) * 2, so the
    table at 1686:0BAF is six block-type handlers plus a no-op default:
      [0]=2E6C [1]=3059 [2]=2EBB [3]=2EFD [4]=2F58 [5]=2FCC [6..]=3AC9
    Slots 3 and 5 never appeared in any replay -> 2EFD/2FCC unobserved -> their
    `ret`s stubbed fail-loud -> the crash.

For the block table it goes further and says WHICH LEVEL to drive: ROADS.LZS
decodes to a UINT16LE road array, and the dispatch index is a cell's high-byte
low nibble, so the levels carrying an uncovered block type can be listed
directly (that is how level 14 and level 8 were chosen).

This reports; it never invents coverage. Closing a gap still means executing the
code -- see docs/cpuless_standalone.md for the replay-synthesis recipe.

Usage:
    python scripts/coverage_audit.py
"""
from __future__ import annotations

import json
import struct
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "dos_re"))

CODEMAP = ROOT / "artifacts" / "codemap"
BOOT_IMG = ROOT / "artifacts" / "boot_image" / "memory_1mb.bin"
GAME_SEG = 0x1010

#: Known dispatch TABLES in the data segment: (name, seg, off, entries, note).
#: Each was located from the dispatch site's addressing mode -- see the module
#: docstring for how 1686:0BAF was read off `call [bx+0x0BAF]`.
TABLES = (
    ("block-type handlers", 0x1686, 0x0BAF, 12,
     "1010:2DD4, index = (road cell >> 8) & 0x0F"),
)


def _observed() -> set[str]:
    return set(json.loads((CODEMAP / "observed.json").read_text())["executed"])


def _ir_functions() -> set[str]:
    return set(json.loads((CODEMAP / "recovery_ir.json").read_text())["functions"])


def audit_tables(img: bytes, observed: set[str], ir: set[str]) -> list[tuple]:
    """Read each dispatch table out of the image; report unobserved entries."""
    gaps = []
    for name, seg, off, count, note in TABLES:
        base = seg * 16 + off
        print(f"\n[audit] {name} @ {seg:04X}:{off:04X} -- {note}")
        seen_default = None
        for i in range(count):
            target = int.from_bytes(img[base + i * 2: base + i * 2 + 2], "little")
            key = f"{GAME_SEG:04X}:{target:04X}"
            is_fn = key in ir
            obs = key in observed
            # slots past the real handlers repeat one no-op default; name it once
            if seen_default == target:
                continue
            tag = "observed" if obs else "*** NEVER OBSERVED ***"
            print(f"    [{i:2d}] {key} {'' if is_fn else '(not an IR function) '}{tag}")
            if not obs and is_fn:
                gaps.append((name, i, key))
            if i >= 5:
                seen_default = target
    return gaps


def levels_carrying(slot: int) -> list[tuple[int, int]]:
    """Levels whose road contains cells selecting ``slot`` -- where to drive."""
    from skyroads.native.level_load import decode_level_files, read_game_file
    from skyroads.handrecovered import roads_archive
    roads = read_game_file(ROOT / "assets", "ROADS.LZS")
    out = []
    for lv in range(roads_archive.level_count(roads)):
        d = decode_level_files(lv, game_root=ROOT / "assets")
        cells = struct.unpack("<%dH" % (len(d.road) // 2),
                              d.road[:len(d.road) // 2 * 2])
        n = Counter((c >> 8) & 0x0F for c in cells).get(slot, 0)
        if n:
            out.append((lv, n))
    out.sort(key=lambda t: -t[1])
    return out


def main() -> int:
    if not BOOT_IMG.exists():
        print("[audit] no boot image -- build it first (scripts/build_boot_image.py)")
        return 2
    img = BOOT_IMG.read_bytes()
    observed, ir = _observed(), _ir_functions()
    print(f"[audit] census: {len(observed)} executed addresses; IR: {len(ir)} functions")

    gaps = audit_tables(img, observed, ir)
    if not gaps:
        print("\n[audit] no dispatch-table gaps: every table entry has been executed.")
        return 0

    print(f"\n[audit] {len(gaps)} UNOBSERVED dispatch target(s) -- each is a "
          f"fail-loud stop waiting to happen:")
    for name, slot, key in gaps:
        print(f"\n  {key}  ({name} slot {slot})")
        try:
            carriers = levels_carrying(slot)
        except Exception as e:                      # noqa: BLE001 -- assets optional
            print(f"      (cannot scan levels: {type(e).__name__}: {e})")
            continue
        if carriers:
            top = ", ".join(f"L{lv} x{n}" for lv, n in carriers[:6])
            print(f"      drive one of these levels to cover it: {top}")
        else:
            print("      no level carries this cell -- reachable another way")
    print("\n[audit] to close: synthesize a replay that plays such a level, add it "
          "to DEFAULT_REPLAYS + BOUNDARY_REPLAYS in scripts/build_codemap.py, then "
          "`python scripts/rebuild_all.py`.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
