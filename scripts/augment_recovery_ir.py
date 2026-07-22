"""Add witnessed executable entries to retained SkyRoads Recovery IR.

This is the narrow path for a generated-runtime frontier: decode only the
newly witnessed entries from an explicit *unpoisoned* development snapshot,
merge those immutable records into the retained IR, and emit just their VMless
modules.  It intentionally never re-decodes the existing retained corpus:
different runtime-phase snapshots may contain different mutable code/data.

Usage:
    python scripts/augment_recovery_ir.py --snapshot artifacts/irgen_source
    python scripts/build_atlas.py --from-ir
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
IR = ROOT / "recovery" / "recovery_ir.json"
OBSERVED = ROOT / "recovery" / "observed_entry_points.txt"
EMIT_DIR = ROOT / "skyroads" / "lifted" / "functions"
BOUNDARY_HEADS = ROOT / "recovery" / "boundary_heads.txt"


def _module_path(entry: str) -> Path:
    try:
        segment, offset = entry.split(":", 1)
        if int(segment, 16) != 0x1010:
            raise ValueError
        return EMIT_DIR / f"lifted_1010_{int(offset, 16):04x}.py"
    except ValueError as exc:
        raise ValueError(f"expected a real-mode SkyRoads CS:IP, got {entry!r}") from exc


def _addresses(path: Path) -> tuple[str, ...]:
    values: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        value = line.split("#", 1)[0].strip()
        if value:
            values.append(value.upper())
    return tuple(values)


def _ensure_unpoisoned(snapshot: Path) -> None:
    manifest_path = snapshot / "manifest.json"
    if not snapshot.is_dir():
        raise FileNotFoundError(f"snapshot does not exist: {snapshot}")
    if not manifest_path.is_file():
        return
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("poison", {}).get("enabled"):
        raise RuntimeError(
            "a code-poisoned release image cannot supply Recovery IR bytes; "
            "build an explicit development image with:\n"
            "  python scripts/build_boot_image.py --no-poison --out artifacts/irgen_source"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--snapshot", required=True,
                        help="unpoisoned development snapshot with live code bytes")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--entry", action="append",
                        help="CS:IP entry; defaults to observed_entry_points.txt")
    group.add_argument("--remove", action="append",
                       help="withdraw a wrongly-versioned or superseded entry")
    group.add_argument("--dispatch-entry", action="append",
                       help="interior replay-base re-entry into an existing function")
    args = parser.parse_args(argv)
    snapshot = Path(args.snapshot)
    _ensure_unpoisoned(snapshot)
    if args.remove:
        retained = json.loads(IR.read_text(encoding="utf-8"))
        for entry in (item.upper() for item in args.remove):
            if retained["functions"].pop(entry, None) is None:
                raise RuntimeError(f"retained Recovery IR does not own {entry}")
            path = _module_path(entry)
            if path.exists():
                path.unlink()
        IR.write_text(json.dumps(retained, indent=1) + "\n", encoding="utf-8")
        print("removed Recovery IR entries: " + ", ".join(args.remove))
        return 0

    dispatch_entries = tuple(item.upper() for item in args.dispatch_entry or ())
    if dispatch_entries:
        retained = json.loads(IR.read_text(encoding="utf-8"))
        functions = retained["functions"]
        owners: dict[str, str] = {}
        for address in dispatch_entries:
            _, offset = address.split(":", 1)
            for identity, record in functions.items():
                if any(
                    instruction.get("ip", "").upper() == offset
                    for block in record.get("blocks", ())
                    for instruction in block.get("instructions", ())
                ):
                    owners[address] = identity
                    break
            else:
                raise RuntimeError(
                    f"no retained function contains replay-base entry {address}")
        entries = tuple(sorted(set(owners.values())))
    else:
        entries = tuple(item.upper() for item in args.entry) if args.entry else _addresses(OBSERVED)

    if not entries:
        raise SystemExit("no witnessed entries supplied")
    if not IR.is_file():
        raise FileNotFoundError(f"retained Recovery IR is missing: {IR}")

    with tempfile.TemporaryDirectory(prefix="skyroads-ir-augment-") as tmp:
        tmp_path = Path(tmp)
        entry_file = tmp_path / "entries.txt"
        entry_file.write_text("".join(f"{item}\n" for item in entries), encoding="utf-8")
        dispatch_file = tmp_path / "dispatch_entries.txt"
        if dispatch_entries:
            dispatch_file.write_text(
                "".join(f"{item}\n" for item in dispatch_entries),
                encoding="utf-8",
            )
        fragment = tmp_path / "fragment.json"
        subprocess.run((
            sys.executable, str(ROOT / "dos_re" / "tools" / "irgen.py"),
            "--exe", str(ROOT / "assets" / "SKYROADS.EXE"),
            "--snapshot", str(snapshot),
            "--game-root", str(ROOT / "assets"),
            "--entries-file", str(entry_file),
            "--boundary-heads", f"@{BOUNDARY_HEADS}",
            *(
                ("--dispatch-entries", f"@{dispatch_file}")
                if dispatch_entries else ()
            ),
            "--out", str(fragment),
        ), cwd=ROOT, check=True)
        retained = json.loads(IR.read_text(encoding="utf-8"))
        additions = json.loads(fragment.read_text(encoding="utf-8"))["functions"]
        existing = retained["functions"]
        for identity in entries:
            if identity in existing and not dispatch_entries:
                raise RuntimeError(f"retained Recovery IR already owns {identity}")
            try:
                existing[identity] = additions[identity]
            except KeyError as exc:
                raise RuntimeError(f"irgen did not produce requested entry {identity}") from exc
        IR.write_text(json.dumps(retained, indent=1) + "\n", encoding="utf-8")

        selected = dict(additions)
        selected_document = dict(retained)
        selected_document["functions"] = selected
        selected_ir = tmp_path / "selected.json"
        selected_ir.write_text(json.dumps(selected_document, indent=1) + "\n", encoding="utf-8")
        result = subprocess.run((
            sys.executable, str(ROOT / "dos_re" / "tools" / "liftemit.py"),
            "--from-ir", str(selected_ir),
            "--boundary-heads", f"@{BOUNDARY_HEADS}",
            *(
                ("--dispatch-entries", f"@{dispatch_file}")
                if dispatch_entries else ()
            ),
            "--desmc", "--emit-dir", str(EMIT_DIR),
        ), cwd=ROOT)
        if result.returncode:
            raise SystemExit(result.returncode)
    if dispatch_entries:
        print("added replay-base dispatch entries: " + ", ".join(dispatch_entries))
    else:
        print("added witnessed Recovery IR entries: " + ", ".join(entries))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
