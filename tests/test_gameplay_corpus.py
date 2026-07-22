from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from dos_re.replay import ReplayArtifact

from skyroads.gameplay_corpus import report_artifacts
from skyroads.gameplay_region import _coverage
from scripts.play import SkyroadsFrontend


def test_corpus_reports_only_explicit_lifecycle_evidence(tmp_path: Path) -> None:
    artifact = ReplayArtifact.create(
        tmp_path / "replay", timeline_id="test", events=(),
        metadata={"gameplay_coverage": {
            "schema": "skyroads:gameplay-lifecycle-coverage/v1",
            "levels": [0, 30],
            "paths": {"entry": 2, "semantic-tick": 9},
        }},
    )
    report = report_artifacts((artifact,), level_count=31)

    assert report.levels == (0, 30)
    assert report.missing_levels[0] == 1
    assert "exit:gameplay-result" in report.missing_paths


def test_recording_coverage_is_scoped_to_recorded_interval() -> None:
    runtime = SimpleNamespace()
    frontend = SkyroadsFrontend(Path(__file__).resolve().parents[1])
    args = SimpleNamespace(practice_level_position=None, composition="faithful-product")

    _coverage(runtime, "entry", level=4)
    _coverage(runtime, "semantic-tick", level=4)
    frontend.recording_started(runtime, args, record_event=lambda _event: None)
    _coverage(runtime, "semantic-tick", level=4)
    _coverage(runtime, "semantic-tick", level=4)
    _coverage(runtime, "exit:gameplay-result")

    metadata = frontend.recording_finished(runtime, args)["gameplay_coverage"]
    assert metadata["levels"] == [4]
    assert metadata["paths"] == {
        "exit:gameplay-result": 1,
        "semantic-tick": 2,
    }
    assert metadata["semantic_ticks"] == 2
    assert runtime._skyroads_gameplay_coverage["paths"]["entry"] == 1
    assert not hasattr(runtime, "_skyroads_recording_gameplay_coverage")
