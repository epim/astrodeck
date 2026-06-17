"""Google OIDC building blocks (W2.4 Stage C) -- the GoogleOIDC client.

This module owns the *mechanics* of the Authorization-Code + PKCE flow against
Google's OpenID Connect endpoints, plus the security-critical ID-token
verification. It is deliberately stdlib + ``httpx``-only (no PyJWT / authlib /
google-auth / cryptography wheel is installed in the venv), so RS256 signature
verification is implemented here in pure Python (RSA verify = modular
exponentiation of the signature against the JWKS modulus/exponent, then a
constant-time compare of the PKCS#1 v1.5 SHA-256 DigestInfo).

The provider in ``auth/routes.py`` wires this client to the CORE
``GoogleAuthProvider`` (session-cookie resolution) and the
``role_allowlist``/``default_role`` mapping. Nothing here yields a Principal
directly; it returns a *verified claims dict* (email/email_verified/hd/sub/...)
and the route layer maps that to a role and mints a CORE session cookie.

Security checks enforced by ``verify_id_token`` (all fail-closed -> raise):
  - exactly three JWS segments; header ``alg`` in {RS256} (NOT ``none``);
  - the signing key (by ``kid``) is found in the supplied JWKS;
  - RSA PKCS#1 v1.5 / SHA-256 signature verifies over ``header.payload``;
  - ``iss`` in the accepted Google issuers; ``aud`` == our client_id;
  - ``exp`` not past and ``iat``/``nbf`` not in the future (with small skew);
  - ``email_verified`` is true; ``hd`` equals the pinned domain when configured;
  - ``nonce`` matches the one bound to this login (replay defense).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

import httpx

# Google's discovery document is static enough to hard-pin the endpoints; the
# JWKS uri is fetched (keys rotate). Pinning avoids a discovery round-trip and a
# discovery-spoofing surface. These are the canonical OIDC endpoints.
GOOGLE_AUTH_URI = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"
GOOGLE_JWKS_URI = "https://www.googleapis.com/oauth2/v3/certs"
GOOGLE_ISSUERS = ("https://accounts.google.com", "accounts.google.com")

# Accepted ID-token signing algorithms. Google signs ID tokens with RS256. We
# explicitly DENY ``none`` and any symmetric alg here (the classic JWT-confusion
# attack): a token must be RS256, verified against the JWKS public key.
_ACCEPTED_ALGS = ("RS256",)

# Allowed clock skew (seconds) for exp/iat/nbf checks.
_CLOCK_SKEW_S = 120

# JWKS cache TTL (seconds). Keys rotate ~daily; a short cache bounds the spoof
# window while avoiding a fetch on every callback.
_JWKS_TTL_S = 3600


class OIDCError(Exception):
    """Any OIDC failure (bad token, bad exchange, key not found, claim
    mismatch). Always fail-closed: the route layer turns this into a 401/403 and
    NEVER mints a session."""


# --------------------------------------------------------------- base64url glue

def _b64u_decode(seg: str) -> bytes:
    if isinstance(seg, bytes):
        seg = seg.decode("ascii")
    pad = "=" * (-len(seg) % 4)
    return base64.urlsafe_b64decode(seg + pad)


def _b64u_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64u_to_int(seg: str) -> int:
    return int.from_bytes(_b64u_decode(seg), "big")


# ------------------------------------------------------------------- PKCE / state

def new_pkce_verifier() -> str:
    """A high-entropy PKCE code_verifier (RFC 7636 unreserved chars, 43..128)."""
    # token_urlsafe gives url-safe base64; 64 bytes -> ~86 chars, within bounds.
    return secrets.token_urlsafe(64)


def pkce_challenge(verifier: str) -> str:
    """S256 challenge = base64url(SHA256(verifier)) (no padding)."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return _b64u_encode(digest)


def new_state() -> str:
    """An unguessable CSRF ``state`` value bound to the pre-auth cookie."""
    return secrets.token_urlsafe(32)


def new_nonce() -> str:
    """An unguessable ``nonce`` echoed in the ID token to defeat replay."""
    return secrets.token_urlsafe(32)


# ----------------------------------------------------------- RS256 verification

