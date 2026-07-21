"""SkyRoads execution composition for the dos_re 3.0 planner.

This is the single authority for baseline providers, authored overrides,
coverage and composition presets.  Backend modules contain mechanics only;
they do not select themselves or install implementations by import side effect.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from hashlib import sha256
import json
from pathlib import Path
from typing import Iterable

from dos_re.atlas import ExecutionAtlas
from dos_re.execution import (
    BackendAdapter,
    BootstrapArtifact,
    BuildImageBootstrapProvider,
    BuildTarget,
    DependencyCapability,
    EvidenceGrade,
    ExecutionConfiguration,
    ExecutionRegionContract,
    ExeBootstrapProvider,
    FeatureCatalog,
    FeatureCategory,
    FeatureDescriptor,
    GENERATED_CPULESS_CARRIER,
    GENERATED_VMLESS_CARRIER,
    INTERPRETED_CPU_CARRIER,
    DOS_MEMORY_CARRIER,
    ImplementationContract,
    ImplementationCatalog,
    ImplementationDescriptor,
    ImplementationEntry,
    ImplementationOrigin,
    OverrideCategory,
    RecoveryLevel,
    RegionAdapter,
    RegionEntryPoint,
    RegionExitPoint,
    RegionStateOwnership,
    RuntimeServiceCatalog,
    RuntimeServiceDescriptor,
    profile_configuration,
)

from skyroads.content_identity import (
    callable_digest,
    content_digest,
    source_path,
    tree_sources,
)
from skyroads.authored_inventory import runtime_source_paths
from skyroads.identities import (
    CODE_SEG,
    GAMEPLAY_ENTRY_POINT,
    GAMEPLAY_RESUME_POINT,
    GAMEPLAY_CALLER_CONTINUATION,
    GAMEPLAY_ROAD_DEPARTURE_CONTINUATION,
    GAMEPLAY_REGION,
    IMAGE,
    PROGRAM_ID,
    PROGRAM_ROOT,
    execution_point_identity,
    function_identity,
)
from skyroads.gameplay_region import (
    GAMEPLAY_ABORTED_EXIT,
    GAMEPLAY_ENTRY_ID,
    GAMEPLAY_RESUME_ENTRY_ID,
    GAMEPLAY_RESULT_EXIT,
    GAMEPLAY_TICK_BOUNDARY,
    ROAD_DEPARTURE_EXIT,
    activate_gameplay_region,
)
from skyroads.native.loop import native_gameplay_body
from skyroads.product_features import (
    FEATURE_SAFE_BOUNDARY,
    PRACTICE_FEATURE_CHANNEL,
    PRACTICE_LEVEL_FEATURE_ID,
)

SOURCE_ROOT = Path(__file__).resolve().parents[1]
ROOT = SOURCE_ROOT
ATLAS_DIR = ROOT / "recovery" / "atlas"
BOOT_DIR = ROOT / "artifacts" / "boot_image"
BOOTSTRAP_INSTRUCTION = "run: python scripts/build_boot_image.py"
FRAME_PARK_SERVICE_ID = "skyroads:frame-park"
_REAL_MODE_ADAPTER_SOURCES = (
    SOURCE_ROOT / "dos_re" / "dos_re" / "hooks.py",
    SOURCE_ROOT / "dos_re" / "dos_re" / "lift" / "runtime.py",
)

_GAMEPLAY_COVERED_OFFSETS = frozenset({
    0x04C0, 0x074C, 0x0C98, 0x12F8, 0x1732, 0x186B, 0x1B49,
    0x1C62, 0x1CCD, 0x1DFA, 0x1FD9, 0x2D1F, 0x3153, 0x3190,
    0x325B, 0x33FD, 0x34AE, 0x39D4, 0x3A22, 0x5D4C, 0x5D8C, 0x5E5A,
})

def _hook_tables():
    from skyroads.hooks import (
        FAITHFUL_OVERRIDE_ADAPTERS,
        GENERATED_FUNCTION_ADAPTERS,
    )
    return (
        FAITHFUL_OVERRIDE_ADAPTERS,
        GENERATED_FUNCTION_ADAPTERS,
    )


def coverage() -> ExecutionAtlas:
    """The persistent Atlas is the sole reachability authority."""
    try:
        return ExecutionAtlas.open(ATLAS_DIR)
    except FileNotFoundError as exc:
        raise RuntimeError(
            "SkyRoads Execution Atlas is missing; run "
            "`python scripts/build_atlas.py --from-ir`"
        ) from exc


def _activate_cpu_hook(ip: int, name: str, adapter, identity):
    def activate(runtime, targets: tuple[str, ...]) -> None:
        expected = identity(ip)
        if targets != (expected,):
            raise RuntimeError(
                f"{name} activation expected {expected}, got {targets!r}"
            )
        key = (CODE_SEG, ip)
        runtime.cpu.replacement_hooks[key] = adapter
        runtime.cpu.hook_names[key] = name
    return activate


def _real_mode_contract(target: str) -> ImplementationContract:
    return ImplementationContract(
        contract_id=f"{target}:real-mode-call/v1",
        inputs=("adapter-declared registers/stack/DOS memory",),
        outputs=("adapter-declared registers/DOS memory", "continuation"),
        observable_effects=("DOS memory", "CPU flags", "device I/O when declared"),
        state_authority="DOS continuation state",
        preservation=("unmentioned continuation state",),
    )


def _adapter_digest(target: str, carrier: str, adapter) -> str:
    return content_digest(
        (source_path(adapter), *_REAL_MODE_ADAPTER_SOURCES),
        repository_root=SOURCE_ROOT,
        records=(("target", target), ("carrier", carrier)),
    )


def _generated_function_entries(
    table: dict[int, tuple[str, object]],
) -> Iterable[ImplementationEntry]:
    for ip, (name, implementation) in sorted(table.items()):
        target = function_identity(ip)
        implementation_id = (
            f"generated-function:{CODE_SEG:04x}:{ip:04x}:{name}"
        )
        yield ImplementationEntry(
            ImplementationDescriptor(
                implementation_id=implementation_id,
                targets=frozenset({target}),
                origin=ImplementationOrigin.GENERATED,
                category=OverrideCategory.BASELINE,
                recovery_level=RecoveryLevel.GENERATED_VMLESS,
                contract=_real_mode_contract(target),
                properties=frozenset({"cpu-adapted", "dos-memory-backed"}),
                required_capabilities=frozenset({
                    DependencyCapability.CPU_MODEL.value,
                    DependencyCapability.DOS_MEMORY.value,
                    DependencyCapability.DOS_SERVICES.value,
                    DependencyCapability.DOS_RE_RUNTIME.value,
                }),
                implementation_digest=content_digest((
                    source_path(implementation),
                    *_REAL_MODE_ADAPTER_SOURCES,
                ), repository_root=SOURCE_ROOT),
            ),
            implementation=implementation,
            adapters=(BackendAdapter(
                f"{implementation_id}/interpreted-cpu",
                INTERPRETED_CPU_CARRIER,
                _activate_cpu_hook(ip, name, implementation, function_identity),
                _adapter_digest(target, INTERPRETED_CPU_CARRIER, implementation),
            ),),
        )


def _authored_entries(
    table: dict[int, tuple[str, object, object]],
    *,
    category: OverrideCategory,
    prefix: str,
    execution_points: frozenset[int] = frozenset(),
) -> Iterable[ImplementationEntry]:
    for ip, (name, semantic, adapter) in sorted(table.items()):
        identity = execution_point_identity if ip in execution_points else (
            function_identity
        )
        implementation_id = f"{prefix}:{CODE_SEG:04x}:{ip:04x}:{name}"
        target = identity(ip)
        activate = _activate_cpu_hook(ip, name, adapter, identity)
        yield ImplementationEntry(
            ImplementationDescriptor(
                implementation_id=implementation_id,
                targets=frozenset({target}),
                origin=ImplementationOrigin.AUTHORED,
                category=category,
                recovery_level=RecoveryLevel.AUTHORED_NATIVE,
                evidence_grade=EvidenceGrade.FOCUSED,
                verification_evidence=frozenset({
                    f"skyroads:focused-oracle:{CODE_SEG:04x}:{ip:04x}",
                }),
                contract=_real_mode_contract(target),
                properties=frozenset({
                    "semantic-cpuless",
                    "cpu-adapted",
                    "dos-memory-backed",
                }),
                required_capabilities=frozenset({
                    DependencyCapability.CPU_MODEL.value,
                    DependencyCapability.DOS_MEMORY.value,
                    DependencyCapability.DOS_SERVICES.value,
                    DependencyCapability.DOS_RE_RUNTIME.value,
                }),
                implementation_digest=callable_digest(
                    semantic, adapter, repository_root=SOURCE_ROOT,
                ),
            ),
            implementation=semantic,
            adapters=(
                BackendAdapter(
                    f"{implementation_id}/interpreted-cpu",
                    INTERPRETED_CPU_CARRIER,
                    activate,
                    _adapter_digest(target, INTERPRETED_CPU_CARRIER, adapter),
                ),
                BackendAdapter(
                    f"{implementation_id}/generated-vmless",
                    GENERATED_VMLESS_CARRIER,
                    activate,
                    _adapter_digest(target, GENERATED_VMLESS_CARRIER, adapter),
                ),
            ),
        )


def _vmless_content_digest() -> str:
    return content_digest((
        SOURCE_ROOT / "skyroads" / "vmless_backend.py",
        SOURCE_ROOT / "skyroads" / "runtime.py",
        SOURCE_ROOT / "recovery" / "recovery_ir.json",
        *tree_sources(SOURCE_ROOT / "skyroads" / "lifted" / "functions"),
        *_REAL_MODE_ADAPTER_SOURCES,
    ), repository_root=SOURCE_ROOT)


def _vmless_owned_targets(atlas, program_coverage) -> frozenset[str]:
    """Include emitted internal points that are not separate Atlas functions.

    Static recovery deliberately leaves calls such as ``0069 -> 630F`` as
    frontiers because ``630F`` is not a recovered function entry. The selected
    generated provider nevertheless emits it as a block inside ``6001`` and
    installs its resume entry. Claiming that exact point lets resolved-plan
    closure distinguish an emitted internal transfer from missing code.
    """
    ir = json.loads(
        (SOURCE_ROOT / "recovery" / "recovery_ir.json").read_text(
            encoding="utf-8"
        )
    )
    emitted = {
        str(instruction["ip"]).upper()
        for function in ir["functions"].values()
        for block in function.get("blocks", ())
        for instruction in block.get("instructions", ())
    }
    nodes = {item.identity: item for item in atlas.nodes()}
    claimed = set(program_coverage.reachable)
    for edge in program_coverage.unresolved_edges:
        _, separator, target = edge.rpartition("-->")
        if not separator:
            continue
        target = target.strip()
        node = nodes.get(target)
        address = "" if node is None else str(
            node.metadata.get("address", "")
        )
        try:
            cs, ip = address.split(":")
        except ValueError:
            continue
        if cs.upper() == f"{CODE_SEG:04X}" and ip.upper() in emitted:
            claimed.add(target)
    return frozenset(claimed)


def _cpuless_content_digest() -> str:
    return content_digest((
        SOURCE_ROOT / "skyroads" / "cpuless_backend.py",
        SOURCE_ROOT / "skyroads" / "cpuless_driver.py",
        SOURCE_ROOT / "skyroads" / "development_guard.py",
        SOURCE_ROOT / "skyroads" / "crash_report.py",
        *tree_sources(SOURCE_ROOT / "skyroads" / "recovered"),
        SOURCE_ROOT / "dos_re" / "dos_re" / "lift" / "platform.py",
        SOURCE_ROOT / "dos_re" / "dos_re" / "lift" / "runtime.py",
    ), repository_root=SOURCE_ROOT)


def _gameplay_region_entry() -> ImplementationEntry:
    contract = ExecutionRegionContract(
        region_id=GAMEPLAY_REGION,
        carrier_id=DOS_MEMORY_CARRIER,
        state_ownership=RegionStateOwnership.SHARED_DOS_MEMORY,
        entries=(
            RegionEntryPoint(GAMEPLAY_ENTRY_ID, GAMEPLAY_ENTRY_POINT),
            RegionEntryPoint(
                GAMEPLAY_RESUME_ENTRY_ID, GAMEPLAY_RESUME_POINT,
            ),
        ),
        exits=(
            RegionExitPoint(GAMEPLAY_RESULT_EXIT, GAMEPLAY_CALLER_CONTINUATION),
            RegionExitPoint(
                ROAD_DEPARTURE_EXIT,
                GAMEPLAY_ROAD_DEPARTURE_CONTINUATION,
            ),
            RegionExitPoint(GAMEPLAY_ABORTED_EXIT, GAMEPLAY_CALLER_CONTINUATION),
        ),
        covered_targets=frozenset(
            function_identity(offset) for offset in _GAMEPLAY_COVERED_OFFSETS
        ),
        replay_boundaries=frozenset({GAMEPLAY_TICK_BOUNDARY}),
        state_inputs=(
            "shared DOS memory image",
            "keyboard state row",
            "virtual timer ticks",
            "gameplay stack locals at 1010:2317",
        ),
        state_outputs=(
            "shared DOS gameplay state",
            "VGA presentation memory",
            "generated sound-device effects through the 1010:03C2 seam",
            "generated continuation register and stack seed",
            "named generated continuation",
        ),
    )
    implementation_id = "faithful-region:skyroads.gameplay"
    implementation_sources = (
        SOURCE_ROOT / "skyroads" / "gameplay_region.py",
        *runtime_source_paths(SOURCE_ROOT),
        SOURCE_ROOT / "skyroads" / "bridge" / "dgroup_view.py",
    )
    return ImplementationEntry(
        ImplementationDescriptor(
            implementation_id=implementation_id,
            targets=frozenset({
                GAMEPLAY_REGION,
                GAMEPLAY_ENTRY_POINT,
                GAMEPLAY_RESUME_POINT,
                GAMEPLAY_CALLER_CONTINUATION,
                GAMEPLAY_ROAD_DEPARTURE_CONTINUATION,
            }),
            origin=ImplementationOrigin.AUTHORED,
            category=OverrideCategory.FAITHFUL,
            recovery_level=RecoveryLevel.AUTHORED_NATIVE,
            evidence_grade=EvidenceGrade.FOCUSED,
            verification_evidence=frozenset({
                "skyroads:focused-oracle:native-gameplay-substep",
                "skyroads:focused-oracle:native-render-pipeline",
                "skyroads:replay-lockstep:native-gameplay-loop",
            }),
            properties=frozenset({
                "long-lived-region",
                "cpuless",
                "shared-dos-memory",
                "generated-continuation",
            }),
            required_capabilities=frozenset({
                DependencyCapability.DOS_MEMORY.value,
                DependencyCapability.DOS_SERVICES.value,
                DependencyCapability.DOS_RE_RUNTIME.value,
            }),
            execution_carrier=DOS_MEMORY_CARRIER,
            implementation_digest=content_digest(
                implementation_sources,
                repository_root=SOURCE_ROOT,
                records=(("region", GAMEPLAY_REGION),),
            ),
            region_id=GAMEPLAY_REGION,
            region_contract=contract,
        ),
        # The catalog points at the backend-independent recovered body.  The
        # generated-carrier adapter reconstructs pacing and lifecycle from the
        # original 1FD9/2B3D/01B8 control flow around it.
        implementation=native_gameplay_body,
        region_adapters=(RegionAdapter(
            f"{implementation_id}/generated-vmless",
            GENERATED_VMLESS_CARRIER,
            DOS_MEMORY_CARRIER,
            activate_gameplay_region,
            content_digest(
                (
                    SOURCE_ROOT / "skyroads" / "gameplay_region.py",
                    SOURCE_ROOT / "skyroads" / "vmless_backend.py",
                    SOURCE_ROOT / "dos_re" / "dos_re" / "regions.py",
                ),
                repository_root=SOURCE_ROOT,
                records=(
                    ("entry", GAMEPLAY_ENTRY_POINT),
                    ("resume", GAMEPLAY_RESUME_POINT),
                ),
            ),
        ),),
    )


def _file_digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest() if path.is_file() else ""


def catalog() -> ImplementationCatalog:
    faithful, generated = _hook_tables()
    atlas = coverage()
    program_coverage = atlas.coverage_for("game")
    all_targets = program_coverage.reachable
    vmless_targets = _vmless_owned_targets(atlas, program_coverage)
    entries: list[ImplementationEntry] = [
        ImplementationEntry(ImplementationDescriptor(
            implementation_id="baseline:interpreted-exe",
            targets=all_targets,
            origin=ImplementationOrigin.INTERPRETED,
            recovery_level=RecoveryLevel.INTERPRETED,
            execution_carrier=INTERPRETED_CPU_CARRIER,
            region_id=PROGRAM_ROOT,
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
            implementation_digest=IMAGE.content_digest,
        )),
        ImplementationEntry(ImplementationDescriptor(
            implementation_id="baseline:generated-vmless",
            targets=vmless_targets,
            origin=ImplementationOrigin.GENERATED,
            recovery_level=RecoveryLevel.GENERATED_VMLESS,
            execution_carrier=GENERATED_VMLESS_CARRIER,
            properties=frozenset({
                "cpu-backed", "vmless", "dos-memory-backed", "exe-detached",
            }),
            required_capabilities=frozenset({
                DependencyCapability.CPU_MODEL.value,
                DependencyCapability.DOS_MEMORY.value,
                DependencyCapability.DOS_SERVICES.value,
                DependencyCapability.DOS_RE_RUNTIME.value,
            }),
            implementation_digest=_vmless_content_digest(),
            region_id=PROGRAM_ROOT,
        )),
        ImplementationEntry(ImplementationDescriptor(
            implementation_id="baseline:generated-cpuless",
            targets=all_targets,
            origin=ImplementationOrigin.GENERATED,
            recovery_level=RecoveryLevel.GENERATED_ABI,
            execution_carrier=GENERATED_CPULESS_CARRIER,
            properties=frozenset({
                "cpuless", "abi-recovered", "dos-memory-backed", "exe-detached",
            }),
            required_capabilities=frozenset({
                DependencyCapability.DOS_MEMORY.value,
                DependencyCapability.DOS_SERVICES.value,
                DependencyCapability.DOS_RE_RUNTIME.value,
            }),
            implementation_digest=_cpuless_content_digest(),
            region_id=PROGRAM_ROOT,
        )),
    ]
    entries.extend(_generated_function_entries(generated))
    entries.extend(_authored_entries(
        faithful,
        category=OverrideCategory.FAITHFUL,
        prefix="faithful",
    ))
    entries.append(_gameplay_region_entry())
    return ImplementationCatalog(tuple(entries))


def services() -> RuntimeServiceCatalog:
    """Runtime interceptors selected by the execution plan."""
    return RuntimeServiceCatalog((RuntimeServiceDescriptor(
        service_id=FRAME_PARK_SERVICE_ID,
        product_safe=True,
        required_capabilities=frozenset({
            DependencyCapability.CPU_MODEL.value,
            DependencyCapability.DOS_MEMORY.value,
            DependencyCapability.DOS_SERVICES.value,
            DependencyCapability.DOS_RE_RUNTIME.value,
        }),
        implementation_digest=content_digest((
            SOURCE_ROOT / "skyroads" / "pacing.py",
            SOURCE_ROOT / "scripts" / "play.py",
        ), repository_root=SOURCE_ROOT),
    ),))


def features() -> FeatureCatalog:
    return FeatureCatalog((FeatureDescriptor(
        feature_id=PRACTICE_LEVEL_FEATURE_ID,
        category=FeatureCategory.BEHAVIORAL,
        changes_authoritative_state=True,
        replay_channel=PRACTICE_FEATURE_CHANNEL,
        safe_boundaries=frozenset({FEATURE_SAFE_BOUNDARY}),
        default_value=None,
        required_capabilities=frozenset({
            DependencyCapability.DOS_MEMORY.value,
        }),
        feature_digest=content_digest((
            SOURCE_ROOT / "skyroads" / "product_features.py",
            SOURCE_ROOT / "skyroads" / "bridge" / "dgroup_view.py",
        ), repository_root=SOURCE_ROOT),
    ),))


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
    if composition in {
        "oracle", "workbench-auto",
    }:
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
                expected_sha256=IMAGE.content_digest,
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
            provider_digest=content_digest((
                SOURCE_ROOT / "skyroads" / "runtime.py",
                SOURCE_ROOT / "scripts" / "play.py",
            ), repository_root=SOURCE_ROOT),
        )

    runtime_capabilities = {
        DependencyCapability.DOS_MEMORY.value,
        DependencyCapability.DOS_SERVICES.value,
        DependencyCapability.DOS_RE_RUNTIME.value,
    }
    if composition == "faithful-product":
        runtime_capabilities.add(DependencyCapability.CPU_MODEL.value)
    artifact_specs = (
        ("state", "state.json"),
        ("memory", "memory_1mb.bin"),
        ("manifest", "manifest.json"),
    )
    artifact_digests = {
        filename: _file_digest(BOOT_DIR / filename)
        for _, filename in artifact_specs
    }
    artifacts = tuple(
        BootstrapArtifact(
            artifact_id=f"skyroads-boot-{name}",
            runtime_path=f"artifacts/boot_image/{filename}",
            source_path=str(BOOT_DIR / filename),
            generation_instruction=BOOTSTRAP_INSTRUCTION,
            expected_sha256=artifact_digests[filename],
        )
        for name, filename in artifact_specs
    )
    provider_digest = content_digest(
        (
            SOURCE_ROOT / "scripts" / "build_boot_image.py",
            SOURCE_ROOT / "skyroads" / "native" / "boot.py",
            SOURCE_ROOT / "dos_re" / "dos_re" / "bootimage.py",
            SOURCE_ROOT / "dos_re" / "dos_re" / "independence.py",
            SOURCE_ROOT / "recovery" / "recovery_ir.json",
        ),
        repository_root=SOURCE_ROOT,
        records=(
            (
                f"artifacts/boot_image/{filename}",
                artifact_digests[filename] or "missing",
            )
            for _, filename in artifact_specs
        ),
    )
    return BuildImageBootstrapProvider(
        provider_id=f"skyroads-{composition}-build-image",
        state_outputs=(
            "1 MiB DOS memory image",
            "CPU/register seed",
            "DOS and device continuation state",
        ),
        artifacts=artifacts,
        build_required_capabilities=frozenset({
            DependencyCapability.ORIGINAL_EXE.value,
        }),
        runtime_required_capabilities=frozenset(runtime_capabilities),
        initialized_capabilities=frozenset({
            DependencyCapability.CPU_MODEL.value,
            DependencyCapability.DOS_MEMORY.value,
            DependencyCapability.DOS_SERVICES.value,
        }),
        # Differential verification boots the candidate from this immutable
        # image while its separately constructed oracle side boots from EXE.
        valid_profiles=frozenset({
            "development", "verification", "detached", "release",
        }),
        provider_digest=provider_digest,
    )


def configuration(
    profile: str,
    composition: str,
    *,
    build_platform: str = "windows",
    requested_capabilities: Iterable[str] = (),
    enabled_features: Iterable[str] = (),
    include_authored: bool = True,
) -> ExecutionConfiguration:
    """Map a product composition onto dos_re's orthogonal policy axes."""
    if composition == "auto":
        composition = "generated-detached" if profile in {"detached", "release"} else (
            "faithful-product"
        )
    enabled_features = tuple(enabled_features)
    if enabled_features and composition == "generated-detached":
        raise ValueError(
            "SkyRoads product features do not yet have a generated-CPUless "
            "state adapter"
        )
    preferences: tuple[str, ...]
    selected: tuple[str, ...] = ()
    # Interpreter-backed compositions all use the same stateless semantic
    # frame seam.  It is scheduling infrastructure, not a recovery-level hook:
    # oracle, generated and authored bodies must stop at the identical blocked
    # main-loop wait for replay points to be backend-independent.
    product_services: tuple[str, ...] = (
        (FRAME_PARK_SERVICE_ID,)
        if composition in {
            "oracle", "workbench-auto",
        }
        else ()
    )
    if composition == "oracle":
        preferences = ("baseline:interpreted-exe",)
    elif composition == "workbench-auto":
        selected = (
            implementation_ids(OverrideCategory.FAITHFUL)
            if include_authored else ()
        )
        preferences = (
            *selected, *generated_function_ids(), "baseline:interpreted-exe",
        )
    elif composition == "faithful-product":
        selected = (
            implementation_ids(OverrideCategory.FAITHFUL)
            if include_authored else ()
        )
        preferences = (*selected, "baseline:generated-vmless")
    elif composition == "generated-detached":
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
        enabled_features=enabled_features,
        product_services=product_services,
        requested_capabilities=requested_capabilities,
        bootstrap_provider=bootstrap_provider(composition),
        build_target=target,
    )
    if selected:
        config = replace(
            config,
            execution_policy=replace(
                config.execution_policy,
                minimum_authored_evidence=EvidenceGrade.FOCUSED,
            ),
        )
    return config


