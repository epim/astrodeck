"""Integrity check for the third-party SDK binaries AstroDeck BUNDLES (OPEN-007).

The ZWO and Player One SDK shared libraries execute in-process via ctypes. We
ship them under ``astrodeck/vendor/`` (see each vendor's LICENSE there), so a
tampered or swapped bundled binary would run with the server's privileges. This
module pins the SHA-256 of every binary we ship in ``vendor/manifest.json`` and
re-verifies it at load time.

Scope, deliberately narrow: we verify only the binaries WE bundle. A user's own
separately-installed vendor SDK (reached via ``ASTRODECK_*_SDK_DIR`` or a system
install path) is out of our provenance and is NOT hash-pinned -- it is the
user's trusted install, and pinning a version we do not control would just break
on the vendor's next release. So ``verify_if_vendored`` fails closed for a path
under ``vendor/`` and is a no-op for anything outside it.

The manifest ships inside the release tarball, which is Ed25519-signed as a
whole (scripts/sign_release.py), so the manifest itself is covered by the
release signature; this runtime check catches post-install tampering (DLL
planting, a swapped vendor .dll) that the release signature cannot see.

Regenerate the manifest after intentionally updating a bundled binary:

    python -m astrodeck.devices.vendor_verify --write
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

#: Root of the bundled-SDK tree. ``vendor_verify`` lives in astrodeck/devices/,
#: so its grandparent-relative ``vendor`` is astrodeck/vendor/.
VENDOR_ROOT = Path(__file__).resolve().parent.parent / "vendor"
MANIFEST_PATH = VENDOR_ROOT / "manifest.json"

#: The binary kinds we ship and load in-process.
_BINARY_SUFFIXES = (".dll", ".dylib")


def _is_shared_library(p: Path) -> bool:
    """True for a .dll, a .dylib, or ANY ``.so`` -- including the fully
    versioned sonames the Linux vendor libs actually ship as
    (``libPlayerOneCamera.so.3.10.0``, whose ``Path.suffix`` is ``.0``).

    The first manifest matched on ``suffix`` alone and silently omitted every
    versioned ``.so``; the fail-closed check then refused Player One on Linux
    (the Orange Pi) as "not in the manifest". Caught by the first Linux CI run,
    2026-09-05. Judge on all suffixes, not the last one."""
    if not p.is_file():
        return False
    suffixes = [s.lower() for s in p.suffixes]
    return p.suffix.lower() in _BINARY_SUFFIXES or ".so" in suffixes

MANIFEST_SCHEMA = 1


class VendorIntegrityError(Exception):
    """A bundled SDK binary is missing from, or does not match, the manifest."""


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def iter_vendor_binaries(root: Path = VENDOR_ROOT) -> list[Path]:
    """Every bundled binary under ``root`` (sorted, for a stable manifest)."""
    if not root.is_dir():
        return []
    return sorted(p for p in root.rglob("*") if _is_shared_library(p))


def _lexical(path: Path) -> Path:
    """Absolute, ``..``-normalised path WITHOUT following symlinks.

    Containment and the manifest key must be judged on the path as named, not
    on what it points at: ``resolve()`` would let a symlink planted at
    ``vendor/zwo/ASICamera2.dll`` escape to ``/tmp/evil.dll`` (outside vendor ->
    check skipped) or re-key onto a different manifested binary (a name that
    still hashes clean). Found in re-review 2026-09-05."""
    return Path(os.path.normpath(os.path.abspath(str(path))))


def _rel(path: Path, root: Path = VENDOR_ROOT) -> str:
    """POSIX-style path relative to the vendor root -- the manifest key, stable
    across OSes. Lexical (see ``_lexical``)."""
    return _lexical(path).relative_to(_lexical(root)).as_posix()


def build_manifest(root: Path = VENDOR_ROOT) -> dict:
    """Compute the manifest from the on-disk bundled binaries."""
    binaries = {}
    for p in iter_vendor_binaries(root):
        rel = _rel(p, root)
        binaries[rel] = {
            "sha256": _sha256(p),
            "size": p.stat().st_size,
            # top-level dir names the vendor (playerone / zwo).
            "vendor": rel.split("/", 1)[0],
        }
    return {"schema": MANIFEST_SCHEMA, "binaries": binaries}


def load_manifest(path: Path = MANIFEST_PATH) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict) or "binaries" not in data:
        raise VendorIntegrityError("vendor manifest is malformed")
    return data


def _is_under_vendor(path: Path, root: Path = VENDOR_ROOT) -> bool:
    try:
        _lexical(path).relative_to(_lexical(root))
        return True
    except ValueError:
        return False


def verify_if_vendored(path: Path, *, manifest: dict | None = None,
                       root: Path = VENDOR_ROOT) -> None:
    """Verify a to-be-loaded SDK binary IF it is one we bundle; else no-op.

    Raises ``VendorIntegrityError`` when ``path`` is under ``vendor/`` but is
    absent from the manifest (an unexpected planted binary) or its hash does not
    match (tampered/swapped). A path outside ``vendor/`` -- a user's own
    installed SDK -- is not ours to pin and passes untouched."""
    path = Path(path)
    if not _is_under_vendor(path, root):
        return
    # Our bundled binaries are never symlinks (a wheel and a git checkout ship
    # real files). A symlink under vendor/ is a planted redirect; refuse it
    # outright rather than hash whatever it happens to point at.
    if path.is_symlink():
        raise VendorIntegrityError(
            f"bundled SDK path is a symlink: {_rel(path, root)} "
            f"(refusing to follow a redirect out of vendor/)")
    try:
        man = manifest if manifest is not None else load_manifest()
    except (OSError, ValueError) as exc:
        # A missing/corrupt manifest is fail-closed too, with a clear reason.
        raise VendorIntegrityError(
            f"vendor manifest unavailable, refusing to load a bundled SDK: {exc}"
        ) from exc
    rel = _rel(path, root)
    entry = man.get("binaries", {}).get(rel)
    if entry is None:
        raise VendorIntegrityError(
            f"bundled SDK binary not in the manifest: {rel} "
            f"(refusing to load an unrecognised binary from vendor/)")
    actual = _sha256(path)
    if actual != entry.get("sha256"):
        raise VendorIntegrityError(
            f"bundled SDK binary failed its integrity check: {rel} "
            f"(expected {entry.get('sha256')}, got {actual})")


def verify_all(root: Path = VENDOR_ROOT, manifest: dict | None = None) -> int:
    """Verify every on-disk bundled binary against the manifest. Returns the
    count checked; raises on the first mismatch. A startup/self-test hook."""
    man = manifest if manifest is not None else load_manifest()
    binaries = iter_vendor_binaries(root)
    for p in binaries:
        verify_if_vendored(p, manifest=man, root=root)
    return len(binaries)


def _main(argv: list[str]) -> int:  # pragma: no cover - dev tool
    if "--write" in argv:
        man = build_manifest()
        MANIFEST_PATH.write_text(
            json.dumps(man, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"wrote {MANIFEST_PATH} ({len(man['binaries'])} binaries)")
        return 0
    # default: verify
    n = verify_all()
    print(f"vendor integrity OK ({n} binaries)")
    return 0


if __name__ == "__main__":  # pragma: no cover
    import sys
    raise SystemExit(_main(sys.argv[1:]))