# PKCS#1 v1.5 DigestInfo prefix for SHA-256 (RFC 8017 / 3447). The full encoded
# message is: 0x00 0x01 PS 0x00 || DigestInfo, where DigestInfo for SHA-256 is
# this fixed ASN.1 prefix followed by the 32-byte digest.
_SHA256_DIGESTINFO_PREFIX = bytes.fromhex(
    "3031300d060960864801650304020105000420")


def _rsa_verify_pkcs1_sha256(n: int, e: int, signature: bytes,
                             message: bytes) -> bool:
    """Verify an RSA PKCS#1 v1.5 / SHA-256 signature in pure Python.

    ``s^e mod n`` recovers the EMSA-PKCS1-v1_5 encoded message; we rebuild the
    expected encoding for ``message`` and compare in constant time. Any size or
    structural mismatch -> False (fail-closed)."""
    k = (n.bit_length() + 7) // 8
    if len(signature) != k:
        return False
    sig_int = int.from_bytes(signature, "big")
    if sig_int >= n:
        return False
    em_int = pow(sig_int, e, n)
    em = em_int.to_bytes(k, "big")
    # Build the expected EM: 0x00 0x01 || PS(0xFF...) || 0x00 || DigestInfo||H.
    digest = hashlib.sha256(message).digest()
    t = _SHA256_DIGESTINFO_PREFIX + digest
    ps_len = k - len(t) - 3
    if ps_len < 8:  # RFC requires at least 8 octets of padding
        return False
    expected = b"\x00\x01" + (b"\xff" * ps_len) + b"\x00" + t
    return hmac.compare_digest(em, expected)


def _select_jwk(jwks: dict, kid: str | None) -> dict | None:
    """Pick the JWK matching ``kid`` (or the sole RSA key if ``kid`` absent)."""
    keys = jwks.get("keys") if isinstance(jwks, dict) else None
    if not isinstance(keys, list):
        return None
    rsa_keys = [k for k in keys if isinstance(k, dict) and k.get("kty") == "RSA"]
    if kid:
        for k in rsa_keys:
            if k.get("kid") == kid:
                return k
        return None
    return rsa_keys[0] if len(rsa_keys) == 1 else None


def decode_jwt_unverified(token: str) -> tuple[dict, dict, bytes, bytes]:
    """Split a JWT into (header, payload, signing_input, signature_bytes).

    Does NOT verify anything -- callers MUST run ``verify_id_token``. Raises
    ``OIDCError`` on a structurally invalid token."""
    if not token or not isinstance(token, str):
        raise OIDCError("empty token")
    parts = token.split(".")
    if len(parts) != 3:
        raise OIDCError("malformed JWT (expected header.payload.sig)")
    h_b64, p_b64, s_b64 = parts
    try:
        header = json.loads(_b64u_decode(h_b64))
        payload = json.loads(_b64u_decode(p_b64))
        sig = _b64u_decode(s_b64)
    except (ValueError, json.JSONDecodeError) as exc:
        raise OIDCError(f"bad JWT encoding: {exc}") from exc
    if not isinstance(header, dict) or not isinstance(payload, dict):
        raise OIDCError("JWT header/payload not an object")
    signing_input = f"{h_b64}.{p_b64}".encode("ascii")
    return header, payload, signing_input, sig