def selected_whole_program_provider(plan) -> str:
    for binding in plan.bindings:
        if binding.target == PROGRAM_ROOT:
            return binding.implementation_id
    raise RuntimeError("execution plan has no SkyRoads program-root binding")


@dataclass(frozen=True)
class SkyroadsProviderDiagnostics:
    """Product-role projection of one already-resolved execution plan."""

    frontend_provider: str
    level_selection_provider: str
    gameplay_provider: str
    renderer_provider: str
    covered_original_identities: tuple[str, ...]
    collapsed_internal_boundaries: int
    remaining_external_seams: tuple[str, ...]
    selected_generated_fallbacks: int
    selected_interpreted_fallbacks: int
    exe_dependency: bool
    dos_re_runtime_dependency: bool
    active_region: str = ""


def provider_diagnostics(plan, runtime=None) -> SkyroadsProviderDiagnostics:
    """Expose provider roles without selecting or re-resolving anything."""
    root = selected_whole_program_provider(plan)
    regions = tuple(plan.regions)
    gameplay = next(
        (item for item in regions if item.region_id == GAMEPLAY_REGION), None,
    )
    covered = set(gameplay.covered_targets if gameplay else ())
    descriptors = {
        item.implementation_id: item for item in plan.implementations
    }
    generated = interpreted = 0
    for binding in plan.bindings:
        if binding.target in covered:
            continue
        origin = descriptors[binding.implementation_id].origin
        if origin is ImplementationOrigin.GENERATED:
            generated += 1
        elif origin is ImplementationOrigin.INTERPRETED:
            interpreted += 1
    seams: tuple[str, ...] = ()
    if gameplay is not None:
        seams = tuple(
            [
                *(f"entry:{item.entry_id}->{item.target}" for item in gameplay.entries),
                *(f"exit:{item.exit_id}->{item.continuation}" for item in gameplay.exits),
                "service:sfx->function:1010:03c2",
            ]
        )
    dispatcher = getattr(runtime, "execution_regions", None)
    active_region = ""
    if dispatcher is not None and dispatcher.active:
        active_region = dispatcher.active_region_id
    capabilities = set(plan.report.required_capabilities)
    region_provider = gameplay.implementation_id if gameplay else root
    return SkyroadsProviderDiagnostics(
        frontend_provider=root,
        level_selection_provider=root,
        gameplay_provider=region_provider,
        renderer_provider=region_provider,
        covered_original_identities=tuple(sorted(covered)),
        collapsed_internal_boundaries=(
            len(gameplay.suppressed_bindings) if gameplay else 0
        ),
        remaining_external_seams=seams,
        selected_generated_fallbacks=generated,
        selected_interpreted_fallbacks=interpreted,
        exe_dependency=DependencyCapability.ORIGINAL_EXE.value in capabilities,
        dos_re_runtime_dependency=(
            DependencyCapability.DOS_RE_RUNTIME.value in capabilities
        ),
        active_region=active_region,
    )


def format_provider_diagnostics(plan, runtime=None) -> str:
    report = provider_diagnostics(plan, runtime)
    covered = report.covered_original_identities
    seams = report.remaining_external_seams
    return "\n".join((
        f"active frontend provider: {report.frontend_provider}",
        f"active level-selection provider: {report.level_selection_provider}",
        f"active gameplay provider: {report.gameplay_provider}",
        f"active renderer provider: {report.renderer_provider}",
        "covered original identities: "
        + (f"{len(covered)} ({', '.join(covered)})" if covered else "0"),
        f"collapsed internal boundaries: {report.collapsed_internal_boundaries}",
        "remaining external seams: " + (", ".join(seams) or "none"),
        f"selected generated fallbacks: {report.selected_generated_fallbacks}",
        f"selected interpreted fallbacks: {report.selected_interpreted_fallbacks}",
        f"EXE dependency: {str(report.exe_dependency).lower()}",
        "dos_re runtime dependency: "
        f"{str(report.dos_re_runtime_dependency).lower()}",
        "runtime active region: " + (report.active_region or "none"),
    ))
