"""Deterministic content identities for SkyRoads execution artifacts.

Execution-plan identities must change when the implementation bytes change.
This module is deliberately small and side-effect free: callers name the exact
repository files that make up an implementation or provider, and the digest
commits to both their repository-relative POSIX paths and their bytes.
"""
from __future__ import annotations

from hashlib import sha256
import inspect
from pathlib import Path
from types import ModuleType
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]


def _repo_path(
    path: str | Path,
    repository_root: str | Path,
) -> tuple[str, Path]:
    root = Path(repository_root).resolve()
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = root / candidate
    candidate = candidate.resolve()
    try:
        relative = candidate.relative_to(root).as_posix()
    except ValueError as exc:
        raise ValueError(
            f"content identity path is outside the SkyRoads repository: {candidate}"
        ) from exc
    if not candidate.is_file():
        raise FileNotFoundError(
            f"content identity input is missing or not a file: {candidate}"
        )
    return relative, candidate


def content_digest(
    paths: Iterable[str | Path],
    *,
    repository_root: str | Path = ROOT,
    records: Iterable[tuple[str, str]] = (),
) -> str:
    """Hash sorted repository-relative path names and file bytes.

    Including names prevents two differently-scoped source sets with identical
    concatenated bytes from sharing an identity. Missing inputs fail loudly;
    there is no version-label fallback.
    """
    files = sorted(
        {
            _repo_path(path, repository_root)
            for path in paths
        },
        key=lambda item: item[0],
    )
    if not files:
        raise ValueError("content identity requires at least one source file")
    digest = sha256()
    for relative, path in files:
        name = relative.encode("utf-8")
        data = path.read_bytes()
        digest.update(len(name).to_bytes(8, "big"))
        digest.update(name)
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    for name, value in sorted(records):
        encoded_name = name.encode("utf-8")
        encoded_value = value.encode("utf-8")
        digest.update(b"record\0")
        digest.update(len(encoded_name).to_bytes(8, "big"))
        digest.update(encoded_name)
        digest.update(len(encoded_value).to_bytes(8, "big"))
        digest.update(encoded_value)
    return digest.hexdigest()


def source_path(value: object) -> Path:
    """Return the repository source file which defines a callable or module."""
    if isinstance(value, ModuleType):
        filename = inspect.getsourcefile(value)
    else:
        filename = inspect.getsourcefile(value)
    if filename is None:
        raise ValueError(f"cannot identify source file for {value!r}")
    return Path(filename)


def callable_digest(
    *values: object,
    extra_paths: Iterable[str | Path] = (),
    repository_root: str | Path = ROOT,
) -> str:
    """Hash the defining modules for callables plus explicitly named helpers."""
    return content_digest(
        [*(source_path(value) for value in values), *extra_paths],
        repository_root=repository_root,
    )


def tree_sources(
    path: str | Path,
    *,
    repository_root: str | Path = ROOT,
) -> tuple[Path, ...]:
    """Return every Python source below a repository directory, sorted."""
    root = Path(path)
    if not root.is_absolute():
        root = Path(repository_root) / root
    root = root.resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"content identity source tree is missing: {root}")
    files = tuple(sorted(root.rglob("*.py"), key=lambda item: item.as_posix()))
    if not files:
        raise ValueError(f"content identity source tree has no Python files: {root}")
    return files
