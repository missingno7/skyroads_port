"""Closed-world release export gates."""
from __future__ import annotations

import json

import pytest

from dos_re.execution import ExecutionPlanError
from dos_re.export import export_release
from skyroads import release
from skyroads import execution


def test_export_rejects_missing_boot_image(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(execution, "BOOT_DIR", tmp_path / "missing")
    with pytest.raises(ExecutionPlanError, match="scripts/build_boot_image.py"):
        release.export_factory()


def test_export_contains_only_the_audited_product_closure(
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

    plan, files, launcher = release.export_factory()
    manifest = export_release(
        plan, files, tmp_path / "product", launcher=launcher)

    destinations = {destination for destination, _digest in manifest.files}
    assert launcher in destinations
    assert "artifacts/boot_image/state.json" in destinations
    assert manifest.bootstrap_provider_id == "skyroads-cpuless-build-image"
    release_manifest = json.loads(
        (tmp_path / "product" / "dos_re_release.json").read_text(
            encoding="utf-8"
        )
    )
    assert release_manifest["bootstrap_artifacts"] == {
        "skyroads-boot-manifest": "artifacts/boot_image/manifest.json",
        "skyroads-boot-memory": "artifacts/boot_image/memory_1mb.bin",
        "skyroads-boot-state": "artifacts/boot_image/state.json",
    }
    assert all(not name.lower().endswith((".exe", ".com")) for name in destinations)
    assert not any(name.startswith("dos_re/cpu") for name in destinations)
    assert not any(name.startswith("dos_re/replay") for name in destinations)
