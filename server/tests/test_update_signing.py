"""Ed25519 signing/verification for release artifacts (fail-closed)."""
import base64

import pytest

from astrodeck.update import signing as S


def test_keypair_sign_verify_roundtrip():
    seed, pub = S.generate_keypair()
    data = b"the quick brown fox" * 100
    sig = S.sign_bytes(data, seed)
    assert S.verify_bytes(data, sig, pub) is True


def test_verify_rejects_tampered_data():
    seed, pub = S.generate_keypair()
    sig = S.sign_bytes(b"original payload", seed)
    assert S.verify_bytes(b"tampered payload", sig, pub) is False


def test_verify_rejects_wrong_key():
    seed1, _pub1 = S.generate_keypair()
    _seed2, pub2 = S.generate_keypair()
    sig = S.sign_bytes(b"payload", seed1)
    assert S.verify_bytes(b"payload", sig, pub2) is False


def test_verify_failclosed_on_bad_inputs():
    _seed, pub = S.generate_keypair()
    assert S.verify_bytes(b"x", "not-base64!!", pub) is False
    assert S.verify_bytes(b"x", base64.b64encode(b"short").decode(), pub) is False
    assert S.verify_bytes(b"x", "AAAA", "") is False              # empty pubkey
    assert S.verify_bytes(b"x", "AAAA", "not-base64!!") is False


def test_verify_artifact_happy_path(tmp_path):
    seed, pub = S.generate_keypair()
    art = tmp_path / "astrodeck-0.2.0.tar.gz"
    art.write_bytes(b"PK\x03\x04 pretend-archive" * 50)
    sha_path, sig_path = S.write_sidecars(art, seed)
    ok, reason = S.verify_artifact(
        art, pub,
        sha256_text=sha_path.read_text(),
        signature_b64=sig_path.read_text())
    assert ok is True, reason


def test_verify_artifact_detects_sha_mismatch(tmp_path):
    seed, pub = S.generate_keypair()
    art = tmp_path / "a.tar.gz"
    art.write_bytes(b"good bytes")
    _sha, sig_path = S.write_sidecars(art, seed)
    bad_sha = "0" * 64 + "  a.tar.gz\n"
    ok, reason = S.verify_artifact(art, pub, sha256_text=bad_sha,
                                   signature_b64=sig_path.read_text())
    assert ok is False
    assert "sha256" in reason


def test_verify_artifact_detects_bad_signature(tmp_path):
    seed, pub = S.generate_keypair()
    art = tmp_path / "a.tar.gz"
    art.write_bytes(b"good bytes")
    sha_path, _sig = S.write_sidecars(art, seed)
    # a valid signature, but of DIFFERENT content -> must be rejected
    forged = S.sign_bytes(b"different content", seed)
    ok, reason = S.verify_artifact(art, pub, sha256_text=sha_path.read_text(),
                                   signature_b64=forged)
    assert ok is False
    assert "signature" in reason.lower()


def test_verify_artifact_requires_pinned_pubkey(tmp_path):
    seed, _pub = S.generate_keypair()
    art = tmp_path / "a.tar.gz"
    art.write_bytes(b"bytes")
    sha_path, sig_path = S.write_sidecars(art, seed)
    ok, reason = S.verify_artifact(art, "", sha256_text=sha_path.read_text(),
                                   signature_b64=sig_path.read_text())
    assert ok is False
    assert "public key" in reason.lower()


def test_sha256_sidecar_roundtrip(tmp_path):
    art = tmp_path / "x.bin"
    art.write_bytes(b"abc")
    text = S.sha256_sidecar_text(art)
    # sha256("abc")
    assert text.startswith(
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")
    assert S.parse_sha256_sidecar(text) == \
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    assert S.parse_sha256_sidecar("garbage") is None
