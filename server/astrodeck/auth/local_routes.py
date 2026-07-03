"""Local username+password auth + user management routes (W2.6).

A self-contained ``APIRouter`` carrying the LOCAL (offline/LAN) auth surface and
the admin user-management CRUD. It is wired into the app by being included into
the existing ``auth/routes.py`` ``router`` (so ``api/app.py`` is NEVER edited --
the apply-lane already does ``include_router(auth.routes.router)``).

Surface (all paths relative to the app root):

  Login / bootstrap (open -- reachable before any session exists):
    POST /auth/local           -> verify username+password, mint the ``ad_session``
                                  cookie (the SAME cookie google login mints), 200.
                                  Active only when "local" is an enabled method,
                                  else 404. Generic 401 on any failure (no reason
                                  leaked -- unknown user / bad password / disabled
                                  are indistinguishable).
    POST /auth/setup/local     -> FIRST-RUN: create the first ADMIN. Gated to
                                  (local enabled) AND (``local_enabled_first_run``)
                                  AND (the user store is EMPTY). Auto-closes (409)
                                  the instant any user exists. Mints a session +
                                  cookie for the new admin so the browser is
                                  logged straight in.
    GET  /api/auth/methods     -> the tiny unauthenticated "what login do I show?"
                                  signal for the UI: {methods, google_configured,
                                  first_run}. Always open (it leaks no secret).

  User management (ALL behind require(admin.users)):
    GET    /api/users          -> list ``to_public()`` users
    POST   /api/users          -> create a user
    PATCH  /api/users/{id}     -> set role / enabled / rename / email
    POST   /api/users/{id}/password  -> reset a password
    DELETE /api/users/{id}     -> delete a user

``to_public()`` is the ONLY user shape returned -- the bcrypt ``password_hash``
is never present in any response. "Last admin" protection in the store raises
``ValueError("last admin")`` which these routes map to 409. A duplicate username
(also a ``ValueError`` from the store) maps to 409; a too-long password maps to
422; an unknown role maps to 400.

NON-BREAKING: ``POST /auth/local`` and ``POST /auth/setup/local`` 404 when local
is not enabled, and the user-CRUD is admin-gated, so a default (open, no methods)
deployment exposes only the inert ``GET /api/auth/methods`` signal (which then
reports ``methods == []`` -- "no login screen").
"""
from __future__ import annotations

import secrets
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..config import config_store
from . import users as users_mod
from .capabilities import CAP_ADMIN_USERS, ROLES
from .deps import _scope_is_remote, require
from .passwords import PasswordTooLongError, PasswordTooShortError
from .session import sign_session

router = APIRouter(tags=["auth-local"])

# Every user-management route is gated by this ONE dependency: require(admin.users).
# Declared at the route decorator (``dependencies=[...]``) so the boot-time RBAC
# assertion (auth/rbac.py) reads the capability back off ``route.dependant`` and
# confirms each mutating /api/users route is gated. Under the open default the
# caller resolves to admin, so these pass with no creds; once a method is enabled
# a non-admin principal gets 403.
_ADMIN_USERS = [Depends(require(CAP_ADMIN_USERS))]

SESSION_COOKIE = "ad_session"          # MUST match SessionCookieProvider.COOKIE_NAME


# ----------------------------------------------------------- AuthConfig access

def _auth_cfg() -> Any:
    """Live read of the current ``AuthConfig`` (honors a runtime ``set_auth`` and
    the test monkeypatch of ``config_store``)."""
    return config_store.cfg().auth


def _methods(auth_cfg: Any) -> list[str]:
    """Effective enabled methods (migration-aware)."""
    try:
        return list(auth_cfg.methods_effective())
    except Exception:  # noqa: BLE001 - duck-typed fallback
        return [m for m in ("local", "google")
                if m in (getattr(auth_cfg, "methods", []) or [])]


def _local_enabled(auth_cfg: Any) -> bool:
    return "local" in _methods(auth_cfg)


def _google_configured(auth_cfg: Any) -> bool:
    return bool((getattr(auth_cfg, "google_client_id", "") or "").strip()
                and (getattr(auth_cfg, "google_client_secret", "") or "").strip()
                and (getattr(auth_cfg, "google_redirect_uri", "") or "").strip())


def _session_ttl(auth_cfg: Any) -> int:
    ttl = int(getattr(auth_cfg, "session_ttl_s", 28800) or 28800)
    return ttl if ttl > 0 else 28800


def _store() -> users_mod.UserStore:
    """The active user store. Read off the module so tests can monkeypatch the
    ``user_store`` singleton (point it at a temp ``users.json``)."""
    return users_mod.user_store


