"""Every replay base address is a re-entry point into its containing function.

A ReplayArtifact resumes from its recording profile's base continuation, which
may be captured wherever the machine happened to be -- usually a tick-wait,
render loop, or mid-blit rather than a function entry.

When interpreter fallback is forbidden, resuming there reports an unresolved
frontier at frame 0. The containing function is lifted and contains the
address; there is simply no selected entry at it, because the census exports
entries and re-entry points, and a
mid-function address is neither. So it reads like a census gap and is not one,
which is what makes it expensive: the answer is not to lift anything, only to
say that this address is reachable from outside.

The evidence is direct: the ReplayArtifact base continuation IS the machine at
that address. Declare each base CS:IP as a dynamic dispatch entry and the
emitter makes it a block leader with a re-entry hook, sharing the recovered
blocks rather than cloning them.

Cheap when it is redundant: an address that is already an entry or a resume
point just resolves to the same block.

Usage:
    python scripts/find_replay_base_entries.py
    python scripts/find_replay_base_entries.py --print
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dos_re"))
sys.path.insert(0, str(ROOT))

from skyroads.replay import recording_artifacts, recording_base  # noqa: E402

CODE_SEG = 0x1010


def replay_base_entries(replays_dir: Path) -> list[tuple[str, int, int]]:
    """Return ``(artifact_name, cs, ip)`` for every replay recording base."""
    out: list[tuple[str, int, int]] = []
    for artifact in recording_artifacts(replays_dir):
        cpu = recording_base(artifact).metadata["cpu"]
        out.append((artifact.directory.name, int(cpu["cs"]) & 0xFFFF,
                    int(cpu["ip"]) & 0xFFFF))
    return sorted(out, key=lambda t: (t[1], t[2]))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--replays", default=str(ROOT / "artifacts" / "replays"))
    ap.add_argument("--out",
                    default=str(ROOT / "artifacts" / "codemap" /
                                "replay_base_entries.txt"))
    ap.add_argument("--seg", default=f"{CODE_SEG:04X}",
                    help="only export entries in this segment")
    ap.add_argument("--print", dest="show", action="store_true")
    args = ap.parse_args(argv)

    seg = int(args.seg, 16)
    found = replay_base_entries(Path(args.replays))
    lines = [
        "# Replay-base re-entry points -- DERIVED by",
        "# scripts/find_replay_base_entries.py.",
        "# A ReplayArtifact resumes from its recording base, which catches the machine",
        "# wherever it was -- almost never at a function entry, usually in whatever",
        "# loop the game spends its time in. Resuming at a mid-function address",
        "# needs a selected entry there or frame 0 reports an unresolved frontier",
        "# that IS lifted. The evidence is the replay base itself: the machine was",
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
    print(f"[replay-bases] {len(keep)} distinct re-entry addresses from "
          f"{len(found)} artifacts -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
