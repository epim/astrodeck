"""Unpack a VERIFIED release tarball into ``releases/<version>/`` and reconcile
venv deps (Python era).

Extraction is hardened against path-traversal / link members even though the
artifact was signature-verified upstream (defense in depth). The future
single-binary release removes the dependency-reconcile step entirely.
"""
from __future__ import annotations

import os
import re
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


def reconcile_deps(release_dir: Path, python_exe: str,
                   timeout_s: float = 600.0) -> tuple[bool, str]:
    """Best-effort ``pip install`` of the staged server package into the venv so a
    new version's added deps are present after the swap. Non-fatal: the supervisor
    will roll back (and pip-sync the venv back to the pre-update snapshot -- see
    ``snapshot_env``) if the new version then fails to come up healthy.

    ``timeout_s`` bounds the pip subprocess so a slow/hung PyPI mirror can never
    wedge the caller forever; the caller runs this off the event loop
    (``asyncio.to_thread``) so the server stays responsive while pip works."""
    server_dir = release_dir / "server"
    if not (server_dir / "pyproject.toml").exists():
        return True, "no pyproject; skipped dependency reconcile"
    try:
        subprocess.run(
            [python_exe, "-m", "pip", "install", str(server_dir)],
            check=True, capture_output=True, timeout=timeout_s)
        return True, "dependencies reconciled"
    except subprocess.TimeoutExpired:
        return False, f"pip install timed out after {timeout_s:.0f}s"
    except Exception as e:  # noqa: BLE001 - surfaced as a non-fatal reason
        return False, f"pip install failed: {e}"


# Only well-formed ``name==version`` PyPI pins are restorable on rollback; drop
# editable/URL/local lines (``-e``, ``pkg @ file://...``) and the astrodeck package
# itself (never published to an index, so ``pip install -r`` would abort on it).
_PIN_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==[^\s@]+$")


def snapshot_env(python_exe: str, timeout_s: float = 120.0) -> "str | None":
    """Capture ``pip freeze`` of the SHARED venv as a restorable requirements list,
    taken BEFORE ``reconcile_deps`` mutates it. The supervisor pip-syncs back to
    this on rollback so a rolled-back version runs against the deps it shipped with
    rather than the new version's (the venv is shared across all releases).

    Returns the filtered requirements text, or ``None`` if freeze failed (rollback
    then simply skips the restore -- best effort, never blocks the apply)."""
    try:
        out = subprocess.run(
            [python_exe, "-m", "pip", "freeze"],
            check=True, capture_output=True, text=True, timeout=timeout_s)
    except Exception:  # noqa: BLE001 - best effort; caller treats None as "no snapshot"
        return None
    pins = []
    for line in out.stdout.splitlines():
        m = _PIN_RE.match(line.strip())
        if not m:
            continue
        if m.group(1).replace("_", "-").lower() == "astrodeck":
            continue
        pins.append(line.strip())
    return "\n".join(pins) + "\n" if pins else ""
