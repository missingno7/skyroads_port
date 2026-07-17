"""Per-SITE dynamic-dispatch evidence -- what each near-indirect call can reach.

``find_dispatch_targets`` proves the flat SET of reachable dispatch targets (for
the census, so the VMless wall covers them). CPUless promotion needs one thing
more: which SITE reaches which targets, so the fixpoint can decide -- per
dispatcher -- whether all its targets are themselves promoted. A dispatcher
whose target is kept lifted (a snapshot resume point, say) must stay lifted too,
or its recovered ``_dyn`` call resolves into an empty registry slot at runtime
(the UnknownDispatchTarget frontier witness).

This derives the site->targets map from the SAME two structural proofs as
find_dispatch_targets, so the evidence is exactly as trustworthy:

  1. BOUNDED INDEXED DISPATCH  `and bx,MASK; call [bx+TABLE]`
       site   = the call_ip
       targets = the TABLE entries (read from the boot image + every snapshot,
                 because a table's contents depend on what the machine was doing)

  2. RUNTIME-BUILT TABLE  `call [imm16]` through a slot filled by a straight-line
     run of `mov word [imm16], imm16`
       site   = the call_ip (its disp16 IS the slot)
       targets = the code pointers stored into that slot (from the IR -- covers
                 every fill variant, incl. the mode a demo never took), plus any
                 runtime value caught in an image at that slot

Emits ``artifacts/codemap/dispatch_evidence.json`` in cpuless_promote's
``--dyn-evidence`` schema: ``{"sites": [{"site": "CS:IP", "targets":
{"CS:IP": <provenance>, ...}}]}``.

Usage:
    python scripts/find_dispatch_evidence.py            # -> dispatch_evidence.json
    python scripts/find_dispatch_evidence.py --print
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from find_dispatch_targets import (  # noqa: E402
    CODE_SEG, DGROUP_SEG, _CALL_MEM16, _SEG_PREFIXES, _strip_seg_prefix,
    code_pointer_stores, indexed_tables, read_table)


def call_imm16_sites(ir: dict) -> list[tuple[int, int]]:
    """`call [imm16]` sites -- (call_ip, slot_off). The disp16 is the table
    slot the runtime-built dispatch reads its target pointer from."""
    out: list[tuple[int, int]] = []
    for rec in ir["functions"].values():
        for blk in rec["blocks"]:
            for inst in blk["instructions"]:
                if inst["kind"] != "call_ind":
                    continue
                b = _strip_seg_prefix(inst["bytes"])
                if not b.startswith(_CALL_MEM16):
                    continue
                raw = bytes.fromhex(b)
                out.append((int(inst["ip"], 16), raw[2] | (raw[3] << 8)))
    return sorted(set(out))


def build(ir: dict, images: list[Path]) -> dict:
    sites: dict[int, dict[str, str]] = {}   # call_ip -> {target_key: provenance}
    #: a dispatch target is ALWAYS a function entry (it is called). So an image
    #: read of a slot is trustworthy only when it lands on one -- that filter is
    #: what makes reading a `call [imm16]` slot safe (a menu snapshot where the
    #: slot is still data reads garbage like A72A, which is not an entry).
    fn_entries = {int(k.split(":")[1], 16) for k in ir["functions"]}

    def add(call_ip: int, target: int, why: str, *, verified_entry: bool) -> None:
        if not target:
            return
        if verified_entry and target not in fn_entries:
            return
        sites.setdefault(call_ip, {})[f"{CODE_SEG:04X}:{target:04X}"] = why

    # 1. bounded indexed dispatch -- the mask PROVES the entry count, so every
    #    table slot read from an image is a real branch (verified as an entry).
    for call_ip, off, count in indexed_tables(ir):
        for img in images:
            for i, t in enumerate(read_table(img.read_bytes(), seg=DGROUP_SEG,
                                             off=off, count=count)):
                add(call_ip, t, f"table {DGROUP_SEG:04X}:{off:04X}[{i}] "
                                f"({img.parent.parent.name})", verified_entry=True)

    # 2. runtime-built table -- slot -> its target(s). The IR store SHAPE proves
    #    a stored value IS a code pointer (find_dispatch_targets' rationale), so
    #    those need no entry filter; an image read of the slot fills in variants
    #    the stores miss (e.g. 0E40, filled at runtime) but only when it lands on
    #    a real function entry -- else it is snapshot garbage.
    stores_by_slot: dict[int, set[int]] = {}
    for _ip, off, t in code_pointer_stores(ir):
        stores_by_slot.setdefault(off, set()).add(t)
    for call_ip, slot in call_imm16_sites(ir):
        for t in sorted(stores_by_slot.get(slot, ())):
            add(call_ip, t, f"stored -> {DGROUP_SEG:04X}:{slot:04X}",
                verified_entry=False)
        for img in images:
            (t,) = read_table(img.read_bytes(), seg=DGROUP_SEG, off=slot, count=1)
            add(call_ip, t, f"live {DGROUP_SEG:04X}:{slot:04X} "
                            f"({img.parent.parent.name})", verified_entry=True)

    return {
        "_notice": "GENERATED by scripts/find_dispatch_evidence.py -- per-site "
                   "dynamic-dispatch targets for cpuless_promote --dyn-evidence. "
                   "Derived from the recovery IR + booted images; do not hand-edit.",
        "sites": [{"site": f"{CODE_SEG:04X}:{ip:04X}", "targets": tgts}
                  for ip, tgts in sorted(sites.items())],
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ir",
                    default=str(ROOT / "artifacts" / "codemap" / "recovery_ir.json"))
    ap.add_argument("--image", action="append", dest="images", default=None,
                    help="booted images to read tables from (repeatable; "
                         "defaults to the boot image + every demo snapshot)")
    ap.add_argument("--out",
                    default=str(ROOT / "artifacts" / "codemap" / "dispatch_evidence.json"))
    ap.add_argument("--print", dest="show", action="store_true")
    args = ap.parse_args(argv)

    images = args.images
    if images is None:
        images = [ROOT / "artifacts" / "boot_image" / "memory_1mb.bin"]
        images += sorted((ROOT / "artifacts" / "demos").glob("*/snapshot/memory_1mb.bin"))
    images = [Path(p) for p in images if Path(p).exists()]

    ir = json.loads(Path(args.ir).read_text(encoding="utf-8"))
    doc = build(ir, images)
    text = json.dumps(doc, indent=2) + "\n"
    if args.show:
        print(text)
    Path(args.out).write_text(text, encoding="utf-8")
    n_t = sum(len(s["targets"]) for s in doc["sites"])
    print(f"[dispatch-evidence] {len(doc['sites'])} sites, {n_t} site-target "
          f"edges over {len(images)} image(s) -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
