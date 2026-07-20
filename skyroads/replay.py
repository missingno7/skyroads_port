"""SkyRoads adapters for the dos_re 3.0 deterministic replay artifact.

The interactive player remains responsible only for acquiring host input.
This module turns that input into the same :class:`dos_re.replay.ReplayArtifact`
used by oracle/candidate verification: one stable frame timeline, embedded base
continuation state, immutable events, execution identity, and persistent
base-relative boundaries.
"""
from __future__ import annotations

import hashlib
import types
from datetime import datetime
from pathlib import Path
from typing import Callable

from dos_re.input_demo import mouse_sample
from dos_re.replay import (
    CanonicalState,
    ContinuationState,
    ExecutionProfile,
    ReplayArtifact,
    ReplayDriver,
    ReplayEvent,
    ReplayPoint,
    machine_projection,
)
from dos_re.snapshot import (
    apply_runtime_continuation,
    capture_runtime_continuation,
)
from dos_re.snapshot_headless import capture_dos_state
from dos_re.snapshot_headless import _restore_dos_state


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TIMELINE_ID = "skyroads-presented-frame-boundaries-v1"
PROJECTION_SCHEMA = "skyroads-complete-machine-v1"


def point(ordinal: int) -> ReplayPoint:
    return ReplayPoint(int(ordinal), TIMELINE_ID)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _source_identity(paths: list[Path]) -> str:
    """Hash source contents, including dirty worktrees, not merely git HEAD."""
    h = hashlib.sha256()
    files: list[Path] = []
    for path in paths:
        if path.is_dir():
            files.extend(path.rglob("*.py"))
        elif path.exists():
            files.append(path)
    for path in sorted(set(p.resolve() for p in files), key=lambda p: p.as_posix()):
        try:
            rel = path.relative_to(PROJECT_ROOT.resolve()).as_posix()
        except ValueError:
            rel = path.as_posix()
        payload = path.read_bytes()
        h.update(len(rel).to_bytes(4, "little"))
        h.update(rel.encode("utf-8"))
        h.update(len(payload).to_bytes(8, "little"))
        h.update(payload)
    return h.hexdigest()


def execution_profile(rt, *, role: str | None = None) -> ExecutionProfile:
    """Describe the exact SkyRoads execution configuration being recorded."""
    hooks = getattr(rt.cpu, "replacement_hooks", {})
    inferred_role = "candidate" if hooks else "oracle"
    role = inferred_role if role is None else role
    if role not in ("oracle", "candidate"):
        raise ValueError("role must be oracle or candidate")
    exe_path = Path(rt.program.exe.path)
    runtime_hash = _source_identity([
        PROJECT_ROOT / "dos_re" / "dos_re",
        PROJECT_ROOT / "skyroads",
        PROJECT_ROOT / "scripts" / "play.py",
    ])
    device_hash = _source_identity([
        PROJECT_ROOT / "dos_re" / "dos_re" / name
        for name in ("dos.py", "memory.py", "pic.py", "sblaster.py", "snapshot_headless.py")
    ])
    overrides = tuple(
        f"{cs:04x}:{ip:04x}:{getattr(rt.cpu, 'hook_names', {}).get((cs, ip), 'unnamed')}"
        for cs, ip in sorted(hooks)
    )
    profile_id = "skyroads-oracle" if role == "oracle" else "skyroads-hooked"
    return ExecutionProfile(
        profile_id=profile_id,
        role=role,
        implementation=("interpreted-original" if role == "oracle"
                        else "interpreter-with-region-overrides"),
        image=f"sha256:{_sha256_file(exe_path)}",
        runtime=f"sha256:{runtime_hash}",
        devices=f"sha256:{device_hash}",
        continuation_schema="dos-re-real-mode-continuation-v1",
        projection_schema=PROJECTION_SCHEMA,
        overrides=overrides if role == "candidate" else (),
    )


