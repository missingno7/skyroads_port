"""Report trusted lifecycle coverage contributed by SkyRoads ReplayArtifacts.

The report does not promote a replay.  Record with the canonical player, run
oracle/candidate differential verification, then use the normal promotion flow;
this script only makes missing levels and lifecycle exits explicit.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "dos_re"))

from skyroads.gameplay_corpus import report_directory  # noqa: E402
from skyroads.launch_inputs import LEVEL_COUNT  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("directory", nargs="?", default=str(ROOT / "artifacts" / "replays"))
    parser.add_argument("--require-complete", action="store_true",
                        help="fail unless every level and required lifecycle path is covered")
    args = parser.parse_args(argv)
    report = report_directory(args.directory, level_count=LEVEL_COUNT)
    print(f"artifacts: {len(report.artifacts)} ({', '.join(report.artifacts) or 'none'})")
    print("covered levels: " + (", ".join(map(str, report.levels)) or "none"))
    print("covered paths: " + (", ".join(report.paths) or "none"))
    print("missing levels: " + (", ".join(map(str, report.missing_levels)) or "none"))
    print("missing paths: " + (", ".join(report.missing_paths) or "none"))
    return 0 if report.complete or not args.require_complete else 2


if __name__ == "__main__":
    raise SystemExit(main())
