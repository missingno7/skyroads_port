"""The SkyRoads planner is the only implementation-selection authority."""
from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from dos_re import player
from dos_re.cpu import CPU8086
from dos_re.execution import (
    ClosureFindingKind,
    DependencyCapability,
    ExecutionPlanError,
    GENERATED_VMLESS_CARRIER,
    INTERPRETED_CPU_CARRIER,
    ImplementationOrigin,
    OverrideCategory,
    plan_execution,
)
from dos_re.materialized_plan import (
    load_materialized_plan,
    write_materialized_plan,
)
from dos_re.memory import Memory
from dos_re.replay import ReplayEvent, ReplayPoint
from scripts.play import SkyroadsFrontend
from skyroads import execution as execution_model
from skyroads import hooks
from skyroads.bridge.dgroup_view import GameView
from skyroads.content_identity import content_digest
from skyroads.execution import (
    FRAME_PARK_SERVICE_ID,
    bootstrap_provider,
    catalog,
    configuration,
    coverage,
    features,
    provider_diagnostics,
    selected_whole_program_provider,
    services,
)
from skyroads.hooks import CODE_SEG
from skyroads.identities import (
    GAMEPLAY_REGION,
    IMAGE,
    PROGRAM_ROOT,
    execution_point_identity,
    function_identity,
)
from skyroads.pacing import (
    FADE_WAIT_COMPARE_IP,
    MENU_ANIM_WAIT_IP,
    MENU_SCENE_FRAME_IP,
    PACING_SPIN_IP,
    ROAD_DEPARTURE_WAIT_IP,
    install_frame_park,
)
from skyroads.product_features import (
    PRACTICE_FEATURE_CHANNEL,
    PRACTICE_LEVEL_FEATURE_ID,
    SkyroadsFeatureState,
)
from skyroads.presentation.features import NATIVE_3D_RENDERER_FEATURE_ID
from skyroads.launch_inputs import (
    DIRECT_LEVEL_ADAPTER_ID,
    LEVEL_SELECTION_IP,
    SELECTED_LEVEL_OFFSET,
    install_direct_level_launch,
)


FAITHFUL_IPS = {
    0x04C0,
    0x0F62,
    0x1732,
    0x3153,
    0x3190,
    0x325B,
    0x32C1,
    0x33FD,
    0x4052,
    0x3A22,
    0x66E6,
}
HEX_DIGEST = re.compile(r"[0-9a-f]{64}")


def _plan(profile: str, composition: str):
    return plan_execution(
        configuration(profile, composition),
        coverage(),
        catalog(),
        services(),
        features(),
    )