def _is_secure(request: Request) -> bool:
    """True when the request arrived over HTTPS (sets the Secure cookie flag).
    Honors ``X-Forwarded-Proto`` for a TLS-terminating proxy; False on plain HTTP
    so the cookie is still delivered on a LAN/dev rig."""
    xfp = request.headers.get("x-forwarded-proto", "")
    if xfp:
        return xfp.split(",")[0].strip().lower() == "https"
    return request.url.scheme == "https"


def _new_jti() -> str:
    return secrets.token_urlsafe(16)


def _set_session_cookie(resp: Response, token: str, *, secure: bool,
                        ttl_s: int) -> None:
    resp.set_cookie(
        key=SESSION_COOKIE, value=token, max_age=ttl_s, httponly=True,
        secure=secure, samesite="strict", path="/")


def _mint_session_response(body: dict, *, role: str, email: str | None,
                           request: Request, ttl_s: int) -> JSONResponse:
    """Mint a signed session for ``role`` and attach it as the ``ad_session``
    cookie (the SAME cookie family google login mints)."""
    jti = _new_jti()
    token = sign_session(role, email=email, jti=jti, ttl_s=ttl_s)
    resp = JSONResponse(body)
    _set_session_cookie(resp, token, secure=_is_secure(request), ttl_s=ttl_s)
    return resp


# --------------------------------------------------------- request body models

class LocalLogin(BaseModel):
    username: str
    password: str


class SetupLocal(BaseModel):
    username: str
    password: str
    email: str | None = None


class UserCreate(BaseModel):
    username: str
    password: str
    role: str = "viewer"
    email: str | None = None
    enabled: bool = True


class UserPatch(BaseModel):
    role: str | None = None
    enabled: bool | None = None
    username: str | None = None
    email: str | None = None


class PasswordReset(BaseModel):
    password: str


# --------------------------------------------------------------- POST /auth/local

@router.post("/auth/local")
async def local_login(body: LocalLogin, request: Request):
    """Local login: verify username+password, mint the session cookie.

    404 when local auth is not enabled (so a default/open or google-only
    deployment never exposes a local login). On a bad/disabled/unknown account
    the store's ``verify`` returns None and we 401 with a GENERIC message (the
    failure reason is never leaked)."""
    auth_cfg = _auth_cfg()
    if not _local_enabled(auth_cfg):
        raise HTTPException(status_code=404, detail="local auth not enabled")

    user = _store().verify(body.username, body.password)
    if user is None:
        # One generic failure: unknown user, wrong password, and disabled account
        # are indistinguishable to the caller (no account-enumeration oracle).
        raise HTTPException(status_code=401, detail="invalid username or password")

    return _mint_session_response(
        {"role": user.role, "email": user.email},
        role=user.role, email=user.email, request=request,
        ttl_s=_session_ttl(auth_cfg))


# --------------------------------------------------------- POST /auth/setup/local

@router.post("/auth/setup/local")
async def setup_local_admin(body: SetupLocal, request: Request):
    """FIRST-RUN: create the first ADMIN, then auto-close.

    Gated to ALL of: local enabled, ``local_enabled_first_run`` true, and the
    user store EMPTY. The moment ANY user exists this returns 409 (the path is
    permanently closed once seeded). The new admin is logged straight in (a
    session cookie is set) so the browser does not bounce back to a login form.

    This is anti-lockout #3: it lets you create the first admin from the LAN
    without the CLI, and it cannot be used to add a second backdoor admin
    later.

    W3 remote interlock: first-run admin bootstrap is LAN-ONLY. A relay-tunnelled
    request (scope ``astrodeck_remote``) 404s here, mirroring the ``none``-provider
    remote hard-deny -- otherwise, during the first-run window (local enabled +
    store empty), a remote attacker could seize the rig by POSTing the initial
    admin before the operator does."""
    if _scope_is_remote(request):
        # Bootstrap must never be reachable over the relay; 404 (indistinguishable
        # from "route not present" to the untrusted remote caller).
        raise HTTPException(status_code=404, detail="not found")
    auth_cfg = _auth_cfg()
    if not _local_enabled(auth_cfg):
        raise HTTPException(status_code=404, detail="local auth not enabled")
    if not bool(getattr(auth_cfg, "local_enabled_first_run", True)):
        raise HTTPException(status_code=404, detail="first-run setup disabled")
    store = _store()
    if not store.is_empty():
        # Auto-closed: a user already exists, so first-run is over.
        raise HTTPException(status_code=409, detail="setup already completed")

    try:
        user = store.create(username=body.username, password=body.password,
                            role="admin", email=body.email, enabled=True)
    except (PasswordTooLongError, PasswordTooShortError) as exc:
        # too-long OR blank/too-short password -> 422 (no empty-password admin)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ValueError as exc:
        # blank/duplicate username, unknown role (role is fixed admin here)
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return _mint_session_response(
        user.to_public() | {"role": user.role},
        role=user.role, email=user.email, request=request,
        ttl_s=_session_ttl(auth_cfg))


