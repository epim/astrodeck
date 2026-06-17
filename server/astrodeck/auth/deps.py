"""The PINNED ``require(capability)`` FastAPI dependency + the active-provider
resolver (W2.2 enforcement).

``require(cap)`` returns a FastAPI dependency that resolves the caller to a
``Principal`` (via the active provider) and 403s unless ``cap`` is held. Under
the ``none`` provider (the default) every caller resolves to admin, so with NO
provider and NO token configured, ``require(cap)`` always passes -- behavior is
byte-for-byte today. Enforcement only activates when an admin token or a Google
provider is explicitly configured.

The dependency ALSO RETURNS the ``Principal`` so a handler can do field-level
checks (e.g. ``POST /api/config``, ``PUT /api/site``).

Import-light: this module imports the auth package internals and FastAPI, but
NOT ``api.app`` / ``hub``. The active provider is held in a module-level slot
that ``create_app()`` sets from ``AuthConfig`` -- the config read happens at the
call site, not at import, so there is no import cycle.
"""
from __future__ import annotations

from typing import Awaitable, Callable

from fastapi import HTTPException, Request

from .capabilities import (CAP_ADMIN_USERS, CAP_CONFIG_ALERTS,
                           CAP_CONFIG_BACKEND, CAP_CONFIG_SAFETY,
                           CAP_CONFIG_SITE_OPTICS, CAP_CONTROL_CAPTURE,
                           CAP_CONTROL_GUIDE, CAP_CONTROL_MOUNT,
                           CAP_CONTROL_POWER, CAP_VIEW_MEDIA, CAP_VIEW_PREVIEW,
                           CAP_VIEW_STATUS)
from .principal import Principal
from .providers import (AuthProvider, GoogleAuthProvider, NoneAuthProvider,
                        SessionCookieProvider, TokenAdminProvider)

# ----------------------------------------------------- active provider slot
# Default = open admin. ``create_app()`` calls ``set_active_provider()`` (or
# ``configure_provider_from_auth()``) to install the configured provider. Held
# at module scope (mirrors the ``hub`` / ``config_store`` singleton pattern).
_active_provider: AuthProvider = NoneAuthProvider()


def set_active_provider(provider: AuthProvider) -> None:
    """Install the active auth provider (called once at app create)."""
    global _active_provider
    _active_provider = provider


def get_active_provider() -> AuthProvider:
    return _active_provider


def reset_active_provider() -> None:
    """Restore the open-default provider (tests + a clean app re-create)."""
    set_active_provider(NoneAuthProvider())


def build_provider(auth_cfg) -> AuthProvider:
    """Select an ``AuthProvider`` from an ``AuthConfig``-shaped object.

    Pinned selection:
      - ``provider == "google"`` -> ``GoogleAuthProvider`` (allowlist/default_role/hd)
      - ``provider == "none"`` AND an ``admin_token`` is set -> ``TokenAdminProvider``
        (the generalized ASTRODECK_TOKEN: bearer => admin)
      - otherwise -> ``NoneAuthProvider`` (open admin; today's default)

    Accepts a duck-typed object (so this module never imports ``config``); reads
    only the attributes it needs, each with a safe default."""
    provider = getattr(auth_cfg, "provider", "none") or "none"
    revoked = frozenset(getattr(auth_cfg, "revoked_jti", []) or [])
    if provider == "google":
        return GoogleAuthProvider(
            role_allowlist=getattr(auth_cfg, "role_allowlist", {}) or {},
            default_role=getattr(auth_cfg, "default_role", None),
            hd=getattr(auth_cfg, "google_hd", "") or "",
            revoked_jti=revoked,
        )
    admin_token = (getattr(auth_cfg, "admin_token", "") or "").strip()
    if admin_token:
        return TokenAdminProvider(admin_token)
    return NoneAuthProvider()


def configure_provider_from_auth(auth_cfg) -> AuthProvider:
    """Build + install the provider for ``auth_cfg`` and return it."""
    provider = build_provider(auth_cfg)
    set_active_provider(provider)
    return provider


# ----------------------------------------------------------------- resolver

async def resolve_principal(request: Request, *, remote: bool = False) -> Principal | None:
    """Resolve a request to a ``Principal`` via the active provider, or None.

    ``remote`` is the W3 relay interlock (seam): when a scope is REMOTE-flagged
    AND the active provider is ``none``, resolution HARD-DENIES (returns None,
    NOT admin) so the open-default can never leak over a relay. On the
    LAN-direct path ``remote=False``, so today's open behavior is untouched."""
    provider = _active_provider
    if remote and getattr(provider, "name", None) == "none":
        return None  # open-default must never be served remotely (W3 interlock)
    return await provider.resolve(request)


async def get_principal(request: Request) -> Principal:
    """FastAPI dependency: resolve the caller, 401 if unauthenticated.

    Standalone (no capability gate) -- used by ``GET /api/me`` and any route
    that just needs the resolved identity. FAIL-CLOSED: a None resolution is a
    401, never a default-admin."""
    principal = await resolve_principal(request)
    if principal is None:
        raise HTTPException(status_code=401, detail="authentication required")
    return principal


def require(cap: str) -> Callable[[Request], Awaitable[Principal]]:
    """Return a FastAPI dependency enforcing capability ``cap``.

    Resolves the caller -> ``Principal`` (active provider), 401s if
    unauthenticated, 403s unless ``cap`` is held, else returns the Principal so
    a handler can run field-level checks. Under the ``none`` provider every
    caller is admin, so this always passes when nothing is configured."""
    async def _dep(request: Request) -> Principal:
        principal = await resolve_principal(request)
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        if not principal.has(cap):
            # 403 = "view only / not permitted" (W2.5). 401 is reserved for
            # "who are you?"; 403 is "you, but not allowed".
            raise HTTPException(status_code=403, detail="capability not held")
        return principal
    # Stamp the cap onto the dependency callable so the boot route-assertion
    # (auth/rbac.assert_route_capabilities) can read back which capability a
    # ``Depends(require(cap))`` enforces without a separate marker.
    _dep._rbac_cap = cap  # type: ignore[attr-defined]
    return _dep


# ``requires`` alias -- the spec text uses both ``require`` and ``requires``;
# keep both names bound to the SAME factory so route declarations match either.
requires = require


__all__ = [
    "require", "requires", "get_principal", "resolve_principal",
    "set_active_provider", "get_active_provider", "reset_active_provider",
    "build_provider", "configure_provider_from_auth",
    # re-export the cap strings most routes reference, so app.py can import the
    # dependency factory and the caps from one module.
    "CAP_VIEW_STATUS", "CAP_VIEW_PREVIEW", "CAP_VIEW_MEDIA",
    "CAP_CONTROL_CAPTURE", "CAP_CONTROL_MOUNT", "CAP_CONTROL_GUIDE",
    "CAP_CONTROL_POWER", "CAP_CONFIG_SAFETY", "CAP_CONFIG_BACKEND",
    "CAP_CONFIG_SITE_OPTICS", "CAP_CONFIG_ALERTS", "CAP_ADMIN_USERS",
]
