"""The canonical repository gate works with clean and enriched checkouts."""
from __future__ import annotations

from pathlib import Path

from scripts import check_all


def test_pytest_command_falls_back_to_serial_without_xdist() -> None:
    assert check_all.pytest_argv("tests/", xdist_available=False) == [
        "-m", "pytest", "tests/", "-q",
    ]


def test_pytest_command_uses_xdist_when_available() -> None:
    assert check_all.pytest_argv("tests/", xdist_available=True) == [
        "-m", "pytest", "tests/", "-q", "-n", "4",
    ]


def test_clean_checkout_requires_actionable_bootstrap_diagnostic(
    tmp_path: Path,
    monkeypatch,
) -> None:
    missing = tuple(tmp_path / name for name in (
        "state.json", "memory_1mb.bin", "manifest.json",
    ))
    monkeypatch.setattr(check_all, "BOOTSTRAP_ARTIFACTS", missing)

    assert check_all._release_plan_expectations() == (
        "execution profile 'release' cannot be planned",
        "unresolved control-flow edges",
        "missing bootstrap artifacts",
        "python scripts/build_boot_image.py",
    )


def test_materialized_bootstrap_still_requires_unresolved_frontier_diagnostic(
    tmp_path: Path,
    monkeypatch,
) -> None:
    present = tuple(tmp_path / name for name in (
        "state.json", "memory_1mb.bin", "manifest.json",
    ))
    for path in present:
        path.write_bytes(b"present")
    monkeypatch.setattr(check_all, "BOOTSTRAP_ARTIFACTS", present)

    assert check_all._release_plan_expectations() == (
        "execution profile 'release' cannot be planned",
        "unresolved control-flow edges",
    )


def test_development_preflight_accepts_a_materialized_build_image(
    tmp_path: Path,
    monkeypatch,
) -> None:
    present = tuple(tmp_path / name for name in (
        "state.json", "memory_1mb.bin", "manifest.json",
    ))
    for path in present:
        path.write_bytes(b"present")
    monkeypatch.setattr(check_all, "BOOTSTRAP_ARTIFACTS", present)

    assert check_all._development_plan_expectations() == (
        0,
        ("execution profile: development", "bound identities:"),
    )


def test_development_preflight_requires_actionable_build_image_diagnostic(
    tmp_path: Path,
    monkeypatch,
) -> None:
    missing = tuple(tmp_path / name for name in (
        "state.json", "memory_1mb.bin", "manifest.json",
    ))
    monkeypatch.setattr(check_all, "BOOTSTRAP_ARTIFACTS", missing)

    assert check_all._development_plan_expectations() == (
        2,
        (
            "missing bootstrap artifacts",
            "python scripts/build_boot_image.py",
        ),
    )