# --------------------------------------------------------- GET /api/auth/methods

@router.get("/api/auth/methods")
async def auth_methods():
    """The unauthenticated "what login UI should I render?" signal.

    Open (leaks no secret): the UI reads this before any session exists to decide
    whether to show the local form, the Google button, and/or the first-run
    create-admin form. ``first_run`` is true ONLY when local is enabled, the
    first-run flag is on, AND the store is still empty."""
    auth_cfg = _auth_cfg()
    methods = _methods(auth_cfg)
    local_on = "local" in methods
    first_run = bool(
        local_on
        and getattr(auth_cfg, "local_enabled_first_run", True)
        and _store().is_empty())
    return {
        "methods": methods,
        "google_configured": _google_configured(auth_cfg),
        "first_run": first_run,
    }


# ===================================================== user management (admin)
# Every route below is gated by require(admin.users): under the open default
# (no methods, no token) the caller resolves to admin so these pass; once a
# method is enabled a non-admin principal gets 403.

@router.get("/api/users", dependencies=_ADMIN_USERS)
async def list_users():
    """List all users (``to_public()`` shape -- no password_hash)."""
    return {"users": [u.to_public() for u in _store().list()]}


@router.post("/api/users", status_code=201, dependencies=_ADMIN_USERS)
async def create_user(body: UserCreate):
    """Create a user. 409 on a duplicate username, 422 on a too-long password,
    400 on an unknown role."""
    if body.role not in ROLES:
        raise HTTPException(status_code=400, detail=f"unknown role: {body.role!r}")
    try:
        user = _store().create(username=body.username, password=body.password,
                               role=body.role, email=body.email,
                               enabled=body.enabled)
    except (PasswordTooLongError, PasswordTooShortError) as exc:
        # too-long OR blank/too-short password -> 422 (no empty-password user)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ValueError as exc:
        # duplicate / blank username, unknown role
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return user.to_public()


@router.patch("/api/users/{user_id}", dependencies=_ADMIN_USERS)
async def patch_user(user_id: str, body: UserPatch):
    """Update a user's role / enabled / username / email.

    Applies only the provided fields. "Last admin" protection (demote or disable
    the last enabled admin) -> 409; a duplicate username -> 409; an unknown role
    -> 400; an unknown user -> 404."""
    store = _store()
    if store.get(user_id) is None:
        raise HTTPException(status_code=404, detail="user not found")
    try:
        if body.role is not None:
            if body.role not in ROLES:
                raise HTTPException(status_code=400,
                                    detail=f"unknown role: {body.role!r}")
            store.set_role(user_id, body.role)
        if body.username is not None:
            store.rename(user_id, body.username)
        if body.enabled is not None:
            store.set_enabled(user_id, body.enabled)
        if body.email is not None:
            user = store.get(user_id)
            user.email = body.email  # email is non-secret metadata; no store helper
            store._save()  # noqa: SLF001 - persist the email edit through the store
    except HTTPException:
        raise
    except ValueError as exc:
        msg = str(exc)
        code = 409 if ("last admin" in msg or "exists" in msg) else 400
        raise HTTPException(status_code=code, detail=msg) from exc
    return store.get(user_id).to_public()


@router.post("/api/users/{user_id}/password", dependencies=_ADMIN_USERS)
async def reset_password(user_id: str, body: PasswordReset):
    """Reset a user's password. 404 unknown user, 422 too-long password."""
    store = _store()
    if store.get(user_id) is None:
        raise HTTPException(status_code=404, detail="user not found")
    try:
        store.set_password(user_id, body.password)
    except (PasswordTooLongError, PasswordTooShortError) as exc:
        # too-long OR blank/too-short password -> 422
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return store.get(user_id).to_public()


@router.delete("/api/users/{user_id}", dependencies=_ADMIN_USERS)
async def delete_user(user_id: str):
    """Delete a user. 404 unknown user, 409 deleting the last enabled admin."""
    store = _store()
    if store.get(user_id) is None:
        raise HTTPException(status_code=404, detail="user not found")
    try:
        store.delete(user_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True}


__all__ = ["router", "SESSION_COOKIE"]
