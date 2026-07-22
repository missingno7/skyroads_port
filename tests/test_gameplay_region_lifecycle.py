"""Asset-backed product-flow proof for the generated/native composition."""
from __future__ import annotations

from pathlib import Path

import pytest

from dos_re import player
from dos_re.dos import ConsoleInputWouldBlock
from dos_re.replay import ReplayArtifact, machine_projection
from dos_re.replay_input import RealModeInputAdapter
from dos_re.snapshot import (
    capture_runtime_continuation,
    runtime_machine_projection_digest,
)

from scripts.play import SkyroadsFrontend
from skyroads.execution import GENERATED_VMLESS_CARRIER
from skyroads.gameplay_region import GAMEPLAY_RESULT_EXIT, GAMEPLAY_RESUME_IP
from skyroads.identities import CODE_SEG
from skyroads.launch_inputs import DIRECT_LEVEL_ADAPTER_ID
from skyroads.vmless_backend import create_planned_runtime


ROOT = Path(__file__).resolve().parents[1]
REPLAY = (
    ROOT / "artifacts" / "replays"
    / "replay_candidate_smoke_20260720_214152"
)
BOOT = ROOT / "artifacts" / "boot_image" / "state.json"
LEVEL_SELECTION = (CODE_SEG, 0x5180)

pytestmark = pytest.mark.skipif(
    not (REPLAY.exists() and BOOT.exists()),
    reason="needs the active ReplayArtifact and generated boot image",
)


def test_generated_selector_native_gameplay_generated_retry_lifecycle() -> None:
    artifact = ReplayArtifact.open(REPLAY)
    frontend = SkyroadsFrontend(ROOT)
    args = player.build_arg_parser(frontend).parse_args([
        "--headless",
        "--composition", "faithful-product",
        "--play-replay", str(REPLAY),
    ])
    frontend.apply_replay_metadata(args, artifact.metadata)
    args.profile = "development"
    args.composition = "faithful-product"
    args.execution_plan = frontend.resolve_execution_plan(args)
    runtime, _manifest = create_planned_runtime(
        args,
        bootstrap_artifacts=args.execution_plan.bootstrap_artifact_paths(),
        bind_plan=lambda current: frontend.bind_execution_plan(
            current,
            args.execution_plan,
            carrier_id=GENERATED_VMLESS_CARRIER,
        ),
    )
    runtime.dos.console_input_fallback = None
    runtime.dos.mouse_present = bool(
        artifact.metadata.get("mouse_present", False)
    )

    selections: list[int] = []
    original_selection = runtime.cpu.replacement_hooks[LEVEL_SELECTION]

    def observe_level_selection(cpu) -> None:
        selections.append(getattr(runtime, "_skyroads_gameplay_entries", 0))
        original_selection(cpu)

    observe_level_selection.owns_time = getattr(
        original_selection, "owns_time", False,
    )
    runtime.cpu.replacement_hooks[LEVEL_SELECTION] = observe_level_selection
    inputs = RealModeInputAdapter(artifact.events)

    for frame in range(min(900, artifact.end_point.ordinal)):
        inputs.apply_to_runtime(
            frame,
            runtime,
            deliver=lambda current, scancode: frontend.deliver_input(
                current, scancode,
            ),
        )
        try:
            frontend.advance_frame(runtime, args, frame)
        except ConsoleInputWouldBlock:
            pass
        if getattr(runtime, "_skyroads_gameplay_entries", 0):
            break

    assert selections, "generated level-selection provider was never reached"
    assert selections[0] == 0, "gameplay ran before generated level selection"
    assert runtime.execution_regions.active
    assert runtime._skyroads_gameplay_entries == 1
    control_device = runtime.cpu.mem.rw(runtime.cpu.s.ds, 0x95F6)

    # Force the original state-two gate.  1FD9 returns two verbatim and 01B8
    # retries the same selected level through 2B3D without re-entering 5180.
    # This is the control-flow contract the former manual integration rewrote.
    runtime.dos.mouse_buttons = 0
    runtime.dos.key_queue.clear()
    runtime.dos.pending_console_scancode = None
    for offset in range(0x0BD0, 0x0BE0):
        runtime.cpu.mem.wb(runtime.cpu.s.ds, offset, 0)
    session = runtime.execution_regions._active.session
    session.view.game_state = 2

    for frame in range(120):
        try:
            frontend.advance_frame(runtime, args, 900 + frame)
        except ConsoleInputWouldBlock:
            pass
        if runtime._skyroads_gameplay_entries >= 2:
            break

    assert runtime._skyroads_last_region_exit == GAMEPLAY_RESULT_EXIT
    assert runtime._skyroads_gameplay_entries >= 2, (
        "generated same-level retry was not reached; machine="
        f"{runtime.cpu.s.cs:04X}:{runtime.cpu.s.ip:04X}, "
        f"game_state={runtime.cpu.mem.rw(runtime.cpu.s.ds, 0x456E)}, "
        f"control_device={control_device}, "
        f"entries={runtime._skyroads_gameplay_entries}, "
        f"boundary={runtime._skyroads_vmless_driver.last_boundary_kind!r}"
    )
    assert selections == [0]
    assert runtime.execution_regions.active
    assert runtime._skyroads_gameplay_entries == 2


