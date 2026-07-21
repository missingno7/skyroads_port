"""The SkyRoads planner is the only implementation-selection authority."""
from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from dos_re import player
from dos_re.execution import (
    CPU_MODEL_BACKEND,
    DependencyCapability,
    ExecutionPlanError,
    ImplementationOrigin,
    OverrideCategory,
    plan_execution,
)
from scripts.play import SkyroadsFrontend
from skyroads import execution as execution_model
from skyroads import hooks
from skyroads.content_identity import content_digest
from skyroads.execution import (
    FRAME_PARK_SERVICE_ID,
    bootstrap_provider,
    catalog,
    configuration,
    coverage,
    services,
)
from skyroads.hooks import CODE_SEG
from skyroads.identities import IMAGE, function_identity
from skyroads.pacing import (
    MENU_ANIM_WAIT_IP,
    PACING_SPIN_IP,
    install_frame_park,
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
    0x3A22,
}
HEX_DIGEST = re.compile(r"[0-9a-f]{64}")


def _plan(profile: str, composition: str):
    return plan_execution(
        configuration(profile, composition),
        coverage(),
        catalog(),
        services(),
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
    assert {service.service_id for service in plan.services} == {
        FRAME_PARK_SERVICE_ID
    }
    assert all(service.product_safe for service in plan.services)


def test_generated_cpu_composes_selected_faithful_adapters(
    original_exe,
) -> None:
    plan = _plan("development", "generated-cpu")
    selected = {
        item.implementation_id for item in plan.implementations
    }
    assert "baseline:generated-vmless" in selected
    faithful_ids = execution_model.implementation_ids(OverrideCategory.FAITHFUL)
    assert set(faithful_ids) <= selected
    assert plan.configuration.selected_overrides == faithful_ids


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
    }
    assert set(faithful_entries) == {
        function_identity(ip) for ip in FAITHFUL_IPS
    }
    assert all(
        entry.descriptor.category is OverrideCategory.FAITHFUL
        for entry in faithful_entries.values()
    )

    for ip, (name, semantic, adapter) in hooks.FAITHFUL_OVERRIDE_ADAPTERS.items():
        entry = faithful_entries[function_identity(ip)]
        assert entry.implementation is semantic
        runtime = SimpleNamespace(cpu=SimpleNamespace(
            replacement_hooks={},
            hook_names={},
        ))
        cpu_adapter = next(
            adapter for adapter in entry.adapters
            if adapter.backend_id == CPU_MODEL_BACKEND
        )
        cpu_adapter.activate(runtime, tuple(entry.descriptor.targets))
        assert runtime.cpu.replacement_hooks[(CODE_SEG, ip)] is adapter
        assert runtime.cpu.hook_names[(CODE_SEG, ip)] == name


def test_frame_park_service_installs_only_the_two_empty_waits() -> None:
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
        HEX_DIGEST.fullmatch(service.implementation_digest)
        for service in services().services
    )
    for composition in ("oracle", "generated-cpu", "generated-abi"):
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