def verify_id_token(token: str, *, jwks: dict, client_id: str,
                    nonce: str | None = None, hd: str = "",
                    now: float | None = None,
                    issuers: tuple[str, ...] = GOOGLE_ISSUERS) -> dict:
    """Verify a Google ID token and return its claims, or raise ``OIDCError``.

    Performs (in order, all fail-closed):
      1. structural decode; ``alg`` in the accepted RS256 set (rejects ``none``);
      2. JWKS key lookup by ``kid``; RSA PKCS#1v1.5/SHA-256 signature verify;
      3. ``iss`` in ``issuers``; ``aud`` == ``client_id`` (string or list);
      4. ``exp`` not past, ``iat``/``nbf`` not in the future (with skew);
      5. ``email_verified`` truthy; ``hd`` == pinned ``hd`` when configured;
      6. ``nonce`` matches the login-bound nonce when supplied.
    """
    if now is None:
        now = time.time()
    header, payload, signing_input, sig = decode_jwt_unverified(token)

    alg = header.get("alg")
    if alg not in _ACCEPTED_ALGS:
        raise OIDCError(f"unexpected id_token alg: {alg!r}")
    jwk = _select_jwk(jwks, header.get("kid"))
    if jwk is None:
        raise OIDCError("no matching JWKS key for id_token kid")
    try:
        n = _b64u_to_int(jwk["n"])
        e = _b64u_to_int(jwk["e"])
    except (KeyError, ValueError) as exc:
        raise OIDCError(f"malformed JWK: {exc}") from exc
    if not _rsa_verify_pkcs1_sha256(n, e, sig, signing_input):
        raise OIDCError("id_token signature verification failed")

    iss = payload.get("iss")
    if iss not in issuers:
        raise OIDCError(f"untrusted issuer: {iss!r}")

    aud = payload.get("aud")
    aud_ok = (aud == client_id) or (isinstance(aud, list) and client_id in aud)
    if not client_id or not aud_ok:
        raise OIDCError("audience mismatch")

    exp = payload.get("exp")
    try:
        if exp is None or float(exp) < float(now) - _CLOCK_SKEW_S:
            raise OIDCError("id_token expired")
    except (TypeError, ValueError) as exc:
        raise OIDCError(f"bad exp claim: {exc}") from exc
    for claim in ("iat", "nbf"):
        val = payload.get(claim)
        if val is None:
            continue
        try:
            if float(val) > float(now) + _CLOCK_SKEW_S:
                raise OIDCError(f"id_token {claim} in the future")
        except (TypeError, ValueError) as exc:
            raise OIDCError(f"bad {claim} claim: {exc}") from exc

    if nonce is not None:
        tok_nonce = payload.get("nonce")
        if not tok_nonce or not hmac.compare_digest(str(tok_nonce), str(nonce)):
            raise OIDCError("nonce mismatch (possible replay)")

    email = payload.get("email")
    if not email or not isinstance(email, str):
        raise OIDCError("id_token has no email")
    # email_verified may be a bool or the string "true" depending on encoding.
    ev = payload.get("email_verified")
    if ev is not True and str(ev).lower() != "true":
        raise OIDCError("email not verified")

    pinned = (hd or "").strip()
    if pinned:
        if (payload.get("hd") or "") != pinned:
            raise OIDCError("hosted-domain (hd) mismatch")

    return payload


# ------------------------------------------------------------- the OIDC client

@dataclass
class GoogleOIDCConfig:
    """The Google client knobs pulled from ``AuthConfig`` (duck-typed)."""
    client_id: str = ""
    client_secret: str = ""
    redirect_uri: str = ""
    hd: str = ""
    auth_uri: str = GOOGLE_AUTH_URI
    token_uri: str = GOOGLE_TOKEN_URI
    jwks_uri: str = GOOGLE_JWKS_URI

    @classmethod
    def from_auth(cls, auth_cfg: Any) -> "GoogleOIDCConfig":
        return cls(
            client_id=(getattr(auth_cfg, "google_client_id", "") or "").strip(),
            client_secret=getattr(auth_cfg, "google_client_secret", "") or "",
            redirect_uri=(getattr(auth_cfg, "google_redirect_uri", "") or "").strip(),
            hd=(getattr(auth_cfg, "google_hd", "") or "").strip(),
        )

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret and self.redirect_uri)