def cpuless_execution_profile() -> ExecutionProfile:
    """Identity of the standalone recovered corpus (no CPU/interpreter)."""
    exe_path = PROJECT_ROOT / "assets" / "SKYROADS.EXE"
    runtime_hash = _source_identity([
        PROJECT_ROOT / "dos_re" / "dos_re",
        PROJECT_ROOT / "skyroads",
        PROJECT_ROOT / "scripts" / "play_cpuless.py",
    ])
    device_hash = _source_identity([
        PROJECT_ROOT / "dos_re" / "dos_re" / name
        for name in ("dos.py", "memory.py", "pic.py", "sblaster.py", "snapshot_headless.py")
    ])
    return ExecutionProfile(
        profile_id="skyroads-cpuless",
        role="candidate",
        implementation="standalone-recovered-corpus-no-cpu",
        image=f"sha256:{_sha256_file(exe_path)}",
        runtime=f"sha256:{runtime_hash}",
        devices=f"sha256:{device_hash}",
        continuation_schema="skyroads-cpuless-continuation-v1",
        projection_schema="skyroads-semantic-state-v1",
        overrides=("skyroads:recovered-corpus",),
    )


def _capture_with_driver_state(
    rt, *, event_cursor: int,
    last_mouse: tuple[float, float, int] | None,
) -> ContinuationState:
    state = capture_runtime_continuation(rt, event_cursor=event_cursor).normalized()
    metadata = dict(state.metadata)
    metadata["skyroads_replay_driver"] = {
        "last_mouse": None if last_mouse is None else list(last_mouse),
    }
    return ContinuationState(
        state.schema_id, metadata, state.regions, state.event_cursor).normalized()


def _capture_cpuless(
    rt, regs: dict, *, event_cursor: int,
    last_mouse: tuple[float, float, int] | None,
) -> ContinuationState:
    dos_state = capture_dos_state(rt.dos, rt.mem)
    regions = {"memory": bytes(rt.mem.data)}
    file_regions = {}
    for handle, file_handle in sorted(rt.dos.files.items()):
        name = f"dos-file-{handle}"
        regions[name] = bytes(file_handle.data)
        file_regions[str(handle)] = name
    dos_state["file_regions"] = file_regions
    return ContinuationState(
        "skyroads-cpuless-continuation-v1",
        {
            "regs": {str(k): int(v) for k, v in regs.items()},
            "clock": int(rt.clock),
            "dos": dos_state,
            "skyroads_replay_driver": {
                "last_mouse": None if last_mouse is None else list(last_mouse),
            },
        },
        regions,
        event_cursor,
    ).normalized()