def test_direct_level_uses_the_same_planned_gameplay_region() -> None:
    artifact = ReplayArtifact.open(REPLAY)
    frontend = SkyroadsFrontend(ROOT)
    args = player.build_arg_parser(frontend).parse_args([
        "--headless",
        "--composition", "faithful-product",
        "--level", "14",
    ])
    args.execution_plan = frontend.resolve_execution_plan(args)
    runtime, _manifest = create_planned_runtime(
        args,
        bootstrap_artifacts=args.execution_plan.bootstrap_artifact_paths(),
        bind_plan=lambda current: frontend.bind_execution_plan(
            current,
            args.execution_plan,
            carrier_id=GENERATED_VMLESS_CARRIER,
        ),
    )
    runtime.dos.console_input_fallback = None
    runtime.dos.mouse_present = bool(
        artifact.metadata.get("mouse_present", False)
    )
    inputs = RealModeInputAdapter(artifact.events)

    for frame in range(min(900, artifact.end_point.ordinal)):
        inputs.apply_to_runtime(
            frame,
            runtime,
            deliver=lambda current, scancode: frontend.deliver_input(
                current, scancode,
            ),
        )
        try:
            frontend.advance_frame(runtime, args, frame)
        except ConsoleInputWouldBlock:
            pass
        if getattr(runtime, "_skyroads_gameplay_entries", 0):
            break

    assert runtime._skyroads_direct_level_applied == 14
    assert runtime._skyroads_gameplay_level == 14
    assert runtime._skyroads_gameplay_entries == 1
    assert runtime.execution_regions.active
    assert runtime.cpu.hook_names.get(LEVEL_SELECTION) != DIRECT_LEVEL_ADAPTER_ID


def test_escape_from_native_gameplay_uses_generated_confirmation_and_reenters() -> None:
    """The native island owns gameplay only; its abort continuation is real."""
    artifact = ReplayArtifact.open(REPLAY)
    frontend = SkyroadsFrontend(ROOT)
    args = player.build_arg_parser(frontend).parse_args([
        "--headless", "--composition", "faithful-product", "--level", "14",
    ])
    args.execution_plan = frontend.resolve_execution_plan(args)
    runtime, _manifest = create_planned_runtime(
        args,
        bootstrap_artifacts=args.execution_plan.bootstrap_artifact_paths(),
        bind_plan=lambda current: frontend.bind_execution_plan(
            current, args.execution_plan, carrier_id=GENERATED_VMLESS_CARRIER,
        ),
    )
    runtime.dos.console_input_fallback = None
    runtime.dos.mouse_present = bool(artifact.metadata.get("mouse_present", False))
    inputs = RealModeInputAdapter(artifact.events)
    selections: list[int] = []
    original_selection = runtime.cpu.replacement_hooks[LEVEL_SELECTION]

    def observe_selection(cpu) -> None:
        selections.append(getattr(runtime, "_skyroads_gameplay_entries", 0))
        original_selection(cpu)

    observe_selection.owns_time = getattr(original_selection, "owns_time", False)
    runtime.cpu.replacement_hooks[LEVEL_SELECTION] = observe_selection
    for frame in range(min(900, artifact.end_point.ordinal)):
        inputs.apply_to_runtime(frame, runtime,
                                deliver=lambda current, sc: frontend.deliver_input(current, sc))
        try:
            frontend.advance_frame(runtime, args, frame)
        except ConsoleInputWouldBlock:
            pass
        if getattr(runtime, "_skyroads_gameplay_entries", 0):
            break
    assert runtime.execution_regions.active
    selections.clear()

    runtime.cpu.mem.wb(runtime.cpu.s.ds, 0x0BDA, 0x80)  # Escape make
    for frame in range(180):
        try:
            frontend.advance_frame(runtime, args, 900 + frame)
        except ConsoleInputWouldBlock:
            pass
        if selections:
            break

    assert runtime._skyroads_last_region_exit == "gameplay-aborted"
    # The original generated shell asks for one key at 1010:5FED (DOS AH=07)
    # before it decides the next lifecycle action.  Direct launch is a one-shot
    # menu selection, so this observed abort path confirms and re-enters the
    # selected level; it must not be relabelled as an invented native menu.
    runtime.cpu.mem.wb(runtime.cpu.s.ds, 0x0BDA, 0)  # Escape break
    frontend.deliver_input(runtime, 0x1C)  # Enter
    for frame in range(180, 360):
        try:
            frontend.advance_frame(runtime, args, 900 + frame)
        except ConsoleInputWouldBlock:
            pass
        if selections:
            break
    assert not selections
    assert runtime.execution_regions.active
    assert runtime._skyroads_gameplay_entries >= 2


