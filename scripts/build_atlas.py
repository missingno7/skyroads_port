"""Regenerate SkyRoads retained Recovery IR and its persistent Execution Atlas.

The first retained entry census is seeded from the already generated CPUless
corpus.  That corpus is discovery evidence only: after generation,
``recovery/recovery_ir.json`` is the static authority consumed by the Atlas.
No Atlas code decodes instructions.

Usage:
    python scripts/build_atlas.py --snapshot artifacts/SNAPSHOT_DIR
    python scripts/build_atlas.py --from-ir
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "dos_re"))

from dos_re.atlas import ExecutionAtlas  # noqa: E402
from skyroads.identities import (  # noqa: E402
    IMAGE,
    PROGRAM,
    PROGRAM_ROOT,
    RECOVERY_ENTRY_FUNCTION,
    function_identity,
)
from skyroads.pacing import (  # noqa: E402
    FADE_WAIT_IP,
    MENU_ANIM_WAIT_IP,
    PACING_SPIN_IP,
)

RECOVERY = ROOT / "recovery"
IR = RECOVERY / "recovery_ir.json"
ATLAS = RECOVERY / "atlas"
PRODUCT_PROFILES = (
    "game/oracle", "game/faithful", "game/play", "game/behavioral",
    "game/vmless", "game/cpuless",
)


def _entry_census() -> tuple[str, ...]:
    entries = []
    for path in (ROOT / "skyroads" / "recovered").glob("func_1010_*.py"):
        offset = path.stem.rsplit("_", 1)[-1]
        entries.append(f"1010:{int(offset, 16):04X}")
    if not entries:
        raise RuntimeError("the generated recovered corpus has no function entries")
    return tuple(sorted(set(entries)))


def _generate_ir(snapshot: Path) -> None:
    if not snapshot.is_dir():
        raise FileNotFoundError(
            f"snapshot does not exist: {snapshot}\n"
            "Pass a complete oracle snapshot with --snapshot.")
    RECOVERY.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="skyroads-atlas-") as temp:
        temp = Path(temp)
        entries = temp / "entries.txt"
        entries.write_text(
            "".join(f"{entry}\n" for entry in _entry_census()),
            encoding="utf-8", newline="\n")
        boundaries = temp / "boundary_heads.txt"
        boundaries.write_text(
            "".join(
                f"1010:{offset:04X}\n"
                for offset in sorted({
                    PACING_SPIN_IP, FADE_WAIT_IP, MENU_ANIM_WAIT_IP,
                })
            ),
            encoding="utf-8", newline="\n")
        subprocess.run([
            sys.executable, str(ROOT / "dos_re" / "tools" / "irgen.py"),
            "--exe", str(ROOT / "assets" / "SKYROADS.EXE"),
            "--snapshot", str(snapshot),
            "--game-root", str(ROOT / "assets"),
            "--entries-file", str(entries),
            "--boundary-heads", f"@{boundaries}",
            "--out", str(IR),
        ], cwd=ROOT, check=True)


def _build_atlas() -> ExecutionAtlas:
    temp = RECOVERY / ".atlas-new"
    if temp.exists():
        shutil.rmtree(temp)
    atlas = ExecutionAtlas.create(temp, program=PROGRAM)
    atlas.import_recovery_ir(
        IR, image=IMAGE, roots=["1010:61F3"])
    retained_entries = _entry_census()
    from skyroads.hooks import (
        BEHAVIORAL_OVERRIDE_ADAPTERS,
        FAITHFUL_OVERRIDE_ADAPTERS,
        GENERATED_FUNCTION_ADAPTERS,
    )
    retained_offsets = {
        int(entry.split(":")[1], 16) for entry in retained_entries}
    hook_offsets = (
        set(FAITHFUL_OVERRIDE_ADAPTERS)
        | set(BEHAVIORAL_OVERRIDE_ADAPTERS)
        | set(GENERATED_FUNCTION_ADAPTERS)
    )
    manual_hook_nodes = [
        {
            "id": function_identity(offset),
            "kind": "function",
            "label": f"1010:{offset:04X}",
            "metadata": {
                "entry": f"1010:{offset:04X}",
                "liftable": None,
                "manual_recovered_identity": True,
            },
        }
        for offset in sorted(hook_offsets - retained_offsets)
    ]
    atlas.add_manual_facts(
        "skyroads-retained-entry-census-v1",
        nodes=[
            {
                "id": PROGRAM_ROOT,
                "kind": "region",
                "label": "SkyRoads program",
                "metadata": {"role": "product-root"},
            },
            *manual_hook_nodes,
        ],
        edges=[
            {
                "source": PROGRAM_ROOT,
                "target": RECOVERY_ENTRY_FUNCTION,
                "kind": "entry",
                "status": "resolved",
            },
            *(
                {
                    "source": PROGRAM_ROOT,
                    "target": function_identity(int(entry.split(":")[1], 16)),
                    "kind": "retained-recovery-entry",
                    "status": "resolved",
                }
                for entry in retained_entries
            ),
            *(
                {
                    "source": PROGRAM_ROOT,
                    "target": function_identity(offset),
                    "kind": "manually-recovered-entry",
                    "status": "resolved",
                }
                for offset in sorted(hook_offsets - retained_offsets)
            ),
        ],
    )
    for profile in PRODUCT_PROFILES:
        atlas.set_product_roots(profile, [PROGRAM_ROOT])
    for manifest in sorted((RECOVERY / "replays").glob("*/replay.json")):
        atlas.ingest_replay(manifest.parent)
    atlas.validate()
    if ATLAS.exists():
        shutil.rmtree(ATLAS)
    temp.replace(ATLAS)
    return ExecutionAtlas.open(ATLAS)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument(
        "--from-ir", action="store_true",
        help="rebuild normalized Atlas sources/indexes from retained IR only")
    args = parser.parse_args(argv)
    if not args.from_ir:
        if args.snapshot is None:
            parser.error("--snapshot is required unless --from-ir is used")
        _generate_ir(args.snapshot.resolve())
    elif not IR.exists():
        parser.error(f"retained Recovery IR is missing: {IR}")
    atlas = _build_atlas()
    coverage = atlas.coverage_for("game/play")
    print(
        f"SkyRoads Atlas {atlas.identity_digest}: "
        f"{len(atlas.nodes(kind='function'))} functions, "
        f"{len(coverage.reachable)} reachable identities, "
        f"{len(coverage.unresolved_edges)} unresolved frontiers")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
