"""Read-only reachability audit over the recovery IR -- DIAGNOSTIC, never a pruner.

Reachability is under-approximated more often than code is over-emitted. A
function absent from the static near-call closure is NOT dead -- it is almost
always reached by a dynamic edge the static graph cannot see (a dispatch table,
an IVT vector, a scheduler resume). So this tool assembles EVERY known
root/evidence source and classifies every IR function by WHY it is retained.
Anything it cannot place is flagged "requires explanation / retention", never
"dead": the finding is a hole in the ASSEMBLED ROOT SET, not excess code.

It reuses dos_re's runtime-closure walk (tools/cpuless_closure.walk_closure is
the promotion-frontier form); the classification here is richer because it must
label the RETENTION REASON, not just promoted/frontier.

This local report predates the retained Atlas sources and does not update or
override them. Promote useful facts into the shared evidence model.

Inputs inspected by this diagnostic:

    canonical entry   the boot far-jump target (build_boot_image / --extra)
    IVT handlers      observed.json  ivt_game_vectors  (hardware-entered ISRs)
    dynamic dispatch  artifacts/codemap/dispatch_extra.txt   (indirect-call targets)
    boundary heads    artifacts/codemap/boundary_heads.txt   (scheduler resume points)
    replay bases      artifacts/codemap/replay_base_entries.txt (resume addresses)

Usage:
    python scripts/reachability_audit.py
    python scripts/reachability_audit.py --json artifacts/codemap/reachability_audit.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dos_re"))

CODE_SEG = 0x1010
CANONICAL_ENTRY = "1010:61F3"


def _load_pairs(path: Path) -> set[str]:
    out: set[str] = set()
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            out.add(line.upper())
    return out


def _near_far_targets(fn: dict, seg: int):
    """Static call edges out of one IR function record -> set of 'CS:IP' keys."""
    out: set[str] = set()
    for t in fn.get("calls_near", []):
        off = t if isinstance(t, int) else int(t, 16)
        out.add(f"{seg:04X}:{off:04X}")
    for pair in fn.get("calls_far", []):
        s, o = pair if isinstance(pair, (list, tuple)) else (seg, pair)
        out.add(f"{int(s):04X}:{int(o):04X}")
    return out


def _unresolved_sites(fn: dict, key: str):
    """(key, ip, kind) for every indirect call/jmp the static graph cannot follow."""
    sites = []
    for blk in fn["blocks"]:
        for inst in blk["instructions"]:
            if inst.get("kind") in ("call_ind", "jmp_ind"):
                sites.append((key, inst["ip"], inst["kind"]))
    return sites


def audit(ir: dict, roots_by_source: dict[str, set[str]]) -> dict:
    fns = ir["functions"]
    all_keys = set(fns)

    # 1. static-call reachability from ALL roots (near + far edges only).
    static_reached: set[str] = set()
    work = [r for src in roots_by_source.values() for r in src]
    while work:
        k = work.pop()
        if k in static_reached:
            continue
        static_reached.add(k)
        fn = fns.get(k)
        if not fn:
            continue
        seg = int(k.split(":")[0], 16)
        for t in _near_far_targets(fn, seg):
            if t not in static_reached:
                work.append(t)

    # 2. per-function retention reason, by precedence:
    #    interrupt/root entry > dynamic dispatch > boundary/replay-base resume >
    #    reachable via static calls > NOT reached (requires explanation).
    ivt = roots_by_source["ivt"]
    canon = roots_by_source["canonical"]
    dyn = roots_by_source["dynamic"]
    heads = roots_by_source["boundary"]
    replay_bases = roots_by_source["replay_base"]

    buckets = {
        "interrupt_or_root_entry": [],
        "retained_dynamic_dispatch": [],
        "retained_scheduler_resume": [],
        "reachable_static_calls": [],
        "not_reached_requires_explanation": [],
    }
    for k in sorted(all_keys):
        if k in ivt or k in canon:
            buckets["interrupt_or_root_entry"].append(k)
        elif k in dyn:
            buckets["retained_dynamic_dispatch"].append(k)
        elif k in heads or k in replay_bases:
            buckets["retained_scheduler_resume"].append(k)
        elif k in static_reached:
            buckets["reachable_static_calls"].append(k)
        else:
            buckets["not_reached_requires_explanation"].append(k)

    # 3. unresolved indirect edge sites (what the static walk cannot follow).
    unresolved = []
    for k, fn in fns.items():
        unresolved.extend(_unresolved_sites(fn, k))
    unresolved.sort()

    return {
        "ir_functions": len(all_keys),
        "roots_by_source": {s: sorted(v) for s, v in roots_by_source.items()},
        "classification": buckets,
        "unresolved_indirect_sites": [
            {"function": k, "ip": ip, "kind": kind} for k, ip, kind in unresolved],
        "counts": {name: len(v) for name, v in buckets.items()},
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--ir", default=str(ROOT / "recovery/recovery_ir.json"),
        help="retained Recovery IR to inspect",
    )
    ap.add_argument("--observed", default=str(ROOT / "artifacts/codemap/observed.json"))
    ap.add_argument("--codemap-dir", default=str(ROOT / "artifacts/codemap"))
    ap.add_argument("--json", default=None, help="also write the full report here")
    args = ap.parse_args(argv)

    ir = json.loads(Path(args.ir).read_text(encoding="utf-8"))
    obs = json.loads(Path(args.observed).read_text(encoding="utf-8"))
    cm = Path(args.codemap_dir)

    ivt = {v.upper() for v in (obs.get("ivt_game_vectors") or {}).values()}
    ivt |= {v.upper() for v in (obs.get("int_entries") or [])}
    roots_by_source = {
        "canonical": {CANONICAL_ENTRY},
        "ivt": ivt,
        "dynamic": _load_pairs(cm / "dispatch_extra.txt"),
        "boundary": _load_pairs(cm / "boundary_heads.txt"),
        "replay_base": _load_pairs(cm / "replay_base_entries.txt"),
    }

    rep = audit(ir, roots_by_source)
    c = rep["counts"]
    print("=== dos_re reachability audit (DIAGNOSTIC -- never deletes) ===")
    print(f"IR functions ................................ {rep['ir_functions']}")
    print(f"Reachable by static call edges .............. {c['reachable_static_calls']}")
    print(f"Retained by dynamic dispatch evidence ....... {c['retained_dynamic_dispatch']}")
    print(f"Retained as IVT / root entry ................ {c['interrupt_or_root_entry']}")
    print(f"Retained as scheduler resume (head/replay) ... {c['retained_scheduler_resume']}")
    print(f"NOT reached by assembled roots (EXPLAIN) .... {c['not_reached_requires_explanation']}")
    print(f"Unresolved indirect edge sites .............. {len(rep['unresolved_indirect_sites'])}")
    print()
    roots_total = len(set().union(*roots_by_source.values()))
    print(f"assembled root set: {roots_total} addresses across "
          f"{sum(1 for v in roots_by_source.values() if v)} sources")
    for src, v in roots_by_source.items():
        print(f"    {src:10s} {len(v)}")
    nre = rep["classification"]["not_reached_requires_explanation"]
    if nre:
        print()
        print("NOT reached by assembled roots -- each REQUIRES EXPLANATION or a "
              "missing root, NOT deletion:")
        for k in nre:
            print(f"    {k}")

    if args.json:
        Path(args.json).write_text(json.dumps(rep, indent=1), encoding="utf-8")
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
