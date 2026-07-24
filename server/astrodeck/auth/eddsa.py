"""Ed25519 (EdDSA) primitives for ASYMMETRIC session tokens (W3 seam).

Kept SEPARATE from :mod:`astrodeck.auth.session` so the default home-session
path stays stdlib-only (HMAC-SHA256, no external crypto). ``session.py`` imports
this module lazily and ONLY when EdDSA is actually selected, so a crypto-less
environment keeps the HS256 default working unchanged.

Why EdDSA at all: a relay can verify a home-issued principal with ONLY the
PUBLIC key -- it never holds the home's signing secret. The home signs with an
Ed25519 private seed; the relay (or the home itself) verifies with the matching
public key.

Key convention MIRRORS ``astrodeck.update.signing`` EXACTLY so keys are
interchangeable across the two subsystems:

    private "seed":  base64(32-byte Ed25519 seed)   -> AuthConfig.session_private_key
    public  key:     base64(32-byte Ed25519 pubkey) -> AuthConfig.session_public_key
                                                        / relay_pubkey / viewer_link_pubkey

Signing produces a base64url (no padding) detached signature over the EXACT
``signing_input`` bytes (``header_b64 + "." + payload_b64``) -- the same segment
encoding the HS256 MAC already uses, so the compact ``h.p.s`` token shape is
unchanged. Verification is FAIL-CLOSED: any error (missing ``cryptography``, bad
key, bad base64, bad signature) returns ``False`` and NEVER raises out of
:func:`verify`.
"""
from __future__ import annotations

import base64

try:  # cryptography is a server dependency; guard so import never bricks auth.
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
        Ed25519PublicKey,
    )
    _HAVE_ED25519 = True
except Exception:  # pragma: no cover - exercised only in a crypto-less env
    _HAVE_ED25519 = False


class EdDSAUnavailable(RuntimeError):
    """Raised by the SIGN path (:func:`sign`) when ``cryptography`` is missing or
    the seed is malformed. The VERIFY path never raises this -- it returns
    ``False`` so a token can never be accepted without real verification."""


def have_eddsa() -> bool:
    """True iff the ``cryptography`` Ed25519 backend is importable."""
    return _HAVE_ED25519


# ---------------------------------------------------------------- base64url glue
# Mirror session.py's segment encoding so a signature slots into the compact
# ``header.payload.sig`` token identically to the HS256 MAC.

def _b64u_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64u_decode(seg: str) -> bytes:
    pad = "=" * (-len(seg) % 4)
    return base64.urlsafe_b64decode(seg + pad)


# ------------------------------------------------------------------ key material
# Base64 (standard, NOT url-safe) 32-byte raw keys -- identical to update/signing.

def generate_keypair() -> tuple[str, str]:
    """Generate a fresh Ed25519 keypair. Returns ``(private_seed_b64, public_b64)``.

    Standard-base64, 32-byte raw seed/pubkey -- byte-compatible with
    ``astrodeck.update.signing.generate_keypair``. Used by key provisioning and
    tests (which always use EPHEMERAL keypairs -- never a hardcoded seed)."""
    if not _HAVE_ED25519:
        raise EdDSAUnavailable("cryptography is required to generate keys")
    from cryptography.hazmat.primitives import serialization
    priv = Ed25519PrivateKey.generate()
    seed = priv.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub = priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return base64.b64encode(seed).decode(), base64.b64encode(pub).decode()


def _load_private(seed_b64: str) -> "Ed25519PrivateKey":
    if not _HAVE_ED25519:
        raise EdDSAUnavailable("cryptography is required to sign")
    try:
        seed = base64.b64decode((seed_b64 or "").strip(), validate=True)
    except Exception as e:  # noqa: BLE001 - malformed seed -> uniform sign error
        raise EdDSAUnavailable(f"malformed private seed: {e}") from e
    if len(seed) != 32:
        raise EdDSAUnavailable(f"private seed must be 32 bytes, got {len(seed)}")
    return Ed25519PrivateKey.from_private_bytes(seed)


def _load_public(pub_b64: str) -> "Ed25519PublicKey | None":
    """Load a base64 Ed25519 public key, or ``None`` on ANY problem (missing
    crypto, bad base64, wrong length, bad key). Fail-closed for the verify path."""
    if not _HAVE_ED25519:
        return None
    try:
        raw = base64.b64decode((pub_b64 or "").strip(), validate=True)
    except Exception:
        return None
    if len(raw) != 32:
        return None
    try:
        return Ed25519PublicKey.from_public_bytes(raw)
    except Exception:
        return None


# ------------------------------------------------------------------ core crypto

def sign(signing_input: bytes, private_seed_b64: str) -> str:
    """Ed25519-sign ``signing_input`` with the base64 seed; return a base64url
    (no padding) signature -- the same segment encoding the HS256 MAC uses.

    Raises :class:`EdDSAUnavailable` if crypto is missing or the seed is bad
    (the caller is minting a token and must fail loudly, not silently)."""
    priv = _load_private(private_seed_b64)
    return _b64u_encode(priv.sign(signing_input))


def verify(signing_input: bytes, sig_b64u: str, public_b64: str) -> bool:
    """True iff ``sig_b64u`` (base64url, no padding) is a valid Ed25519 signature
    of ``signing_input`` under ``public_b64`` (standard base64 pubkey).

    FAIL-CLOSED: any error (missing crypto, bad/empty key, bad base64, signature
    mismatch) returns ``False`` and NEVER raises -- a scope can never be tricked
    into accepting an unverifiable token."""
    pub = _load_public(public_b64)
    if pub is None:
        return False
    try:
        sig = _b64u_decode((sig_b64u or "").strip())
    except Exception:
        return False
    try:
        pub.verify(sig, signing_input)
        return True
    except InvalidSignature:
        return False
    except Exception:  # pragma: no cover - defensive
        return False
