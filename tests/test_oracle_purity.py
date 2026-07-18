"""``create_game_runtime(install_replacements=False)`` must be a PURE oracle.

Regression pin for a bug that made a cold-start differential lie. The flag used
to guard only the ``from . import hooks`` import inside create_game_runtime --
which gates nothing, because hooks register at decoration time and
``skyroads.hooks`` is transitively imported by nearly every entry point (this
test file's own imports included) long before the call. The registry was
already populated, dos_re's create_runtime installed it unconditionally, and 31
replacements rode onto the "pure" oracle -- among them the deliberately
behaviour-changing ``fade_loop_tick_gate`` optimisation, which suppresses one
1010:6168 call per frame. The differential dutifully reported a palette
divergence and blamed its candidate for what the ORACLE was doing.

So the load-bearing case is the one below where ``skyroads.hooks`` is imported
FIRST: a test that only ever ran in a fresh process would have passed
throughout the bug's lifetime.

CI has no game files: skip when assets/SKYROADS.EXE is missing (same pattern as
test_skyroads_boot.py).
"""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_EXE = ROOT / "assets" / "SKYROADS.EXE"
if not _EXE.is_file():
    pytest.skip("assets/SKYROADS.EXE not present — game files are never committed",
                allow_module_level=True)

from dos_re.hooks import assert_pure_oracle, registry  # noqa: E402

import skyroads.hooks  # noqa: E402,F401  -- THE failure mode: registry populated
from skyroads.runtime import create_game_runtime  # noqa: E402


def test_registry_is_actually_populated() -> None:
    """Guards the guard: if nothing is registered, the purity assertions below
    would pass vacuously and stop pinning anything."""
    assert len(registry.replacements) > 0


def test_pure_oracle_carries_no_game_replacements() -> None:
    rt = create_game_runtime(_EXE, install_replacements=False)
    live = registry.installed_on(rt.cpu)
    assert live == [], (
        f"oracle is not pure: {len(live)} replacement(s) installed -- "
        + ", ".join(f"{cs:04X}:{ip:04X} {name}" for (cs, ip), name in live))
    assert_pure_oracle(rt.cpu)          # the harness-facing form must agree


def test_play_path_still_installs_the_replacements() -> None:
    """The fix must not disarm normal play: the fade optimisation and the rest
    of the recovered corpus are legitimate there and must stay wired."""
    rt = create_game_runtime(_EXE, install_replacements=True)
    live = registry.installed_on(rt.cpu)
    assert len(live) == len(registry.replacements)
    with pytest.raises(RuntimeError, match="oracle is NOT pure"):
        assert_pure_oracle(rt.cpu)


def test_fade_loop_tick_gate_is_the_hook_that_must_not_reach_the_oracle() -> None:
    """Names the specific offender, so a future re-registration under a
    different key does not quietly re-open the hole."""
    key = (0x1010, 0x434A)
    assert key in registry.replacements
    assert registry.replacements[key].name == "fade_loop_tick_gate"
    pure = create_game_runtime(_EXE, install_replacements=False)
    play = create_game_runtime(_EXE, install_replacements=True)
    assert key not in pure.cpu.replacement_hooks
    assert key in play.cpu.replacement_hooks
