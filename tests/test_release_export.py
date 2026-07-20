"""Closed-world release export gates."""
from __future__ import annotations

import json

import pytest

from dos_re.export import ExportError, export_release
from skyroads import release


def test_export_rejects_missing_boot_image(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(release, "BOOT_DIR", tmp_path / "missing")
    with pytest.raises(ExportError, match="release boot image is missing"):
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
    monkeypatch.setattr(release, "BOOT_DIR", boot)

    plan, files, launcher = release.export_factory()
    manifest = export_release(
        plan, files, tmp_path / "product", launcher=launcher)

    destinations = {destination for destination, _digest in manifest.files}
    assert launcher in destinations
    assert "artifacts/boot_image/state.json" in destinations
    assert all(not name.lower().endswith((".exe", ".com")) for name in destinations)
    assert not any(name.startswith("dos_re/cpu") for name in destinations)
    assert not any(name.startswith("dos_re/replay") for name in destinations)
