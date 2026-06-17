"""Google OIDC login routes (W2.4 Stage C) -- a self-contained ``APIRouter``.

This router owns the home-terminated OIDC login dance and the AstroDeck session
cookie lifecycle. The apply-lane just does ``app.include_router(auth_router)``
in ``create_app()`` -- this module NEVER edits ``api/app.py``.

Routes:
  GET  /auth/login            -> start Authorization-Code + PKCE, set the signed
                                 pre-auth cookie (state/nonce/code_verifier),
                                 302 to Google's consent screen.
  GET  /auth/google/callback  -> verify state + PKCE, exchange the code, verify
                                 the ID token (sig/iss/aud/exp/nonce/email_
                                 verified/hd), map email->role via AuthConfig,
                                 mint + set the CORE session cookie, 302 home.
  POST /auth/logout           -> clear the session cookie AND append its jti to
                                 the append-only ``revoked_jti`` registry.
  GET  /auth/me               -> the resolved Principal (fail-closed 401).

The provider is INACTIVE unless Google is an enabled auth method (i.e. "google"
is in ``AuthConfig.methods_effective()``; the legacy ``provider == "google"`` is
folded in by the migration validator): /auth/login and the callback 404/400 when
Google is not enabled, so a default (open) deployment is byte-for-byte unchanged
and these endpoints are inert.

Cookies:
  - The PRE-AUTH cookie (``ad_oauth``) carries the per-login secrets, signed with
    the home HMAC secret (the same key family as the session). It is short-lived
    (10 min), HttpOnly, SameSite=Lax (the redirect back from Google is a
    top-level GET, so Lax delivers it), and Secure off only on plain-HTTP dev.
  - The SESSION cookie (``ad_session``, the CORE ``SessionCookieProvider`` name)
    carries the signed ``{role,email,jti,exp}`` token. HttpOnly, SameSite=Strict
    (it is never needed on a cross-site top-level navigation), Secure on HTTPS.

CSRF: the session cookie is SameSite=Strict so it is not sent on cross-site
requests at all; mutations also accept the session as a ``Bearer`` header (the
SPA attaches it), and the callback is hardened by the ``state`` round-trip.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse

from ..config import config_store
from .deps import configure_provider_from_auth, resolve_principal
from .google import (GoogleOIDCClient, GoogleOIDCConfig, OIDCError, new_nonce,
                     new_pkce_verifier, new_state, pkce_challenge)
from .session import session_secret, sign_session, verify_session

router = APIRouter(tags=["auth"])

# Cookie names. ``ad_session`` MUST match ``SessionCookieProvider.COOKIE_NAME``
# so the CORE provider can resolve what this router mints.
SESSION_COOKIE = "ad_session"
PREAUTH_COOKIE = "ad_oauth"

# The signed pre-auth payload lives only for the duration of the consent dance.
_PREAUTH_TTL_S = 600
# Default session lifetime (8h -- one observing night).
_SESSION_TTL_S = 8 * 3600

# Where to land the browser after a successful login / after logout. Kept to the
# SPA root; the app re-reads /auth/me on load.
_POST_LOGIN_PATH = "/"


# --------------------------------------------------------- AuthConfig access

def _auth_cfg() -> Any:
    """Live read of the current ``AuthConfig`` (so a runtime ``set_auth`` and the
    test monkeypatch of ``config_store`` are both honored)."""
    return config_store.cfg().auth


def _google_enabled(auth_cfg: Any) -> bool:
    """True when Google is an enabled auth METHOD (methods-aware).

    The UI enables Google by writing ``methods=["google"]`` and never touches the
    legacy ``provider`` field, so we read the effective methods first. ``auth_cfg``
    is typed ``Any`` here, so we duck-type ``methods_effective`` (a callable on the
    real ``AuthConfig``). The migration validator already folds a legacy
    ``provider == "google"`` into ``methods``, so ``methods_effective`` covers both
    shapes; the ``provider`` fallback is kept only for old-shaped objects that
    lack ``methods_effective`` entirely (back-compat safety)."""
    eff = getattr(auth_cfg, "methods_effective", None)
    if callable(eff):
        return "google" in eff()
    return getattr(auth_cfg, "provider", "none") == "google"


def _oidc_client(auth_cfg: Any) -> GoogleOIDCClient:
    return GoogleOIDCClient(GoogleOIDCConfig.from_auth(auth_cfg))


def _is_secure(request: Request) -> bool:
    """True when the request arrived over HTTPS (sets the Secure cookie flag).

    Honors ``X-Forwarded-Proto`` so a TLS-terminating reverse proxy is handled,
    falling back to the request scheme. On plain-HTTP LAN dev this is False so
    the cookie is still delivered."""
    xfp = request.headers.get("x-forwarded-proto", "")
    if xfp:
        return xfp.split(",")[0].strip().lower() == "https"
    return request.url.scheme == "https"


# ---------------------------------------------------- pre-auth cookie (signed)
# A compact ``b64url(json).b64url(hmac)`` envelope binding state/nonce/verifier
# to this browser. Signed with the home secret so a forged callback can't pass
# its own state. Distinct from the session token (different payload + a domain
# separation tag in the MAC) so the two can never be confused.

_PREAUTH_TAG = b"adoauth.v1"


def _sign_preauth(payload: dict, *, now: float | None = None) -> str:
    if now is None:
        now = time.time()
    body = dict(payload)
    body["exp"] = int(now) + _PREAUTH_TTL_S
    raw = json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8")
    p_b64 = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    mac = hmac.new(session_secret(), _PREAUTH_TAG + p_b64.encode("ascii"),
                   hashlib.sha256).digest()
    s_b64 = base64.urlsafe_b64encode(mac).rstrip(b"=").decode("ascii")
    return f"{p_b64}.{s_b64}"


def _verify_preauth(token: str, *, now: float | None = None) -> dict | None:
    if not token or not isinstance(token, str) or token.count(".") != 1:
        return None
    if now is None:
        now = time.time()
    p_b64, s_b64 = token.split(".")
    expected = hmac.new(session_secret(), _PREAUTH_TAG + p_b64.encode("ascii"),
                        hashlib.sha256).digest()
    exp_b64 = base64.urlsafe_b64encode(expected).rstrip(b"=").decode("ascii")
    if not hmac.compare_digest(s_b64, exp_b64):
        return None
    try:
        pad = "=" * (-len(p_b64) % 4)
        body = json.loads(base64.urlsafe_b64decode(p_b64 + pad))
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(body, dict):
        return None
    exp = body.get("exp")
    try:
        if exp is None or float(exp) < float(now):
            return None
    except (TypeError, ValueError):
        return None
    return body


def _set_cookie(resp: Response, name: str, value: str, *, max_age: int,
                secure: bool, samesite: str) -> None:
    resp.set_cookie(
        key=name, value=value, max_age=max_age, httponly=True,
        secure=secure, samesite=samesite, path="/")


def _clear_cookie(resp: Response, name: str, *, secure: bool,
                  samesite: str) -> None:
    resp.delete_cookie(key=name, path="/", httponly=True, secure=secure,
                       samesite=samesite)


# ------------------------------------------------------------------- /auth/login

@router.get("/auth/login")
async def auth_login(request: Request):
    """Start the OIDC login: set the signed pre-auth cookie + 302 to Google."""
    auth_cfg = _auth_cfg()
    if not _google_enabled(auth_cfg):
        raise HTTPException(status_code=404, detail="google auth not enabled")
    cfg = GoogleOIDCConfig.from_auth(auth_cfg)
    if not cfg.configured:
        raise HTTPException(status_code=503,
                            detail="google auth not fully configured")

    state = new_state()
    nonce = new_nonce()
    verifier = new_pkce_verifier()
    challenge = pkce_challenge(verifier)

    client = GoogleOIDCClient(cfg)
    url = client.build_auth_url(state=state, nonce=nonce,
                                code_challenge=challenge)

    resp = RedirectResponse(url=url, status_code=302)
    preauth = _sign_preauth({"state": state, "nonce": nonce, "cv": verifier})
    # SameSite=Lax: the callback is a top-level GET navigation back from Google,
    # so Lax delivers the cookie; Strict would drop it on that cross-site hop.
    _set_cookie(resp, PREAUTH_COOKIE, preauth, max_age=_PREAUTH_TTL_S,
                secure=_is_secure(request), samesite="lax")
    return resp


# -------------------------------------------------------- /auth/google/callback

@router.get("/auth/google/callback")
async def auth_callback(request: Request, code: str = "", state: str = "",
                        error: str = ""):
    """Verify state + PKCE, exchange the code, verify the ID token, mint a
    session cookie. Any failure is fail-closed (4xx) and mints NOTHING."""
    auth_cfg = _auth_cfg()
    if not _google_enabled(auth_cfg):
        raise HTTPException(status_code=404, detail="google auth not enabled")
    if error:
        raise HTTPException(status_code=401, detail=f"google error: {error}")
    if not code or not state:
        raise HTTPException(status_code=400, detail="missing code/state")

    pre = _verify_preauth(request.cookies.get(PREAUTH_COOKIE, ""))
    if pre is None:
        raise HTTPException(status_code=400, detail="missing/expired login state")
    # CSRF: the callback state MUST match the one bound to this browser's cookie.
    if not hmac.compare_digest(str(state), str(pre.get("state", ""))):
        raise HTTPException(status_code=400, detail="state mismatch")

    cfg = GoogleOIDCConfig.from_auth(auth_cfg)
    if not cfg.configured:
        raise HTTPException(status_code=503,
                            detail="google auth not fully configured")
    client = GoogleOIDCClient(cfg)
    try:
        tokens = await client.exchange_code(code=code, code_verifier=pre["cv"])
        claims = await client.verify(tokens["id_token"], nonce=pre.get("nonce"))
    except OIDCError as exc:
        # Do not leak token internals to the browser; 401 + a generic detail.
        raise HTTPException(status_code=401,
                            detail="google id_token verification failed") from exc

    email = claims.get("email")
    # Map email -> role via the live allowlist (re-evaluated EVERY request), else
    # the configured default_role, else DENY. Mirrors GoogleAuthProvider.
    role = _role_for_email(auth_cfg, email)
    if role is None:
        # Authenticated by Google but not authorized for any role here.
        raise HTTPException(status_code=403, detail="email not authorized")

    jti = _new_jti()
    token = sign_session(role, email=email, jti=jti, ttl_s=_SESSION_TTL_S)

    resp = RedirectResponse(url=_POST_LOGIN_PATH, status_code=302)
    secure = _is_secure(request)
    # Session cookie: SameSite=Strict (never needed cross-site) + HttpOnly.
    _set_cookie(resp, SESSION_COOKIE, token, max_age=_SESSION_TTL_S,
                secure=secure, samesite="strict")
    # The pre-auth cookie has done its job; clear it.
    _clear_cookie(resp, PREAUTH_COOKIE, secure=secure, samesite="lax")
    return resp


def _role_for_email(auth_cfg: Any, email: str | None) -> str | None:
    """email -> role via ``role_allowlist`` then ``default_role`` (None = deny).

    Re-reads the live ``auth_cfg`` so a removed allowlist entry denies the next
    login immediately."""
    if not email:
        return None
    allow = getattr(auth_cfg, "role_allowlist", {}) or {}
    if email in allow:
        return allow[email]
    return getattr(auth_cfg, "default_role", None)


def _new_jti() -> str:
    import secrets
    return secrets.token_urlsafe(16)


# ------------------------------------------------------------------ /auth/logout

@router.post("/auth/logout")
async def auth_logout(request: Request):
    """Clear the session cookie AND append its jti to the revoke registry.

    Appending the jti means the cookie is dead on its very next use even if the
    browser kept a copy. Requires no ``admin.users`` -- you may always revoke
    your OWN session. A logout with no/invalid session still clears the cookie
    (idempotent) and is a no-op on the registry."""
    secure = _is_secure(request)
    resp = JSONResponse({"ok": True})
    _clear_cookie(resp, SESSION_COOKIE, secure=secure, samesite="strict")

    raw = _present_session(request)
    if raw:
        claims = verify_session(raw)
        jti = (claims or {}).get("jti")
        if isinstance(jti, str) and jti:
            _revoke_jti(jti)
    return resp


def _present_session(request: Request) -> str | None:
    cookie = request.cookies.get(SESSION_COOKIE)
    if cookie:
        return cookie
    authz = request.headers.get("authorization")
    if authz:
        parts = authz.split(None, 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return parts[1].strip()
    return None


def _revoke_jti(jti: str) -> None:
    """Append ``jti`` to the append-only ``revoked_jti`` registry (idempotent).

    Goes through ``ConfigStore.set_auth`` so the append-only invariant +
    version bump are enforced centrally. A duplicate jti is a no-op.

    HIGH fix: the active provider snapshots ``revoked_jti`` into a frozenset at
    construction, so persisting alone does NOT kill the cookie -- the live
    provider must be REBUILT for the new jti to enter its deny set. We therefore
    re-install the provider from the freshly-persisted config here (mirroring the
    admin ``/api/auth/revoke`` route), so a user's own logout kills their cookie
    on its very next request instead of waiting for an unrelated provider rebuild
    or a restart."""
    cur = config_store.cfg().auth
    if jti in (cur.revoked_jti or []):
        return
    updated = cur.model_copy(update={"revoked_jti": [*cur.revoked_jti, jti]})
    config_store.set_auth(updated)
    # Rebuild the running provider so the new jti is in its live deny set NOW.
    configure_provider_from_auth(config_store.cfg().auth)


# ---------------------------------------------------------------------- /auth/me

@router.get("/auth/me")
async def auth_me(request: Request):
    """The resolved Principal for the caller. FAIL-CLOSED: an unauthenticated
    caller is 401 (NEVER default-admin) under a real provider; under the open
    ``none`` provider the active provider resolves admin as usual."""
    principal = await resolve_principal(request)
    if principal is None:
        raise HTTPException(status_code=401, detail="authentication required")
    return principal.to_public()


# --------------------------------------------------- local auth + user mgmt
# The LOCAL (offline/LAN) login, first-run setup, and user-management CRUD live
# in ``local_routes.py``. They are folded into THIS router so the apply-lane's
# single ``include_router(auth.routes.router)`` in ``api/app.py`` picks them up
# too -- ``api/app.py`` is never edited. Guarded so a partial checkout that
# lacks ``local_routes`` still imports the google router cleanly.
try:
    from .local_routes import router as _local_router

    router.include_router(_local_router)
except Exception:  # noqa: BLE001 - local-auth surface optional / seam reserved
    pass


__all__ = [
    "router", "SESSION_COOKIE", "PREAUTH_COOKIE",
]
