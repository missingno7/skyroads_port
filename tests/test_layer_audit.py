"""Enforce pitfall #17 (tools/audit_layers.py) on every pure layer: no
dos_re/cpu/mem imports, no VM types. skyroads/handrecovered/__init__.py's own
docstring documents this bar for that directory; skyroads/native and
skyroads/bridge (added 2026-07-11 with the native frame-stepper work) hold
the same bar -- see docs/state_mirrors.md.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from audit_layers import audit_file  # noqa: E402

PURE_ROOTS = ["skyroads/handrecovered", "skyroads/native", "skyroads/bridge"]


def test_pure_layers_have_no_vm_leakage() -> None:
    issues = []
    for root in PURE_ROOTS:
        for path in sorted((ROOT / root).rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            issues.extend(audit_file(path, ("dos_re",), set()))
    assert not issues, "\n".join(f"{i.path}:{i.lineno}: {i.message}" for i in issues)
