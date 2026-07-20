#!/usr/bin/env python3
"""Validate a SkyRoads ReplayArtifact by exact cached X→Y replay.

This checks the recording profile itself: restore the nearest persistent
boundary, lazily cache X, replay only X→Y, and compare the complete continuation
state with the state cached when recording stopped.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "dos_re")]

from dos_re import player  # noqa: E402
from dos_re.replay import machine_projection, run_interval  # noqa: E402
from scripts.play import SkyroadsFrontend  # noqa: E402
from skyroads.replay import (  # noqa: E402
    SkyroadsReplayDriver,
    SkyroadsReplayPlayback,
    execution_profile,
    point,
)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("artifact")
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--end", type=int)
    args_cli = ap.parse_args(argv)

    playback = SkyroadsReplayPlayback.load(args_cli.artifact)
    if playback.recording_profile.continuation_schema != \
            "dos-re-real-mode-continuation-v1":
        raise SystemExit(
            "check_replay validates VM-backed recordings; use "
            "verify_cpuless.py for a CPUless session artifact")
    end = playback.end_boundary if args_cli.end is None else args_cli.end
    if not 0 <= args_cli.start <= end <= playback.end_boundary:
        raise SystemExit("require 0 <= start <= end <= recorded end")
    if not playback.artifact.has_cached(playback.recording_profile, point(end)):
        raise SystemExit(
            "the selected endpoint has no trusted cached recording state; "
            "use the recorded end or a previously verified boundary")

    frontend = SkyroadsFrontend(ROOT)
    args = player.build_arg_parser(frontend).parse_args(["--headless"])
    frontend.apply_demo_metadata(args, playback.manifest["metadata"])
    rt = frontend.load_demo_runtime(args, playback)
    frontend.apply_hook_mode(rt, args)
    player.use_real_console_input(rt)

    current = execution_profile(rt, role=playback.recording_profile.role)
    if current != playback.recording_profile:
        raise SystemExit(
            "recording execution identity is stale; runtime/image/devices/"
            "overrides no longer match")
    expected_state = playback.artifact.restore(
        playback.recording_profile, point(end))
    expected = machine_projection(
        expected_state, schema_id=playback.recording_profile.projection_schema)
    driver = SkyroadsReplayDriver(
        frontend, args, rt, playback.artifact, playback.recording_profile)
    result = run_interval(
        playback.artifact, driver, point(args_cli.start), point(end))
    comparison = expected.compare(result.projection)
    if not comparison.equivalent:
        print(f"DIVERGENT {args_cli.start}->{end}")
        for difference in comparison.differences:
            print("  " + difference)
        return 1
    print(
        f"EQUIVALENT {args_cli.start}->{end}; restored from "
        f"{result.restored_from.ordinal}; {comparison.oracle_digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
