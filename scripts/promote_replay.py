"""Promote a fully validated replay into a compact retained corpus artifact.

Derived cache boundaries are intentionally omitted. The source artifact keeps
every investigation boundary and scoped result; the retained copy contains the
immutable timeline, authoritative execution evidence, full validation claim,
and only the profile bases required to restore it.

Usage:
    python scripts/promote_replay.py artifacts/replays/REPLAY DESTINATION
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "dos_re"))

from dos_re.replay import ReplayArtifact, ReplayPoint  # noqa: E402


def _write_json(path: Path, value) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args(argv)
    source = ReplayArtifact.open(args.source.resolve())
    destination = args.destination.resolve()
    if destination.exists():
        parser.error(f"destination already exists: {destination}")
    if not source.trusted:
        parser.error("source replay is not fully oracle-validated")
    evidence = source.execution_evidence()
    if evidence is None:
        parser.error("source replay has no authoritative execution evidence")
    evidence_profile = source.profile_by_digest(
        evidence.profile_identity_digest)
    if evidence_profile.role != "oracle":
        parser.error("execution evidence was not collected from an oracle")

    end = ReplayPoint.from_json(source.metadata["end_point"])
    start = ReplayPoint(0, source.timeline_id)
    full = tuple(
        validation for validation in source.validations()
        if validation.equivalent
        and validation.start == start
        and validation.end == end
    )
    if not full:
        parser.error("source has no equivalent full-timeline validation")

    keep_digests = {
        source.capture_profile().identity_digest,
        evidence_profile.identity_digest,
    }
    for validation in full:
        keep_digests.add(validation.oracle_profile_identity_digest)
        keep_digests.add(validation.candidate_profile_identity_digest)

    manifest = json.loads(source.path.read_text(encoding="utf-8"))
    profiles = manifest["profiles"]
    kept_profiles = {
        profile_id: record
        for profile_id, record in profiles.items()
        if record["identity_digest"] in keep_digests
    }
    found_digests = {
        record["identity_digest"] for record in kept_profiles.values()
    }
    if found_digests != keep_digests:
        missing = sorted(keep_digests - found_digests)
        parser.error(f"required replay profiles are missing: {missing}")

    # Investigation annotations stay with the source artifact. The retained
    # corpus carries only its authoritative full-timeline endpoint claim, so
    # stale pre-fix divergence details do not get duplicated onto every Atlas
    # coverage record.
    kept_profile_ids = set(kept_profiles)
    retained_points = {}
    for key, point_record in manifest.get("points", {}).items():
        annotations = [
            annotation
            for annotation in point_record.get("annotations", ())
            if annotation.get("kind") == "verified-endpoint"
            and annotation.get("point") == end.to_json()
            and annotation.get("metadata", {}).get("oracle_profile")
            in kept_profile_ids
            and annotation.get("metadata", {}).get("candidate_profile")
            in kept_profile_ids
        ]
        if annotations:
            retained_points[key] = {
                **point_record,
                "annotations": annotations,
            }

    destination.mkdir(parents=True)
    for record in kept_profiles.values():
        base_manifest = source.directory / record["base"]
        relative_base = base_manifest.parent.relative_to(source.directory)
        shutil.copytree(base_manifest.parent, destination / relative_base)
        record["boundaries"] = {}
        record["pending_boundaries"] = {}
    manifest["profiles"] = kept_profiles
    manifest["validations"] = [item.to_json() for item in full]
    manifest["points"] = retained_points
    _write_json(destination / "replay.json", manifest)

    promoted = ReplayArtifact.open(destination)
    if not promoted.trusted:
        raise RuntimeError("compacted replay lost its full validation claim")
    if promoted.execution_evidence() != evidence:
        raise RuntimeError("compacted replay changed execution evidence")
    print(
        f"{destination}: promoted {len(promoted.timeline_coordinates)} points, "
        f"{len(promoted.function_visits())} functions, "
        f"{len(evidence.transfers)} edges, {len(kept_profiles)} profile bases"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
