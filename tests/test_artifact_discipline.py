"""The artifact boundary, enforced (docs/architecture.md, second hard rule).

``artifacts/`` may hold generated outputs, recordings, snapshots, diagnostics and
build products. It must NOT hold live or authoritative code: every executable
module and every canonical generated corpus lives at its package location, and
every gate verifies exactly the artifact that ships.

This is a test rather than a convention because the failure mode is SILENT.
``verify_vmless_demo`` defaulted ``--lift-dir`` to ``artifacts/lifted_full`` --
a path nothing had written since the dos_re 2.0 rename -- while the generator
emitted to, and the runner imported from, ``skyroads/lifted/functions``. The two
agreed byte-for-byte on all 182 shared modules, so the gate stayed green; it only
stopped covering reality when a census added three functions to the shipped
corpus and not to the orphan. Nothing raised. Nothing went red. The gate had
simply detached from what it claimed to prove.

So the invariant is checked directly: one path per corpus, shared by the tool
that WRITES it, the runner that SHIPS it, and the gate that PROVES it.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

#: corpus -> (package path that ships, the scripts that must all agree on it).
#: A corpus is only trustworthy if its emitter, runner and verifier name ONE
#: location; any of them drifting is the bug this file exists to catch.
CORPORA = {
    "lifted": (
        "skyroads/lifted/functions",
        ("close_vmless_wall.py",     # emits it
         "play_vmless.py",           # ships it
         "verify_vmless_demo.py"),   # proves it
    ),
    "recovered": (
        "skyroads/recovered",
        ("build_recovered.py",       # emits it
         "play_cpuless.py",          # ships it
         "verify_cpuless.py"),       # proves it
    ),
}

#: Path-ish argparse defaults that name a CODE directory. A default here that
#: points into artifacts/ is the exact drift described above.
_CODE_DIR_OPTS = ("--lift-dir", "--recovered-dir", "--corpus", "--emit-dir",
                  "--adapter-dir", "--import-base")


def _script(name: str) -> str:
    return (ROOT / "scripts" / name).read_text(encoding="utf-8")


@pytest.mark.parametrize("corpus", sorted(CORPORA))
def test_emitter_runner_and_verifier_name_one_path(corpus):
    """The three tools that write, ship and prove a corpus must agree."""
    package_path, scripts = CORPORA[corpus]
    parts = package_path.split("/")
    # The same location is written several ways -- ROOT / "skyroads" / "lifted"
    # / "functions", "skyroads/lifted/functions", skyroads.lifted.functions --
    # so compare on a normalised form with the separators and quoting removed.
    squash = lambda s: re.sub(r"""[\s"'/\\.]""", "", s)   # noqa: E731
    needle = squash(package_path)
    for script in scripts:
        src = squash(_script(script))
        assert needle in src, (
            f"{script} does not name the shipped {corpus} corpus "
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
    for name in ("play_cpuless.py", "play_vmless.py", "play.py"):
        src = _script(name)
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
