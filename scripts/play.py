"""The single SkyRoads execution, verification and release launch pipeline.

Examples::

    python scripts/play.py
    python scripts/play.py --level 14
    python scripts/play.py --profile verification --composition workbench-auto --play-replay artifacts/replays/replay_name --verify-start 0 --verify-end 10
    python scripts/play.py --profile development --composition generated-detached --headless
    python scripts/play.py --profile release --composition generated-detached --plan-only

Execution profile controls what dependencies and services are legal.
Composition controls which implementations satisfy the program identities.
Recovery levels are implementation properties, not separate players.
"""
from __future__ import annotations

from copy import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "dos_re"))

from dos_re import player  # noqa: E402
from dos_re.dos import ConsoleInputWouldBlock  # noqa: E402
from dos_re.execution import (  # noqa: E402
    DependencyCapability,
    GENERATED_VMLESS_CARRIER,
    INTERPRETED_CPU_CARRIER,
)
from dos_re.interrupts import deliver_interrupt  # noqa: E402
from dos_re.replay import (  # noqa: E402
    GUEST_INSTRUCTION_COORDINATE,
    ReplayError,
)
from skyroads.identities import PROGRAM_ID  # noqa: E402
from skyroads.gameplay_region import reset_gameplay_region_for_restore  # noqa: E402
from skyroads.native.exe_image import build_program_image  # noqa: E402
from skyroads.launch_inputs import (  # noqa: E402
    install_direct_level_launch,
    validate_level,
)
from skyroads.execution import (  # noqa: E402
    FRAME_PARK_SERVICE_ID,
    catalog,
    configuration,
    coverage,
    features,
    format_provider_diagnostics,
    PRACTICE_LEVEL_FEATURE_ID,
    selected_whole_program_provider,
    services,
)
from skyroads.product_features import SkyroadsFeatureState  # noqa: E402
from skyroads.presentation.features import (  # noqa: E402
    ENHANCED_STEREO_AUDIO_FEATURE_ID,
    NATIVE_3D_RENDERER_FEATURE_ID,
    NATIVE_FAITHFUL_AUDIO_FEATURE_ID,
    TWEENING_FEATURE_ID,
    WIDESCREEN_FEATURE_ID,
)
from skyroads.presentation.runtime import install_presentation  # noqa: E402
from skyroads.presentation.renderer import DEBUG_RENDER_MODES  # noqa: E402
from skyroads.pacing import (  # noqa: E402
    begin_frame_park,
    FrameIdle,
    install_frame_park,
    suspend_frame_park,
)
from skyroads.device_config import capture_sound_blaster_pcm  # noqa: E402
from skyroads.runtime import create_game_runtime, load_game_snapshot  # noqa: E402


