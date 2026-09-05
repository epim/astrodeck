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
from pathlib import Path

#: Root of the bundled-SDK tree. ``vendor_verify`` lives in astrodeck/devices/,
#: so its grandparent-relative ``vendor`` is astrodeck/vendor/.
VENDOR_ROOT = Path(__file__).resolve().parent.parent / "vendor"
MANIFEST_PATH = VENDOR_ROOT / "manifest.json"

#: The binary extensions we ship and load in-process.
_BINARY_SUFFIXES = (".dll", ".so", ".dylib")

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
    return sorted(
        p for p in root.rglob("*")
        if p.is_file() and p.suffix.lower() in _BINARY_SUFFIXES
    )


def _rel(path: Path, root: Path = VENDOR_ROOT) -> str:
    """POSIX-style path relative to the vendor root -- the manifest key, stable
    across OSes."""
    return path.resolve().relative_to(root.resolve()).as_posix()


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
        path.resolve().relative_to(root.resolve())
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
    man = manifest if manifest is not None else load_manifest()
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
