"""Signed session tokens (stdlib HMAC default; optional asymmetric EdDSA).

A session token is a compact, URL-safe, signed envelope carrying the caller's
``role`` + ``email`` (+ ``jti`` + ``exp``). Two signing algorithms:

* **HS256 (default, stdlib-only)** -- the home server is BOTH issuer and verifier
  of its own sessions; the symmetric secret comes from the ``ASTRODECK_SECRET``
  env var with a safe, LOUD dev default. No external crypto wheel needed.
* **EdDSA (opt-in, asymmetric)** -- so a RELAY can verify a home-issued principal
  with ONLY the PUBLIC key, never the home secret. Selected when a caller passes
  ``alg="EdDSA"`` (or a ``private_key``), or when ``AuthConfig.session_signing_alg
  == "EdDSA"`` AND a ``session_private_key`` is configured. The Ed25519 crypto
  lives in :mod:`astrodeck.auth.eddsa`, imported lazily ONLY on the EdDSA path so
  the HS256 default stays stdlib-only. The default is UNCHANGED: with no private
  key configured the home keeps signing HS256 byte-for-byte.

Format (compact, three base64url segments, no padding):
    base64url(header_json) "." base64url(payload_json) "." base64url(sig)
where header = {"alg": "HS256"|"EdDSA", "typ": "ADSESS"} and the signature covers
the exact ASCII bytes ``header_b64 + "." + payload_b64``.

**Algorithm-confusion defense (JWS):** the VERIFIER decides which alg(s) it
accepts and uses the MATCHING key material -- HS256 verifies with the HMAC
``secret``, EdDSA verifies with an Ed25519 PUBLIC key. An EdDSA-signed token is
NEVER accepted by the HMAC path (and vice-versa), and a public key is NEVER used
as an HMAC secret: a token whose header ``alg`` is not one the caller configured
to accept is rejected before any key material is touched. A relay verifies with
``decode_session(token, public_key=relay_pubkey)`` (EdDSA-only; the home secret
is never consulted).

Verification is constant-time on the HMAC signature and fail-closed on EVERY
error (bad shape, bad/mismatched alg, bad signature, expired, malformed JSON) --
a failure returns None, never a partially-trusted payload.
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
# EdDSA (asymmetric) header -- same ``typ`` so only the alg distinguishes the two
# signing paths; the compact ``h.p.s`` shape is unchanged.
_HEADER_EDDSA = {"alg": "EdDSA", "typ": "ADSESS"}
_ALG_EDDSA = "EdDSA"

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


def _current_session_epoch() -> int:
    """The active config's ``auth.session_epoch``, or 0 when unreadable.

    Lazy config read (mirrors ``_secret_dir``) so this module stays
    import-light. Every mint path stamps this into the token automatically, so
    a future login route can never forget the claim and mint a token that dies
    at the next epoch bump (R4B-AUTH-01)."""
    try:
        from ..config import config_store
        return int(getattr(config_store.cfg().auth, "session_epoch", 0) or 0)
    except Exception:  # noqa: BLE001 - unreadable config -> epoch 0 (legacy)
        return 0


def _config_session_sign() -> tuple[str, str]:
    """(session_signing_alg, session_private_key) from the active config, or
    ("HS256", "") when unreadable. Lazy read (mirrors ``_current_session_epoch``)
    so this module stays import-light. Fail-safe to HS256 -- an unreadable config
    never flips the signing path to EdDSA."""
    try:
        from ..config import config_store
        auth = config_store.cfg().auth
        return (getattr(auth, "session_signing_alg", _ALG) or _ALG,
                getattr(auth, "session_private_key", "") or "")
    except Exception:  # noqa: BLE001 - unreadable config -> HS256 default
        return _ALG, ""


def _config_session_verify() -> tuple[str, str]:
    """(session_signing_alg, session_public_key) from the active config for the
    HOME self-verify path, or ("HS256", "") when unreadable. Only used to let the
    home ALSO accept its own EdDSA-signed sessions once it has opted in (alg is
    EdDSA AND a public key is pinned); otherwise the home verifies HS256 only."""
    try:
        from ..config import config_store
        auth = config_store.cfg().auth
        return (getattr(auth, "session_signing_alg", _ALG) or _ALG,
                getattr(auth, "session_public_key", "") or "")
    except Exception:  # noqa: BLE001 - unreadable config -> HS256 default
        return _ALG, ""


def _resolve_sign_alg(alg: str | None, private_key: str | None,
                      secret: bytes | None) -> tuple[str, str | None]:
    """Decide the signing algorithm + Ed25519 private seed (or None for HS256).

    Selection (the ONLY ways EdDSA is chosen -- the default stays HS256):
      * an explicit ``alg="EdDSA"`` param, or an explicit ``private_key`` seed
        (a caller handing over a seed clearly intends asymmetric signing);
      * config: ``session_signing_alg == "EdDSA"`` AND a ``session_private_key``
        is configured (non-empty). With no key configured -- the default -- this
        resolves to HS256, so every existing deployment/test signs identically.

    An explicit symmetric ``secret`` (with no ``alg``/``private_key`` override)
    always forces HS256 -- a caller handing over an HMAC secret wants HMAC.

    Returns ``("HS256", None)`` or ``("EdDSA", <seed_b64>)``. Raises
    ``EdDSAUnavailable`` (via the caller) only when EdDSA is explicitly requested
    but no private key is available."""
    if alg is None and private_key is not None:
        alg = _ALG_EDDSA  # a private seed handed in => asymmetric intent
    if alg is None and secret is not None:
        return _ALG, None  # explicit symmetric secret => HMAC
    if alg is None:
        cfg_alg, cfg_priv = _config_session_sign()
        if cfg_alg == _ALG_EDDSA and cfg_priv:
            return _ALG_EDDSA, cfg_priv
        return _ALG, None
    if alg == _ALG_EDDSA:
        seed = private_key or _config_session_sign()[1]
        if not seed:
            from .eddsa import EdDSAUnavailable
            raise EdDSAUnavailable(
                "EdDSA session signing selected but no session_private_key "
                "is configured")
        return _ALG_EDDSA, seed
    return _ALG, None


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
                 now: float | None = None,
                 epoch: int | None = None,
                 alg: str | None = None,
                 private_key: str | None = None) -> str:
    """Mint a signed session token carrying ``role`` (+ ``email``/``jti``/exp).

    ``ttl_s`` (seconds) sets an ``exp`` claim; None => no expiry claim (the
    token never times out -- use only for long-lived dev sessions). ``secret``
    overrides the env secret (tests). Returns the compact ``h.p.s`` string.

    Signing algorithm (see :func:`_resolve_sign_alg`): HS256 by DEFAULT (the home
    signs + verifies its own sessions with the symmetric secret). EdDSA is used
    only when opted in -- ``alg="EdDSA"``, an explicit ``private_key`` seed, or a
    config that sets ``session_signing_alg=="EdDSA"`` WITH a ``session_private_key``.
    With no key configured the default is byte-for-byte identical to before.

    Every token carries an ``epoch`` claim (the active config's
    ``auth.session_epoch``, or the explicit ``epoch`` override for tests).
    Providers reject tokens whose epoch is below the configured one, so a
    session minted before authentication was (re)enabled never survives the
    transition (R4B-AUTH-01).

    FAIL-CLOSED: if the interlock is armed (a real auth method is enabled) and no
    explicit ``secret`` is supplied while the effective HMAC secret is still the
    public dev default, raise ``InsecureSessionSecretError`` rather than mint a
    forgeable admin cookie on a publicly-known key. (The interlock guards the
    HS256 path; the EdDSA path signs with a private key, not the dev secret.)"""
    resolved_alg, priv_seed = _resolve_sign_alg(alg, private_key, secret)
    if resolved_alg == _ALG:  # HS256 -- default path, byte-identical to before
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
    payload["epoch"] = _current_session_epoch() if epoch is None else int(epoch)
    payload["iat"] = int(now)
    if ttl_s is not None:
        payload["exp"] = int(now) + int(ttl_s)
    header = _HEADER_EDDSA if resolved_alg == _ALG_EDDSA else _HEADER
    header_b64 = _b64u_encode(json.dumps(header, separators=(",", ":"),
                                         sort_keys=True).encode("utf-8"))
    payload_b64 = _b64u_encode(json.dumps(payload, separators=(",", ":"),
                                          sort_keys=True).encode("utf-8"))
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    if resolved_alg == _ALG_EDDSA:
        from .eddsa import sign as _eddsa_sign
        sig = _eddsa_sign(signing_input, priv_seed)  # type: ignore[arg-type]
    else:
        sig = _sign(signing_input, secret)
    return f"{header_b64}.{payload_b64}.{sig}"


def decode_session(token: str, *, secret: bytes | None = None,
                   now: float | None = None, strict: bool = False,
                   public_key: str | None = None,
                   accept_alg: str | None = None) -> dict | None:
    """Verify ``token`` and return its claims dict, or None on ANY failure.

    Checks, in order (all fail-closed): exactly three segments; header ``alg`` is
    one the caller is configured to accept; signature match with the MATCHING key
    material; well-formed JSON payload; ``exp`` not in the past. ``strict=True``
    raises ``SessionError`` instead of returning None.

    Algorithm selection + **alg-confusion defense**. The verifier decides which
    alg(s) it accepts and uses the matching key material -- a token whose header
    ``alg`` is not accepted is rejected BEFORE any key material is used:

    * ``public_key`` given (RELAY / pubkey-only path) -> accept ONLY EdDSA,
      verified with that Ed25519 PUBLIC key. The HMAC secret is NEVER consulted,
      so the home secret is not needed and a public key can NEVER be used as an
      HMAC secret.
    * ``secret`` given -> accept ONLY HS256 with that HMAC secret (today's
      behavior for callers that pass ``secret=...``).
    * neither given (default home path) -> accept HS256 with the home secret; and
      ALSO accept the home's own EdDSA sessions IFF it opted in (config alg is
      EdDSA AND a ``session_public_key`` is pinned).

    ``accept_alg`` further narrows the accepted set to exactly that one alg
    (defense-in-depth). An EdDSA token can therefore NEVER be accepted by the
    HMAC path, nor an HS256 token by the EdDSA path."""
    def _fail(msg: str) -> None:
        if strict:
            raise SessionError(msg)
        return None

    # Build the accepted {alg: (kind, key_material)} map + the matching key.
    # ``kind`` is "hmac" (key_material=secret bytes) or "eddsa" (key_material=
    # base64 pubkey). This is the single place alg->key binding is decided, so an
    # alg can never be verified with the wrong key material.
    verifiers: dict[str, tuple[str, object]] = {}
    if public_key is not None:
        # RELAY / pubkey-only: EdDSA ONLY, verified with the public key. The HMAC
        # secret is deliberately never read on this path.
        verifiers[_ALG_EDDSA] = ("eddsa", public_key)
    elif secret is not None:
        verifiers[_ALG] = ("hmac", secret)
    else:
        # Default home path. Fail-closed interlock unchanged: while armed and the
        # secret is still the public dev default, accept NO session (an attacker
        # who knows the public key could otherwise forge any role).
        if _require_real_secret and secret_is_default():
            return _fail("insecure default session secret (method enabled)")
        verifiers[_ALG] = ("hmac", session_secret())
        cfg_alg, cfg_pub = _config_session_verify()
        if cfg_alg == _ALG_EDDSA and cfg_pub:
            verifiers[_ALG_EDDSA] = ("eddsa", cfg_pub)
    if accept_alg is not None:
        verifiers = {a: km for a, km in verifiers.items() if a == accept_alg}

    if now is None:
        now = time.time()
    if not token or not isinstance(token, str):
        return _fail("empty token")
    parts = token.split(".")
    if len(parts) != 3:
        return _fail("malformed token (expected header.payload.sig)")
    header_b64, payload_b64, sig_b64 = parts
    # Header: the alg MUST be one this verifier accepts. A token claiming alg=none,
    # an EdDSA token on the HMAC path, or an HS256 token on the pubkey path are all
    # rejected HERE -- before any key material is touched (alg-confusion defense).
    try:
        header = json.loads(_b64u_decode(header_b64))
    except (ValueError, json.JSONDecodeError):
        return _fail("bad header encoding")
    if not isinstance(header, dict):
        return _fail("unexpected alg")
    alg = header.get("alg")
    if not isinstance(alg, str) or alg not in verifiers:
        return _fail("unexpected alg")
    kind, key_material = verifiers[alg]
    # Signature: verify with the key material bound to THIS alg only.
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    if kind == "hmac":
        expected = _sign(signing_input, key_material)  # type: ignore[arg-type]
        if not hmac.compare_digest(sig_b64, expected):
            return _fail("bad signature")
    else:  # eddsa -- verify with the Ed25519 public key (fail-closed, never raises)
        from .eddsa import verify as _eddsa_verify
        if not _eddsa_verify(signing_input, sig_b64, key_material):  # type: ignore[arg-type]
            return _fail("bad signature")
    # Payload: only trusted AFTER the signature verifies.
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
                   now: float | None = None, public_key: str | None = None,
                   accept_alg: str | None = None) -> dict | None:
    """Convenience alias for ``decode_session(..., strict=False)`` (passes the
    EdDSA ``public_key`` / ``accept_alg`` through for relay/pubkey callers)."""
    return decode_session(token, secret=secret, now=now, strict=False,
                          public_key=public_key, accept_alg=accept_alg)


# EdDSA note (was TODO W3): asymmetric sessions now exist -- a relay verifies a
# home-issued EdDSA token with ONLY the public key via
# ``decode_session(token, public_key=relay_pubkey)``. The Ed25519 crypto lives in
# ``eddsa.py`` (imported lazily only on the EdDSA path), so the HS256 default here
# stays stdlib-only. ``AuthConfig.session_signing_alg`` /
# ``session_private_key`` / ``session_public_key`` / ``relay_pubkey`` /
# ``viewer_link_pubkey`` carry the key material.