class GoogleOIDCClient:
    """A thin Authorization-Code + PKCE client for Google OIDC.

    Stateless w.r.t. the per-login secrets (state/nonce/verifier) -- those live
    in the pre-auth cookie set by the route layer. This client only builds the
    auth URL, exchanges the code, fetches+caches the JWKS, and verifies the
    returned ID token.
    """

    def __init__(self, cfg: GoogleOIDCConfig,
                 *, http_client: httpx.AsyncClient | None = None):
        self.cfg = cfg
        self._http = http_client  # injectable for tests; else a per-call client
        self._jwks: dict | None = None
        self._jwks_fetched_at: float = 0.0

    # -- auth URL -----------------------------------------------------------
    def build_auth_url(self, *, state: str, nonce: str,
                       code_challenge: str,
                       scope: str = "openid email profile") -> str:
        """The Google consent URL for an Authorization-Code + PKCE login."""
        params = {
            "client_id": self.cfg.client_id,
            "redirect_uri": self.cfg.redirect_uri,
            "response_type": "code",
            "scope": scope,
            "state": state,
            "nonce": nonce,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "access_type": "online",
            "prompt": "select_account",
        }
        if self.cfg.hd:
            params["hd"] = self.cfg.hd  # hint Google to the Workspace domain
        return f"{self.cfg.auth_uri}?{urlencode(params)}"

    # -- token exchange -----------------------------------------------------
    async def exchange_code(self, *, code: str, code_verifier: str) -> dict:
        """Exchange an authorization ``code`` (+ PKCE verifier) for tokens.

        Returns the raw token response dict (carrying ``id_token``). Raises
        ``OIDCError`` on a non-2xx or malformed response."""
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": self.cfg.client_id,
            "client_secret": self.cfg.client_secret,
            "redirect_uri": self.cfg.redirect_uri,
            "code_verifier": code_verifier,
        }
        client = self._http
        owns = False
        if client is None:
            client = httpx.AsyncClient(timeout=10.0)
            owns = True
        try:
            resp = await client.post(self.cfg.token_uri, data=data)
        except httpx.HTTPError as exc:
            raise OIDCError(f"token endpoint unreachable: {exc}") from exc
        finally:
            if owns:
                await client.aclose()
        if resp.status_code != 200:
            raise OIDCError(
                f"token exchange failed ({resp.status_code})")
        try:
            payload = resp.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise OIDCError(f"token response not JSON: {exc}") from exc
        if "id_token" not in payload:
            raise OIDCError("token response missing id_token")
        return payload

    # -- JWKS ---------------------------------------------------------------
    async def get_jwks(self, *, force: bool = False,
                       now: float | None = None) -> dict:
        """Fetch (and cache) Google's JWKS. Cache honored for ``_JWKS_TTL_S``."""
        if now is None:
            now = time.time()
        if (not force and self._jwks is not None
                and (now - self._jwks_fetched_at) < _JWKS_TTL_S):
            return self._jwks
        client = self._http
        owns = False
        if client is None:
            client = httpx.AsyncClient(timeout=10.0)
            owns = True
        try:
            resp = await client.get(self.cfg.jwks_uri)
        except httpx.HTTPError as exc:
            raise OIDCError(f"JWKS endpoint unreachable: {exc}") from exc
        finally:
            if owns:
                await client.aclose()
        if resp.status_code != 200:
            raise OIDCError(f"JWKS fetch failed ({resp.status_code})")
        try:
            jwks = resp.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise OIDCError(f"JWKS not JSON: {exc}") from exc
        self._jwks = jwks
        self._jwks_fetched_at = now
        return jwks

    # -- full verify --------------------------------------------------------
    async def verify(self, id_token: str, *, nonce: str | None,
                     now: float | None = None) -> dict:
        """Fetch the JWKS (cached) and verify ``id_token`` -> verified claims.

        On a ``kid`` miss (key rotation), force-refreshes the JWKS once and
        retries before giving up."""
        jwks = await self.get_jwks(now=now)
        try:
            return verify_id_token(
                id_token, jwks=jwks, client_id=self.cfg.client_id,
                nonce=nonce, hd=self.cfg.hd, now=now)
        except OIDCError as first:
            if "kid" not in str(first):
                raise
            jwks = await self.get_jwks(force=True, now=now)
            return verify_id_token(
                id_token, jwks=jwks, client_id=self.cfg.client_id,
                nonce=nonce, hd=self.cfg.hd, now=now)


__all__ = [
    "OIDCError", "GoogleOIDCConfig", "GoogleOIDCClient",
    "GOOGLE_AUTH_URI", "GOOGLE_TOKEN_URI", "GOOGLE_JWKS_URI", "GOOGLE_ISSUERS",
    "new_pkce_verifier", "pkce_challenge", "new_state", "new_nonce",
    "decode_jwt_unverified", "verify_id_token",
]