class SkyroadsReplayRecorder:
    """Drop-in player recorder that publishes a ReplayArtifact on stop."""

    def __init__(
        self, *, root: Path, name: str,
        metadata: dict[str, object] | None = None,
    ) -> None:
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "_"
                       for ch in str(name).strip()) or "skyroads"
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        self.directory = Path(root) / f"replay_{safe}_{stamp}"
        self.metadata = dict(metadata or {})
        self._events: list[ReplayEvent] = []
        self._start_boundary = 0
        self._profile: ExecutionProfile | None = None
        self._base_state = None
        self._rt = None
        self._capture_current = None
        self._active = False
        self._last_mouse: tuple[float, float, int] | None = None

    @property
    def active(self) -> bool:
        return self._active

    @property
    def event_count(self) -> int:
        return len(self._events)

    def start(self, rt, *, boundary: int, write_start_snapshot: bool = True) -> Path:
        del write_start_snapshot  # replay artifacts always embed their base state
        if self._active:
            raise RuntimeError("replay recording is already active")
        self._start_boundary = max(0, int(boundary))
        requested_role = self.metadata.get("execution_role")
        self._profile = execution_profile(
            rt, role=None if requested_role is None else str(requested_role))
        self._capture_current = lambda cursor, mouse: _capture_with_driver_state(
            rt, event_cursor=cursor, last_mouse=mouse)
        self._base_state = self._capture_current(0, None)
        self._rt = rt
        self._active = True
        return self.directory

    def start_cpuless(
        self, rt, regs_supplier: Callable[[], dict], *, boundary: int = 0,
    ) -> Path:
        """Begin a detached CPUless recording with explicit native registers."""
        if self._active:
            raise RuntimeError("replay recording is already active")
        self._start_boundary = max(0, int(boundary))
        self._profile = cpuless_execution_profile()
        self._capture_current = lambda cursor, mouse: _capture_cpuless(
            rt, regs_supplier(), event_cursor=cursor, last_mouse=mouse)
        self._base_state = self._capture_current(0, None)
        self._rt = rt
        self._active = True
        return self.directory

    def _relative(self, boundary: int) -> int:
        return max(0, int(boundary) - self._start_boundary)

    def _append(self, boundary: int, channel: str, payload: dict) -> None:
        if not self._active:
            raise RuntimeError("replay recording is not active")
        self._events.append(ReplayEvent(
            point(self._relative(boundary)), len(self._events), channel, payload))

    def record_scan(self, *, boundary: int, scancode: int) -> None:
        self._append(boundary, "scan", {"value": int(scancode) & 0xFF})

    def record_mouse(
        self, *, boundary: int, u: float, v: float, buttons: int,
    ) -> tuple[float, float, int]:
        sample = mouse_sample(u, v, buttons)
        if sample != self._last_mouse:
            self._append(boundary, "mouse", {
                "u": sample[0], "v": sample[1], "buttons": sample[2],
            })
            self._last_mouse = sample
        return sample

    def record_dos_key(
        self, *, boundary: int, scancode: int, text: str, value: int,
    ) -> None:
        self._append(boundary, "dos-key", {
            "value": int(value) & 0xFFFF,
            "scancode": int(scancode) & 0xFF,
            "text": str(text),
        })

    def stop(self, *, boundary: int) -> Path:
        if not self._active or self._profile is None or self._base_state is None:
            raise RuntimeError("replay recording is not active")
        end = point(self._relative(boundary))
        metadata = {
            **self.metadata,
            "artifact_kind": "oracle-verifiable-replay",
            "timeline_id": TIMELINE_ID,
            "start_boundary": self._start_boundary,
            "end_ordinal": end.ordinal,
            "recording_profile": self._profile.profile_id,
            "recorded_at": datetime.now().isoformat(timespec="seconds"),
        }
        artifact = ReplayArtifact.create(
            self.directory,
            timeline_id=TIMELINE_ID,
            events=self._events,
            metadata=metadata,
        )
        artifact.register_profile(
            self._profile, base_point=point(0), base_state=self._base_state)
        if end.ordinal and self._rt is not None and self._capture_current is not None:
            endpoint = self._capture_current(len(self._events), self._last_mouse)
            artifact.cache(
                self._profile, end, endpoint,
                metadata={"kind": "recording-end"},
            )
        self._active = False
        self._rt = None
        self._capture_current = None
        return self.directory


