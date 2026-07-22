"""Report native-presentation ownership and original visual-state transitions.

This diagnostic replays the immutable input stream through the selected
composition and prints only state changes for either the oracle or candidate
driver. It is intentionally headless and never captures framebuffer pixels as
presentation authority.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "dos_re")]

from dos_re import player  # noqa: E402
from dos_re.replay import ReplayArtifact, ReplayPoint  # noqa: E402
from scripts.play import SkyroadsFrontend  # noqa: E402
from skyroads.bridge.dgroup_view import GameView  # noqa: E402
from skyroads.presentation.runtime import SkyroadsPresentation  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("replay", type=Path)
    parser.add_argument("--composition", default="workbench-auto")
    parser.add_argument(
        "--driver", choices=("oracle", "candidate"), default="oracle",
        help="execution side whose presentation ownership is inspected",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def _snapshot(driver, ordinal: int, presentation=None) -> dict:
    runtime = driver.runtime
    cpu = runtime.cpu
    ds = int(cpu.s.ds) & 0xFFFF
    dispatcher = getattr(runtime, "execution_regions", None)
    palette = bytes(
        component
        for color in runtime.dos.vga_palette
        for component in color[:3]
    )
    memory = cpu.mem

    def stack_word(offset: int) -> int:
        return int(memory.rw(int(cpu.s.ss) & 0xFFFF, offset & 0xFFFF))

    def rw(offset: int) -> int:
        return int(memory.rw(ds, offset))

    track = rw(0x9618) | (rw(0x961A) << 16)
    cached_track = rw(0x0E1C) | (rw(0x0E1E) << 16)
    page = rw(0x9334)
    # Mode 13h display memory is always A000:0000.  The game's A200 value is
    # an off-screen paragraph address used while composing a page; treating it
    # as a VGA start-address selector made earlier transition diagnostics
    # misleading.  Hash indices (and the two logical bands) so palette-only
    # fades remain distinguishable from framebuffer replacement.
    visible_indices = bytes(memory.rb(0xA000, offset)
                            for offset in range(320 * 200))
    coordinate = driver.artifact.timeline_coordinate(
        ReplayPoint(ordinal, driver.artifact.timeline_id),
    )
    result = {
        "ordinal": ordinal,
        "coordinate": coordinate.value,
        "machine": f"{int(cpu.s.cs):04X}:{int(cpu.s.ip):04X}",
        "active_region": (
            None if dispatcher is None else dispatcher.active_region_id
        ),
        "game_state": rw(0x456E),
        "level": rw(0x9332),
        "road_rows": rw(0x41C0),
        "track_position": track,
        "track_row": track >> 16,
        "cached_track_position": cached_track,
        "cached_track_row": cached_track >> 16,
        "lateral_position": rw(0xAF1C),
        "height": rw(0xAF2C),
        "cached_lateral_position": rw(0x0E20),
        "cached_height": rw(0x0E22),
        "cached_sprite": rw(0x0E24),
        "page_selector": page,
        "visible_segment": "A000",
        "visible_indices_sha256": hashlib.sha256(visible_indices).hexdigest()[:12],
        "playfield_indices_sha256": hashlib.sha256(
            visible_indices[:320 * 138]
        ).hexdigest()[:12],
        "dashboard_indices_sha256": hashlib.sha256(
            visible_indices[320 * 138:]
        ).hexdigest()[:12],
        "air_counter": rw(0x456A),
        "offscreen_mode": rw(0x003C),
        "dashboard_segment": rw(0x5478),
        "render_params": [rw(0x0E28 + index * 2) for index in range(8)],
        "shadow_offset": rw(0x0E70),
        "palette_sha256": hashlib.sha256(palette).hexdigest()[:12],
        "palette_peak": max(palette, default=0),
        "last_region_exit": getattr(
            runtime, "_skyroads_last_region_exit", None,
        ),
    }
    if int(cpu.s.cs) == 0x1010 and 0x4331 <= int(cpu.s.ip) <= 0x4467:
        bp = int(cpu.s.bp) & 0xFFFF
        caller_bp = stack_word(bp)
        result["fade_frame"] = {
            "bp": bp,
            "caller_bp": caller_bp,
            "return_ip": stack_word(bp + 2),
            "target_palette": stack_word(bp + 4),
            "source_palette": stack_word(bp + 6),
            "steps": stack_word(bp + 8),
            "outer_return_ip": stack_word(caller_bp + 2),
            "outer_arg0": stack_word(caller_bp + 4),
            "outer_arg1": stack_word(caller_bp + 6),
            "outer_arg2": stack_word(caller_bp + 8),
        }
    if presentation is not None:
        view = GameView(cpu.mem.data, base=ds << 4)
        owns = presentation._observe_ownership(view)
        result["native_presentation_owner"] = owns
        result["ownership_phase"] = presentation._ownership_phase
        if owns:
            scene = presentation._scene()
            result["native_scene"] = {
                "track_position": scene.track_position,
                "track_row": scene.track_row,
                "lateral_position": scene.lateral_position,
                "height": scene.height,
                "ship_sprite": scene.ship_sprite_index,
                "ship_screen": [scene.ship_screen_x, scene.ship_screen_y],
            }
    return result


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    artifact = ReplayArtifact.open(args.replay.resolve())
    frontend = SkyroadsFrontend(ROOT)
    launch_args = player.build_arg_parser(frontend).parse_args([
        "--profile", "verification",
        "--composition", args.composition,
        # Renderer selection is intentionally omitted: this command observes
        # execution ownership and must not create another candidate profile.
        "--play-replay", str(artifact.directory),
        "--headless",
    ])
    frontend.apply_replay_metadata(launch_args, artifact.metadata)
    launch_args.execution_plan = frontend.resolve_execution_plan(launch_args)
    oracle, candidate = frontend.verification_drivers(
        launch_args, launch_args.execution_plan, artifact,
    )
    driver = oracle if args.driver == "oracle" else candidate
    base_point = artifact.cached_points(driver.profile)[0]
    driver.restore(artifact.restore(driver.profile, base_point), base_point)
    launch_args.renderer = "native-3d"
    launch_args.widescreen = False
    launch_args.tweening = False
    launch_args.render_debug = "final"
    presentation = SkyroadsPresentation(driver.runtime, launch_args)
    end = ReplayPoint.from_json(artifact.metadata["end_point"]).ordinal
    records = []
    previous = None
    error = None
    for ordinal in range(end + 1):
        if ordinal:
            try:
                driver.replay_to(
                    artifact, ReplayPoint(ordinal, artifact.timeline_id),
                )
            except Exception as exc:  # diagnostic must retain the last valid state
                error = f"{type(exc).__name__}: {exc}"
                current = _snapshot(driver, ordinal - 1, presentation)
                current["replay_error_at"] = ordinal
                current["replay_error"] = error
                records.append(current)
                break
        current = _snapshot(driver, ordinal, presentation)
        signature = {key: value for key, value in current.items()
                     if key not in {"ordinal", "coordinate"}}
        if signature != previous or ordinal == end:
            records.append(current)
            previous = signature
    if args.json:
        print(json.dumps({"states": records, "error": error},
                         indent=2, sort_keys=True))
    else:
        for item in records:
            print(
                f"{item['ordinal']:4d} {item['machine']} "
                f"region={item['active_region'] or '-'} "
                f"state={item['game_state']} level={item['level']} "
                f"road={item['road_rows']} row={item['track_row']}/"
                f"cached={item['cached_track_row']} "
                f"pos={item['lateral_position']:04X},{item['height']:04X}/"
                f"cached={item['cached_lateral_position']:04X},"
                f"{item['cached_height']:04X} "
                f"page={item['visible_segment']}:{item['visible_indices_sha256']} "
                f"air={item['air_counter']} palette={item['palette_sha256']} "
                f"peak={item['palette_peak']} owner="
                f"{'native' if item.get('native_presentation_owner') else 'original'}:"
                f"{item.get('ownership_phase', '-')} "
                f"params={','.join(f'{value:04X}' for value in item['render_params'])} "
                f"exit={item['last_region_exit'] or '-'} "
                f"fade={('-' if 'fade_frame' not in item else '/'.join(format(item['fade_frame'][name], '04X') for name in ('return_ip', 'outer_return_ip', 'outer_arg0', 'outer_arg1', 'outer_arg2')))}"
            )
        if error:
            print(f"STOPPED: {error}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
