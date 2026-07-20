"""The SkyRoads planner is the only implementation-selection authority."""
from __future__ import annotations

import pytest

from dos_re.execution import ExecutionPlanError, OverrideCategory, plan_execution
from scripts.play import SkyroadsFrontend
from skyroads import execution as execution_model
from skyroads.execution import catalog, configuration, coverage


def _plan(profile: str, composition: str):
    return plan_execution(configuration(profile, composition), coverage(), catalog())


@pytest.fixture
def original_exe(tmp_path, monkeypatch):
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "SKYROADS.EXE").write_bytes(b"MZ")
    monkeypatch.setattr(execution_model, "ROOT", tmp_path)


def test_default_window_scale_fits_a_768_line_desktop() -> None:
    assert SkyroadsFrontend.default_scale == 2
    assert 200 * 1.2 * SkyroadsFrontend.default_scale <= 480


def test_oracle_plan_selects_only_the_untouched_exe(original_exe) -> None:
    plan = _plan("development", "oracle")
    assert {item.implementation_id for item in plan.implementations} == {
        "baseline:interpreted-exe"
    }
    assert plan.configuration.selected_overrides == ()


def test_authored_candidate_plan_selects_replacements_explicitly(
    original_exe,
) -> None:
    plan = _plan("verification", "authored-candidates")
    selected = {
        item.implementation_id: item for item in plan.implementations
    }
    assert "baseline:interpreted-exe" in selected
    assert any(
        item.category is OverrideCategory.FAITHFUL
        for item in selected.values()
    )
    assert all(
        item.category is not OverrideCategory.BEHAVIORAL
        for item in selected.values()
    )


def test_default_play_is_fast_but_has_no_behavioral_modifications(
    original_exe,
) -> None:
    plan = _plan("development", "auto")
    categories = {
        item.category for item in plan.implementations
        if item.category is not None
    }
    assert OverrideCategory.FAITHFUL in categories
    assert OverrideCategory.ENHANCEMENT in categories
    assert OverrideCategory.BEHAVIORAL not in categories
    assert "enhancement:frame-park" in {
        item.implementation_id for item in plan.implementations
    }


def test_release_readiness_rejects_atlas_control_flow_frontiers(
    tmp_path, monkeypatch,
) -> None:
    boot = tmp_path / "boot"
    boot.mkdir()
    (boot / "state.json").write_text("{}", encoding="utf-8")
    (boot / "memory_1mb.bin").write_bytes(b"\0")
    (boot / "manifest.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(execution_model, "BOOT_DIR", boot)
    with pytest.raises(ExecutionPlanError) as caught:
        _plan("release", "generated-abi")
    report = caught.value.report
    assert report.unresolved_edges
    assert not report.missing_bootstrap_artifacts
    assert report.is_detached_from("original-exe")
    assert report.is_detached_from("interpreter")
    assert report.bootstrap_provider_id == "skyroads-generated-abi-build-image"
    assert not report.package_ready
    assert "unresolved control-flow edges" in str(caught.value)


def test_release_plan_fails_before_launch_when_bootstrap_is_missing(
    tmp_path, monkeypatch,
) -> None:
    monkeypatch.setattr(execution_model, "BOOT_DIR", tmp_path / "missing")
    with pytest.raises(ExecutionPlanError) as caught:
        _plan("release", "generated-abi")
    message = str(caught.value)
    assert "missing bootstrap artifacts" in message
    assert "python scripts/build_boot_image.py" in message