class SkyroadsFrontend(player.GameFrontend):
    name = "skyroads"
    default_exe = str(ROOT / "assets" / "SKYROADS.EXE")
    default_game_root = str(ROOT / "assets")
    default_steps_per_frame = 48_000
    default_timer_irqs_per_frame = 6
    # The guest programs PIT channel 0 to 180 Hz and software-divides it by six:
    # authoritative gameplay is 30 Hz. Host presentation is an independent
    # clock and may redraw/interpolate the latest state more often.
    default_simulation_hz = 30
    default_present_hz = 60
    # ``adlib`` is the emulated-device reference. ``native-faithful`` consumes
    # the same exact OPL/DMA command streams through opl3_fast and the recovered
    # shipped PCM catalog. ``native-stereo`` is a separate presentation-only
    # spatial enhancement over that same verified command/effect stream.
    audio_choices = ("adlib", "native-faithful", "native-stereo", "off")
    # 3x produces a 960x720 client area before window chrome and Windows DPI
    # scaling, which can place the title bar above a 768-line desktop. Keep the
    # portable default at 640x480; larger displays can opt into --scale 3.
    default_scale = 2
    semantic_replay_coordinate = "skyroads:main-loop-or-input-boundary:v1"
    # An interpreted render/transition can require several million guest
    # instructions even though it represents one game boundary.  This is only
    # a runaway guard for offline seeking; FrameIdle/input-block normally stop
    # execution much earlier.
    offline_semantic_seek_budget = 20_000_000
    offline_semantic_seek_chunks = 8

    def add_arguments(self, parser) -> None:
        execution = parser.add_argument_group("skyroads composition")
        execution.add_argument(
            "--composition",
            choices=(
                "auto", "oracle", "workbench-auto",
                "faithful-product", "generated-detached",
            ),
            default="auto",
            help="product composition (auto selects faithful-product for "
                 "development/verification and generated-detached for "
                 "detached/release)",
        )
        execution.add_argument(
            "--level",
            type=validate_level,
            help="launch the requested 0..29 level through the selected "
                 "loader and the canonical gameplay provider",
        )
        execution.add_argument(
            "--practice-level-position",
            type=lambda value: int(value, 0),
            help="record a behavioral feature event that enters level select "
                 "at the raw 0..0x2AAA road position",
        )
        execution.add_argument(
            "--no-sound", action="store_true",
            help="run without SkyRoads sound hardware",
        )
        presentation = parser.add_argument_group("native gameplay presentation")
        presentation.add_argument(
            "--renderer", choices=("original", "native-3d"), default="original",
            help="presentation backend; native-3d is a read-only semantic scene renderer "
                 "for the complete loaded-level lifecycle, including generated transitions",
        )
        presentation.add_argument(
            "--widescreen", action="store_true",
            help="expand native gameplay to the available width while preserving "
                 "the physical 4:3 recovered centre",
        )
        presentation.add_argument(
            "--tweening", action="store_true",
            help="interpolate presentation-only ship/camera state between fixed ticks",
        )
        presentation.add_argument(
            "--render-debug", choices=DEBUG_RENDER_MODES, default="final",
            help="native-3d view: complete enhanced frame, recovered terrain, wireframe "
                 "polygons, stable source IDs, collision structures, or original oracle frame",
        )

    def replay_metadata(self, args):
        metadata = super().replay_metadata(args)
        metadata.update({
            "capture_execution_profile": args.profile,
            "capture_composition": args.composition,
            "audio": args.audio,
            "sound_enabled": not args.no_sound,
            "practice_level_position": args.practice_level_position,
            "direct_level": args.level,
        })
        return metadata

    def replay_projection_schema(self, args, runtime) -> str:
        from skyroads.replay import PROJECTION_SCHEMA
        return PROJECTION_SCHEMA

    def apply_replay_metadata(self, args, metadata) -> None:
        super().apply_replay_metadata(args, metadata)
        if "audio" in metadata:
            args.audio = str(metadata["audio"])
        if "sound_enabled" in metadata:
            # Explicit --no-sound is a stricter execution capability choice
            # and remains authoritative when replaying a corpus captured with
            # sound hardware present. The resulting candidate profile is kept
            # distinct; this never promotes cross-profile evidence as trusted.
            args.no_sound = args.no_sound or not bool(metadata["sound_enabled"])
        if "practice_level_position" in metadata:
            value = metadata["practice_level_position"]
            args.practice_level_position = None if value is None else int(value)
        if "direct_level" in metadata:
            value = metadata["direct_level"]
            args.level = None if value is None else validate_level(value)

    def program_identity(self, args):
        return PROGRAM_ID

    def execution_configuration(self, args):
        if args.practice_level_position is not None \
                and not (args.record_replay or args.play_replay):
            raise ValueError(
                "--practice-level-position changes authoritative state and "
                "must be used with --record-replay (or restored from one)"
            )
        enabled_features = []
        if args.practice_level_position is not None:
            enabled_features.append(PRACTICE_LEVEL_FEATURE_ID)
        if args.renderer == "native-3d":
            enabled_features.append(NATIVE_3D_RENDERER_FEATURE_ID)
        if args.widescreen:
            enabled_features.append(WIDESCREEN_FEATURE_ID)
        if args.tweening:
            enabled_features.append(TWEENING_FEATURE_ID)
        if args.audio in {"native-faithful", "native-stereo"}:
            enabled_features.append(NATIVE_FAITHFUL_AUDIO_FEATURE_ID)
        if args.audio == "native-stereo":
            enabled_features.append(ENHANCED_STEREO_AUDIO_FEATURE_ID)
        generated_only = (
            args.composition == "generated-detached"
            or (args.composition == "auto" and args.profile in {"detached", "release"})
        )
        if args.renderer == "native-3d" and generated_only:
            raise ValueError(
                "--renderer native-3d requires the selected faithful native gameplay "
                "island; generated-detached currently has no gameplay-region adapter"
            )
        return configuration(
            args.profile,
            args.composition,
            requested_capabilities=self.requested_capabilities(args),
            enabled_features=tuple(enabled_features),
        )

    def execution_coverage(self, args):
        return coverage()

    def execution_implementations(self, args):
        return catalog()

    def execution_services(self, args):
        return services()

    def execution_features(self, args):
        return features()

    def format_execution_plan(self, args, plan):
        report = super().format_execution_plan(args, plan)
        report += "\n" + format_provider_diagnostics(plan)
        if args.level is not None:
            report += f"\ndirect launch level: {args.level}"
        report += (
            "\npresentation requested: "
            f"renderer={args.renderer}, audio={args.audio}, "
            f"widescreen={str(args.widescreen).lower()}, "
            f"tweening={str(args.tweening).lower()}, "
            f"debug-view={args.render_debug}, "
            f"simulation-hz={args.simulation_hz}, "
            f"presentation-hz={args.present_hz}"
        )
        return report

    def bind_execution_plan(self, runtime, plan, *,
                            carrier_id=INTERPRETED_CPU_CARRIER) -> None:
        super().bind_execution_plan(runtime, plan, carrier_id=carrier_id)
        install_direct_level_launch(
            runtime, getattr(runtime, "_skyroads_direct_level_request", None),
        )
        if plan.features and not hasattr(runtime, "_skyroads_feature_state"):
            runtime._skyroads_feature_state = SkyroadsFeatureState(plan.features)
        if FRAME_PARK_SERVICE_ID in {
            service.service_id for service in plan.services
        }:
            install_frame_park(runtime)
        options = getattr(runtime, "_skyroads_presentation_options", None)
        if options is None:
            selected_features = {item.feature_id for item in plan.features}
            options = SimpleNamespace(
                renderer=("native-3d" if NATIVE_3D_RENDERER_FEATURE_ID in selected_features
                          else "original"),
                widescreen=WIDESCREEN_FEATURE_ID in selected_features,
                tweening=TWEENING_FEATURE_ID in selected_features,
                render_debug="final",
                game_root=str(getattr(runtime.dos, "game_root", ROOT / "assets")),
                present_hz=self.default_present_hz,
                simulation_hz=self.default_simulation_hz,
            )
        install_presentation(runtime, options)
        runtime._dos_re_product_diagnostics = lambda: self.runtime_diagnostics(
            runtime, options,
        )

    @staticmethod
    def runtime_diagnostics(runtime, args) -> tuple[str, ...]:
        presentation = getattr(runtime, "_skyroads_presentation", None)
        if presentation is None:
            return ()
        state = presentation.diagnostics()
        dispatcher = getattr(runtime, "execution_regions", None)
        active = bool(dispatcher is not None and dispatcher.active)
        coverage = getattr(runtime, "_skyroads_gameplay_coverage", {})
        paths = coverage.get("paths", {})
        levels = sorted(coverage.get("levels", ()))
        path_text = ", ".join(
            f"{name}={count}" for name, count in sorted(paths.items())
        ) or "none yet"
        frame_park = bool(getattr(
            runtime.cpu, "_skyroads_frame_park_installed", False,
        ))
        diagnostics = [
            "runtime acceleration: frame-park="
            f"{str(frame_park).lower()} selected-cpu-bindings="
            f"{len(runtime.cpu.replacement_hooks)} "
            "live-differential-comparison=false",
            f"native gameplay island: {'active' if active else 'inactive'}",
            "gameplay presentation owner: "
            f"{'native-3d' if state.active else 'generated/original'} "
            f"phase={state.ownership_phase} execution-region="
            f"{'active' if state.execution_region_active else 'inactive'}",
            f"presentation: renderer={state.renderer} audio={args.audio} "
            f"widescreen={str(state.widescreen).lower()} "
            f"tweening={str(state.tweening).lower()}",
            f"render world: view={state.debug_view} rows={state.visible_rows} "
            f"vertices={state.world_vertices} triangles={state.world_triangles} "
            f"mesh={state.world_digest} camera="
            f"{'exact-rle-reference' if state.debug_view in ('exact-projection', 'original') else 'continuous-world-lens'}",
            "render reference: TREKDAT-phase="
            f"{state.projection_phase if state.projection_phase is not None else 'none'} "
            f"draw-calls={state.projection_draw_calls} "
            f"spans={state.projection_spans} trace={state.projection_digest}",
            "render source: selected-level="
            f"{state.level if state.level is not None else 'none'} "
            "road-archive-entry="
            f"{state.road_archive_index if state.road_archive_index is not None else 'none'} "
            f"ship-screen={state.ship_screen} ship-frame="
            f"{state.ship_sprite_index if state.ship_sprite_index is not None else 'none'}",
            f"presentation rates: authoritative={state.simulation_hz}Hz "
            f"host={state.presentation_hz}Hz last-tick="
            f"{state.last_semantic_tick if state.last_semantic_tick is not None else 'none'}",
            "gameplay replay coverage: levels="
            + (",".join(str(item) for item in levels) if levels else "none")
            + f" paths={path_text}",
            "verification projection: gameplay semantic state + scene contents; "
            "machine continuation only at generated seams",
        ]
        audio_sink = getattr(runtime, "_skyroads_audio_sink", None)
        if audio_sink is not None and hasattr(audio_sink, "diagnostics"):
            audio = audio_sink.diagnostics()
            diagnostics.append(
                "audio evidence: "
                f"mode={audio['mode']} renderer={audio['music_renderer']} "
                f"opl-writes={audio['opl_writes']} sfx-plays={audio['sfx_plays']} "
                f"stereo={audio['stereo']} enhancement={audio['enhancement']}"
            )
            pacing = audio.get("pacing", {})
            diagnostics.append(
                "audio output: synthesis="
                f"{pacing.get('python_synthesis', 'unknown')} "
                f"buffer={pacing.get('buffer_depth_ms', 0)}ms "
                f"chunk={pacing.get('queue_chunk_ms', 0)}ms "
                f"underruns={pacing.get('underruns', 0)} "
                f"synth-mean={pacing.get('synthesis_mean_ms', 0)}ms "
                f"synth-max={pacing.get('synthesis_max_ms', 0)}ms "
                f"callback-max={pacing.get('command_callback_max_ms', 0)}ms "
                f"output-gap-max={pacing.get('max_output_block_gap_ms', 0)}ms"
            )
        return tuple(diagnostics)

    def recording_finished(self, rt, args):
        coverage = getattr(rt, "_skyroads_recording_gameplay_coverage", {})
        if hasattr(rt, "_skyroads_recording_gameplay_coverage"):
            del rt._skyroads_recording_gameplay_coverage
        return {"gameplay_coverage": {
            "schema": "skyroads:gameplay-lifecycle-coverage/v1",
            "levels": sorted(int(item) for item in coverage.get("levels", ())),
            "paths": {
                str(name): int(count)
                for name, count in sorted(coverage.get("paths", {}).items())
            },
            "semantic_ticks": int(coverage.get("ticks", 0)),
            "composition": args.composition,
        }}

    def apply_replay_state(self, runtime, state) -> None:
        reset_gameplay_region_for_restore(runtime)
        super().apply_replay_state(runtime, state)

    def materialize_replay_profile_base(
        self, args, runtime, artifact, *, source_profile,
        requested_profile, source_state,
    ):
        """Build a strict SkyRoads point-zero base for one selected carrier.

        DOS-memory-backed carriers share authoritative CPU/DOS memory, but a
        generated capture can poison recovered instruction bytes and each
        carrier can select a different optional Sound Blaster topology.  Those
        differences are projected once into a new profile-local base.  The
        capture cache itself is never restored under another identity.
        """
        if source_profile.image != requested_profile.image:
            raise ValueError("SkyRoads replay executable identity differs")
        if source_profile.continuation_schema != requested_profile.continuation_schema:
            raise ValueError("SkyRoads replay continuation schema differs")

        from skyroads.execution import BOOT_DIR
        from skyroads.native.exe_image import build_program_image
        from skyroads.replay import project_base_to_runtime_devices

        executable_ranges = ()
        executable_image = None
        manifest_path = BOOT_DIR / "manifest.json"
        requested_carrier = selected_whole_program_provider(
            args.execution_plan
        )
        if (
            requested_carrier == "baseline:interpreted-exe"
            and manifest_path.is_file()
        ):
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            poison = manifest.get("poison", {})
            ranges = tuple(
                (int(start), int(length))
                for start, length in poison.get("ranges", ())
            )
            captured_memory = source_state.regions["memory"]
            capture_is_poisoned = bool(
                poison.get("enabled")
                and ranges
                and all(
                    not any(captured_memory[start:start + length])
                    for start, length in ranges
                )
            )
            if capture_is_poisoned:
                executable_ranges = ranges
                executable_image = build_program_image(args.exe, 0x1010)

        projected = project_base_to_runtime_devices(
            runtime,
            source_state,
            executable_ranges=executable_ranges,
            executable_image=executable_image,
            executable_base=0x10100,
        )
        point_zero = artifact.timeline_coordinate(
            player.ReplayPoint(0, artifact.timeline_id)
        )
        value = point_zero.value
        cpu_state = projected.metadata.get("cpu")
        if (
            isinstance(value, dict)
            and value.get("kind") == "frame-park"
            and isinstance(cpu_state, dict)
            and int(cpu_state.get("cs", 0)) == 0x1010
            and int(cpu_state.get("ip", 0)) == 0x22FB
        ):
            # 22FB is after the MOV that loads the wait operand.  It cannot be
            # a portable pre-operation boundary: an interrupt may change the
            # operand before resume while the saved flags still describe the
            # old comparison. Canonicalize the profile-local base to 22F8 so
            # every carrier re-evaluates MOV+CMP after external effects.
            metadata = dict(projected.metadata)
            metadata["cpu"] = dict(cpu_state, ip=0x22F8)
            projected = type(projected)(
                projected.schema_id,
                metadata,
                projected.regions,
                projected.event_cursor,
            ).normalized()
        return projected

    def recording_started(self, rt, args, *, record_event) -> None:
        # Replay evidence is scoped to the captured interval.  A player may
        # spend minutes in menus or gameplay before recording begins, so the
        # cumulative runtime diagnostics cannot be copied into the artifact.
        rt._skyroads_recording_gameplay_coverage = {
            "paths": {}, "levels": set(), "ticks": 0,
        }
        if args.practice_level_position is None:
            return
        rt._skyroads_feature_state.request_level_position(
            args.practice_level_position,
            ordinal=0,
            record_event=record_event,
        )

    def apply_replay_event(self, rt, args, event) -> None:
        state = getattr(rt, "_skyroads_feature_state", None)
        if state is None:
            return super().apply_replay_event(rt, args, event)
        state.accept_replay_event(event)

    @staticmethod
    def _apply_product_features(rt) -> None:
        state = getattr(rt, "_skyroads_feature_state", None)
        if state is not None:
            state.apply_main_loop_boundary(rt)

    def launch(self, args, plan):
        provider = selected_whole_program_provider(plan)
        bootstrap_artifacts = plan.bootstrap_artifact_paths()
        if provider == "baseline:generated-vmless":
            from skyroads.vmless_backend import launch
            return launch(
                args,
                bootstrap_artifacts=bootstrap_artifacts,
                bind_plan=lambda runtime: self.bind_execution_plan(
                    runtime, plan, carrier_id=GENERATED_VMLESS_CARRIER),
                frontend=self,
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
        return capture_sound_blaster_pcm(args)

    def create_runtime(self, args):
        args.execution_plan.require_capability(
            DependencyCapability.ORIGINAL_EXE,
            consumer=f"{type(self).__name__}.create_runtime",
        )
        args.execution_plan.require_capability(
            DependencyCapability.INTERPRETER,
            consumer=f"{type(self).__name__}.create_runtime",
        )
        runtime = create_game_runtime(
            args.exe,
            game_root=args.game_root,
            command_tail=args.dos_args,
            enable_sound=not args.no_sound,
            capture_sb_pcm=self._capture_sb(args),
        )
        runtime._skyroads_direct_level_request = args.level
        runtime._skyroads_presentation_options = args
        return runtime

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
        runtime = load_game_snapshot(
            args.exe,
            snapshot_dir,
            game_root=args.game_root,
            enable_sound=not args.no_sound,
            capture_sb_pcm=self._capture_sb(args),
        )
        runtime._skyroads_direct_level_request = args.level
        runtime._skyroads_presentation_options = args
        return runtime

    def replay_point_coordinate(
        self,
        rt,
        args,
        *,
        point_ordinal: int | None = None,
        event_cursor: int,
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
            "timeline_position": ordinal,
            "event_cursor": int(event_cursor),
            "kind": kind,
        }
        boundary_identity = getattr(
            rt, "_skyroads_replay_boundary_identity", None,
        )
        if boundary_identity is not None:
            value["boundary_identity"] = str(boundary_identity)
        if kind == "guest-fallback":
            value.update({
                "guest_instruction_count": int(rt.cpu.instruction_count),
                "guest_budget": max(1, int(args.steps_per_frame)),
                "fallback_reason": "semantic-boundary-not-reached-within-budget",
                "machine_position": {
                    "cs": int(rt.cpu.s.cs),
                    "ip": int(rt.cpu.s.ip),
                },
            })
        return self.semantic_replay_coordinate, value

    def _advance_to_semantic_boundary(
        self, rt, args, *, offline_replay: bool = False,
    ) -> str:
        generated_driver = getattr(rt, "_skyroads_vmless_driver", None)
        if generated_driver is not None:
            if not generated_driver.frame():
                from dos_re.cpu import HaltExecution
                raise HaltExecution
            kind = generated_driver.last_boundary_kind
            if kind not in {"frame-park", "input-block", "guest-fallback"}:
                raise ReplayError(
                    f"generated provider produced no semantic boundary: {kind!r}"
                )
            rt._skyroads_replay_boundary_kind = kind
            self._apply_product_features(rt)
            if kind == "input-block":
                raise ConsoleInputWouldBlock
            return kind
        begin_frame_park(rt)
        for _ in range(max(0, args.timer_irqs_per_frame)):
            deliver_interrupt(rt, 0x08)
        # A replay point is a semantic boundary, not an arbitrary slice of
        # interpreter work.  Keep seeking the same boundary without injecting
        # another timer batch: otherwise a slow interpreter records thousands
        # of synthetic points (and IRQ batches) where a generated carrier
        # records one.  Interactive execution uses small cooperative chunks so
        # the canonical viewer can service its host window while loading or a
        # long guest operation is in progress.
        guest_slice = max(1, int(args.steps_per_frame))
        budget = guest_slice
        if offline_replay:
            # Verification follows the recorded game event, not the capture
            # carrier's instruction throughput.  The interactive viewer still
            # uses one bounded presentation slice; offline replay may seek much
            # farther to the same frame park or blocking-input seam.
            budget = max(budget, self.offline_semantic_seek_budget)
            chunks = self.offline_semantic_seek_chunks
        else:
            total_budget = (
                self.offline_semantic_seek_budget
                * self.offline_semantic_seek_chunks
            )
            chunks = max(1, (total_budget + guest_slice - 1) // guest_slice)
        host_yield = getattr(rt, "_dos_re_host_yield", None)
        for chunk in range(chunks):
            try:
                rt.cpu.run(budget)
            except FrameIdle:
                rt._skyroads_replay_boundary_kind = "frame-park"
                rt._skyroads_replay_boundary_identity = getattr(
                    rt.cpu, "_skyroads_frame_park_identity", None,
                )
                self._apply_product_features(rt)
                return "frame-park"
            except ConsoleInputWouldBlock:
                rt._skyroads_replay_boundary_kind = "input-block"
                rt._skyroads_replay_boundary_identity = "dos:console-input"
                self._apply_product_features(rt)
                raise
            if host_yield is not None and chunk + 1 < chunks:
                host_yield()
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
        if expected.get("timeline_position", frame + 1) != frame + 1:
            raise ReplayError("invalid SkyRoads semantic timeline position")
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
            machine_position = expected.get("machine_position")
            if machine_position is not None and machine_position != {
                "cs": int(rt.cpu.s.cs),
                "ip": int(rt.cpu.s.ip),
            }:
                raise ReplayError(
                    f"implementation reached the wrong machine position at "
                    f"diagnostic fallback point {frame + 1}: expected "
                    f"{machine_position!r}, got "
                    f"{{'cs': {int(rt.cpu.s.cs)}, 'ip': {int(rt.cpu.s.ip)}}}")
            return
        try:
            actual = self._advance_to_semantic_boundary(
                rt, args, offline_replay=True,
            )
        except ConsoleInputWouldBlock:
            actual = "input-block"
        if actual != expected.get("kind"):
            dispatcher = getattr(rt, "execution_regions", None)
            active_region = (
                dispatcher.active_region_id
                if dispatcher is not None and dispatcher.active else None
            )
            bp = int(rt.cpu.s.bp) & 0xFFFF
            ss = int(rt.cpu.s.ss) & 0xFFFF
            stack_words = {
                offset: int(rt.cpu.mem.rw(ss, (bp + offset) & 0xFFFF))
                for offset in (-12, -10, -8, -6, -4, -2, 4, 6, 8)
            }
            generated_driver = getattr(rt, "_skyroads_vmless_driver", None)
            seen_boundaries = tuple(sorted(
                getattr(generated_driver, "_seen", ()),
            ))
            raise ReplayError(
                f"SkyRoads semantic boundary {frame + 1} differs: "
                f"expected {expected.get('kind')!r}, got {actual!r}; "
                f"machine={int(rt.cpu.s.cs):04X}:{int(rt.cpu.s.ip):04X}, "
                f"instructions={int(rt.cpu.instruction_count)}, "
                f"active_region={active_region!r}, "
                f"seen_boundaries={seen_boundaries!r}, "
                f"stack_words={stack_words!r}"
            )
        expected_identity = expected.get("boundary_identity")
        actual_identity = getattr(
            rt, "_skyroads_replay_boundary_identity", None,
        )
        if (
            expected_identity is not None
            and actual_identity != expected_identity
        ):
            raise ReplayError(
                f"SkyRoads semantic boundary {frame + 1} identity differs: "
                f"expected {expected_identity!r}, got {actual_identity!r}"
            )

    def verification_drivers(self, args, plan, artifact):
        provider = selected_whole_program_provider(plan)
        if provider not in {
            "baseline:interpreted-exe", "baseline:generated-vmless",
        }:
            raise RuntimeError(
                "ReplayArtifact differential verification currently requires a "
                "DOS-memory-backed interpreted or generated VMless carrier"
            )
        from skyroads.replay import (
            capture_base,
            capture_profile,
            SkyroadsReplayDriver,
        )

        source_profile = capture_profile(artifact)
        source_base = capture_base(artifact)
        base_point = artifact.cached_points(source_profile)[0]

        def ensure_profile_base(runtime, profile_args, profile):
            known = {
                item.profile_id: item for item, _ in artifact.profiles()
            }
            if profile.profile_id in known:
                artifact.require_profile(profile)
                return
            materialized = self.materialize_replay_profile_base(
                profile_args,
                runtime,
                artifact,
                source_profile=source_profile,
                requested_profile=profile,
                source_state=source_base,
            )
            artifact.register_profile(
                profile,
                base_point=base_point,
                base_state=materialized,
            )

        oracle_args = copy(args)
        oracle_args.profile = "development"
        oracle_args.composition = "oracle"
        oracle_args.execution_plan = self.resolve_execution_plan(oracle_args)
        oracle = self.create_runtime(oracle_args)
        self.bind_execution_plan(oracle, oracle_args.execution_plan)
        current_oracle_profile = self.replay_profile(oracle_args, oracle)
        ensure_profile_base(oracle, oracle_args, current_oracle_profile)
        if provider == "baseline:generated-vmless":
            from skyroads.vmless_backend import create_planned_runtime
            candidate, _ = create_planned_runtime(
                args,
                bootstrap_artifacts=plan.bootstrap_artifact_paths(),
                bind_plan=lambda runtime: self.bind_execution_plan(
                    runtime, plan, carrier_id=GENERATED_VMLESS_CARRIER,
                ),
            )
        else:
            candidate = self.create_runtime(args)
            self.bind_execution_plan(candidate, plan)
        candidate_profile = self.replay_profile(args, candidate)
        ensure_profile_base(candidate, args, candidate_profile)
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
        if args.no_sound or args.audio == "off":
            return None
        if args.audio in {"native-faithful", "native-stereo"}:
            from skyroads.audio.sink import (
                EnhancedStereoAudioSink, NativeFaithfulAudioSink,
            )
            sink_type = (
                EnhancedStereoAudioSink
                if args.audio == "native-stereo"
                else NativeFaithfulAudioSink
            )
            return sink_type(
                pygame, rt, args.present_hz, game_root=args.game_root,
            )
        from skyroads.audio.sink import SkyroadsAudioSink
        sink = SkyroadsAudioSink(pygame, rt, args.present_hz)
        return sink if sink.available else None

    def create_gpu_frame_presenter(self, rt, args):
        if args.renderer != "native-3d":
            return None
        from skyroads.presentation.moderngl_presenter import ModernGLFramePresenter
        presentation = None if rt is None else getattr(rt, "_skyroads_presentation", None)
        return ModernGLFramePresenter(presentation)

    def render_presentation_frame(self, rt, args, *, interpolation: float):
        presentation = getattr(rt, "_skyroads_presentation", None)
        if presentation is None:
            return self.decode_frame(rt)
        return presentation.frame(
            lambda: self.decode_frame(rt), interpolation=interpolation,
        )


def main(argv: list[str] | None = None) -> int:
    return player.main(SkyroadsFrontend(ROOT), argv, description=__doc__)


if __name__ == "__main__":
    raise SystemExit(main())