class SkyroadsReplayPlayback:
    """Player-compatible event view over a ReplayArtifact."""

    def __init__(self, artifact: ReplayArtifact):
        self.artifact = artifact
        meta = artifact.metadata
        self.manifest = {"metadata": meta}
        self.events = artifact.events
        self._index = 0
        self._last_mouse: tuple[float, float, int] | None = None
        profile_id = str(meta["recording_profile"])
        matches = [profile for profile, _ in artifact.profiles()
                   if profile.profile_id == profile_id]
        if len(matches) != 1:
            raise ValueError(f"recording profile is missing: {profile_id!r}")
        self.recording_profile = matches[0]

    @classmethod
    def load(cls, path: str | Path) -> "SkyroadsReplayPlayback":
        return cls(ReplayArtifact.open(path))

    @property
    def is_cold_start(self) -> bool:
        # The base continuation is embedded even when capture began at power-on.
        return False

    def snapshot_path(self) -> Path:
        raise ValueError("ReplayArtifact embeds continuation state; it has no snapshot path")

    @property
    def next_event_index(self) -> int:
        return self._index

    @property
    def end_boundary(self) -> int:
        return int(self.manifest["metadata"]["end_ordinal"])

    @property
    def mouse_present_hint(self) -> bool:
        return bool(self.manifest["metadata"].get("mouse_present", False))

    @property
    def exhausted(self) -> bool:
        return self._index >= len(self.events)

    def finished(self, boundary: int) -> bool:
        return int(boundary) >= self.end_boundary

    def reset(self) -> None:
        self._index = 0
        self._last_mouse = None

    def base_state(self):
        return self.artifact.restore(self.recording_profile, point(0))

    def apply_to_runtime(
        self, boundary: int, rt, *,
        deliver: Callable = lambda runtime, scan: None,
        single: bool = False,
    ) -> int:
        boundary = max(0, int(boundary))
        applied = 0
        while self._index < len(self.events):
            event = self.events[self._index]
            if event.point.ordinal > boundary:
                break
            payload = event.payload
            if event.channel == "scan":
                deliver(rt, int(payload["value"]) & 0xFF)
            elif event.channel == "dos-key":
                rt.dos.key_queue.append(int(payload["value"]) & 0xFFFF)
            elif event.channel == "mouse":
                self._last_mouse = (
                    float(payload["u"]), float(payload["v"]),
                    int(payload["buttons"]) & 0xFF,
                )
            else:
                raise ValueError(f"unknown SkyRoads replay channel: {event.channel!r}")
            self._index += 1
            applied += 1
            if single:
                break
        if self._last_mouse is not None:
            setter = getattr(rt.dos, "set_mouse_norm", None)
            if setter is not None:
                setter(*self._last_mouse)
        return applied


def restore_recording_base(rt, playback: SkyroadsReplayPlayback) -> None:
    """Restore the embedded profile base into an already configured shell."""
    state = playback.base_state()
    if state.schema_id == "dos-re-real-mode-continuation-v1":
        apply_runtime_continuation(rt, state)
        return
    if state.schema_id != "skyroads-cpuless-continuation-v1":
        raise ValueError(f"unsupported SkyRoads replay base: {state.schema_id!r}")

    # A detached CPUless base still carries the historical machine image and a
    # full register bundle, so the original interpreter can resume as oracle.
    from dos_re.cpu import CPUState

    regs = state.metadata["regs"]
    cpu_fields = CPUState.__dataclass_fields__
    cpu_raw = {name: int(regs[name]) for name in cpu_fields if name in regs}
    cpu_raw["flags"] = int(regs.get(
        "flags", regs.get("_flags", regs.get("_flags_in", cpu_raw.get("flags", 2)))))
    machine = ContinuationState(
        "dos-re-real-mode-continuation-v1",
        {
            "cpu": cpu_raw,
            "instruction_count": int(state.metadata["clock"]),
            "halted": False,
            "call_depth": 0,
            "dos": state.metadata["dos"],
        },
        state.regions,
        state.event_cursor,
    )
    apply_runtime_continuation(rt, machine)


