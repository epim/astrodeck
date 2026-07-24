"""Asymmetric EdDSA sessions + the JWS algorithm-confusion defense (W3).

Covers the security-critical seam in ``astrodeck.auth.session`` / ``.eddsa``:

* HS256 round-trip is UNCHANGED (the default home path stays byte-identical).
* EdDSA sign -> verify round-trips with a generated (ephemeral) keypair.
* a RELAY verifies a home-issued EdDSA token with ONLY the public key -- no
  home secret involved.
* **algorithm-confusion is REJECTED** both ways: an EdDSA token is never
  accepted by the HMAC path, an HS256 token is never accepted by the EdDSA
  path, and a public key used as an HMAC secret does NOT verify.
* tampered payload / signature, wrong pubkey, and an expired ``exp`` on the
  EdDSA path are all rejected.

All keypairs here are EPHEMERAL (generated per test) -- no hardcoded seed.
"""
from __future__ import annotations

import pytest

from astrodeck.auth import eddsa
from astrodeck.auth.session import (decode_session, sign_session,
                                    verify_session)

pytestmark = pytest.mark.skipif(
    not eddsa.have_eddsa(), reason="cryptography (Ed25519) not available")


@pytest.fixture()
def keypair():
    """A fresh ephemeral Ed25519 keypair as ``(private_seed_b64, public_b64)``."""
    return eddsa.generate_keypair()


# ------------------------------------------------------------- HS256 unchanged

def test_hs256_default_roundtrip_unchanged():
    """The default (no alg / no key) path still signs+verifies HS256 exactly as
    before -- the header alg is HS256 and a plain verify accepts it."""
    tok = sign_session("operator", email="o@x", jti="abc", secret=b"s3cret")
    # header alg is HS256 (default), NOT EdDSA
    import base64
    import json
    head = json.loads(base64.urlsafe_b64decode(
        tok.split(".")[0] + "=="))
    assert head["alg"] == "HS256"
    claims = verify_session(tok, secret=b"s3cret")
    assert claims is not None and claims["role"] == "operator"


# --------------------------------------------------------- EdDSA sign -> verify

def test_eddsa_roundtrip_with_keypair(keypair):
    """sign_session(alg="EdDSA", private_key=seed) -> the header is EdDSA and the
    matching public key verifies the token."""
    priv, pub = keypair
    tok = sign_session("admin", email="a@x", jti="j1",
                       alg="EdDSA", private_key=priv)
    import base64
    import json
    head = json.loads(base64.urlsafe_b64decode(tok.split(".")[0] + "=="))
    assert head["alg"] == "EdDSA"
    claims = decode_session(tok, public_key=pub)
    assert claims is not None
    assert claims["role"] == "admin" and claims["email"] == "a@x"


def test_relay_verifies_home_token_with_pubkey_only(keypair):
    """THE relay seam: a home mints an EdDSA session with its PRIVATE seed; a
    relay that holds ONLY the public key verifies it -- no home secret needed,
    and the HMAC secret is never consulted on this path."""
    home_priv, home_pub = keypair
    token = sign_session("viewer", email="v@x", jti="jr",
                         alg="EdDSA", private_key=home_priv)
    # relay side: ONLY the public key
    claims = decode_session(token, public_key=home_pub)
    assert claims is not None and claims["role"] == "viewer"


# ------------------------------------------- ALGORITHM-CONFUSION defense (core)

def test_eddsa_token_rejected_by_hmac_path(keypair):
    """An EdDSA-signed token fed to the HMAC verifier (secret path) is rejected --
    the header alg (EdDSA) is not one the HMAC verifier accepts."""
    priv, _pub = keypair
    tok = sign_session("admin", alg="EdDSA", private_key=priv)
    assert decode_session(tok, secret=b"any-hmac-secret") is None
    assert verify_session(tok, secret=b"any-hmac-secret") is None


def test_hs256_token_rejected_by_eddsa_path(keypair):
    """An HS256 token fed to the EdDSA (pubkey) verifier is rejected -- the header
    alg (HS256) is not accepted on the pubkey path."""
    _priv, pub = keypair
    tok = sign_session("admin", secret=b"home-secret")
    assert decode_session(tok, public_key=pub) is None


