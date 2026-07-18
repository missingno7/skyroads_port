"""The stitch seam's invariants (skyroads/cpuless_overrides.py).

The generated corpus is the frame; overrides patch addresses inside it. Three
properties have to hold or the seam is worse than not having it:

  * with no overrides the composite is EXACTLY the generated program -- which is
    what makes adopting the seam a no-op the existing cold-start differential
    already proves;
  * an override for an address the corpus no longer contains FAILS LOUD -- a
    stale entry that silently stops applying is the failure mode that makes a
    registry untrustworthy as the corpus is regenerated;
  * the generated body stays reachable, so a delegating override can wrap the
    mechanical implementation instead of reimplementing it.
"""
from __future__ import annotations

import pytest

from skyroads import cpuless_overrides as ov


def test_every_override_is_a_shadow_PROVEN_body():
    """The gating invariant, as a test rather than a docstring.

    This used to assert the registry was EMPTY, which was a real gate only while
    it was empty: the first entry would simply have edited it away. So the gate
    is now the property that actually matters -- an address may drive only if the
    exact callable driving it is one dos_re's shadow rung has compared against
    the generated body on real calls, whole contract, no exemptions.

    An override written by hand straight into this dict, with no counterpart in
    island_bodies, is precisely the unproven swap the ladder exists to prevent.
    """
    from skyroads.island_bodies import BODIES

    assert ov.OVERRIDES, "the registry is empty -- nothing is being absorbed"
    for addr, impl in ov.OVERRIDES.items():
        assert BODIES.get(addr) is impl, (
            f"{addr} drives with a callable that is not the shadow-proven body "
            f"from skyroads.island_bodies -- it has no evidence behind it")


def test_installing_the_registry_reports_exactly_what_it_installed():
    installed = ov.install_overrides()
    try:
        assert sorted(installed) == sorted(ov.OVERRIDES)
    finally:
        ov.uninstall_overrides()


def test_address_naming_matches_the_corpus_convention():
    assert ov.module_name("1010:04C0") == "skyroads.recovered.func_1010_04c0"
    assert ov.func_name("1010:04C0") == "func_1010_04c0"


def test_generated_body_is_reachable_for_delegation():
    """A delegating override must be able to call what it wraps."""
    fn = ov.generated("1010:04C0")
    assert callable(fn) and fn.__name__ == "func_1010_04c0"


def test_override_for_a_missing_address_fails_loud():
    """A stale entry must break, not quietly stop applying."""
    ov.OVERRIDES["1010:FFFF"] = lambda *a, **k: None
    try:
        with pytest.raises(RuntimeError, match="no generated counterpart"):
            ov.install_overrides()
    finally:
        ov.OVERRIDES.pop("1010:FFFF", None)
        ov.uninstall_overrides()


def test_shadow_replaces_only_the_named_function():
    """Installing must not disturb the rest of the module's contents."""
    sentinel = object()

    def fake(*a, **k):
        return ({}, {"flags": 0, "fmask": 0, "cost": 0})

    ov.OVERRIDES["1010:04C0"] = fake
    try:
        ov.install_overrides()
        import importlib
        mod = importlib.import_module("skyroads.recovered.func_1010_04c0")
        assert getattr(mod, "func_1010_04c0") is fake
        # the untouched original is still retrievable for delegation
        assert ov.generated("1010:04C0") is not fake
    finally:
        ov.OVERRIDES.pop("1010:04C0", None)
        ov.uninstall_overrides()


def test_override_reaches_callers_imported_BEFORE_installation():
    """The failure mode that made a counter read zero, pinned as a test.

    A generated module binds its callees at import time, so shadowing
    sys.modules only helps imports that have not happened yet. install_shadow
    itself imports the module (via generated()), which eagerly binds ITS callees
    -- so installing along a call chain guarantees the later installs miss
    unless already-bound references are retro-patched.
    """
    import importlib

    # Import a CALLER first, so it binds the real callee before we install.
    caller = importlib.import_module("skyroads.recovered.func_1010_4331")
    callee_name = "func_1010_6168"
    if not hasattr(caller, callee_name):
        pytest.skip("1010:4331 does not bind 1010:6168 directly in this corpus")
    original = getattr(caller, callee_name)

    def fake(*a, **k):
        return ({}, {"flags": 0, "fmask": 0, "cost": 0})

    ov.OVERRIDES["1010:6168"] = fake
    try:
        ov.install_overrides(["1010:6168"])
        assert getattr(caller, callee_name) is fake, (
            "the already-imported caller still holds the ORIGINAL callee -- the "
            "override would silently do nothing for every call through it")
    finally:
        ov.OVERRIDES.pop("1010:6168", None)
        ov.uninstall_overrides()
    assert getattr(caller, callee_name) is original, "uninstall must restore"
