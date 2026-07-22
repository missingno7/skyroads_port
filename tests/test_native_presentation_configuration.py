from __future__ import annotations

from pathlib import Path

import pytest

from dos_re import player
from dos_re.execution import FeatureCategory

from scripts.play import SkyroadsFrontend
from skyroads.presentation.features import PRESENTATION_FEATURE_IDS


ROOT = Path(__file__).resolve().parents[1]


def test_native_presentation_is_selected_as_non_authoritative_plan_features() -> None:
    frontend = SkyroadsFrontend(ROOT)
    args = player.build_arg_parser(frontend).parse_args([
        "--composition", "faithful-product", "--renderer", "native-3d",
        "--widescreen", "--tweening", "--audio", "native-stereo",
        "--simulation-hz", "30", "--present-hz", "90",
    ])
    plan = frontend.resolve_execution_plan(args)

    selected = {item.feature_id: item for item in plan.features}
    assert set(PRESENTATION_FEATURE_IDS) <= set(selected)
    assert all(
        selected[item].category is FeatureCategory.PRESENTATION
        and not selected[item].changes_authoritative_state
        for item in PRESENTATION_FEATURE_IDS
    )


def test_faithful_audio_does_not_select_the_stereo_enhancement() -> None:
    frontend = SkyroadsFrontend(ROOT)
    args = player.build_arg_parser(frontend).parse_args([
        "--audio", "native-faithful",
    ])
    selected = {
        item.feature_id for item in frontend.resolve_execution_plan(args).features
    }
    from skyroads.presentation.features import (
        ENHANCED_STEREO_AUDIO_FEATURE_ID,
        NATIVE_FAITHFUL_AUDIO_FEATURE_ID,
    )
    assert NATIVE_FAITHFUL_AUDIO_FEATURE_ID in selected
    assert ENHANCED_STEREO_AUDIO_FEATURE_ID not in selected


def test_native_renderer_rejects_generated_only_composition() -> None:
    frontend = SkyroadsFrontend(ROOT)
    args = player.build_arg_parser(frontend).parse_args([
        "--profile", "detached", "--renderer", "native-3d",
    ])
    with pytest.raises(ValueError, match="faithful native gameplay island"):
        frontend.execution_configuration(args)


def test_native_renderer_selects_the_moderngl_presenter_only_when_requested() -> None:
    frontend = SkyroadsFrontend(ROOT)
    original = player.build_arg_parser(frontend).parse_args([])
    requested = player.build_arg_parser(frontend).parse_args([
        "--renderer", "native-3d",
    ])

    assert frontend.create_gpu_frame_presenter(None, original) is None
    presenter = frontend.create_gpu_frame_presenter(None, requested)
    assert type(presenter).__name__ == "ModernGLFramePresenter"


def test_host_presentation_rate_does_not_change_skyroads_game_clock() -> None:
    frontend = SkyroadsFrontend(ROOT)
    defaults = player.build_arg_parser(frontend).parse_args([])
    faster_display = player.build_arg_parser(frontend).parse_args([
        "--present-hz", "50",
    ])

    assert defaults.simulation_hz == faster_display.simulation_hz == 30
    assert defaults.present_hz == 60
    assert faster_display.present_hz == 50
    assert faster_display.timer_irqs_per_frame == 6
