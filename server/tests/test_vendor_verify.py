"""OPEN-007: bundled SDK binaries are hash-pinned and verified before load.

The ZWO / Player One shared libraries run in-process. A tampered or swapped
bundled binary must be refused; a user's own separately-installed SDK is out of
our provenance and must NOT be pinned (it would break on the vendor's next
release).
"""
from __future__ import annotations

import pytest

from astrodeck.devices.vendor_verify import (
    VENDOR_ROOT,
    VendorIntegrityError,
    build_manifest,
    iter_vendor_binaries,
    load_manifest,
    verify_all,
    verify_if_vendored,
)


def test_manifest_is_in_sync_with_the_bundled_binaries():
    """If someone updates a bundled DLL without regenerating the manifest, this
    fails -- the whole point of the pin. Regenerate with
    `python -m astrodeck.devices.vendor_verify --write`."""
    assert build_manifest() == load_manifest()


def test_every_bundled_binary_verifies():
    n = verify_all()
    assert n == len(iter_vendor_binaries())
    assert n >= 1  # we do ship bundled SDKs


def test_verify_accepts_a_real_bundled_binary():
    a_real_one = iter_vendor_binaries()[0]
    verify_if_vendored(a_real_one)  # must not raise


def test_verify_rejects_a_tampered_bundled_binary(tmp_path):
    (tmp_path / "zwo").mkdir()
    lib = tmp_path / "zwo" / "fake.dll"
    lib.write_bytes(b"the-real-thing")
    man = build_manifest(tmp_path)          # pins the good bytes
    lib.write_bytes(b"malicious-payload")   # swap after pinning
    with pytest.raises(VendorIntegrityError):
        verify_if_vendored(lib, manifest=man, root=tmp_path)


def test_verify_rejects_an_unknown_binary_planted_in_vendor(tmp_path):
    (tmp_path / "zwo").mkdir()
    man = build_manifest(tmp_path)          # empty manifest
    planted = tmp_path / "zwo" / "evil.dll"
    planted.write_bytes(b"planted")
    with pytest.raises(VendorIntegrityError):
        verify_if_vendored(planted, manifest=man, root=tmp_path)


def test_verify_skips_a_user_installed_sdk_outside_vendor(tmp_path):
    """A path outside vendor/ (env override or a system install) is the user's
    own trusted SDK; we do not hash-pin it, so it passes untouched."""
    outside = tmp_path / "Program Files" / "ZWO" / "ASICamera2.dll"
    outside.parent.mkdir(parents=True)
    outside.write_bytes(b"whatever the user installed")
    verify_if_vendored(outside)  # default vendor root; not under it -> no-op


def test_all_bundled_paths_declare_a_vendor():
    man = load_manifest()
    for rel, entry in man["binaries"].items():
        assert entry["vendor"] in {"playerone", "zwo"}, rel
        assert entry["sha256"] and entry["size"] > 0
