"""SkyRoads execution composition for the dos_re 3.0 planner.

This is the single authority for baseline providers, authored overrides,
coverage and composition presets.  Backend modules contain mechanics only;
they do not select themselves or install implementations by import side effect.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Iterable

from dos_re.execution import (
    BootstrapArtifact,
    BuildImageBootstrapProvider,
    BuildTarget,
    DependencyCapability,
    ExecutionConfiguration,
    ExeBootstrapProvider,
    ImplementationCatalog,
    ImplementationDescriptor,
    ImplementationEntry,
    ImplementationOrigin,
    OverrideCategory,
    ProgramCoverage,
    profile_configuration,
)

PROGRAM_ID = "skyroads:1.0"
PROGRAM_ROOT = f"{PROGRAM_ID}:program"
CODE_SEG = 0x1010
ROOT = Path(__file__).resolve().parents[1]
BOOT_DIR = ROOT / "artifacts" / "boot_image"
BOOTSTRAP_INSTRUCTION = "run: python scripts/build_boot_image.py"


def function_identity(offset: int) -> str:
    """Stable identity shared by replay metadata and the future atlas."""
    return f"{PROGRAM_ID}:function:{CODE_SEG:04x}:{offset:04x}"


def _hook_tables():
    from skyroads.hooks import (
        BEHAVIORAL_OVERRIDE_ADAPTERS,
        FAITHFUL_OVERRIDE_ADAPTERS,
        GENERATED_FUNCTION_ADAPTERS,
    )
    return (
        FAITHFUL_OVERRIDE_ADAPTERS,
        BEHAVIORAL_OVERRIDE_ADAPTERS,
        GENERATED_FUNCTION_ADAPTERS,
    )


def coverage() -> ProgramCoverage:
    faithful, behavioral, generated = _hook_tables()
    from skyroads.pacing import MENU_ANIM_WAIT_IP, PACING_SPIN_IP
    offsets = (
        set(faithful) | set(behavioral) | set(generated)
        | {PACING_SPIN_IP, MENU_ANIM_WAIT_IP}
    )
    reachable = {PROGRAM_ROOT, *(function_identity(ip) for ip in offsets)}
    return ProgramCoverage(
        roots=(PROGRAM_ROOT,),
        reachable=frozenset(reachable),
        evidence_identity="skyroads-recovery-ir-2026-07-20",
    )


def _activate_cpu_hook(ip: int, name: str, implementation):
    def activate(runtime, targets: tuple[str, ...]) -> None:
        expected = function_identity(ip)
        if targets != (expected,):
            raise RuntimeError(
                f"{name} activation expected {expected}, got {targets!r}"
            )
        key = (CODE_SEG, ip)
        runtime.cpu.replacement_hooks[key] = implementation
        runtime.cpu.hook_names[key] = name
    return activate


def _function_entries(
    table: dict[int, tuple[str, object]],
    *,
    origin: ImplementationOrigin,
    category: OverrideCategory,
    prefix: str,
) -> Iterable[ImplementationEntry]:
    for ip, (name, implementation) in sorted(table.items()):
        implementation_id = f"{prefix}:{CODE_SEG:04x}:{ip:04x}:{name}"
        yield ImplementationEntry(
            ImplementationDescriptor(
                implementation_id=implementation_id,
                targets=frozenset({function_identity(ip)}),
                origin=origin,
                category=category,
                properties=frozenset({"cpu-adapted", "dos-memory-backed"}),
                required_capabilities=frozenset({
                    DependencyCapability.CPU_MODEL.value,
                    DependencyCapability.DOS_MEMORY.value,
                    DependencyCapability.DOS_SERVICES.value,
                    DependencyCapability.DOS_RE_RUNTIME.value,
                }),
                implementation_digest=f"skyroads-{name}-v1",
            ),
            implementation=implementation,
            activate=_activate_cpu_hook(ip, name, implementation),
        )


def catalog() -> ImplementationCatalog:
    faithful, behavioral, generated = _hook_tables()
    all_targets = coverage().reachable
    entries: list[ImplementationEntry] = [
        ImplementationEntry(ImplementationDescriptor(
            implementation_id="baseline:interpreted-exe",
            targets=all_targets,
            origin=ImplementationOrigin.INTERPRETED,
            properties=frozenset({"cpu-backed", "dos-memory-backed"}),
            required_capabilities=frozenset({
                DependencyCapability.ORIGINAL_EXE.value,
                DependencyCapability.ORIGINAL_CODE.value,
                DependencyCapability.INTERPRETER.value,
                DependencyCapability.CPU_MODEL.value,
                DependencyCapability.DOS_MEMORY.value,
                DependencyCapability.DOS_SERVICES.value,
                DependencyCapability.DOS_RE_RUNTIME.value,
            }),
            implementation_digest="skyroads-exe-interpreter-v1",
        )),
        ImplementationEntry(ImplementationDescriptor(
            implementation_id="baseline:generated-vmless",
            targets=all_targets,
            origin=ImplementationOrigin.GENERATED,
            properties=frozenset({
                "cpu-backed", "vmless", "dos-memory-backed", "exe-detached",
            }),
            required_capabilities=frozenset({
                DependencyCapability.CPU_MODEL.value,
                DependencyCapability.DOS_MEMORY.value,
                DependencyCapability.DOS_SERVICES.value,
                DependencyCapability.DOS_RE_RUNTIME.value,
            }),
            implementation_digest="skyroads-generated-vmless-corpus-v1",
            region_id=PROGRAM_ROOT,
        )),
        ImplementationEntry(ImplementationDescriptor(
            implementation_id="baseline:generated-cpuless",
            targets=all_targets,
            origin=ImplementationOrigin.GENERATED,
            properties=frozenset({
                "cpuless", "abi-recovered", "dos-memory-backed", "exe-detached",
            }),
            required_capabilities=frozenset({
                DependencyCapability.DOS_MEMORY.value,
                DependencyCapability.DOS_SERVICES.value,
                DependencyCapability.DOS_RE_RUNTIME.value,
            }),
            implementation_digest="skyroads-generated-cpuless-corpus-v1",
            region_id=PROGRAM_ROOT,
        )),
    ]
    entries.extend(_function_entries(
        generated, origin=ImplementationOrigin.GENERATED,
        category=OverrideCategory.BASELINE, prefix="generated-function"))
    entries.extend(_function_entries(
        faithful, origin=ImplementationOrigin.AUTHORED,
        category=OverrideCategory.FAITHFUL, prefix="faithful"))
    entries.extend(_function_entries(
        {ip: value for ip, value in behavioral.items() if ip != 0x434A},
        origin=ImplementationOrigin.AUTHORED,
        category=OverrideCategory.BEHAVIORAL, prefix="behavioral"))
    from skyroads.pacing import (
        FADE_WAIT_IP,
        MENU_ANIM_WAIT_IP,
        PACING_SPIN_IP,
        install_frame_park,
    )
    park_targets = frozenset(function_identity(ip) for ip in (
        PACING_SPIN_IP, FADE_WAIT_IP, MENU_ANIM_WAIT_IP))
    entries.append(ImplementationEntry(
        ImplementationDescriptor(
            implementation_id="enhancement:frame-park",
            targets=park_targets,
            origin=ImplementationOrigin.AUTHORED,
            category=OverrideCategory.ENHANCEMENT,
            properties=frozenset({"cpu-adapted", "dos-memory-backed"}),
            required_capabilities=frozenset({
                DependencyCapability.CPU_MODEL.value,
                DependencyCapability.DOS_MEMORY.value,
                DependencyCapability.DOS_SERVICES.value,
                DependencyCapability.DOS_RE_RUNTIME.value,
            }),
            implementation_digest="skyroads-frame-park-v1",
            region_id="skyroads:frame-pacing",
        ),
        implementation=install_frame_park,
        activate=lambda runtime, targets: install_frame_park(runtime),
    ))
    return ImplementationCatalog(tuple(entries))


def implementation_ids(category: OverrideCategory) -> tuple[str, ...]:
    return tuple(
        entry.descriptor.implementation_id
        for entry in catalog().entries
        if entry.descriptor.category is category
        and entry.descriptor.origin is ImplementationOrigin.AUTHORED
    )


def generated_function_ids() -> tuple[str, ...]:
    return tuple(
        entry.descriptor.implementation_id
        for entry in catalog().entries
        if entry.descriptor.implementation_id.startswith("generated-function:")
    )


def bootstrap_provider(composition: str):
    if composition in {"oracle", "faithful", "play", "behavioral"}:
        exe = ROOT / "assets" / "SKYROADS.EXE"
        return ExeBootstrapProvider(
            provider_id="skyroads-exe-bootstrap",
            state_outputs=(
                "loaded SkyRoads process",
                "CPU and DOS continuation state",
            ),
            artifacts=(BootstrapArtifact(
                artifact_id="skyroads-exe",
                runtime_path="SKYROADS.EXE",
                source_path=str(exe),
                generation_instruction=(
                    "place the original SKYROADS.EXE under assets/"
                ),
            ),),
            runtime_required_capabilities=frozenset({
                DependencyCapability.INTERPRETER.value,
                DependencyCapability.CPU_MODEL.value,
                DependencyCapability.DOS_MEMORY.value,
                DependencyCapability.DOS_SERVICES.value,
                DependencyCapability.DOS_RE_RUNTIME.value,
            }),
            initialized_capabilities=frozenset({
                DependencyCapability.CPU_MODEL.value,
                DependencyCapability.DOS_MEMORY.value,
                DependencyCapability.DOS_SERVICES.value,
            }),
            valid_profiles=frozenset({"development", "verification"}),
            provider_digest="skyroads-exe-bootstrap-v1",
        )

    runtime_capabilities = {
        DependencyCapability.DOS_MEMORY.value,
        DependencyCapability.DOS_SERVICES.value,
        DependencyCapability.DOS_RE_RUNTIME.value,
    }
    if composition == "vmless":
        runtime_capabilities.add(DependencyCapability.CPU_MODEL.value)
    return BuildImageBootstrapProvider(
        provider_id=f"skyroads-{composition}-build-image",
        state_outputs=(
            "1 MiB DOS memory image",
            "CPU/register seed",
            "DOS and device continuation state",
        ),
        artifacts=tuple(
            BootstrapArtifact(
                artifact_id=f"skyroads-boot-{name}",
                runtime_path=f"artifacts/boot_image/{filename}",
                source_path=str(BOOT_DIR / filename),
                generation_instruction=BOOTSTRAP_INSTRUCTION,
            )
            for name, filename in (
                ("state", "state.json"),
                ("memory", "memory_1mb.bin"),
                ("manifest", "manifest.json"),
            )
        ),
        build_required_capabilities=frozenset({
            DependencyCapability.ORIGINAL_EXE.value,
        }),
        runtime_required_capabilities=frozenset(runtime_capabilities),
        initialized_capabilities=frozenset({
            DependencyCapability.CPU_MODEL.value,
            DependencyCapability.DOS_MEMORY.value,
            DependencyCapability.DOS_SERVICES.value,
        }),
        valid_profiles=frozenset({"development", "detached", "release"}),
        provider_digest=f"skyroads-{composition}-build-image-v1",
    )


def configuration(
    profile: str,
    composition: str,
    *,
    build_platform: str = "windows",
) -> ExecutionConfiguration:
    """Map a product composition onto dos_re's orthogonal policy axes."""
    if composition == "auto":
        composition = "cpuless" if profile in {"detached", "release"} else (
            "faithful" if profile == "verification" else "play"
        )
    preferences: tuple[str, ...]
    selected: tuple[str, ...] = ()
    if composition == "oracle":
        preferences = ("baseline:interpreted-exe",)
    elif composition == "faithful":
        selected = implementation_ids(OverrideCategory.FAITHFUL)
        preferences = (
            *selected, *generated_function_ids(), "baseline:interpreted-exe",
        )
    elif composition == "play":
        selected = (
            *implementation_ids(OverrideCategory.FAITHFUL),
            *implementation_ids(OverrideCategory.ENHANCEMENT),
        )
        preferences = (
            *selected, *generated_function_ids(), "baseline:interpreted-exe",
        )
    elif composition == "behavioral":
        selected = (
            *implementation_ids(OverrideCategory.FAITHFUL),
            *implementation_ids(OverrideCategory.ENHANCEMENT),
            *implementation_ids(OverrideCategory.BEHAVIORAL),
        )
        preferences = (
            *selected, *generated_function_ids(), "baseline:interpreted-exe",
        )
    elif composition == "vmless":
        preferences = ("baseline:generated-vmless",)
    elif composition == "cpuless":
        preferences = ("baseline:generated-cpuless",)
    else:
        raise ValueError(f"unknown SkyRoads composition {composition!r}")

    target = (
        BuildTarget(build_platform, "standalone")
        if profile == "release" else None
    )
    config = profile_configuration(
        profile,
        program_identity=PROGRAM_ID,
        product_profile="game",
        provider_preference=preferences,
        selected_overrides=selected,
        bootstrap_provider=bootstrap_provider(composition),
        build_target=target,
    )
    # Preserve the resolved product composition in the profile identity without
    # adding another mutable configuration authority.
    return replace(config, product_profile=f"game/{composition}")


def selected_whole_program_provider(plan) -> str:
    for binding in plan.bindings:
        if binding.target == PROGRAM_ROOT:
            return binding.implementation_id
    raise RuntimeError("execution plan has no SkyRoads program-root binding")
