"""Export a closed-world standalone SkyRoads artifact."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "dos_re"))

from dos_re.export import ExportError, export_release  # noqa: E402
from skyroads.release import export_factory  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output")
    args = parser.parse_args(argv)
    try:
        plan, files, launcher = export_factory()
        manifest = export_release(plan, files, args.output, launcher=launcher)
    except ExportError as error:
        parser.error(str(error))
    print(f"exported {len(manifest.files)} files")
    print(f"plan digest: {manifest.plan_digest}")
    print(f"artifact: {Path(args.output).resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
