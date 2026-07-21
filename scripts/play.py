"""The single SkyRoads execution, verification and release launch pipeline.

Examples::

    python scripts/play.py
    python scripts/play.py --composition authored-candidates
    python scripts/play.py --profile verification --composition generated-functions --play-replay artifacts/replays/replay_name --verify-start 0 --verify-end 10
    python scripts/play.py --profile development --composition generated-abi --headless
    python scripts/play.py --profile release --composition generated-abi --plan-only

Execution profile controls what dependencies and services are legal.
Composition controls which implementations satisfy the program identities.
Recovery levels are implementation properties, not separate players.
"""
from __future__ import annotations

from copy import copy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "dos_re"))

from dos_re import player  # noqa: E402
from dos_re.dos import ConsoleInputWouldBlock  # noqa: E402
from dos_re.execution import CPU_MODEL_BACKEND, DependencyCapability  # noqa: E402
from dos_re.interrupts import deliver_interrupt  # noqa: E402
from dos_re.replay import (  # noqa: E402
    GUEST_INSTRUCTION_COORDINATE,
    ReplayError,
)
from skyroads.identities import PROGRAM_ID  # noqa: E402
from skyroads.execution import (  # noqa: E402
    FRAME_PARK_SERVICE_ID,
    catalog,
    configuration,
    coverage,
    selected_whole_program_provider,
    services,
)
from skyroads.pacing import (  # noqa: E402
    FrameIdle,
    install_frame_park,
    suspend_frame_park,
)
from skyroads.runtime import create_game_runtime, load_game_snapshot  # noqa: E402


