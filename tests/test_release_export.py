"""Closed-world release export gates."""
from __future__ import annotations

import json

import pytest

from dos_re.execution import ExecutionPlanError
from skyroads import release
from skyroads import execution


def test_export_rejects_missing_boot_image(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(execution, "BOOT_DIR", tmp_path / "missing")
    with pytest.raises(ExecutionPlanError, match="scripts/build_boot_image.py"):
        release.export_factory()


def test_export_rejects_atlas_frontiers_even_with_bootstrap(
    tmp_path, monkeypatch
) -> None:
    boot = tmp_path / "boot"
    boot.mkdir()
    (boot / "state.json").write_text("{}", encoding="utf-8")
    (boot / "memory_1mb.bin").write_bytes(b"\0")
    (boot / "manifest.json").write_text(json.dumps({
        "poison": {
            "enabled": True,
            "code_bytes_present_after": 0,
        },
    }), encoding="utf-8")
    monkeypatch.setattr(execution, "BOOT_DIR", boot)

    with pytest.raises(ExecutionPlanError, match="unresolved control-flow edges") as caught:
        release.export_factory()
    assert caught.value.report.unresolved_edges
    assert not caught.value.report.missing_bootstrap_artifacts