def test_replay_continuation_reconstructs_the_active_native_region() -> None:
    artifact = ReplayArtifact.open(REPLAY)
    frontend = SkyroadsFrontend(ROOT)
    args = player.build_arg_parser(frontend).parse_args([
        "--headless", "--composition", "faithful-product",
    ])
    args.execution_plan = frontend.resolve_execution_plan(args)

    def create_runtime():
        runtime, _manifest = create_planned_runtime(
            args,
            bootstrap_artifacts=args.execution_plan.bootstrap_artifact_paths(),
            bind_plan=lambda current: frontend.bind_execution_plan(
                current,
                args.execution_plan,
                carrier_id=GENERATED_VMLESS_CARRIER,
            ),
        )
        runtime.dos.console_input_fallback = None
        runtime.dos.mouse_present = bool(
            artifact.metadata.get("mouse_present", False)
        )
        return runtime

    original = create_runtime()
    inputs = RealModeInputAdapter(artifact.events)
    for frame in range(min(900, artifact.end_point.ordinal)):
        inputs.apply_to_runtime(
            frame,
            original,
            deliver=lambda current, scancode: frontend.deliver_input(
                current, scancode,
            ),
        )
        try:
            frontend.advance_frame(original, args, frame)
        except ConsoleInputWouldBlock:
            pass
        if getattr(original, "_skyroads_gameplay_entries", 0):
            break
    assert original.execution_regions.active
    assert original.cpu.s.ip == GAMEPLAY_RESUME_IP

    continuation = capture_runtime_continuation(
        original, event_cursor=inputs.event_cursor,
    )
    restored = create_runtime()
    frontend.apply_replay_state(restored, continuation)
    assert not restored.execution_regions.active
    projection_schema = frontend.replay_profile(
        args, original,
    ).projection_schema

    # A ReplayArtifact restore carries the shared machine state and the
    # materialized stack scratch, not a serialized Python session.  The next
    # semantic point must recreate the same region and produce the same full
    # continuation-state projection.
    for frame in range(3):
        frontend.advance_frame(original, args, 900 + frame)
        frontend.advance_frame(restored, args, 900 + frame)
        original_digest = runtime_machine_projection_digest(
            original,
            event_cursor=inputs.event_cursor,
            projection_schema=projection_schema,
        )
        restored_digest = runtime_machine_projection_digest(
            restored,
            event_cursor=inputs.event_cursor,
            projection_schema=projection_schema,
        )
        if original_digest != restored_digest:
            original_state = capture_runtime_continuation(
                original, event_cursor=inputs.event_cursor,
            )
            restored_state = capture_runtime_continuation(
                restored, event_cursor=inputs.event_cursor,
            )
            comparison = machine_projection(
                original_state, schema_id=projection_schema,
            ).compare(machine_projection(
                restored_state, schema_id=projection_schema,
            ))
            pytest.fail(
                "; ".join(comparison.differences[:20])
                + "; original ports="
                + repr(original_state.metadata["dos"]["port_log_tail"][-8:])
                + "; restored ports="
                + repr(restored_state.metadata["dos"]["port_log_tail"][-8:])
            )
    assert original.execution_regions.active
    assert restored.execution_regions.active
    assert restored.cpu.s.ip == GAMEPLAY_RESUME_IP