class SkyroadsFrontend(player.GameFrontend):
    name = "skyroads"
    default_exe = str(ROOT / "assets" / "SKYROADS.EXE")
    default_game_root = str(ROOT / "assets")
    default_steps_per_frame = 48_000
    default_timer_irqs_per_frame = 6
    default_present_hz = 30
    # 3x produces a 960x720 client area before window chrome and Windows DPI
    # scaling, which can place the title bar above a 768-line desktop. Keep the
    # portable default at 640x480; larger displays can opt into --scale 3.
    default_scale = 2
    semantic_replay_coordinate = "skyroads:main-loop-or-input-boundary:v1"

    def add_arguments(self, parser) -> None:
        execution = parser.add_argument_group("skyroads composition")
        execution.add_argument(
            "--composition",
            choices=(
                "auto", "oracle", "generated-functions",
                "authored-candidates", "play",
                "generated-cpu", "generated-abi",
            ),
            default="auto",
            help="implementation composition (generated-functions mixes literal "
                 "generated functions with interpreter fallback; auto selects "
                 "practical play for development, literal generated functions "
                 "for verification, and the generated ABI-recovered region for "
                 "detached/release)",
        )
        execution.add_argument(
            "--no-sound", action="store_true",
            help="run without SkyRoads sound hardware",
        )

    def replay_metadata(self, args):
        metadata = super().replay_metadata(args)
        metadata.update({
            "capture_execution_profile": args.profile,
            "capture_composition": args.composition,
            "audio": args.audio,
            "sound_enabled": not args.no_sound,
        })
        return metadata

    def apply_replay_metadata(self, args, metadata) -> None:
        super().apply_replay_metadata(args, metadata)
        if "audio" in metadata:
            args.audio = str(metadata["audio"])
        if "sound_enabled" in metadata:
            args.no_sound = not bool(metadata["sound_enabled"])

    def program_identity(self, args):
        return PROGRAM_ID

    def execution_configuration(self, args):
        return configuration(
            args.profile,
            args.composition,
            requested_capabilities=self.requested_capabilities(args),
        )

    def execution_coverage(self, args):
        return coverage()

    def execution_implementations(self, args):
        return catalog()

    def execution_services(self, args):
        return services()

    def bind_execution_plan(self, runtime, plan, *,
                            backend_id=CPU_MODEL_BACKEND) -> None:
        super().bind_execution_plan(runtime, plan, backend_id=backend_id)
        if FRAME_PARK_SERVICE_ID in {
            service.service_id for service in plan.services
        }:
            install_frame_park(runtime)

    def launch(self, args, plan):
        provider = selected_whole_program_provider(plan)
        bootstrap_artifacts = plan.bootstrap_artifact_paths()
        if provider == "baseline:generated-vmless":
            from skyroads.vmless_backend import launch
            return launch(
                args,
                bootstrap_artifacts=bootstrap_artifacts,
                bind_plan=lambda runtime: self.bind_execution_plan(
                    runtime, plan, backend_id=CPU_MODEL_BACKEND),
            )
        if provider == "baseline:generated-cpuless":
            from skyroads.development_guard import (
                arm_cpuless_import_guard,
                report_cpuless_crash,
            )
            from skyroads.cpuless_backend import run
            arm_cpuless_import_guard()
            return run(
                args,
                bootstrap_artifacts=bootstrap_artifacts,
                diagnostics=report_cpuless_crash,
            )
        return super().launch(args, plan)

    def _capture_sb(self, args) -> bool:
        return (
            not args.no_sound
            and getattr(args, "audio", "off") == "adlib"
            and not args.headless
        )

    def create_runtime(self, args):
        args.execution_plan.require_capability(
            DependencyCapability.ORIGINAL_EXE,
            consumer=f"{type(self).__name__}.create_runtime",
        )
        args.execution_plan.require_capability(
            DependencyCapability.INTERPRETER,
            consumer=f"{type(self).__name__}.create_runtime",
        )
        return create_game_runtime(
            args.exe,
            game_root=args.game_root,
            command_tail=args.dos_args,
            enable_sound=not args.no_sound,
            capture_sb_pcm=self._capture_sb(args),
        )

    def load_snapshot_runtime(self, args, snapshot_dir):
        args.execution_plan.require_capability(
            DependencyCapability.SNAPSHOTS,
            consumer=f"{type(self).__name__}.load_snapshot_runtime",
        )
        args.execution_plan.require_capability(
            DependencyCapability.ORIGINAL_EXE,
            consumer=f"{type(self).__name__}.load_snapshot_runtime",
        )
        args.execution_plan.require_capability(
            DependencyCapability.INTERPRETER,
            consumer=f"{type(self).__name__}.load_snapshot_runtime",
        )
        return load_game_snapshot(
            args.exe,
            snapshot_dir,
            game_root=args.game_root,
            enable_sound=not args.no_sound,
            capture_sb_pcm=self._capture_sb(args),
        )

    def replay_point_coordinate(
        self, rt, args, *, point_ordinal: int | None = None,
    ):
        ordinal = 0 if point_ordinal is None else int(point_ordinal)
        kind = getattr(rt, "_skyroads_replay_boundary_kind", None)
        if ordinal == 0 and kind is None:
            kind = "origin"
        if kind not in {
            "origin", "frame-park", "input-block", "guest-fallback",
        }:
            raise ReplayError(
                "SkyRoads replay points must be captured at the main-loop park "
                "or blocking-input boundary")
        value = {
            "sequence": ordinal,
            "kind": kind,
        }
        if kind == "guest-fallback":
            value["guest_instruction_count"] = int(rt.cpu.instruction_count)
        return self.semantic_replay_coordinate, value

    def _advance_to_semantic_boundary(self, rt, args) -> str:
        for _ in range(max(0, args.timer_irqs_per_frame)):
            deliver_interrupt(rt, 0x08)
        start = int(rt.cpu.instruction_count)
        max_guest = max(1_000_000, int(args.steps_per_frame) * 32)
        while int(rt.cpu.instruction_count) - start <= max_guest:
            try:
                rt.cpu.run(max(1, int(args.steps_per_frame)))
            except FrameIdle:
                rt._skyroads_replay_boundary_kind = "frame-park"
                return "frame-park"
            except ConsoleInputWouldBlock:
                rt._skyroads_replay_boundary_kind = "input-block"
                raise
        # Startup and a genuinely long atomic region may not yet expose a
        # semantic seam. Preserve a clearly labelled diagnostic fallback for
        # that point instead of pretending the host dispatch budget is itself
        # semantic. Later points return to frame-park/input boundaries as soon
        # as the program exposes one.
        rt._skyroads_replay_boundary_kind = "guest-fallback"
        return "guest-fallback"

    def advance_frame(self, rt, args, frame: int) -> None:
        self._advance_to_semantic_boundary(rt, args)

    def advance_replay_frame(self, rt, args, frame, coordinate) -> None:
        # Existing v1 artifacts retain their exact low-level diagnostic clock.
        # New recordings use the semantic boundary and do not require a lifted
        # or native implementation to reproduce guest instruction counts.
        if coordinate.schema_id == GUEST_INSTRUCTION_COORDINATE:
            with suspend_frame_park(rt):
                return super().advance_replay_frame(rt, args, frame, coordinate)
        if coordinate.schema_id != self.semantic_replay_coordinate:
            raise ReplayError(
                f"unsupported SkyRoads replay coordinate {coordinate.schema_id!r}")
        expected = coordinate.value
        if not isinstance(expected, dict) or expected.get("sequence") != frame + 1:
            raise ReplayError("invalid SkyRoads semantic replay coordinate")
        if expected.get("kind") == "guest-fallback":
            for _ in range(max(0, args.timer_irqs_per_frame)):
                deliver_interrupt(rt, 0x08)
            target = int(expected["guest_instruction_count"])
            with suspend_frame_park(rt):
                while rt.cpu.instruction_count < target:
                    rt.cpu.step()
                    if rt.cpu.instruction_count > target:
                        raise ReplayError(
                            f"implementation crossed diagnostic fallback point "
                            f"{frame + 1}: {rt.cpu.instruction_count} > {target}; "
                            "the long implementation needs an explicit yield")
            rt._skyroads_replay_boundary_kind = "guest-fallback"
            return
        try:
            actual = self._advance_to_semantic_boundary(rt, args)
        except ConsoleInputWouldBlock:
            actual = "input-block"
        if actual != expected.get("kind"):
            raise ReplayError(
                f"SkyRoads semantic boundary {frame + 1} differs: "
                f"expected {expected.get('kind')!r}, got {actual!r}")

    def verification_drivers(self, args, plan, artifact):
        provider = selected_whole_program_provider(plan)
        if provider not in {"baseline:interpreted-exe"}:
            raise RuntimeError(
                "ReplayArtifact differential verification currently requires a "
                "DOS-memory-backed interpreted composition; select "
                "--composition generated-functions or authored-candidates"
            )
        from skyroads.replay import (
            capture_base,
            capture_profile,
            SkyroadsReplayDriver,
        )

        oracle_args = copy(args)
        oracle_args.profile = "development"
        oracle_args.composition = "oracle"
        oracle_args.execution_plan = self.resolve_execution_plan(oracle_args)
        oracle = self.create_runtime(oracle_args)
        self.bind_execution_plan(oracle, oracle_args.execution_plan)
        current_oracle_profile = self.replay_profile(oracle_args, oracle)
        base = capture_base(artifact)
        known_profiles = {
            profile.profile_id: profile for profile, _ in artifact.profiles()
        }
        if current_oracle_profile.profile_id not in known_profiles:
            artifact.register_profile(
                current_oracle_profile,
                base_point=artifact.cached_points(capture_profile(artifact))[0],
                base_state=base,
            )
        else:
            artifact.require_profile(current_oracle_profile)
        candidate = self.create_runtime(args)
        self.bind_execution_plan(candidate, plan)
        candidate_profile = self.replay_profile(args, candidate)
        known_profile_ids = {
            profile.profile_id for profile, _ in artifact.profiles()
        }
        if candidate_profile.profile_id not in known_profile_ids:
            artifact.register_profile(
                candidate_profile,
                base_point=artifact.cached_points(capture_profile(artifact))[0],
                base_state=base,
            )
        else:
            artifact.require_profile(candidate_profile)
        return (
            SkyroadsReplayDriver(
                self, args, oracle, artifact,
                current_oracle_profile,
            ),
            SkyroadsReplayDriver(
                self, args, candidate, artifact,
                candidate_profile,
            ),
        )

    def create_audio_sink(self, pygame, rt, args):
        if args.audio != "adlib":
            return None
        from skyroads.audio.sink import SkyroadsAudioSink
        sink = SkyroadsAudioSink(pygame, rt, args.present_hz)
        return sink if sink.available else None


def main(argv: list[str] | None = None) -> int:
    return player.main(SkyroadsFrontend(ROOT), argv, description=__doc__)


if __name__ == "__main__":
    raise SystemExit(main())
