"""Generated-corpus recipes require explicit, valid evidence inputs."""
from __future__ import annotations

import pytest

from scripts import build_codemap, rebuild_all


def test_build_codemap_requires_an_explicit_evidence_source(capsys) -> None:
    with pytest.raises(SystemExit) as raised:
        build_codemap.main([])

    assert raised.value.code == 2
    assert "no observation evidence selected" in capsys.readouterr().err


def test_build_codemap_rejects_a_missing_replay(tmp_path, capsys) -> None:
    missing = tmp_path / "missing-replay"

    with pytest.raises(SystemExit) as raised:
        build_codemap.main(["--replay", str(missing)])

    assert raised.value.code == 2
    error = capsys.readouterr().err
    assert "ReplayArtifact input(s) missing" in error
    assert str(missing) in error


def test_build_codemap_rejects_a_directory_without_a_manifest(
    tmp_path, capsys,
) -> None:
    directory = tmp_path / "not-an-artifact"
    directory.mkdir()

    with pytest.raises(SystemExit) as raised:
        build_codemap.main(["--replay", str(directory)])

    assert raised.value.code == 2
    assert "ReplayArtifact manifest missing" in capsys.readouterr().err


def test_full_rebuild_requires_explicit_observation_evidence(capsys) -> None:
    with pytest.raises(SystemExit) as raised:
        rebuild_all.main([])

    assert raised.value.code == 2
    assert "requires explicit observation evidence" in capsys.readouterr().err


def test_rebuild_forwards_declared_evidence(monkeypatch) -> None:
    calls: list[tuple[str, tuple[str, ...]]] = []

    def record(script: str, _why: str, extra_args: tuple[str, ...] = ()) -> None:
        calls.append((script, extra_args))

    monkeypatch.setattr(rebuild_all, "_run", record)

    result = rebuild_all.main([
        "--replay", "recovery/replays/a",
        "--replay", "recovery/replays/b",
        "--cold-boot-frames", "12",
    ])

    assert result == 0
    assert calls[0] == (
        "build_codemap.py",
        (
            "--replay", "recovery/replays/a",
            "--replay", "recovery/replays/b",
            "--cold-boot-frames", "12",
        ),
    )
    assert [name for name, _ in calls[1:]] == [
        "expand_vmless_frontier.py",
        "build_recovered.py",
    ]
    assert all(not extra for _, extra in calls[1:])
