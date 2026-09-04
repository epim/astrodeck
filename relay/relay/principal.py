"""Home-verifiable principal tokens minted by the relay (W3.3.2 / W3.3.5).

The relay proves a remote caller's identity (Google OIDC, or a viewer link it
mints) and forwards a **home-verifiable principal token** in the ``REQ_OPEN``
header. The home injects the principal ONLY from that token and re-derives RBAC
itself; the relay's role stamp is advisory.

CRITICAL invariant (W3.7.3): **the relay holds NO signing secret that lets it
mint a home-trusted ADMIN.** Concretely:

  * Identity tokens are signed with an **asymmetric** key: the relay holds the
    PRIVATE half, the home holds the PUBLIC half (``relay_pubkey`` in the home
    config). A relay *compromise* leaks that private key -- but the owner's
    accepted model is "the relay can act as an already-authenticated VIEWER in
    real time" (it is a plaintext MITM); the guard that matters is that the home
    NEVER shares its OWN session secret with the relay, so a relay can't forge a
    *home-issued* token, and -- belt and suspenders -- ``admin``/``config.*``
    routes are tunnel-blocked at the home regardless of the forwarded principal.
  * **Viewer links are signed with a SEPARATE key** (``viewer_link_pubkey``) so
    a link-minting bug can never forge an ``admin``/config session, and so the
    home can revoke ALL viewer links (rotate that one key) without touching the
    OIDC-principal path.

Production crypto is **Ed25519 (EdDSA)** via the ``cryptography`` wheel -- a
small, JWS-shaped ``header.payload.sig`` token (alg ``EdDSA``) the home verifies
with the public key. Missing cryptography or missing seeds fails closed: the
network server constructs no signer. The explicit ``dev_hmac`` constructor
(alg ``HS256-DEV``) exists only for off-wire unit tests and is never selected by
runtime startup.

Token claims:
    {sub, email, role, caps[], jti, iat, exp, kind}   kind in {"oidc","viewer"}
The home maps ``role``/``caps`` through its OWN RBAC (``caps`` is advisory for an
OIDC principal, EXPLICIT-and-frozen for a viewer link).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass, field

try:  # pragma: no cover - import branch, exercised by whichever env runs
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
        Ed25519PublicKey,
    )

    _HAVE_ED25519 = True
except Exception:  # noqa: BLE001 - any import failure => Ed25519 unavailable
    _HAVE_ED25519 = False


# Token kinds.
KIND_OIDC = "oidc"      # a Google-OIDC-authenticated remote user
KIND_VIEWER = "viewer"  # a scoped, expiring viewer LINK (no Google account)

# The default capability set a /share viewer LINK carries (mirrors the home's
# VIEWER_LINK_CAPS: live-watch status + downsized preview, NO bulk science
# frames, NO precise coordinates). The home re-pins these; this is the relay's
# advisory stamp. Precise/media caps are grantable ONLY by explicit per-link
# opt-in (see ViewerLinkSpec.extra_caps).
VIEWER_LINK_CAPS = ("view.status", "view.preview")

# Capabilities a viewer link may NEVER carry, even by explicit opt-in -- the
# relay refuses to stamp them and the home re-checks. These are the
# privilege-defining / destructive caps.
VIEWER_FORBIDDEN_CAP_PREFIXES = ("admin", "config", "control")


class PrincipalError(Exception):
    """A token could not be minted/verified (bad key, bad caps, expired)."""


# --------------------------------------------------------------- base64url glue

def _b64u_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64u_decode(seg: str) -> bytes:
    pad = "=" * (-len(seg) % 4)
    return base64.urlsafe_b64decode(seg + pad)


# ------------------------------------------------------------------- the signer

@dataclass
class PrincipalSigner:
    """Mints (and, for tests, verifies) home-verifiable principal tokens.

    Construct with ``from_ed25519_seed`` for production, or ``dev_hmac`` for the
    no-``cryptography`` unit-test/dev path. The relay keeps ONE signer for OIDC
    principals and a SECOND, distinct signer for viewer links (separate keys)."""

    alg: str
    _private: object = None          # Ed25519PrivateKey | HMAC secret bytes
    _public: object = None           # Ed25519PublicKey (verify side; tests only)
    kid: str = ""                    # key id, surfaced in the token header

    # -- constructors ---------------------------------------------------------

    @classmethod
    def from_ed25519_seed(cls, seed: bytes, *, kid: str = "") -> "PrincipalSigner":
        """Build an EdDSA signer from a 32-byte Ed25519 seed (the relay's PRIVATE
        key material). Raises if ``cryptography`` is unavailable."""
        if not _HAVE_ED25519:
            raise PrincipalError(
                "cryptography (Ed25519) not installed; cannot mint a "
                "production principal token. Install relay requirements or use "
                "dev_hmac() for tests."
            )
        if len(seed) != 32:
            raise PrincipalError("Ed25519 seed must be exactly 32 bytes")
        priv = Ed25519PrivateKey.from_private_bytes(seed)
        return cls(alg="EdDSA", _private=priv, _public=priv.public_key(), kid=kid)

    @classmethod
    def dev_hmac(cls, secret: bytes = b"relay-dev-insecure", *,
                 kid: str = "dev") -> "PrincipalSigner":
        """A LOUD dev-only HMAC signer for the no-crypto unit-test path. NEVER
        production-safe (symmetric: anyone with the secret forges tokens)."""
        return cls(alg="HS256-DEV", _private=bytes(secret), _public=bytes(secret),
                   kid=kid)

    def is_dev(self) -> bool:
        """True iff this is the insecure dev-HMAC signer (production guard)."""
        return self.alg == "HS256-DEV"

    def public_key_bytes(self) -> bytes:
        """The raw public key the HOME loads as ``relay_pubkey`` /
        ``viewer_link_pubkey``. For the dev-HMAC signer this is the shared
        secret (dev only)."""
        if self.alg == "EdDSA":
            from cryptography.hazmat.primitives import serialization

            return self._public.public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw,
            )
        return bytes(self._public)

    # -- sign / verify --------------------------------------------------------

    def _sign(self, signing_input: bytes) -> bytes:
        if self.alg == "EdDSA":
            return self._private.sign(signing_input)
        return hmac.new(self._private, signing_input, hashlib.sha256).digest()

    def _verify_sig(self, signing_input: bytes, sig: bytes) -> bool:
        if self.alg == "EdDSA":
            try:
                self._public.verify(sig, signing_input)
                return True
            except Exception:  # noqa: BLE001 - invalid signature
                return False
        expected = hmac.new(self._private, signing_input, hashlib.sha256).digest()
        return hmac.compare_digest(sig, expected)

    def sign(self, claims: dict) -> str:
        """Serialize ``claims`` into a compact JWS-shaped principal token."""
        header = {"alg": self.alg, "typ": "ADPRIN", "kid": self.kid}
        header_b64 = _b64u_encode(
            json.dumps(header, separators=(",", ":"), sort_keys=True).encode()
        )
        payload_b64 = _b64u_encode(
            json.dumps(claims, separators=(",", ":"), sort_keys=True).encode()
        )
        signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
        sig_b64 = _b64u_encode(self._sign(signing_input))
        return f"{header_b64}.{payload_b64}.{sig_b64}"

    def verify(self, token: str, *, now: float | None = None) -> dict:
        """Verify ``token`` and return its claims, or raise ``PrincipalError``.

        This is the HOME's job in production; included here so the relay's own
        tests can assert a minted token round-trips and is well-formed. Checks
        (all fail-closed): three segments; header alg matches; signature valid;
        well-formed JSON; ``exp`` not past."""
        if now is None:
            now = time.time()
        parts = token.split(".")
        if len(parts) != 3:
            raise PrincipalError("malformed token (expected header.payload.sig)")
        header_b64, payload_b64, sig_b64 = parts
        try:
            header = json.loads(_b64u_decode(header_b64))
        except (ValueError, json.JSONDecodeError) as exc:
            raise PrincipalError("bad header encoding") from exc
        if not isinstance(header, dict) or header.get("alg") != self.alg:
            raise PrincipalError("unexpected alg")
        signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
        try:
            sig = _b64u_decode(sig_b64)
        except ValueError as exc:
            raise PrincipalError("bad signature encoding") from exc
        if not self._verify_sig(signing_input, sig):
            raise PrincipalError("bad signature")
        try:
            payload = json.loads(_b64u_decode(payload_b64))
        except (ValueError, json.JSONDecodeError) as exc:
            raise PrincipalError("bad payload encoding") from exc
        if not isinstance(payload, dict):
            raise PrincipalError("payload not an object")
        exp = payload.get("exp")
        if exp is not None and float(exp) < float(now):
            raise PrincipalError("token expired")
        return payload


# ---------------------------------------------------------------- minting API

def mint_oidc_principal(signer: PrincipalSigner, *, sub: str, email: str,
                        role: str, caps: list[str], ttl_s: int = 3600,
                        now: float | None = None,
                        jti: str | None = None) -> str:
    """Mint a token for a Google-OIDC-authenticated remote user.

    ``role``/``caps`` are ADVISORY -- the home re-derives RBAC from its own role
    allowlist; this stamp lets the home short-circuit and audit. Signed with the
    OIDC (``relay_pubkey``) key."""
    if now is None:
        now = time.time()
    if not sub or not email:
        raise PrincipalError("oidc principal needs sub + email")
    claims = {
        "kind": KIND_OIDC,
        "sub": sub,
        "email": email,
        "role": role,
        "caps": sorted(set(caps)),
        "jti": jti or secrets.token_urlsafe(12),
        "iat": int(now),
        "exp": int(now) + int(ttl_s),
    }
    return signer.sign(claims)


@dataclass
class ViewerLinkSpec:
    """The shape of a viewer link a relay admin mints (W3.3.5).

    ``extra_caps`` is the explicit per-link opt-in for ``view.media`` (raw FITS)
    and/or ``view.site_precise`` (precise coordinates) -- BOTH excluded from the
    default ``VIEWER_LINK_CAPS``. ``max_viewers`` and the TTL/renew policy are
    carried so the relay can enforce them; the ``jti`` makes the link revocable
    before ``exp`` independent of any key rotation."""

    label: str = "viewer-link"
    ttl_s: int = 6 * 3600                 # default 6h imaging session
    renew_while_connected: bool = True    # may refresh exp while a viewer holds it
    max_viewers: int = 5                  # concurrent viewers per link
    extra_caps: tuple[str, ...] = field(default_factory=tuple)  # opt-in media/precise


def viewer_link_caps(extra_caps: tuple[str, ...] = ()) -> list[str]:
    """The full cap list for a viewer link: the default read-only set PLUS any
    explicit opt-in caps. Raises if a forbidden (admin/config/control) cap is
    requested -- a viewer link can NEVER carry a privilege-defining cap."""
    caps = set(VIEWER_LINK_CAPS)
    for cap in extra_caps:
        if any(cap == p or cap.startswith(p + ".") for p in VIEWER_FORBIDDEN_CAP_PREFIXES):
            raise PrincipalError(
                f"viewer link may not carry privileged cap {cap!r}"
            )
        caps.add(cap)
    return sorted(caps)


def mint_viewer_link_token(signer: PrincipalSigner, spec: ViewerLinkSpec, *,
                           jti: str | None = None,
                           now: float | None = None) -> tuple[str, str]:
    """Mint a viewer-ONLY token from a ``ViewerLinkSpec``.

    Returns ``(token, jti)``; the ``jti`` goes in the relay's allow/deny
    registry so a leaked ``/share`` link is revocable before ``exp``. Signed
    with the SEPARATE viewer-link key so a bug here can never forge an admin
    session. The cap set is EXPLICIT (frozen) and forbidden caps are rejected at
    mint time AND re-checked at the home."""
    if now is None:
        now = time.time()
    jti = jti or secrets.token_urlsafe(12)
    caps = viewer_link_caps(spec.extra_caps)  # raises on a forbidden cap
    claims = {
        "kind": KIND_VIEWER,
        "sub": f"viewer:{spec.label}",
        "email": None,
        "role": "viewer",
        "caps": caps,
        "jti": jti,
        "label": spec.label,
        "max_viewers": int(spec.max_viewers),
        "iat": int(now),
        "exp": int(now) + int(spec.ttl_s),
    }
    return signer.sign(claims), jti
