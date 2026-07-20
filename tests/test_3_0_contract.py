"""SkyRoads consumes the final dos_re 3.0 lifecycle without compatibility APIs."""
from __future__ import annotations

from pathlib import Path

from dos_re import player
from scripts.play import SkyroadsFrontend

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN = (
    "dos_re.input_demo",
    "dos_re.pm_input_demo",
    "dos_re.pm_player",
    "dos_re.coverage",
    "dos_re.hook_taxonomy",
    "dos_re.lift.standalone",
    "ExecutionProfile",
    "boot_vmless_image",
    "independence_report",
    "install_vmless_graph",
    "(DOS_RE 2.0 stage",
    "hidden compat input, tier",
    "skyroads.cpuless_overrides",
    "skyroads.islands",
    "skyroads.recovered_overrides",
    "--record-demo",
    "--play-demo",
    "--demo-continue",
    "--demo-dir",
)

REMOVED_PATHS = (
    "skyroads/cpuless_overrides.py",
    "skyroads/frame_verify.py",
    "skyroads/island_bodies.py",
    "skyroads/island_shadows.py",
    "skyroads/islands.py",
    "skyroads/recovered_overrides",
    "skyroads/verification.py",
    "scripts/overnight_loop.sh",
    "tools/absorption_ledger.py",
    "tools/display.py",
    "tools/gen_island_manifest.py",
    "tools/run_tests.py",
)


def test_active_port_has_no_legacy_replay_or_player_api() -> None:
    files = [
        path
        for root in ("skyroads", "scripts", "tools", "docs")
        for path in (ROOT / root).rglob("*")
        if path.suffix in {".py", ".md", ".toml", ".sh"}
        and "history" not in path.parts
    ]
    offenders = {
        str(path.relative_to(ROOT)): token
        for path in files
        for token in FORBIDDEN
        if token in path.read_text(encoding="utf-8")
    }
    assert not offenders


def test_parallel_authorities_and_compatibility_shims_are_removed() -> None:
    assert not [path for path in REMOVED_PATHS if (ROOT / path).exists()]


def test_single_player_exposes_region_compositions_not_recovery_modes() -> None:
    parser = player.build_arg_parser(SkyroadsFrontend(ROOT))
    action = next(
        action for action in parser._actions if action.dest == "composition")
    assert set(action.choices) == {
        "auto",
        "oracle",
        "generated-functions",
        "authored-candidates",
        "play",
        "generated-cpu",
        "generated-abi",
    }
    assert "vmless" not in action.choices
    assert "cpuless" not in action.choices
