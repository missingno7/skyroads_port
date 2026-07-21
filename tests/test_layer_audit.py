"""Enforce pitfall #17 (tools/audit_layers.py) on every pure layer: no
dos_re/cpu/mem imports, no VM types. ``skyroads.handrecovered``,
``skyroads.native``, and ``skyroads.bridge`` all share this dependency
boundary.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from audit_layers import audit_file  # noqa: E402
from dos_re.execution import ImplementationOrigin  # noqa: E402
from skyroads.authored_inventory import (  # noqa: E402
    AuthoredRole,
    AuthoredUse,
    authored_modules,
)
from skyroads.execution import catalog  # noqa: E402

PURE_ROOTS = ["skyroads/handrecovered", "skyroads/native", "skyroads/bridge"]


def test_pure_layers_have_no_vm_leakage() -> None:
    issues = []
    for root in PURE_ROOTS:
        for path in sorted((ROOT / root).rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            issues.extend(audit_file(path, ("dos_re",), set()))
    assert not issues, "\n".join(f"{i.path}:{i.lineno}: {i.message}" for i in issues)


def test_every_authored_module_has_one_explicit_disposition() -> None:
    discovered = {
        ".".join(path.relative_to(ROOT).with_suffix("").parts)
        for root in PURE_ROOTS[:2]
        for path in (ROOT / root).glob("*.py")
        if path.name != "__init__.py"
    }
    declared = [item.module for item in authored_modules()]
    assert len(declared) == len(set(declared))
    assert set(declared) == discovered
    assert all(item.reason.strip() for item in authored_modules())
    assert all(isinstance(item.role, AuthoredRole) for item in authored_modules())


def test_runtime_authored_modules_are_exactly_the_declared_overrides() -> None:
    runtime_modules = {
        entry.implementation.__module__
        for entry in catalog().entries
        if entry.descriptor.origin is ImplementationOrigin.AUTHORED
        and entry.implementation is not None
    }
    declared = {
        item.module
        for item in authored_modules(AuthoredUse.RUNTIME)
    }
    # Catalog implementations are the semantic roots.  Their explicitly
    # inventoried runtime dependencies are allowed to be a strict superset.
    assert runtime_modules <= declared
    assert "skyroads.native.loop" in runtime_modules


def _imports(module: str) -> set[str]:
    path = ROOT / Path(*module.split(".")).with_suffix(".py")
    if not path.is_file():
        return set()
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return {name for name in found if name.startswith("skyroads.")}


def test_no_runtime_authored_module_is_disconnected_from_catalog_roots() -> None:
    entries = tuple(
        entry for entry in catalog().entries
        if entry.descriptor.origin is ImplementationOrigin.AUTHORED
    )
    pending = {
        entry.implementation.__module__
        for entry in entries if entry.implementation is not None
    }
    pending.update(
        adapter.activate.__module__
        for entry in entries for adapter in entry.region_adapters
    )
    reachable = set()
    while pending:
        module = pending.pop()
        if module in reachable:
            continue
        reachable.add(module)
        pending.update(_imports(module) - reachable)

    declared_runtime = {
        item.module for item in authored_modules(AuthoredUse.RUNTIME)
    }
    authored_names = {item.module for item in authored_modules()}
    assert reachable & authored_names == declared_runtime


def test_semantic_layer_never_depends_on_native_compositions() -> None:
    offenders = []
    for path in sorted((ROOT / "skyroads" / "handrecovered").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            if any(name == "skyroads.native" or name.startswith(
                "skyroads.native.") for name in names
            ):
                offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert not offenders
