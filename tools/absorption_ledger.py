"""absorption_ledger.py -- who owns each recovered address, and on what evidence.

The port carries four layers that grew at different times and do not yet meet:

    skyroads/lifted/functions/   generated, VM-aware      (186 modules)
    skyroads/recovered/          generated, CPU-free      (180 modules)
    skyroads/handrecovered/      hand-written islands     (42, address-keyed)
    skyroads/native/            hand-written subsystems  (21, NOT address-keyed)

The generated layers are the complete, verified, regenerable program. The
hand-written layers hold the semantic understanding -- names, algorithms,
subsystem boundaries -- that the generated program does not have. Converging
them starts with knowing, per address, WHO OWNS IT and WHAT PROVES IT, and that
is what this reports.

It only reports. Absorption is a separate, gated act: an island may become the
running implementation solely through the authoritative-override seam, which
keeps the generated body as a differential reference. Nothing here changes what
runs.

The three columns that matter:

* **IR**        -- is the address a recovered function at all?
* **status**    -- the island's own evidence level (dos_re.islands ladder:
                   GUESS < OBSERVED < RECOVERED < ASM_MATCHED < VERIFIED <
                   CANONICAL). Only VERIFIED/CANONICAL are candidates to RUN;
                   the rest are documentation until re-proven.
* **observed**  -- did the census actually execute it? An island for an address
                   the program never reaches is knowledge, not code.

Usage:
    python tools/absorption_ledger.py            # summary
    python tools/absorption_ledger.py --full     # every island
    python tools/absorption_ledger.py --orphans  # islands with no IR function
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "dos_re"))

CODEMAP = ROOT / "artifacts" / "codemap"
ISLAND_PACKAGES = ("skyroads.handrecovered",)
#: statuses whose evidence is strong enough to even CONSIDER running the body
RUNNABLE_STATUS = {"VERIFIED", "CANONICAL"}


def _load():
    from dos_re.islands import collect_islands
    ir = json.loads((CODEMAP / "recovery_ir.json").read_text())["functions"]
    observed = set(json.loads((CODEMAP / "observed.json").read_text())["executed"])
    manifest = {}
    mpath = CODEMAP / "cpuless_manifest.json"
    if mpath.exists():
        manifest = json.loads(mpath.read_text())
    islands = collect_islands(list(ISLAND_PACKAGES))
    return islands, set(ir), observed, manifest


def _norm(addr: str) -> str:
    return addr.strip().upper()


#: native/ modules that hold FLOW rather than a routine -- the wrong stitches.
#: The skeleton supersedes these; anything depending on one is sewn to flow that
#: was inferred from the screen instead of lifted from the program.
FLOW_MODULES = {"menus", "level_select", "state", "boot", "classify", "loop",
                "frame"}


def _unstitch_worklist() -> int:
    """Which dependencies sew each skin piece to the wrong flow.

    The hand-written port was assembled with its own wiring, and that wiring is
    what drifted. A leaf cannot simply be lifted out of it: wherever it reaches
    into ``native``'s flow -- its state object, its loop, its menu model -- that
    edge is a stitch to the wrong garment. Cutting those edges is what makes a
    piece attachable at its true address on the skeleton.

    A leaf with no flow edges is already free-standing and can be registered and
    shadowed immediately.
    """
    import re
    src_dir = ROOT / "skyroads" / "native"
    dep = re.compile(r"from\s+skyroads\.native\.(\w+)|from\s+\.(\w+)\s+import")
    ir = set(json.loads((CODEMAP / "recovery_ir.json").read_text())["functions"])
    addr = re.compile(r"1010:[0-9A-Fa-f]{4}")

    free, sewn = [], []
    for path in sorted(src_dir.glob("*.py")):
        if path.name == "__init__.py":
            continue
        name = path.stem
        text = path.read_text(encoding="utf-8")
        anchored = {a.upper() for a in addr.findall(text)} & ir
        if not anchored:
            continue                      # flow, not skin -- see --native
        deps = {m or n for m, n in dep.findall(text)} - {""}
        flow_edges = sorted(deps & FLOW_MODULES)
        # An import edge is only a LOWER BOUND on coupling. A piece that takes
        # the native state object as a PARAMETER is just as sewn to the wrong
        # flow while importing nothing -- `from __future__ import annotations`
        # even turns the type hint into a string. Counting state references
        # caught world_load and level_load, which the import scan called free.
        state_refs = len(re.findall(r"\bstate[:.]|NativeGameState|\bst\.", text))
        if flow_edges or state_refs:
            sewn.append((name, len(anchored), flow_edges, state_refs))
        else:
            free.append((name, len(anchored), flow_edges, 0))

    print("[unstitch] skin pieces and the stitches holding them to the wrong flow\n")
    print(f"FREE -- no flow import, no state coupling; attachable as-is "
          f"({len(free)}):")
    for name, n, _, _ in sorted(free, key=lambda t: -t[1]):
        print(f"    {name:22s} {n:2d} anchored address(es)")
    print(f"\nSEWN -- cut these stitches first ({len(sewn)}):")
    for name, n, edges, refs in sorted(sewn, key=lambda t: (t[3], len(t[2]))):
        what = []
        if edges:
            what.append("imports " + ", ".join(edges))
        if refs:
            what.append(f"{refs} state ref(s)")
        print(f"    {name:22s} {n:2d} anchored  <- {'; '.join(what)}")
    print("\n[unstitch] cut the listed edges, register the piece with @oracle_link,")
    print("[unstitch] then shadow it against its generated body before it drives.")
    return 0


def _native_triage() -> int:
    """Split skyroads/native/ into LEAF candidates and FLOW to retire.

    ``native/`` was assembled bottom-up from hooks and observation, and its
    control flow was never proven against the original -- it has unit tests but
    no cold-start differential, and unit tests over the wrong wiring are coverage
    of the wrong thing. The generated CPUless program is the only artifact whose
    FLOW is proven (672 frames byte-exact from cold start), so it is the frame.

    A native module that anchors to real recovered addresses is a LEAF: it can be
    re-hosted inside that frame, one address at a time, each step gated by the
    differential that already exists. A module that anchors to nothing is FLOW --
    the part the frame supersedes, and the part most likely to be wrong. Its
    value is its naming and state model, which belongs in the Memory Schema, not
    in a second control path.
    """
    import re
    ir = set(json.loads((CODEMAP / "recovery_ir.json").read_text())["functions"])
    observed = set(json.loads((CODEMAP / "observed.json").read_text())["executed"])
    pat = re.compile(r"1010:[0-9A-Fa-f]{4}")
    leaves, flow = [], []
    for path in sorted((ROOT / "skyroads" / "native").glob("*.py")):
        if path.name == "__init__.py":
            continue
        addrs = {_norm(a) for a in pat.findall(path.read_text(encoding="utf-8"))}
        anchored = sorted(a for a in addrs if a in ir)
        (leaves if anchored else flow).append((path.name, addrs, anchored))
    print("[native triage] the generated CPUless program is the FRAME "
          "(only artifact with proven cold-start flow)\n")
    print(f"LEAF candidates -- anchor to recovered functions, re-hostable "
          f"inside the frame ({len(leaves)}):")
    for name, addrs, anchored in sorted(leaves, key=lambda t: -len(t[2])):
        live = sum(1 for a in anchored if a in observed)
        print(f"    {name:22s} {len(anchored):2d} IR-anchored "
              f"({live} executed) of {len(addrs)} mentioned")
    print(f"\nFLOW / unanchored -- superseded by the frame; keep the KNOWLEDGE, "
          f"retire the control path ({len(flow)}):")
    for name, addrs, _ in sorted(flow):
        print(f"    {name:22s} {len(addrs)} address mention(s), 0 IR-anchored")
    print("\n[native triage] none of these are registered with @oracle_link, so "
          "none is visible to the ledger or to any gate -- registering them is "
          "the prerequisite to absorbing any of it.")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--full", action="store_true", help="list every island")
    ap.add_argument("--orphans", action="store_true",
                    help="only islands whose address is not an IR function")
    ap.add_argument("--unstitch", action="store_true",
                    help="the UNSTITCH work list: which intra-native dependencies "
                         "sew each leaf to the wrong flow and must be cut")
    ap.add_argument("--native", action="store_true",
                    help="triage skyroads/native/: which modules are LEAF "
                         "candidates (address-anchored) vs FLOW superseded by "
                         "the generated frame")
    args = ap.parse_args(argv)
    if args.unstitch:
        return _unstitch_worklist()
    if args.native:
        return _native_triage()

    islands, ir, observed, manifest = _load()
    generated = set(manifest.get("functions", {})) or ir

    rows = []
    for module, func, link in islands:
        addr = _norm(link.boundary)
        rows.append({
            "addr": addr, "func": func, "module": module.split(".")[-1],
            "status": link.status,
            "in_ir": addr in ir,
            "observed": addr in observed,
            "merge": link.merge_target or "",
        })
    rows.sort(key=lambda r: r["addr"])

    print(f"[ledger] {len(rows)} islands across {', '.join(ISLAND_PACKAGES)}")
    print(f"[ledger] generated program: {len(ir)} IR functions, "
          f"{len(observed)} executed addresses\n")

    by_status = Counter(r["status"] for r in rows)
    print("islands by evidence level (dos_re.islands ladder):")
    for st, n in sorted(by_status.items(), key=lambda kv: -kv[1]):
        mark = "  <- may run" if st in RUNNABLE_STATUS else "  (documentation until re-proven)"
        print(f"    {st:12s} {n:3d}{mark}")

    anchored = [r for r in rows if r["in_ir"]]
    orphans = [r for r in rows if not r["in_ir"]]
    live = [r for r in anchored if r["observed"]]
    runnable = [r for r in live if r["status"] in RUNNABLE_STATUS]

    print(f"\nanchoring against the generated program:")
    print(f"    {len(anchored):3d} islands name an IR function  (absorbable in principle)")
    print(f"    {len(live):3d} of those are actually EXECUTED by the census")
    print(f"    {len(runnable):3d} of those carry VERIFIED/CANONICAL evidence "
          f"(absorbable TODAY)")
    print(f"    {len(orphans):3d} islands name no IR function -- semantic knowledge "
          f"only (formats, helpers, retired paths)")

    if runnable:
        print("\n  absorbable today (evidence sufficient to become the running body):")
        for r in runnable:
            print(f"    {r['addr']}  {r['status']:11s} {r['func']} "
                  f"({r['module']})")

    if args.orphans or args.full:
        show = orphans if args.orphans else rows
        title = "orphan islands (no IR function)" if args.orphans else "all islands"
        print(f"\n{title}:")
        for r in show:
            flags = ("ir" if r["in_ir"] else "--") + ("/obs" if r["observed"] else "/---")
            print(f"    {r['addr']}  {r['status']:11s} {flags}  {r['func']} "
                  f"({r['module']})" + (f" -> {r['merge']}" if r["merge"] else ""))

    print("\n[ledger] absorption is gated: only the authoritative-override seam "
          "may make an island the running body, and the generated body stays as "
          "the differential reference. See docs/architecture.md.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
