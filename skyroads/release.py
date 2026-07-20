"""Development-time closed-world export factory for SkyRoads."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from dos_re.execution import plan_execution
from dos_re.export import (
    ExportError,
    ExportFile,
)
from skyroads.execution import catalog, configuration, coverage

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = "skyroads/release_launcher.py"
BOOT_DIR = ROOT / "artifacts" / "boot_image"


def _payload_paths() -> tuple[str, ...]:
    """Compute the same all-lazy-import closure enforced by lint_cpuless."""
    tool = ROOT / "dos_re" / "tools" / "lint_cpuless.py"
    spec = importlib.util.spec_from_file_location(
        "_dos_re_lint_cpuless", tool)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load payload analyzer: {tool}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return tuple(module.runtime_payload(
        [(ROOT / LAUNCHER).resolve()],
        ROOT,
        ("dos_re.cpu", "dos_re.cpu386", "skyroads.lifted"),
        ("dos_re", "skyroads"),
        {
            "dos_re": (ROOT / "dos_re" / "dos_re").resolve(),
            "skyroads": (ROOT / "skyroads").resolve(),
        },
    ))


def _destination(relative: str) -> str:
    path = Path(relative)
    if path.parts[:2] == ("dos_re", "dos_re"):
        return (Path("dos_re") / Path(*path.parts[2:])).as_posix()
    return path.as_posix()


def export_factory():
    """Return the package-ready plan and exact import/data closure."""
    plan = plan_execution(
        configuration("release", "cpuless"), coverage(), catalog())
    required_boot_files = (
        BOOT_DIR / "state.json",
        BOOT_DIR / "memory_1mb.bin",
        BOOT_DIR / "manifest.json",
    )
    missing = [path for path in required_boot_files if not path.is_file()]
    if missing:
        raise ExportError(
            "release boot image is missing; build it with "
            "scripts/build_boot_image.py before export: "
            + ", ".join(str(path) for path in missing)
        )
    boot_manifest = json.loads(
        (BOOT_DIR / "manifest.json").read_text(encoding="utf-8"))
    poison = boot_manifest.get("poison", {})
    if not poison.get("enabled") or poison.get("code_bytes_present_after") != 0:
        raise ExportError(
            "release boot image is not code-free; rebuild it with poisoning enabled"
        )
    files = [
        ExportFile(ROOT / relative, _destination(relative))
        for relative in _payload_paths()
    ]

    # The detached provider boots from the code-free recovered image and still
    # consumes the original data files. The original executable is forbidden.
    data_roots = (
        (BOOT_DIR, Path("artifacts/boot_image")),
        (ROOT / "assets", Path("assets")),
    )
    for root, destination_root in data_roots:
        if not root.exists():
            continue
        for source in sorted(path for path in root.rglob("*") if path.is_file()):
            if source.suffix.lower() in {".exe", ".com"}:
                continue
            files.append(ExportFile(
                source,
                (destination_root / source.relative_to(root)).as_posix(),
            ))
    return plan, tuple(files), LAUNCHER
