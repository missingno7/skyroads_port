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


def test_empty_registry_is_a_provable_no_op():
    """Nothing is shadowed, so the program is bit-for-bit the generated one."""
    assert ov.OVERRIDES == {}, (
        "an override landed without updating this test -- each one needs its own "
        "evidence, see the module docstring's gating invariant")
    installed = ov.install_overrides()
    try:
        assert installed == []
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
