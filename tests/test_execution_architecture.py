"""The SkyRoads planner is the only implementation-selection authority."""
from __future__ import annotations

from dos_re.execution import OverrideCategory, plan_execution
from scripts.play import SkyroadsFrontend
from skyroads.execution import catalog, configuration, coverage


def _plan(profile: str, composition: str):
    return plan_execution(configuration(profile, composition), coverage(), catalog())


def test_default_window_scale_fits_a_768_line_desktop() -> None:
    assert SkyroadsFrontend.default_scale == 2
    assert 200 * 1.2 * SkyroadsFrontend.default_scale <= 480


def test_oracle_plan_selects_only_the_untouched_exe() -> None:
    plan = _plan("development", "oracle")
    assert {item.implementation_id for item in plan.implementations} == {
        "baseline:interpreted-exe"
    }
    assert plan.configuration.selected_overrides == ()


def test_faithful_plan_selects_authored_replacements_explicitly() -> None:
    plan = _plan("verification", "faithful")
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


def test_default_play_is_fast_but_has_no_behavioral_modifications() -> None:
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


def test_release_plan_is_closed_world_and_exe_detached() -> None:
    plan = _plan("release", "cpuless")
    assert plan.report.standalone_executable_ready
    assert plan.report.package_ready
    assert not plan.report.exe_dependent
    assert not plan.report.interpreter_dependent
    assert {item.implementation_id for item in plan.implementations} == {
        "baseline:generated-cpuless"
    }