@pytest.fixture
def original_exe(tmp_path, monkeypatch):
    """Provide a hash-valid stand-in for planner-only tests.

    These tests exercise composition and selection, not executable behavior.
    Public CI intentionally cannot contain the proprietary SkyRoads image.
    """
    payload = b"synthetic planner fixture; not an executable\n"
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "SKYROADS.EXE").write_bytes(payload)
    monkeypatch.setattr(
        execution_model,
        "IMAGE",
        replace(
            execution_model.IMAGE,
            content_digest=sha256(payload).hexdigest(),
        ),
    )
    monkeypatch.setattr(execution_model, "ROOT", tmp_path)
    boot = tmp_path / "boot"
    boot.mkdir()
    (boot / "state.json").write_text("{}", encoding="utf-8")
    (boot / "memory_1mb.bin").write_bytes(b"\0")
    (boot / "manifest.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(execution_model, "BOOT_DIR", boot)


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
    plan = _plan("verification", "workbench-auto")
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
    faithful = {
        item.implementation_id
        for item in selected.values()
        if item.category is OverrideCategory.FAITHFUL
    }
    assert faithful == {
        entry.descriptor.implementation_id
        for entry in catalog().entries
        if entry.descriptor.category is OverrideCategory.FAITHFUL
    }
    assert {item.region_id for item in plan.regions} == {GAMEPLAY_REGION}


def test_default_play_is_fast_but_has_no_behavioral_modifications(
    original_exe,
) -> None:
    plan = _plan("development", "auto")
    categories = {
        item.category for item in plan.implementations
        if item.category is not None
    }
    assert OverrideCategory.FAITHFUL in categories
    assert OverrideCategory.ENHANCEMENT not in categories
    assert OverrideCategory.BEHAVIORAL not in categories
    assert selected_whole_program_provider(plan) == "baseline:generated-vmless"
    assert {item.region_id for item in plan.regions} == {GAMEPLAY_REGION}
    assert {item.service_id for item in plan.services} == {
        FRAME_PARK_SERVICE_ID,
    }


def test_faithful_product_composes_selected_faithful_adapters(
    original_exe,
) -> None:
    plan = _plan("development", "faithful-product")
    selected = {
        item.implementation_id for item in plan.implementations
    }
    assert "baseline:generated-vmless" in selected
    faithful_ids = execution_model.implementation_ids(OverrideCategory.FAITHFUL)
    assert set(faithful_ids) <= selected
    assert plan.configuration.selected_overrides == faithful_ids
    assert plan.report.execution_carrier == GENERATED_VMLESS_CARRIER
    assert plan.report.active_boundaries
    assert plan.report.collapsed_edge_count > 1_000

    faithful_functions = [
        entry for entry in catalog().entries
        if entry.descriptor.category is OverrideCategory.FAITHFUL
        and entry.descriptor.region_contract is None
    ]
    assert all(
        {adapter.carrier_id for adapter in entry.adapters} == {
            INTERPRETED_CPU_CARRIER,
            GENERATED_VMLESS_CARRIER,
        }
        for entry in faithful_functions
    )
    gameplay = next(
        entry for entry in catalog().entries
        if entry.descriptor.region_id == GAMEPLAY_REGION
    )
    assert gameplay.adapters == ()
    assert {
        adapter.host_carrier_id for adapter in gameplay.region_adapters
    } == {INTERPRETED_CPU_CARRIER, GENERATED_VMLESS_CARRIER}
    assert plan.regions[0].region_id == GAMEPLAY_REGION


def test_provider_diagnostics_expose_product_roles_and_true_seams(
    original_exe,
) -> None:
    plan = _plan("development", "faithful-product")
    report = provider_diagnostics(plan)

    assert report.frontend_provider == "baseline:generated-vmless"
    assert report.level_selection_provider == "baseline:generated-vmless"
    assert report.gameplay_provider == "faithful-region:skyroads.gameplay"
    assert report.renderer_provider == "faithful-region:skyroads.gameplay"
    assert report.covered_original_identities
    assert report.collapsed_internal_boundaries
    assert "service:sfx->function:1010:03c2" in report.remaining_external_seams
    assert report.selected_generated_fallbacks
    assert report.selected_interpreted_fallbacks == 0
    assert not report.exe_dependency
    assert report.dos_re_runtime_dependency


def test_provider_diagnostics_name_selected_presentation_owner(
    original_exe,
) -> None:
    plan = plan_execution(
        configuration(
            "development",
            "faithful-product",
            enabled_features=(NATIVE_3D_RENDERER_FEATURE_ID,),
        ),
        coverage(),
        catalog(),
        services(),
        features(),
    )

    assert provider_diagnostics(plan).renderer_provider == \
        NATIVE_3D_RENDERER_FEATURE_ID


def test_direct_level_is_a_one_shot_generated_menu_selection() -> None:
    cpu = CPU8086(Memory())
    cpu.s.cs = CODE_SEG
    cpu.s.ds = 0x1686
    cpu.s.ss = 0x1686
    cpu.s.sp = 0xB000
    cpu.push(0x0285)
    selected = lambda current_cpu: None
    key = (CODE_SEG, LEVEL_SELECTION_IP)
    cpu.replacement_hooks[key] = selected
    cpu.hook_names[key] = "selected-generated-loader"
    runtime = SimpleNamespace(cpu=cpu)

    install_direct_level_launch(runtime, 14)
    assert cpu.hook_names[key] == DIRECT_LEVEL_ADAPTER_ID
    cpu.replacement_hooks[key](cpu)

    assert cpu.mem.rw(cpu.s.ds, SELECTED_LEVEL_OFFSET) == 14
    assert (cpu.s.ax, cpu.s.ip, cpu.s.sp) == (0, 0x0285, 0xB000)
    assert cpu.replacement_hooks[key] is selected
    assert cpu.hook_names[key] == "selected-generated-loader"
    assert runtime._skyroads_direct_level_applied == 14


def test_faithful_product_is_oracle_verifiable(original_exe) -> None:
    plan = _plan("verification", "faithful-product")

    assert plan.report.bootstrap_profile_valid
    assert plan.configuration.verification_policy.oracle_required
    assert {item.region_id for item in plan.regions} == {GAMEPLAY_REGION}


def test_disabling_authored_candidates_falls_back_and_collapses_boundaries(
    original_exe,
) -> None:
    mixed = _plan("development", "faithful-product")
    fallback = plan_execution(
        configuration(
            "development", "faithful-product", include_authored=False,
        ),
        coverage(),
        catalog(),
        services(),
        features(),
    )

    assert fallback.configuration.selected_overrides == ()
    assert {item.implementation_id for item in fallback.implementations} == {
        "baseline:generated-vmless",
    }
    assert fallback.report.active_boundaries == ()
    assert fallback.report.collapsed_edge_count > (
        mixed.report.collapsed_edge_count
    )


def test_mixed_plan_materializes_without_runtime_selection(
    original_exe, tmp_path,
) -> None:
    plan = _plan("development", "faithful-product")
    payload = load_materialized_plan(write_materialized_plan(
        plan, tmp_path / "execution_plan.json",
    ))

    assert payload["execution_carrier"] == GENERATED_VMLESS_CARRIER
    assert payload["bindings"][PROGRAM_ROOT] == "baseline:generated-vmless"
    faithful_binding = next(
        implementation_id
        for implementation_id in payload["bindings"].values()
        if implementation_id.startswith("faithful:")
    )
    assert payload["implementations"][faithful_binding]["adapter"][
        "id"
    ].endswith("/generated-vmless")


def test_practice_feature_uses_replay_event_and_safe_game_state_boundary() -> None:
    state = SkyroadsFeatureState(features().features)
    recorded = []
    state.request_level_position(
        0x123,
        ordinal=7,
        record_event=lambda *event: recorded.append(event),
    )
    assert recorded[0][0:2] == (7, PRACTICE_FEATURE_CHANNEL)

    replayed = SkyroadsFeatureState(features().features)
    replayed.accept_replay_event(ReplayEvent(
        ReplayPoint(7, "skyroads-test"),
        0,
        recorded[0][1],
        recorded[0][2],
    ))
    runtime = SimpleNamespace(cpu=SimpleNamespace(
        mem=Memory(),
        s=SimpleNamespace(ds=0x1686),
    ))
    replayed.apply_main_loop_boundary(runtime)
    view = GameView(runtime.cpu.mem, base=0x1686 << 4)
    assert view.game_state == 2
    assert view.entered == 1
    assert view.ship_pos == 0x123


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
        _plan("release", "generated-detached")
    report = caught.value.report
    assert report.unresolved_edges
    assert not report.missing_bootstrap_artifacts
    assert report.is_detached_from("original-exe")
    assert report.is_detached_from("interpreter")
    assert report.bootstrap_provider_id == (
        "skyroads-generated-detached-build-image"
    )
    assert not report.package_ready
    assert "unresolved control-flow edges" in str(caught.value)


def test_detached_plan_classifies_generated_internal_and_real_uncertainty(
    tmp_path, monkeypatch,
) -> None:
    boot = tmp_path / "boot"
    boot.mkdir()
    (boot / "state.json").write_text("{}", encoding="utf-8")
    (boot / "memory_1mb.bin").write_bytes(b"\0")
    (boot / "manifest.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(execution_model, "BOOT_DIR", boot)

    plan = _plan("detached", "faithful-product")
    assert plan.report.is_detached_from("original-exe")
    assert plan.report.is_detached_from("interpreter")
    assert plan.configuration.execution_policy.fallback.value == "forbidden"
    assert plan.report.unresolved_edges
    assert {
        item.classification
        for item in plan.report.closure_findings if item.blocking
    } == {
        ClosureFindingKind.UNKNOWN_DYNAMIC_TARGET,
        ClosureFindingKind.PROBABLE_GAP,
    }
    generated_internal = next(
        item for item in plan.report.closure_findings
        if item.target == execution_point_identity(0x630F)
    )
    assert generated_internal.classification is (
        ClosureFindingKind.SELECTED_IMPLEMENTATION_OWNED
    )
    assert not generated_internal.blocking


def test_release_plan_fails_before_launch_when_bootstrap_is_missing(
    tmp_path, monkeypatch,
) -> None:
    monkeypatch.setattr(execution_model, "BOOT_DIR", tmp_path / "missing")
    with pytest.raises(ExecutionPlanError) as caught:
        _plan("release", "generated-detached")
    message = str(caught.value)
    assert "missing bootstrap artifacts" in message
    assert "python scripts/build_boot_image.py" in message


def test_authored_catalog_contains_only_complete_semantic_adapter_pairs() -> None:
    assert set(hooks.FAITHFUL_OVERRIDE_ADAPTERS) == FAITHFUL_IPS
    assert all(
        len(declaration) == 3
        for declaration in hooks.FAITHFUL_OVERRIDE_ADAPTERS.values()
    )

    faithful_entries = {
        next(iter(entry.descriptor.targets)): entry
        for entry in catalog().entries
        if entry.descriptor.origin is ImplementationOrigin.AUTHORED
        and entry.descriptor.region_contract is None
    }
    assert set(faithful_entries) == {
        function_identity(ip) for ip in FAITHFUL_IPS
    }
    assert all(
        entry.descriptor.category is OverrideCategory.FAITHFUL
        for entry in faithful_entries.values()
    )

    gameplay = next(
        entry for entry in catalog().entries
        if entry.descriptor.region_id == GAMEPLAY_REGION
    )
    assert gameplay.descriptor.category is OverrideCategory.FAITHFUL
    assert gameplay.descriptor.region_contract is not None
    assert gameplay.descriptor.region_contract.verification is not None
    assert (
        gameplay.descriptor.region_contract.verification.contract_id
        == "skyroads:gameplay-region-faithful/v1"
    )
    assert gameplay.adapters == ()
    assert {
        adapter.host_carrier_id for adapter in gameplay.region_adapters
    } == {INTERPRETED_CPU_CARRIER, GENERATED_VMLESS_CARRIER}

    for ip, (name, semantic, adapter) in hooks.FAITHFUL_OVERRIDE_ADAPTERS.items():
        entry = faithful_entries[function_identity(ip)]
        assert entry.implementation is semantic
        runtime = SimpleNamespace(cpu=SimpleNamespace(
            replacement_hooks={},
            hook_names={},
        ))
        cpu_adapter = next(
            adapter for adapter in entry.adapters
            if adapter.carrier_id == INTERPRETED_CPU_CARRIER
        )
        cpu_adapter.activate(runtime, tuple(entry.descriptor.targets))
        assert runtime.cpu.replacement_hooks[(CODE_SEG, ip)] is adapter
        assert runtime.cpu.hook_names[(CODE_SEG, ip)] == name


def test_frame_park_service_preserves_selected_boundary_implementations() -> None:
    fade_4344 = object()
    fade_434a = object()
    cpu = SimpleNamespace(
        replacement_hooks={
            (CODE_SEG, 0x4344): fade_4344,
            (CODE_SEG, 0x434A): fade_434a,
        },
        hook_names={
            (CODE_SEG, 0x4344): "existing-fade-4344",
            (CODE_SEG, 0x434A): "existing-fade-434a",
        },
    )
    install_frame_park(SimpleNamespace(cpu=cpu))

    assert set(cpu.replacement_hooks) == {
        (CODE_SEG, PACING_SPIN_IP),
        (CODE_SEG, MENU_ANIM_WAIT_IP),
        (CODE_SEG, ROAD_DEPARTURE_WAIT_IP),
        (CODE_SEG, FADE_WAIT_COMPARE_IP),
        (CODE_SEG, MENU_SCENE_FRAME_IP),
        (CODE_SEG, 0x4344),
        (CODE_SEG, 0x434A),
    }
    assert cpu.replacement_hooks[(CODE_SEG, 0x4344)] is fade_4344
    assert cpu.replacement_hooks[(CODE_SEG, 0x434A)] is fade_434a
    assert cpu.hook_names[(CODE_SEG, 0x4344)] == "existing-fade-4344"
    assert cpu.hook_names[(CODE_SEG, 0x434A)] == "existing-fade-434a"


def test_player_options_are_declared_as_plan_capabilities() -> None:
    frontend = SkyroadsFrontend(execution_model.SOURCE_ROOT)
    parser = player.build_arg_parser(frontend)
    args = parser.parse_args(["--play-replay", "example-replay"])
    requested = frontend.execution_configuration(args).requested_capabilities
    assert requested == frozenset({
        DependencyCapability.REPLAY.value,
        DependencyCapability.SNAPSHOTS.value,
    })


def test_execution_descriptors_are_content_addressed() -> None:
    assert all(
        HEX_DIGEST.fullmatch(entry.descriptor.implementation_digest)
        for entry in catalog().entries
    )
    assert all(
        HEX_DIGEST.fullmatch(adapter.adapter_digest)
        for entry in catalog().entries
        for adapter in entry.adapters
    )
    assert all(
        HEX_DIGEST.fullmatch(service.implementation_digest)
        for service in services().services
    )
    assert all(
        HEX_DIGEST.fullmatch(feature.feature_digest)
        for feature in features().features
    )
    for composition in (
        "oracle", "faithful-product", "generated-detached",
    ):
        provider = bootstrap_provider(composition)
        assert HEX_DIGEST.fullmatch(provider.provider_digest)
        for artifact in provider.artifacts:
            if artifact.source_path and Path(artifact.source_path).is_file():
                assert HEX_DIGEST.fullmatch(artifact.expected_sha256)
    assert bootstrap_provider("oracle").artifacts[0].expected_sha256 == (
        IMAGE.content_digest
    )


def test_content_digest_is_location_independent_and_byte_sensitive(
    tmp_path,
) -> None:
    left = tmp_path / "left"
    right = tmp_path / "right"
    left.mkdir()
    right.mkdir()
    (left / "implementation.py").write_bytes(b"result = 1\n")
    (right / "implementation.py").write_bytes(b"result = 1\n")

    first = content_digest(
        ("implementation.py",), repository_root=left,
    )
    assert first == content_digest(
        ("implementation.py",), repository_root=right,
    )
    (right / "implementation.py").write_bytes(b"result = 2\n")
    assert first != content_digest(
        ("implementation.py",), repository_root=right,
    )
