"""The artifact boundary, enforced (docs/architecture.md, second hard rule).

``artifacts/`` may hold generated outputs, recordings, snapshots, diagnostics and
build products. It must NOT hold live or authoritative code: every executable
module and every canonical generated corpus lives at its package location, and
every gate verifies exactly the artifact that ships.

The invariant is checked directly: each generated corpus has one package path
shared by its emitter and the backend selected by the unified player.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

#: corpus -> (package path, files that produce or execute it).
CORPORA = {
    "lifted": (
        "skyroads/lifted/functions",
        ("scripts/close_vmless_wall.py", "skyroads/vmless_backend.py"),
    ),
    "recovered": (
        "skyroads/recovered",
        ("scripts/build_recovered.py", "skyroads/cpuless_backend.py"),
    ),
}

#: Path-ish argparse defaults that name a CODE directory. A default here that
#: points into artifacts/ is the exact drift described above.
_CODE_DIR_OPTS = ("--lift-dir", "--recovered-dir", "--corpus", "--emit-dir",
                  "--adapter-dir", "--import-base")


def _source(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


@pytest.mark.parametrize("corpus", sorted(CORPORA))
def test_emitter_runner_and_verifier_name_one_path(corpus):
    """The three tools that write, ship and prove a corpus must agree."""
    package_path, sources = CORPORA[corpus]
    parts = package_path.split("/")
    # The same location is written several ways -- ROOT / "skyroads" / "lifted"
    # / "functions", "skyroads/lifted/functions", skyroads.lifted.functions --
    # so compare on a normalised form with the separators and quoting removed.
    squash = lambda s: re.sub(r"""[\s"'/\\.]""", "", s)   # noqa: E731
    needle = squash(package_path)
    for source in sources:
        src = squash(_source(source))
        assert needle in src, (
            f"{source} does not name the shipped {corpus} corpus "
            f"({package_path}); if it points somewhere else it is not proving, "
            f"running, or producing the artifact that ships")


def test_no_code_directory_default_points_into_artifacts():
    """No script may default a CODE directory to a path under artifacts/."""
    offenders = []
    for path in sorted((ROOT / "scripts").glob("*.py")):
        src = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(src)
        except SyntaxError:                       # pragma: no cover
            continue
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and getattr(node.func, "attr", "") == "add_argument"):
                continue
            opt = next((a.value for a in node.args
                        if isinstance(a, ast.Constant) and isinstance(a.value, str)),
                       "")
            if opt not in _CODE_DIR_OPTS:
                continue
            default = next((kw for kw in node.keywords if kw.arg == "default"), None)
            if default is None:
                continue
            # crude but sufficient: does the default expression mention artifacts?
            if "artifacts" in ast.dump(default):
                offenders.append(f"{path.name}: {opt}")
    assert not offenders, (
        "code directories must live in a package, not artifacts/ "
        f"(docs/architecture.md): {offenders}")


def test_no_shipped_runner_puts_artifacts_on_the_import_path():
    """A runner that adds artifacts/ to sys.path can import code from there."""
    offenders = []
    for name in (
        "scripts/play.py",
        "skyroads/cpuless_backend.py",
        "skyroads/vmless_backend.py",
    ):
        src = _source(name)
        for line in src.splitlines():
            if "sys.path" in line and "artifacts" in line:
                offenders.append(f"{name}: {line.strip()}")
    assert not offenders, f"artifacts/ must never be importable: {offenders}"


def test_artifacts_holds_no_corpus_that_shadows_a_shipped_one():
    """A stale corpus under artifacts/ is what silently detached the gate.

    Any directory of ``lifted_*.py`` / ``func_*.py`` under artifacts/ that is not
    a declared build scratch area is an orphan waiting to be pointed at.
    """
    art = ROOT / "artifacts"
    if not art.exists():
        pytest.skip("no artifacts/ directory")
    #: the ONE sanctioned scratch corpus: cpuless_promote must be given an
    #: --adapter-dir, and its output is a verification shim nothing imports.
    allowed = {"recovered_adapters"}
    offenders = []
    for sub in sorted(p for p in art.iterdir() if p.is_dir()):
        if sub.name in allowed:
            continue
        if any(sub.glob("lifted_*.py")) or any(sub.glob("func_*.py")):
            offenders.append(sub.name)
    assert not offenders, (
        "orphaned corpus under artifacts/ -- move it to its package location or "
        f"delete it (docs/architecture.md): {offenders}")
