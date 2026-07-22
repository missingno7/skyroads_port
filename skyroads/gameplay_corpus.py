"""Lifecycle requirements and report helpers for trusted SkyRoads replays."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from dos_re.replay import ReplayArtifact


REQUIRED_PATHS = (
    "entry",
    "semantic-tick",
    "exit:gameplay-result",
    "exit:road-departure-transition",
    "exit:gameplay-aborted",
)


@dataclass(frozen=True)
class CorpusReport:
    artifacts: tuple[str, ...]
    levels: tuple[int, ...]
    paths: tuple[str, ...]
    missing_levels: tuple[int, ...]
    missing_paths: tuple[str, ...]

    @property
    def complete(self) -> bool:
        return not self.missing_levels and not self.missing_paths


def report_artifacts(
    artifacts: Iterable[ReplayArtifact], *, level_count: int,
) -> CorpusReport:
    """Combine derived lifecycle summaries persisted when a replay is recorded.

    A recording without the v1 coverage summary remains a valid replay, but is
    deliberately reported as contributing no lifecycle claim; post-hoc oracle
    enrichment may write a new derived summary without changing its input stream.
    """
    names: list[str] = []
    levels: set[int] = set()
    paths: set[str] = set()
    for artifact in artifacts:
        names.append(artifact.directory.name)
        coverage = artifact.metadata.get("gameplay_coverage", {})
        if not isinstance(coverage, dict) or coverage.get("schema") != \
                "skyroads:gameplay-lifecycle-coverage/v1":
            continue
        levels.update(int(item) for item in coverage.get("levels", ()))
        path_counts = coverage.get("paths", {})
        if isinstance(path_counts, dict):
            paths.update(str(name) for name, count in path_counts.items() if int(count))
    return CorpusReport(
        tuple(sorted(names)), tuple(sorted(levels)), tuple(sorted(paths)),
        tuple(level for level in range(int(level_count)) if level not in levels),
        tuple(path for path in REQUIRED_PATHS if path not in paths),
    )


def report_directory(directory: str | Path, *, level_count: int) -> CorpusReport:
    root = Path(directory)
    artifacts = tuple(
        ReplayArtifact.open(path.parent) for path in sorted(root.glob("*/replay.json"))
    )
    return report_artifacts(artifacts, level_count=level_count)
