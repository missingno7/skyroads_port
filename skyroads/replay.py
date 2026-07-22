"""SkyRoads adapters for dos_re's authoritative :class:`ReplayArtifact`.

This module owns no format, manifest, recorder, playback clock, or persistence.
It only adapts immutable real-mode input events and machine continuation state
to the SkyRoads frame boundary.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from dos_re.dos import ConsoleInputWouldBlock
from dos_re.observable import (
    PRESENTATION,
    REPLAY_INPUT,
    SEMANTIC_BOUNDARY,
    RollingEffectDigest,
)
from dos_re.replay_input import MOUSE_CHANNEL, SCAN_CHANNEL, RealModeInputAdapter
from dos_re.replay import (
    CanonicalState,
    ContinuationState,
    ReplayExecutionIdentity,
    ReplayArtifact,
    ReplayPoint,
    machine_projection,
)
from dos_re.snapshot import (
    capture_runtime_continuation,
    runtime_machine_projection_digest,
)
from skyroads.verification_contracts import (
    GAMEPLAY_INTERIOR_PROJECTION,
    MACHINE_FALLBACK_PROJECTION,
    PROJECTION_SCHEMA,
    exit_projection,
)

_GAMEPLAY_FIELDS = (
    "speed", "bounce", "game_state", "entered", "gravity", "jump",
    "jump_level_gate", "steer", "lateral_accel", "af1c", "af2c",
    "timer_a", "timer_b", "timer_a_param", "timer_b_param",
    "effect_gate", "f41c0", "unknown_5496", "frame_ctr",
    "unknown_455a", "unknown_af2e", "unknown_af30", "unknown_af38",
    "elapsed_ticks", "ship_pos", "lateral",
)


_SEMANTIC_FRAME_HEADS = frozenset({
    (0x1010, 0x0EF8), (0x1010, 0x22F8), (0x1010, 0x434A),
    (0x1010, 0x4468), (0x1010, 0x47CD), (0x1010, 0x4866),
})


def _semantic_boundary_identity(runtime) -> str:
    declared = getattr(runtime, "_skyroads_replay_boundary_identity", None)
    if declared:
        return str(declared)
    state = runtime.cpu.s
    point = (int(state.cs), int(state.ip))
    if point in _SEMANTIC_FRAME_HEADS:
        return f"{point[0]:04X}:{point[1]:04X}"
    return f"machine:{point[0]:04X}:{point[1]:04X}"


def _semantic_projection(
    runtime, *, event_cursor: int,
) -> CanonicalState:
    """Project a SkyRoads event seam without exposing carrier scratch state.

    CPU registers, instruction counters, VGA programming strategy, Sound
    Blaster bookkeeping, and the generated/native carrier's private use of
    original stack scratch are implementation details at semantic frame and
    input boundaries. The recovered named game state, consumed input, timing,
    semantic audio commands, and presented VGA result are authoritative here.
    """
    from skyroads.bridge.dgroup_view import GameView

    cpu = runtime.cpu
    view = GameView(cpu.mem.data, base=(cpu.s.ds & 0xFFFF) << 4)
    dos = runtime.dos
    fields = {
        "verification": {
            "contract": "skyroads:gameplay-region-faithful/v1",
            "surface": GAMEPLAY_INTERIOR_PROJECTION.projection_id,
        },
        "boundary": _semantic_boundary_identity(runtime),
        "gameplay": {
            name: int(getattr(view, name)) for name in _GAMEPLAY_FIELDS
        },
        "input": {
            "keys": [
                int(cpu.mem.rb(cpu.s.ds, offset))
                for offset in range(0x0BD2, 0x0BE0)
            ],
            "current_scancode": int(dos.current_scancode),
            "mouse": [
                bool(dos.mouse_present), int(dos.mouse_x), int(dos.mouse_y),
                int(dos.mouse_buttons),
            ],
        },
        "timing": {
            "elapsed_ticks": int(view.elapsed_ticks),
            "frame_ctr": int(view.frame_ctr),
        },
        "audio": {
            "claim": "opl-command-stream",
            "opl_registers": {
                str(key): int(value)
                for key, value in sorted(dos.opl_registers.items())
            },
        },
        "presentation": {
            "video_mode": int(dos.video_mode),
            "palette": [
                [int(component) for component in color]
                for color in dos.vga_palette
            ],
        },
    }
    return CanonicalState(
        PROJECTION_SCHEMA,
        int(event_cursor),
        fields,
        # SkyRoads gameplay uses linear VGA mode 13h. The aperture plus DAC
        # palette is the presentation result; comparing it avoids requiring
        # numpy or making native rendering imitate VGA register write order.
        {"vga-aperture": bytes(cpu.mem.data[0xA0000:0xB0000])},
    ).normalized()


_SEAM_REGISTER_FIELDS = (
    "ax", "bx", "cx", "dx", "sp", "bp", "si", "di",
    "cs", "ds", "es", "ss", "ip", "flags", "fsw", "fcw",
)


def project_gameplay_exit(
    runtime, *, event_cursor: int,
) -> CanonicalState:
    """Project the state handed from native gameplay to generated code.

    This is deliberately stronger than an interior gameplay projection.  The
    generated shell may observe live registers, the stack, shared DOS memory,
    interrupt/timing state, and device state after the native island returns.
    A later fully native shell can move this seam outward and declare a smaller
    common authority; until then, this is the faithful continuation contract.
    """
    from skyroads.bridge.dgroup_view import GameView

    exit_id = str(getattr(runtime, "_skyroads_last_region_exit", ""))
    continuation = str(
        getattr(runtime, "_skyroads_last_region_continuation", "")
    )
    if not exit_id or not continuation:
        raise ValueError("SkyRoads runtime is not parked at a gameplay exit seam")
    projection = exit_projection(exit_id)
    cpu = runtime.cpu
    dos = runtime.dos
    state = cpu.s
    view = GameView(cpu.mem.data, base=(state.ds & 0xFFFF) << 4)
    pic = getattr(dos, "pic", None)
    sound_blaster = getattr(dos, "sound_blaster", None)
    fields = {
        "verification": {
            "contract": "skyroads:gameplay-region-faithful/v1",
            "surface": projection.projection_id,
        },
        "exit": {"id": exit_id, "continuation": continuation},
        "continuation": {
            "identity": f"{state.cs:04X}:{state.ip:04X}",
            "registers": {
                name: int(getattr(state, name))
                for name in _SEAM_REGISTER_FIELDS
            },
            # The entire shared memory image below carries live stack words.
            # These named coordinates make the seam diagnostic actionable.
            "stack": {
                "ss": int(state.ss), "sp": int(state.sp),
                "bp": int(state.bp), "call_depth": int(cpu.call_depth),
            },
            "timing": {
                "instruction_count": int(cpu.instruction_count),
                "pic": None if pic is None else {
                    "imr": int(pic.imr), "irr": int(pic.irr),
                    "isr": int(pic.isr),
                },
                "sound_blaster": (
                    None if sound_blaster is None
                    else sound_blaster.snapshot_state()
                ),
            },
        },
        "gameplay": {
            name: int(getattr(view, name)) for name in _GAMEPLAY_FIELDS
        },
    }
    return CanonicalState(
        PROJECTION_SCHEMA,
        int(event_cursor),
        fields,
        {"shared-dos-memory": bytes(cpu.mem.data)},
    ).normalized()


def _at_semantic_boundary(runtime) -> bool:
    dispatcher = getattr(runtime, "execution_regions", None)
    if dispatcher is not None and dispatcher.active_region_id == \
            "skyroads:1.0:region:gameplay":
        return True
    kind = getattr(runtime, "_skyroads_replay_boundary_kind", None)
    if kind in {"frame-park", "input-block"}:
        return True
    cpu = getattr(runtime, "cpu", None)
    if cpu is None:
        return False
    state = cpu.s
    return (int(state.cs), int(state.ip)) in _SEMANTIC_FRAME_HEADS


def capture_profile(artifact: ReplayArtifact) -> ReplayExecutionIdentity:
    """Return the plan identity that captured the immutable input stream."""
    return artifact.capture_profile()


def capture_base(artifact: ReplayArtifact) -> ContinuationState:
    profile = capture_profile(artifact)
    base = artifact.cached_points(profile)[0]
    return artifact.restore(profile, base)


def project_base_to_runtime_devices(
    runtime,
    state: ContinuationState,
    *,
    executable_ranges: tuple[tuple[int, int], ...] = (),
    executable_image: bytes | bytearray | None = None,
    executable_base: int = 0,
) -> ContinuationState:
    """Project a captured base onto one selected runtime profile.

    A replay's input timeline is immutable, but a candidate may deliberately
    select a stricter device topology such as ``--no-sound``.  Its profile
    needs the same CPU, memory, DOS, file, and input state at point zero while
    omitting devices that do not exist in that runtime.  Adding a device whose
    initial state was never recorded remains unsafe and fails explicitly.

    A detached generated capture may deliberately poison implementation-owned
    instruction bytes.  When constructing its interpreter oracle, the caller
    supplies those exact physical ranges plus the verified unpacked oracle
    image.  Writable memory and code-as-data cells remain from
    the replay base; only ranges declared poisoned by the build manifest move.
    """
    state = state.normalized()
    metadata = deepcopy(dict(state.metadata))
    dos_state = metadata.get("dos")
    if not isinstance(dos_state, dict):
        raise ValueError("SkyRoads replay base has no DOS continuation state")
    for state_key, runtime_attribute in (
        ("pic", "pic"),
        ("sound_blaster", "sound_blaster"),
    ):
        runtime_has_device = getattr(
            runtime.dos, runtime_attribute, None,
        ) is not None
        state_has_device = dos_state.get(state_key) is not None
        if runtime_has_device and not state_has_device:
            raise ValueError(
                f"cannot add {state_key} to a replay base that did not "
                "capture its deterministic state"
            )
        if not runtime_has_device:
            dos_state.pop(state_key, None)
    sound_blaster = getattr(runtime.dos, "sound_blaster", None)
    sb_state = dos_state.get("sound_blaster")
    if sound_blaster is not None and isinstance(sb_state, dict):
        for field in ("base", "irq", "dma"):
            if field in sb_state and int(sb_state[field]) != int(
                getattr(sound_blaster, field)
            ):
                raise ValueError(
                    f"cannot project replay Sound Blaster {field}: "
                    f"state={int(sb_state[field])}, "
                    f"runtime={int(getattr(sound_blaster, field))}"
                )
        # Capture versus detection-only is part of the requested profile's
        # device identity.  A profile-local base records that selection
        # explicitly; it is never restored as the capture profile's cache.
        sb_state["detection_only"] = bool(sound_blaster.detection_only)
    regions = dict(state.regions)
    if executable_ranges:
        if executable_image is None:
            raise ValueError(
                "executable replay projection requires an unpacked image"
            )
        memory = bytearray(regions["memory"])
        for start, length in executable_ranges:
            start = int(start)
            length = int(length)
            end = start + length
            source_start = start - int(executable_base)
            source_end = source_start + length
            if start < 0 or length < 0 or end > len(memory) \
                    or source_start < 0 or source_end > len(executable_image):
                raise ValueError(
                    f"invalid executable replay projection range "
                    f"{start:#x}+{length:#x}"
                )
            memory[start:end] = executable_image[source_start:source_end]
        regions["memory"] = bytes(memory)
    return ContinuationState(
        schema_id=state.schema_id,
        metadata=metadata,
        regions=regions,
        event_cursor=state.event_cursor,
    ).normalized()


def replay_artifacts(directory: str | Path) -> tuple[ReplayArtifact, ...]:
    """Open every authoritative replay artifact directly below *directory*."""
    root = Path(directory)
    artifacts = tuple(
        ReplayArtifact.open(manifest.parent)
        for manifest in sorted(root.glob("*/replay.json"))
    )
    for artifact in artifacts:
        capture_profile(artifact)
    return artifacts


def capture_base_memories(
    directory: str | Path,
) -> tuple[tuple[str, bytes], ...]:
    """Return named memory images from authoritative replay recording bases."""
    return tuple(
        (artifact.directory.name, capture_base(artifact).regions["memory"])
        for artifact in replay_artifacts(directory)
    )


class SkyroadsReplayDriver:
    """Replay one interpreted or DOS-memory-backed override composition."""

    def __init__(
        self,
        frontend,
        args,
        runtime,
        artifact: ReplayArtifact,
        profile: ReplayExecutionIdentity,
    ):
        self.frontend = frontend
        self.args = args
        self.runtime = runtime
        self.artifact = artifact
        self._profile = profile
        self._point = ReplayPoint(0, artifact.timeline_id)
        self.input = RealModeInputAdapter(artifact.events)

    @property
    def profile(self) -> ReplayExecutionIdentity:
        return self._profile

    @property
    def current_point(self) -> ReplayPoint:
        return self._point

    def capture(self) -> ContinuationState:
        return capture_runtime_continuation(
            self.runtime, event_cursor=self.input.event_cursor)

    def restore(self, state: ContinuationState, point: ReplayPoint) -> None:
        self.frontend.apply_replay_state(self.runtime, state)
        self.input.seek(state.event_cursor)
        self._point = point
        presentation = getattr(self.runtime, "_skyroads_presentation", None)
        if presentation is not None:
            # GPU/asset caches are deliberately absent from continuation
            # state. Rebuild them at restore time, outside the first replayed
            # semantic point and outside its audio/presentation deadline.
            presentation.prewarm_current()

    def begin_observable_interval(self):
        previous = getattr(self.runtime.dos, "observable_effect_sink", None)
        if previous is not None:
            raise RuntimeError("nested SkyRoads observable intervals are unsupported")
        sink = RollingEffectDigest()
        self.runtime.dos.observable_effect_sink = sink
        return sink, previous

    def end_observable_interval(self, token):
        sink, previous = token
        if self.runtime.dos.observable_effect_sink is not sink:
            raise RuntimeError("SkyRoads observable interval sink was replaced")
        self.runtime.dos.observable_effect_sink = previous
        return sink.finish()

    def replay_to(self, artifact: ReplayArtifact, target: ReplayPoint) -> None:
        if artifact is not self.artifact:
            raise ValueError("driver belongs to another ReplayArtifact")
        if target.timeline_id != artifact.timeline_id:
            raise ValueError("target belongs to another replay timeline")
        if target.ordinal < self._point.ordinal:
            raise ValueError("driver cannot replay backwards")
        while self._point.ordinal < target.ordinal:
            ordinal = self._point.ordinal
            cursor_before = self.input.event_cursor
            self.input.apply_to_runtime(
                ordinal,
                self.runtime,
                deliver=lambda rt, scancode: self.frontend.deliver_input(
                    rt, scancode),
            )
            try:
                self.frontend.advance_replay_frame(
                    self.runtime,
                    self.args,
                    ordinal,
                    artifact.timeline_coordinate(ReplayPoint(
                        ordinal + 1, artifact.timeline_id)),
                )
            except ConsoleInputWouldBlock:
                # The interactive player treats a blocking DOS console read as
                # a stable, resumable frame state. Replays must advance the
                # same timeline so a later recorded key can satisfy the read.
                pass
            sink = getattr(self.runtime.dos, "observable_effect_sink", None)
            if sink is not None:
                # Input application is externally scheduled by ReplayArtifact.
                # Record exactly which immutable events were consumed, then the
                # semantic handoff/presentation fence.  Port I/O and interrupts
                # were recorded directly by dos_re's platform adapters.
                for event in artifact.events[cursor_before:self.input.event_cursor]:
                    channel = (
                        1 if event.channel == SCAN_CHANNEL
                        else 2 if event.channel == MOUSE_CHANNEL
                        else 0)
                    sink.record(
                        REPLAY_INPUT, event.sequence, ordinal, channel)
                coordinate = artifact.timeline_coordinate(ReplayPoint(
                    ordinal + 1, artifact.timeline_id))
                kind = coordinate.value.get("kind") if isinstance(
                    coordinate.value, dict) else "guest-coordinate"
                kind_id = {
                    "frame-park": 1,
                    "input-block": 2,
                    "guest-coordinate": 3,
                    "guest-fallback": 4,
                }.get(kind, 0)
                sink.record(SEMANTIC_BOUNDARY, ordinal + 1, kind_id)
                sink.record(PRESENTATION, ordinal + 1)
            self._point = ReplayPoint(ordinal + 1, artifact.timeline_id)

    def project(self) -> CanonicalState:
        if _at_semantic_boundary(self.runtime):
            return _semantic_projection(
                self.runtime,
                event_cursor=self.input.event_cursor,
            )
        return machine_projection(self.capture(), schema_id=PROJECTION_SCHEMA)

    def verification_projection_contract(self):
        """Declare exactly what the current replay point compares.

        Gameplay ticks use the region's semantic authority.  Other points are
        still inside a shared real-mode carrier and therefore retain complete
        continuation comparison.  Exit seams are exercised through
        :func:`project_gameplay_exit` by the region adapter tests because the
        generated driver is allowed to continue past the handoff in one frame.
        """
        if _at_semantic_boundary(self.runtime):
            return GAMEPLAY_INTERIOR_PROJECTION
        return MACHINE_FALLBACK_PROJECTION

    def point_digest(self) -> str:
        if _at_semantic_boundary(self.runtime):
            return self.project().digest
        return runtime_machine_projection_digest(
            self.runtime, event_cursor=self.input.event_cursor,
            projection_schema=PROJECTION_SCHEMA)
