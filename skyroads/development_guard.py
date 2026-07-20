"""Development-only dynamic backstop for the CPUless composition."""
from __future__ import annotations

from pathlib import Path


def arm_cpuless_import_guard() -> None:
    from dos_re.detachment_guard import install_import_guard

    install_import_guard(extra_forbidden=("skyroads.lifted",))


def report_cpuless_crash(exc, *, mem, dos, driver, stage: str) -> None:
    from skyroads.crash_report import write_crash_bundle, print_crash_summary

    root = Path(__file__).resolve().parents[1]
    bundle = write_crash_bundle(
        root / "artifacts" / "crashes",
        exc,
        mem=mem,
        dos=dos,
        frame=driver.frame,
        head=driver.head,
        stage=stage,
    )
    print_crash_summary(bundle, exc, frame=driver.frame)
