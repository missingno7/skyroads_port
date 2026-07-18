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


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--full", action="store_true", help="list every island")
    ap.add_argument("--orphans", action="store_true",
                    help="only islands whose address is not an IR function")
    args = ap.parse_args(argv)

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