def test_public_key_used_as_hmac_secret_does_not_verify(keypair):
    """The public key must NEVER be usable as an HMAC secret. Feeding an EdDSA
    token AND the pubkey-as-secret to the HMAC path fails-closed (alg mismatch),
    and the pubkey bytes are never accepted as a MAC key."""
    priv, pub = keypair
    tok = sign_session("admin", alg="EdDSA", private_key=priv)
    # pubkey handed in as an HMAC secret -> HMAC path -> alg mismatch -> None
    assert decode_session(tok, secret=pub.encode()) is None
    # and even a token whose alg the HMAC path WOULD accept can't be forged by
    # HMAC-ing with the public key: an HS256 token MAC'd under the real secret
    # is not verifiable with the pubkey-as-secret.
    hs = sign_session("admin", secret=b"real-secret")
    assert decode_session(hs, secret=pub.encode()) is None


def test_accept_alg_narrows_to_single_alg(keypair):
    """``accept_alg`` pins exactly one alg: an HS256 token is rejected when the
    caller only accepts EdDSA, even though a home secret is otherwise present."""
    priv, pub = keypair
    ed = sign_session("admin", alg="EdDSA", private_key=priv)
    hs = sign_session("admin", secret=b"real-secret")
    # EdDSA-only acceptance via pubkey path
    assert decode_session(ed, public_key=pub, accept_alg="EdDSA") is not None
    assert decode_session(hs, public_key=pub, accept_alg="EdDSA") is None
    # HS256-only acceptance rejects an EdDSA token
    assert decode_session(hs, secret=b"real-secret", accept_alg="HS256") is not None
    assert decode_session(ed, secret=b"real-secret", accept_alg="HS256") is None


# ------------------------------------------------- tamper / wrong-key / expiry

def test_eddsa_tampered_payload_rejected(keypair):
    """Flipping a payload byte breaks the Ed25519 signature -> rejected."""
    priv, pub = keypair
    tok = sign_session("admin", email="a@x", alg="EdDSA", private_key=priv)
    head, payload, sig = tok.split(".")
    bad_payload = ("A" if payload[0] != "A" else "B") + payload[1:]
    tampered = f"{head}.{bad_payload}.{sig}"
    assert decode_session(tampered, public_key=pub) is None


def test_eddsa_tampered_signature_rejected(keypair):
    """Flipping a signature byte -> rejected (fail-closed, never raises)."""
    priv, pub = keypair
    tok = sign_session("admin", alg="EdDSA", private_key=priv)
    head, payload, sig = tok.split(".")
    bad_sig = ("A" if sig[0] != "A" else "B") + sig[1:]
    tampered = f"{head}.{payload}.{bad_sig}"
    assert decode_session(tampered, public_key=pub) is None


def test_eddsa_wrong_pubkey_rejected(keypair):
    """A token verified against a DIFFERENT public key is rejected."""
    priv, _pub = keypair
    _other_priv, other_pub = eddsa.generate_keypair()
    tok = sign_session("admin", alg="EdDSA", private_key=priv)
    assert decode_session(tok, public_key=other_pub) is None


def test_eddsa_expired_exp_rejected(keypair):
    """An expired ``exp`` claim is rejected on the EdDSA path too (verified after
    the signature check, exactly like HS256)."""
    priv, pub = keypair
    tok = sign_session("admin", alg="EdDSA", private_key=priv,
                       ttl_s=10, now=1000.0)
    assert decode_session(tok, public_key=pub, now=1005.0) is not None  # valid
    assert decode_session(tok, public_key=pub, now=2000.0) is None       # expired


def test_eddsa_private_key_implies_eddsa(keypair):
    """Passing a ``private_key`` (no explicit alg) implies EdDSA signing."""
    priv, pub = keypair
    tok = sign_session("operator", private_key=priv)
    import base64
    import json
    head = json.loads(base64.urlsafe_b64decode(tok.split(".")[0] + "=="))
    assert head["alg"] == "EdDSA"
    assert decode_session(tok, public_key=pub) is not None


def test_eddsa_verify_fail_closed_on_bad_pubkey(keypair):
    """A malformed/empty public key never raises -- it fails closed to None."""
    priv, _pub = keypair
    tok = sign_session("admin", alg="EdDSA", private_key=priv)
    assert decode_session(tok, public_key="") is None
    assert decode_session(tok, public_key="not-base64!!!") is None
    assert eddsa.verify(b"x", "sig", "bad-key") is False


def test_strict_decode_raises_on_alg_mismatch(keypair):
    """``strict=True`` surfaces the reason as a SessionError instead of None."""
    from astrodeck.auth.session import SessionError
    priv, _pub = keypair
    tok = sign_session("admin", alg="EdDSA", private_key=priv)
    with pytest.raises(SessionError):
        decode_session(tok, secret=b"hmac", strict=True)
