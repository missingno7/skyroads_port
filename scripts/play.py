"""The single SkyRoads execution, verification and release launch pipeline.

Examples::

    python scripts/play.py
    python scripts/play.py --composition faithful
    python scripts/play.py --profile verification --composition faithful \
        --play-demo artifacts/demos/replay_name
    python scripts/play.py --profile development --composition cpuless --headless
    python scripts/play.py --profile release --composition cpuless --plan-only  # frontier report

Execution profile controls what dependencies and services are legal.
Composition controls which implementations satisfy the program identities.
Recovery levels are implementation properties, not separate players.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "dos_re"))

from dos_re import player  # noqa: E402
from dos_re.interrupts import deliver_interrupt  # noqa: E402
from skyroads.execution import (  # noqa: E402
    catalog,
    configuration,
    coverage,
    selected_whole_program_provider,
)
from skyroads.pacing import FrameIdle  # noqa: E402
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

    def add_arguments(self, parser) -> None:
        execution = parser.add_argument_group("skyroads composition")
        execution.add_argument(
            "--composition",
            choices=(
                "auto", "oracle", "faithful", "play",
                "behavioral", "vmless", "cpuless",
            ),
            default="auto",
            help="implementation composition (auto selects practical faithful play "
                 "for development, faithful for verification, and cpuless for "
                 "detached/release)",
        )
        execution.add_argument(
            "--rebuild", action="store_true",
            help="regenerate the selected generated corpus before launch",
        )
        execution.add_argument(
            "--no-sound", action="store_true",
            help="run without SkyRoads sound hardware",
        )
    def program_identity(self, args):
        return "skyroads:1.0"

    def execution_configuration(self, args):
        return configuration(args.profile, args.composition)

    def execution_coverage(self, args):
        return coverage()

    def execution_implementations(self, args):
        return catalog()

    def launch(self, args, plan):
        provider = selected_whole_program_provider(plan)
        bootstrap_artifacts = plan.bootstrap_artifact_paths()
        if provider == "baseline:generated-vmless":
            from skyroads.vmless_backend import launch
            return launch(args, bootstrap_artifacts=bootstrap_artifacts)
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
        return create_game_runtime(
            args.exe,
            game_root=args.game_root,
            command_tail=args.dos_args,
            enable_sound=not args.no_sound,
            capture_sb_pcm=self._capture_sb(args),
        )

    def load_snapshot_runtime(self, args, snapshot_dir):
        return load_game_snapshot(
            args.exe,
            snapshot_dir,
            game_root=args.game_root,
            enable_sound=not args.no_sound,
            capture_sb_pcm=self._capture_sb(args),
        )

    def advance_frame(self, rt, args, frame: int) -> None:
        for _ in range(max(0, args.timer_irqs_per_frame)):
            deliver_interrupt(rt, 0x08)
        try:
            rt.cpu.run(args.steps_per_frame)
        except FrameIdle:
            pass

    def verification_drivers(self, args, plan, artifact):
        provider = selected_whole_program_provider(plan)
        if provider not in {"baseline:interpreted-exe"}:
            raise RuntimeError(
                "ReplayArtifact differential verification currently requires a "
                "DOS-memory-backed interpreted composition; select --composition faithful"
            )
        from skyroads.replay import (
            SkyroadsReplayDriver,
            execution_profile,
            recording_base,
            recording_profile,
        )

        oracle = self.create_runtime(args)
        candidate = self.create_runtime(args)
        self.bind_execution_plan(candidate, plan)
        oracle_profile = recording_profile(artifact)
        candidate_profile = execution_profile(candidate, role="candidate")
        known_profiles = {profile.profile_id for profile, _ in artifact.profiles()}
        if candidate_profile.profile_id not in known_profiles:
            artifact.register_profile(
                candidate_profile,
                base_point=artifact.cached_points(oracle_profile)[0],
                base_state=recording_base(artifact),
            )
        return (
            SkyroadsReplayDriver(
                self, args, oracle, artifact,
                oracle_profile,
            ),
            SkyroadsReplayDriver(
                self, args, candidate, artifact,
                candidate_profile,
            ),
        )

    def create_audio_sink(self, pygame, rt, args):
        if args.audio != "adlib":
            return None
        from skyroads.audio import SkyroadsAudioSink
        sink = SkyroadsAudioSink(pygame, rt, args.present_hz)
        return sink if sink.available else None


def main(argv: list[str] | None = None) -> int:
    return player.main(SkyroadsFrontend(ROOT), argv, description=__doc__)


if __name__ == "__main__":
    raise SystemExit(main())
