"""Explain the first observable-effect difference in one replay interval.

Unlike the allocation-bounded production digest, this diagnostic deliberately
retains primitive records.  Keep it offline and use it only for a small
already-localized interval.
"""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "dos_re")]

from dos_re import player  # noqa: E402
from dos_re.replay import ReplayArtifact, ReplayPoint  # noqa: E402
from scripts.play import SkyroadsFrontend  # noqa: E402


class _TraceSink:
    def __init__(self) -> None:
        self.records: list[tuple[object, ...]] = []

    def record(self, kind, a=0, b=0, c=0, d=0) -> None:
        self.records.append((int(kind), int(a), int(b), int(c), int(d)))

    def record_bytes(self, kind, payload, *, identity=0) -> None:
        value = bytes(payload)
        self.records.append((
            int(kind), int(identity), len(value), hashlib.sha256(value).hexdigest(),
        ))


def _trace(driver, artifact, start: ReplayPoint, end: ReplayPoint):
    restored = artifact.nearest_cached(driver.profile, start)
    driver.restore(artifact.restore(driver.profile, restored), restored)
    driver.replay_to(artifact, start)
    sink = _TraceSink()
    driver.runtime.dos.observable_effect_sink = sink
    try:
        driver.replay_to(artifact, end)
    finally:
        driver.runtime.dos.observable_effect_sink = None
    return sink.records


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("replay", type=Path)
    parser.add_argument("start", type=int)
    parser.add_argument("end", type=int)
    options = parser.parse_args(argv)

    artifact = ReplayArtifact.open(options.replay.resolve())
    frontend = SkyroadsFrontend(ROOT)
    args = player.build_arg_parser(frontend).parse_args([
        "--profile", "verification",
        "--composition", "faithful-product",
        "--play-replay", str(artifact.directory),
        "--verify-start", str(options.start),
        "--verify-end", str(options.end),
        "--headless",
    ])
    frontend.apply_replay_metadata(args, artifact.metadata)
    plan = frontend.resolve_execution_plan(args)
    args.execution_plan = plan
    oracle, candidate = frontend.verification_drivers(args, plan, artifact)
    start = ReplayPoint(options.start, artifact.timeline_id)
    end = ReplayPoint(options.end, artifact.timeline_id)
    left = _trace(oracle, artifact, start, end)
    right = _trace(candidate, artifact, start, end)

    print(f"oracle events: {len(left)}; candidate events: {len(right)}")
    limit = max(len(left), len(right))
    mismatches = []
    for index in range(limit):
        oracle_record = left[index] if index < len(left) else None
        candidate_record = right[index] if index < len(right) else None
        if oracle_record != candidate_record:
            mismatches.append((index, oracle_record, candidate_record))
    if mismatches:
        print(f"different records: {len(mismatches)}")
        for index, oracle_record, candidate_record in mismatches[:20]:
            print(
                f"{index:5d} oracle={oracle_record} candidate={candidate_record}"
            )
        return 1
    print("observable streams are identical")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