def apply_cpuless_recording_base(rt, playback: SkyroadsReplayPlayback) -> dict:
    """Restore either VM-backed or CPUless artifact base into a CPUless shell."""
    state = playback.base_state().normalized()
    if state.schema_id == "skyroads-cpuless-continuation-v1":
        regs = dict(state.metadata["regs"])
        rt.clock = int(state.metadata["clock"])
        dos_state = state.metadata["dos"]
    elif state.schema_id == "dos-re-real-mode-continuation-v1":
        regs = dict(state.metadata["cpu"])
        rt.clock = int(state.metadata["instruction_count"])
        dos_state = state.metadata["dos"]
    else:
        raise ValueError(f"unsupported SkyRoads replay base: {state.schema_id!r}")
    if len(state.regions["memory"]) != len(rt.mem.data):
        raise ValueError("SkyRoads CPUless replay memory size mismatch")
    rt.mem.data[:] = state.regions["memory"]
    _restore_dos_state(types.SimpleNamespace(
        dos=rt.dos, program=types.SimpleNamespace(memory=rt.mem)), dos_state)
    for handle_text, region_name in dos_state.get("file_regions", {}).items():
        handle = int(handle_text)
        if handle not in rt.dos.files or region_name not in state.regions:
            raise ValueError(f"missing replay state for DOS file handle {handle}")
        rt.dos.files[handle].data[:] = state.regions[region_name]
    return regs


class SkyroadsReplayDriver(ReplayDriver):
    """Replay one recorded SkyRoads execution profile at exact frame points."""

    def __init__(self, frontend, args, rt, artifact: ReplayArtifact,
                 profile: ExecutionProfile):
        self.frontend = frontend
        self.args = args
        self.rt = rt
        self.artifact = artifact
        self._profile = profile
        self._point = point(0)
        self._event_cursor = 0
        self._last_mouse: tuple[float, float, int] | None = None

    @property
    def profile(self) -> ExecutionProfile:
        return self._profile

    @property
    def current_point(self) -> ReplayPoint:
        return self._point

    def capture(self) -> ContinuationState:
        return _capture_with_driver_state(
            self.rt, event_cursor=self._event_cursor,
            last_mouse=self._last_mouse)

    def restore(self, state: ContinuationState, restored_point: ReplayPoint) -> None:
        apply_runtime_continuation(self.rt, state)
        driver_state = state.metadata.get("skyroads_replay_driver", {})
        raw_mouse = driver_state.get("last_mouse")
        self._last_mouse = (
            None if raw_mouse is None
            else (float(raw_mouse[0]), float(raw_mouse[1]), int(raw_mouse[2]))
        )
        self._event_cursor = state.event_cursor
        self._point = restored_point

    def replay_to(self, artifact: ReplayArtifact, target: ReplayPoint) -> None:
        if artifact is not self.artifact:
            raise ValueError("driver was created for another replay artifact")
        if target.timeline_id != TIMELINE_ID:
            raise ValueError("target uses another SkyRoads replay timeline")
        if target.ordinal < self._point.ordinal:
            raise ValueError("SkyRoads replay driver cannot run backwards")
        events = artifact.events
        while self._point.ordinal < target.ordinal:
            ordinal = self._point.ordinal
            while self._event_cursor < len(events):
                event = events[self._event_cursor]
                if event.point.ordinal > ordinal:
                    break
                self._apply_event(event)
                self._event_cursor += 1
            if self._last_mouse is not None:
                setter = getattr(self.rt.dos, "set_mouse_norm", None)
                if setter is not None:
                    setter(*self._last_mouse)
            self.frontend.advance_frame(self.rt, self.args, ordinal)
            self._point = point(ordinal + 1)

    def project(self) -> CanonicalState:
        return machine_projection(self.capture(), schema_id=PROJECTION_SCHEMA)

    def _apply_event(self, event: ReplayEvent) -> None:
        payload = event.payload
        if event.channel == "scan":
            self.frontend.deliver_input(self.rt, int(payload["value"]) & 0xFF)
        elif event.channel == "dos-key":
            self.rt.dos.key_queue.append(int(payload["value"]) & 0xFFFF)
        elif event.channel == "mouse":
            self._last_mouse = (
                float(payload["u"]), float(payload["v"]),
                int(payload["buttons"]) & 0xFF,
            )
        else:
            raise ValueError(f"unknown SkyRoads replay channel: {event.channel!r}")
