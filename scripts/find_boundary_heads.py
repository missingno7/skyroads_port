"""Derive the tick-wait BOUNDARY HEADS from the recovery IR.

SkyRoads paces itself off ``ds:[1600]``, the counter its INT 08h ISR bumps. A
frame's timer IRQs are delivered only at frame start, so ``[1600]`` is
architecturally constant for the whole frame: any loop that waits for it to
change cannot exit, and the frame is over the moment the game reaches one.
``irgen --boundary-heads`` turns each head into an emitted observer +
``RESUME_ENTRIES`` so the lifted body parks and resumes instead of spinning.

Why derive instead of hand-list: ``skyroads/pacing.py`` names three heads
(22F8 / 434A / 47CD) and they are correct -- but that list was written for the
INTERPRETED runtime, where an *unlisted* wait merely burned the step budget and
the frame ended anyway. Lifted, an unlisted wait is an infinite Python loop:
the corpus has no way to advance time inside a lifted function. So the lifted
runner needs the COMPLETE set, and completeness is not something to eyeball --
`1010:4468` (a `delay(ticks)` helper: zero [1600], spin until it reaches the
argument) was missed by hand and only surfaced as a MAX_ITERATIONS crash five
screens into the boot.

The rule: an instruction that COMPARES ``ds:[1600]`` and is followed, inside
the same function, by a branch back to at or before itself. That is a spin on
the tick counter by construction. Writes to [1600] (loop resets) and reads
outside a loop are not heads.

Usage:
    python scripts/find_boundary_heads.py            # -> artifacts/codemap/boundary_heads.txt
    python scripts/find_boundary_heads.py --print
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

#: ds:[1600] — the tick counter (skyroads/pacing.py's TICK_ADDR).
TICK_ADDR = 0x1600
#: Heads named by pacing.py as verified recovery facts. Kept as a floor: they
#: were each proven byte-equivalent to burning the full budget, and a couple
#: read the tick in shapes the generic rule below does not model.
KNOWN_HEADS = (0x22F8, 0x434A, 0x47CD)


def spin_heads(ir: dict) -> dict[int, str]:
    """``head_ip -> evidence`` for every tick-compare inside a backward loop."""
    out: dict[int, str] = {}
    for key, fn in ir["functions"].items():
        insts = {int(i["ip"], 16): i
                 for b in fn["blocks"] for i in b["instructions"]}
        for ip, inst in sorted(insts.items()):
            raw = bytes.fromhex(inst["bytes"])
            if not inst["mnemonic"].startswith("cmp"):
                continue
            # absolute [1600] operand (modrm mod=00 rm=110 + disp16)
            if TICK_ADDR.to_bytes(2, "little") not in raw:
                continue
            loops_back = any(
                j.get("target") and j["kind"] in ("jcc", "jmp")
                and int(j["ip"], 16) > ip and int(j["target"], 16) <= ip
                for j in insts.values())
            if loops_back:
                out[ip] = f"cmp ds:[{TICK_ADDR:04X}] in a backward loop (fn {key})"
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ir", default=str(ROOT / "artifacts" / "codemap" / "recovery_ir.json"))
    ap.add_argument("--out", default=str(ROOT / "artifacts" / "codemap" / "boundary_heads.txt"))
    ap.add_argument("--seg", default="1010")
    ap.add_argument("--print", dest="show", action="store_true")
    args = ap.parse_args(argv)

    ir = json.loads(Path(args.ir).read_text())
    found = spin_heads(ir)
    seg = int(args.seg, 16)
    heads = dict.fromkeys(sorted(set(found) | set(KNOWN_HEADS)))
    lines = [
        "# Tick-wait boundary heads -- DERIVED by scripts/find_boundary_heads.py",
        "# from the recovery IR (a cmp of ds:[1600] inside a backward loop),",
        "# unioned with skyroads/pacing.py's verified KNOWN_HEADS.",
        "# Regenerate after any census change: an unlisted wait is an infinite",
        "# loop in the lifted corpus, not merely a wasted step budget.",
    ]
    for ip in heads:
        why = found.get(ip, "skyroads/pacing.py verified head")
        lines.append(f"# {seg:04X}:{ip:04X}  {why}")
    lines += [f"{seg:04X}:{ip:04X}" for ip in heads]
    text = "\n".join(lines) + "\n"
    if args.show:
        print(text)
    Path(args.out).write_text(text)
    print(f"[heads] {len(heads)} boundary heads -> {args.out} "
          f"({len(found)} derived, {len(set(KNOWN_HEADS) - set(found))} from pacing.py only)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
