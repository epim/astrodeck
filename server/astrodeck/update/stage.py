"""Unpack a VERIFIED release tarball into ``releases/<version>/`` and reconcile
venv deps (Python era).

Extraction is hardened against path-traversal / link members even though the
artifact was signature-verified upstream (defense in depth). The future
single-binary release removes the dependency-reconcile step entirely.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tarfile
from pathlib import Path


def _is_within(base: Path, target: Path) -> bool:
    try:
        target.resolve().relative_to(base.resolve())
        return True
    except ValueError:
        return False


def safe_extract(tarball: Path, dest: Path) -> None:
    """Extract ``tarball`` into ``dest``, rejecting absolute paths, ``..``
    traversal, and hard/sym links."""
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tarball, "r:gz") as tf:
        for m in tf.getmembers():
            if m.islnk() or m.issym():
                raise ValueError(f"refusing link member in archive: {m.name}")
            if not _is_within(dest, dest / m.name):
                raise ValueError(f"refusing path-traversal member: {m.name}")
        try:  # py3.12+: the 'data' filter independently enforces the same.
            tf.extractall(dest, filter="data")  # type: ignore[arg-type]
        except TypeError:  # python < 3.12 has no 'data' filter
            # extract the already-validated members one by one (the pre-scan above
            # rejected links + traversal, so no malicious member survives); this
            # avoids extractall's symlink-following on the pre-3.12 path.
            for m in tf.getmembers():
                tf.extract(m, dest)


def stage_release(tarball: Path, releases_dir: Path, version: str) -> Path:
    """Unpack into ``releases/<version>/`` (flattening the single top dir) and
    return that path. Replaces an existing staging of the same version."""
    target = releases_dir / version
    if target.exists():
        shutil.rmtree(target)
    tmp = releases_dir / f".staging-{version}"
    if tmp.exists():
        shutil.rmtree(tmp)
    safe_extract(tarball, tmp)
    # the bundle has a single top dir ``astrodeck-<version>/``; flatten it.
    entries = list(tmp.iterdir())
    root = entries[0] if len(entries) == 1 and entries[0].is_dir() else tmp
    releases_dir.mkdir(parents=True, exist_ok=True)
    os.replace(root, target)
    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)
    return target


def reconcile_deps(release_dir: Path, python_exe: str) -> tuple[bool, str]:
    """Best-effort ``pip install`` of the staged server package into the venv so a
    new version's added deps are present after the swap. Non-fatal: the supervisor
    will roll back if the new version then fails to come up healthy."""
    server_dir = release_dir / "server"
    if not (server_dir / "pyproject.toml").exists():
        return True, "no pyproject; skipped dependency reconcile"
    try:
        subprocess.run(
            [python_exe, "-m", "pip", "install", str(server_dir)],
            check=True, capture_output=True)
        return True, "dependencies reconciled"
    except Exception as e:  # noqa: BLE001 - surfaced as a non-fatal reason
        return False, f"pip install failed: {e}"
