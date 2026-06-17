"""Signed session tokens (stdlib-only, no external crypto dependency).

A session token is a compact, URL-safe, HMAC-signed envelope carrying the
caller's ``role`` + ``email`` (+ ``jti`` + ``exp``). The home server is the
issuer AND the verifier of its own sessions; the secret comes from the
``ASTRODECK_SECRET`` environment variable with a safe, LOUD dev default.

Why HMAC and not EdDSA here: the assigned scope is the local-home session
(home signs, home verifies -- a symmetric secret is correct and needs no
third-party crypto wheel, which is not installed in the venv). The spec's
asymmetric ``EdDSA`` session (so a relay can verify WITHOUT the home secret) is
a W3 seam -- see the TODO at the bottom and ``AuthConfig.session_*_key`` /
``relay_pubkey`` in config.py. The token format is a JWS-shaped
``header.payload.sig`` so swapping the alg later is non-breaking.

Format (compact, three base64url segments, no padding):
    base64url(header_json) "." base64url(payload_json) "." base64url(hmac_sha256)
where header = {"alg": "HS256", "typ": "ADSESS"} and the MAC covers the exact
ASCII bytes ``header_b64 + "." + payload_b64``.

Verification is constant-time on the signature and fail-closed on EVERY error
(bad shape, bad alg, bad MAC, expired, malformed JSON) -- a failure returns
None, never a partially-trusted payload.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time

# Env var holding the session-signing secret. Read LIVE (per-process, tests
# monkeypatch it) so a secret set before launch is honored.
SECRET_ENV_VAR = "ASTRODECK_SECRET"

# A safe, OBVIOUSLY-non-production default so local dev/tests work out of the
# box. It is intentionally a fixed sentinel: ``secret_is_default()`` lets the
# boot path emit a loud warning if a real session provider is configured while
# this default is still in force (mirrors app.py's 0.0.0.0-without-token warn).
DEV_DEFAULT_SECRET = "astrodeck-dev-insecure-secret-change-me"  # noqa: S105 (intentional dev default)

# Filename of the auto-generated persisted secret (under ``config.CONFIG_DIR``).
# When a real auth method is enabled and ``ASTRODECK_SECRET`` is NOT set, the
# boot/enable path generates a random secret here so sessions are never signed
# with the public dev sentinel (see ``ensure_real_secret``). Read live so a
# freshly-written file is honored without a restart.
SECRET_FILE_NAME = "session_secret"  # noqa: S105 (a path, not a secret value)

_HEADER = {"alg": "HS256", "typ": "ADSESS"}
_ALG = "HS256"

# Fail-closed interlock (critical fix): when a real auth method is enabled the
# boot/enable path ARMS this. While armed AND the effective secret is still the
# public dev default, ``sign_session`` raises and ``decode_session`` refuses --
# so an operator who enables local/google auth without ``ASTRODECK_SECRET`` (and
# whose secret somehow did not get persisted) cannot silently run on the public
# dev key and have an attacker mint a valid admin cookie.
_require_real_secret = False


class SessionError(ValueError):
    """Raised only by the strict ``decode_session(..., strict=True)`` path; the
    default ``verify_session`` swallows everything and returns None."""


class InsecureSessionSecretError(RuntimeError):
    """Raised by ``sign_session`` (and surfaced by the boot guard) when a real
    auth method is enabled but the signing secret is still the public dev
    default. Minting a session on the public key would be a full auth bypass."""


def _secret_dir():
    """The directory the auto-persisted secret lives in: the ACTIVE config
    store's directory (so a test pointing ``config_store`` at a temp path keeps
    the secret out of the real ``server/config``), falling back to the static
    ``CONFIG_DIR``. Resolved lazily so this module stays import-light."""
    try:
        from ..config import CONFIG_DIR, config_store
        store_path = getattr(config_store, "_path", None)
        if store_path is not None:
            return store_path.parent
        return CONFIG_DIR
    except Exception:  # noqa: BLE001 - any import/attr issue -> no persisted secret
        return None


def _persisted_secret() -> str:
    """The auto-generated secret persisted next to the active config, or "".

    Read live + lazily so this module stays import-light and a file written at
    first-enable is honored at once."""
    directory = _secret_dir()
    if directory is None:
        return ""
    try:
        raw = (directory / SECRET_FILE_NAME).read_text(encoding="utf-8").strip()
        return raw
    except (OSError, ValueError):
        return ""


def session_secret() -> bytes:
    """The active signing secret as bytes, resolved live.

    Resolution order: ``ASTRODECK_SECRET`` env var -> the auto-persisted
    ``CONFIG_DIR/session_secret`` file -> the LOUD dev default. The persisted
    file is written by ``ensure_real_secret`` when a method is enabled without
    the env var, so a method-enabled rig never signs on the dev sentinel. Local
    open-default use (no method) keeps working out of the box on the default."""
    raw = (os.environ.get(SECRET_ENV_VAR) or "").strip()
    if not raw:
        raw = _persisted_secret()
    if not raw:
        raw = DEV_DEFAULT_SECRET
    return raw.encode("utf-8")


def secret_is_default() -> bool:
    """True when NO real signing secret is configured (env var unset/blank AND
    no auto-persisted secret), so sessions would be signed with the PUBLIC dev
    sentinel. The boot/enable path treats this + an enabled method as a fatal
    misconfiguration (see ``ensure_real_secret`` / the boot guard)."""
    if (os.environ.get(SECRET_ENV_VAR) or "").strip():
        return False
    return _persisted_secret() == ""


def ensure_real_secret() -> bool:
    """Guarantee a NON-default signing secret exists, generating one if needed.

    If ``ASTRODECK_SECRET`` is set, use it (return True). Otherwise, if a secret
    has already been persisted, use it (return True). Otherwise generate a random
    256-bit secret and persist it atomically to ``CONFIG_DIR/session_secret``
    (return True). Returns False only if it could not establish a real secret
    (e.g. the file could not be written) -- in which case the caller must treat
    the rig as misconfigured and arm the fail-closed guard.

    Called by the boot/enable path whenever a real auth method is enabled, so
    enabling local/google auth without the env var can never silently fall back
    to the public dev key."""
    if not secret_is_default():
        return True
    import secrets as _secrets

    from ..persist import ensure_dir
    directory = _secret_dir()
    if directory is None:
        return False
    new_secret = _secrets.token_urlsafe(32)
    try:
        ensure_dir(directory)
        path = directory / SECRET_FILE_NAME
        tmp = path.with_suffix(".tmp")
        tmp.write_text(new_secret, encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        return False
    return not secret_is_default()


def set_require_real_secret(value: bool) -> None:
    """Arm/disarm the fail-closed interlock. ARMED => ``sign_session`` raises and
    ``decode_session`` refuses while the secret is still the public dev default."""
    global _require_real_secret
    _require_real_secret = bool(value)


def require_real_secret() -> bool:
    """True iff the fail-closed interlock is armed (a real method is enabled)."""
    return _require_real_secret


# --------------------------------------------------------------- base64url glue

def _b64u_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64u_decode(seg: str) -> bytes:
    pad = "=" * (-len(seg) % 4)
    return base64.urlsafe_b64decode(seg + pad)


def _sign(signing_input: bytes, secret: bytes) -> str:
    mac = hmac.new(secret, signing_input, hashlib.sha256).digest()
    return _b64u_encode(mac)


# --------------------------------------------------------------------- public

def sign_session(role: str, email: str | None = None, *,
                 jti: str | None = None, ttl_s: int | None = None,
                 secret: bytes | None = None,
                 now: float | None = None) -> str:
    """Mint a signed session token carrying ``role`` (+ ``email``/``jti``/exp).

    ``ttl_s`` (seconds) sets an ``exp`` claim; None => no expiry claim (the
    token never times out -- use only for long-lived dev sessions). ``secret``
    overrides the env secret (tests). Returns the compact ``h.p.s`` string.

    FAIL-CLOSED: if the interlock is armed (a real auth method is enabled) and no
    explicit ``secret`` is supplied while the effective secret is still the
    public dev default, raise ``InsecureSessionSecretError`` rather than mint a
    forgeable admin cookie on a publicly-known key."""
    if secret is None:
        if _require_real_secret and secret_is_default():
            raise InsecureSessionSecretError(
                "refusing to sign a session on the public dev secret; set "
                f"{SECRET_ENV_VAR} (a real method is enabled)")
        secret = session_secret()
    if now is None:
        now = time.time()
    payload: dict[str, object] = {"role": role}
    if email is not None:
        payload["email"] = email
    if jti is not None:
        payload["jti"] = jti
    payload["iat"] = int(now)
    if ttl_s is not None:
        payload["exp"] = int(now) + int(ttl_s)
    header_b64 = _b64u_encode(json.dumps(_HEADER, separators=(",", ":"),
                                         sort_keys=True).encode("utf-8"))
    payload_b64 = _b64u_encode(json.dumps(payload, separators=(",", ":"),
                                          sort_keys=True).encode("utf-8"))
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    sig = _sign(signing_input, secret)
    return f"{header_b64}.{payload_b64}.{sig}"


def decode_session(token: str, *, secret: bytes | None = None,
                   now: float | None = None, strict: bool = False) -> dict | None:
    """Verify ``token`` and return its claims dict, or None on ANY failure.

    Checks, in order (all fail-closed): exactly three segments; header alg ==
    HS256; constant-time HMAC match; well-formed JSON payload; ``exp`` not in
    the past. ``strict=True`` raises ``SessionError`` instead of returning None
    (used by callers that want to log the precise reason)."""
    def _fail(msg: str) -> None:
        if strict:
            raise SessionError(msg)
        return None

    if secret is None:
        # FAIL-CLOSED: while the interlock is armed (a real method is enabled)
        # and the secret is still the public dev default, accept NO session --
        # an attacker who knows the public key could otherwise forge any role.
        if _require_real_secret and secret_is_default():
            return _fail("insecure default session secret (method enabled)")
        secret = session_secret()
    if now is None:
        now = time.time()
    if not token or not isinstance(token, str):
        return _fail("empty token")
    parts = token.split(".")
    if len(parts) != 3:
        return _fail("malformed token (expected header.payload.sig)")
    header_b64, payload_b64, sig_b64 = parts
    # Header: must be our alg/typ. A token claiming alg=none is rejected here.
    try:
        header = json.loads(_b64u_decode(header_b64))
    except (ValueError, json.JSONDecodeError):
        return _fail("bad header encoding")
    if not isinstance(header, dict) or header.get("alg") != _ALG:
        return _fail("unexpected alg")
    # Signature: constant-time compare over the EXACT signing input bytes.
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    expected = _sign(signing_input, secret)
    if not hmac.compare_digest(sig_b64, expected):
        return _fail("bad signature")
    # Payload: only trusted AFTER the MAC verifies.
    try:
        payload = json.loads(_b64u_decode(payload_b64))
    except (ValueError, json.JSONDecodeError):
        return _fail("bad payload encoding")
    if not isinstance(payload, dict):
        return _fail("payload not an object")
    exp = payload.get("exp")
    if exp is not None:
        try:
            if float(exp) < float(now):
                return _fail("token expired")
        except (TypeError, ValueError):
            return _fail("bad exp claim")
    return payload


def verify_session(token: str, *, secret: bytes | None = None,
                   now: float | None = None) -> dict | None:
    """Convenience alias for ``decode_session(..., strict=False)``."""
    return decode_session(token, secret=secret, now=now, strict=False)


# TODO(W3): asymmetric EdDSA sessions so a relay can verify a home-issued
# principal WITHOUT holding the home secret. AuthConfig already carries
# ``session_signing_alg`` / ``session_private_key`` / ``session_public_key`` /
# ``relay_pubkey`` / ``viewer_link_pubkey`` for that. When a crypto backend is
# available, branch on the header ``alg`` here (the token format is already
# JWS-shaped, so adding EdDSA is non-breaking).
