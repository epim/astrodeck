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

from ..devices import vendor_verify


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


def carry_forward_vendor_libraries(src_vendor: Path, dst_vendor: Path) -> dict:
    """Copy into a staged release the bundled SDK libraries its tarball may not
    carry, when this install already has them and the NEW release's manifest
    pins them.

    ``scripts/build_release.py`` omits vendor binaries we are not licensed to
    redistribute (Player One, #199). Their SHA-256 stays in ``vendor/manifest.json``
    because the operator obtains them once and ``vendor_verify`` refuses anything
    else. Without this step every self-update dropped the Player One library and
    the imaging camera vanished on the next boot: the hand deploy of 0.3.23
    copied it by hand, the updater did not.

    Rules: regular files that look like shared libraries only; never a symlink
    (a planted redirect); never overwrite a file the tarball shipped; only when
    the staged manifest pins that exact path AND the local bytes match it. A
    library that does not qualify is left behind and named in ``skipped`` so the
    log can say what to do.

    Returns ``{"carried": [rel, ...], "skipped": [(rel, why), ...], "reason": str}``
    where ``reason`` explains an early return (no manifest, no source tree).
    """
    result: dict = {"carried": [], "skipped": [], "reason": ""}
    if not src_vendor.is_dir():
        result["reason"] = "no bundled SDK tree on this install"
        return result
    manifest_path = dst_vendor / "manifest.json"
    if not manifest_path.is_file():
        result["reason"] = "staged release has no vendor manifest"
        return result
    try:
        manifest = vendor_verify.load_manifest(manifest_path)
    except (OSError, ValueError, vendor_verify.VendorIntegrityError) as exc:
        result["reason"] = f"staged vendor manifest unreadable: {exc}"
        return result
    pinned = manifest.get("binaries") or {}
    for path in sorted(src_vendor.rglob("*")):
        if path.is_symlink() or not vendor_verify._is_shared_library(path):
            continue
        rel = path.relative_to(src_vendor).as_posix()
        dst = dst_vendor / rel
        if dst.exists() or dst.is_symlink():
            continue  # the release shipped its own; the older local copy never wins
        entry = pinned.get(rel)
        if not isinstance(entry, dict) or not entry.get("sha256"):
            result["skipped"].append((rel, "not pinned by the staged manifest"))
            continue
        if vendor_verify._sha256(path) != entry["sha256"]:
            result["skipped"].append((rel, "does not match the staged manifest"))
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dst)
        result["carried"].append(rel)
    return result


def _main(argv: "list[str] | None" = None) -> int:
    """``python -m astrodeck.update.stage carry-forward SRC_VENDOR DST_VENDOR``:
    the same carry-forward the updater runs, for a hand deploy."""
    import argparse
    import json

    ap = argparse.ArgumentParser(prog="python -m astrodeck.update.stage")
    sub = ap.add_subparsers(dest="cmd", required=True)
    cf = sub.add_parser(
        "carry-forward",
        help="copy manifest-pinned SDK libraries this install has into a staged release")
    cf.add_argument("src_vendor", help="this install's astrodeck/vendor directory")
    cf.add_argument("dst_vendor", help="the staged release's astrodeck/vendor directory")
    args = ap.parse_args(argv)
    print(json.dumps(carry_forward_vendor_libraries(
        Path(args.src_vendor), Path(args.dst_vendor)), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
