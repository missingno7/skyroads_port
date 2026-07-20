"""Standalone CPUless SkyRoads product launcher.

This file is exported with the closed-world runtime payload. It deliberately
contains no development profile, oracle, replay, snapshot, planner or EXE path.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

PRODUCT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PRODUCT_ROOT))

from skyroads.cpuless_backend import run
from dos_re.bootstrap_runtime import packaged_bootstrap_artifacts


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--frames", type=int, default=0)
    parser.add_argument("--scale", type=int, default=3)
    parser.add_argument("--square-pixels", action="store_true")
    parser.add_argument("--present-hz", type=int, default=30)
    args = parser.parse_args(argv)
    args.rebuild = False
    bootstrap_artifacts = packaged_bootstrap_artifacts(
        PRODUCT_ROOT,
        expected_provider="skyroads-generated-abi-build-image",
    )
    return run(args, bootstrap_artifacts=bootstrap_artifacts)


if __name__ == "__main__":
    raise SystemExit(main())
