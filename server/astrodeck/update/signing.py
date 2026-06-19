"""Ed25519 detached-signature primitives for release artifacts.

A release ships three files alongside the tarball:

    astrodeck-<v>.tar.gz          the artifact
    astrodeck-<v>.tar.gz.sha256   "<hex>  <filename>"  (GNU sha256sum format)
    astrodeck-<v>.tar.gz.sig      base64 Ed25519 signature over the RAW artifact bytes

Keys are raw 32-byte Ed25519, base64-encoded for transport/config:

    private "seed":  base64(32-byte seed)  -> GitHub Actions secret RELEASE_SIGNING_KEY
    public  key:     base64(32-byte pubkey) -> pinned in UpdateConfig.signing_pubkey

Verification is **FAIL-CLOSED**. A missing public key, missing ``cryptography``,
a sha256 mismatch, or a bad signature all return ``(False, reason)``. There is
deliberately NO HMAC fallback (unlike the relay's dev signer): an update artifact
must be verified with real asymmetric crypto or be rejected. Signing the SHA256
file's digest is also checked so an operator can reproduce the integrity check by
hand with ``sha256sum -c``.

Import-light apart from the optional ``cryptography`` import (already a server dep).
"""
from __future__ import annotations

import base64
import hashlib
from pathlib import Path

try:  # cryptography is a server dependency; guard so import never bricks the app.
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
        Ed25519PublicKey,
    )
    _HAVE_ED25519 = True
except Exception:  # pragma: no cover - exercised only in a crypto-less env
    _HAVE_ED25519 = False


class SigningUnavailable(RuntimeError):
    """Raised by the SIGN path (CI/tests) when ``cryptography`` is missing.

    The VERIFY path never raises this -- it returns ``(False, ...)`` so a scope
    can never be tricked into accepting an unverifiable artifact."""


# ------------------------------------------------------------------ key material

def generate_keypair() -> tuple[str, str]:
    """Generate a fresh Ed25519 keypair. Returns ``(private_seed_b64, public_b64)``.

    The private seed (32 bytes) goes into the ``RELEASE_SIGNING_KEY`` CI secret;
    the public key is pinned in ``UpdateConfig.signing_pubkey``."""
    if not _HAVE_ED25519:
        raise SigningUnavailable("cryptography is required to generate keys")
    priv = Ed25519PrivateKey.generate()
    from cryptography.hazmat.primitives import serialization
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
        raise SigningUnavailable("cryptography is required to sign")
    seed = base64.b64decode(seed_b64.strip(), validate=True)
    if len(seed) != 32:
        raise SigningUnavailable(f"private seed must be 32 bytes, got {len(seed)}")
    return Ed25519PrivateKey.from_private_bytes(seed)


def _load_public(pub_b64: str) -> "Ed25519PublicKey | None":
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

def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sign_bytes(data: bytes, private_seed_b64: str) -> str:
    """Sign ``data`` with the Ed25519 private seed; return a base64 signature."""
    priv = _load_private(private_seed_b64)
    return base64.b64encode(priv.sign(data)).decode()


def verify_bytes(data: bytes, signature_b64: str, public_b64: str) -> bool:
    """True iff ``signature_b64`` is a valid Ed25519 signature of ``data`` under
    ``public_b64``. Fail-closed: any error (missing crypto, bad key, bad b64,
    bad signature) returns ``False``."""
    pub = _load_public(public_b64)
    if pub is None:
        return False
    try:
        sig = base64.b64decode((signature_b64 or "").strip(), validate=True)
    except Exception:
        return False
    try:
        pub.verify(sig, data)
        return True
    except InvalidSignature:
        return False
    except Exception:  # pragma: no cover - defensive
        return False


# --------------------------------------------------------------- file sidecars

def sha256_sidecar_text(artifact: Path, digest_hex: str | None = None) -> str:
    """GNU ``sha256sum`` line for ``artifact``: ``"<hex>  <name>\n"``."""
    if digest_hex is None:
        digest_hex = sha256_hex(artifact.read_bytes())
    return f"{digest_hex}  {artifact.name}\n"


def parse_sha256_sidecar(text: str) -> str | None:
    """Extract the hex digest from a sha256sum-format line. ``None`` if malformed."""
    text = (text or "").strip()
    if not text:
        return None
    head = text.split()[0].lower()
    if len(head) == 64 and all(c in "0123456789abcdef" for c in head):
        return head
    return None


def write_sidecars(artifact: Path, private_seed_b64: str) -> tuple[Path, Path]:
    """Write ``<artifact>.sha256`` and ``<artifact>.sig`` next to ``artifact``.
    Used by the CI sign step. Returns the two sidecar paths."""
    data = artifact.read_bytes()
    sha_path = artifact.with_name(artifact.name + ".sha256")
    sig_path = artifact.with_name(artifact.name + ".sig")
    sha_path.write_text(sha256_sidecar_text(artifact, sha256_hex(data)))
    sig_path.write_text(sign_bytes(data, private_seed_b64) + "\n")
    return sha_path, sig_path


def verify_artifact(artifact: Path, public_b64: str, *,
                    sha256_text: str | None = None,
                    signature_b64: str | None = None) -> tuple[bool, str]:
    """Verify a downloaded artifact. Returns ``(ok, reason)``.

    Order: (1) a public key must be pinned; (2) if a ``.sha256`` sidecar is
    provided it must match the artifact bytes; (3) the Ed25519 signature must
    verify. Any failure => ``(False, reason)`` -- never an exception."""
    if not (public_b64 or "").strip():
        return False, "no signing public key pinned (refusing to trust artifact)"
    if not _HAVE_ED25519:
        return False, "cryptography unavailable; cannot verify signature"
    try:
        data = artifact.read_bytes()
    except OSError as e:
        return False, f"cannot read artifact: {e}"

    digest = sha256_hex(data)
    if sha256_text is not None:
        want = parse_sha256_sidecar(sha256_text)
        if want is None:
            return False, "malformed .sha256 sidecar"
        if want != digest:
            return False, f"sha256 mismatch (want {want[:12]}.., got {digest[:12]}..)"

    if signature_b64 is None:
        return False, "no signature provided"
    if not verify_bytes(data, signature_b64, public_b64):
        return False, "Ed25519 signature verification failed"
    return True, "ok"
