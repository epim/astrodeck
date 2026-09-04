"""Pluggable auth providers (W2.3).

A provider resolves an inbound request -> ``Principal | None``. The active
provider is selected from ``AuthConfig.provider`` at app-create time. The
contract is FAIL-CLOSED: a provider returns None for an unauthenticated caller
and MUST NOT fall back to admin on failure -- the SOLE exception is
``NoneAuthProvider``, whose entire purpose is the open-default (always admin).

Import-light: depends only on ``principal``/``capabilities``/``session`` inside
the package and on ``starlette.requests.Request`` for typing. No ``api.app`` /
``hub`` / ``config`` import (the active-provider selection that reads
``AuthConfig`` lives in ``deps.py``, which is the integration seam).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from .capabilities import caps_for_role
from .principal import Principal, admin_principal
from .session import credential_fingerprint, verify_session

if TYPE_CHECKING:  # avoid importing starlette at module import time for tests
    from starlette.requests import Request


@runtime_checkable
class AuthProvider(Protocol):
    """Resolve an inbound request to a Principal (or None if unauthenticated).

    Implementations MUST be fail-closed: never return an admin principal for a
    request they could not positively authenticate. ``name`` identifies the
    provider for config selection ("none" | "google" | ...)."""

    name: str

    async def resolve(self, request: "Request") -> Principal | None:
        ...


class NoneAuthProvider:
    """The open default. Resolves EVERY caller to admin/ALL_CAPS.

    With this provider active and no ``ASTRODECK_TOKEN`` set, the server behaves
    byte-for-byte as it does today: fully open, caller treated as admin. This is
    the ONLY provider permitted to yield admin without authenticating."""

    name = "none"

    async def resolve(self, request: "Request") -> Principal | None:
        return admin_principal()


class TokenAdminProvider:
    """A shared-secret bearer that maps the configured admin token -> admin.

    Generalizes the existing ``ASTRODECK_TOKEN`` middleware into the provider
    model: a caller presenting the configured ``admin_token`` (X-Auth-Token /
    Bearer / ?token=) resolves to admin; anyone else resolves to None
    (fail-closed). The transport-level 401 is still owned by the existing
    middleware -- this provider supplies the *capability* identity.
    """

    name = "token"

    def __init__(self, admin_token: str):
        token = (admin_token or "").strip()
        # Persisted files can predate current validation.  A legacy weak token
        # fails closed instead of remaining a valid administrative credential.
        self._token = token if len(token.encode("utf-8")) >= 32 else ""

    @staticmethod
    def _present_token(request: "Request") -> str | None:
        hdr = request.headers.get("x-auth-token")
        if hdr:
            return hdr.strip()
        authz = request.headers.get("authorization")
        if authz:
            parts = authz.split(None, 1)
            if len(parts) == 2 and parts[0].lower() == "bearer":
                return parts[1].strip()
            return authz.strip()
        q = request.query_params.get("token")
        if q:
            return q.strip()
        return None

    async def resolve(self, request: "Request") -> Principal | None:
        if not self._token:
            return None  # misconfigured -> fail closed
        import hmac as _hmac
        supplied = self._present_token(request)
        if supplied and _hmac.compare_digest(supplied, self._token):
            return admin_principal()
        return None


class SessionCookieProvider:
    """Resolve the AstroDeck signed-session cookie (or Bearer) -> Principal.

    Verifies the HMAC session token (``auth.session``) and maps its ``role``
    claim to the canonical capability set. This is the home-terminated session
    path used after a Google login mints a cookie. Fail-closed: a missing,
    malformed, expired, or revoked session resolves to None.

    ``revoked_jti`` is checked here so a logged-out / admin-revoked session is
    rejected on its very next request.

    ``min_epoch`` is the session-invalidation floor (R4B-AUTH-01): a token whose
    ``epoch`` claim is below the configured ``auth.session_epoch`` was minted
    before authentication was last (re)enabled and resolves to None -- so a
    session that survived an auth-off interval in some browser can never carry
    authority into the new epoch. 0 (the default) accepts every token including
    legacy ones without the claim, keeping existing deployments byte-for-byte.
    """

    name = "session"
    COOKIE_NAME = "ad_session"

    def __init__(self, revoked_jti: frozenset[str] | None = None, *,
                 min_epoch: int = 0):
        self._revoked = frozenset(revoked_jti or ())
        self._min_epoch = int(min_epoch or 0)

    def _present_session(self, request: "Request") -> str | None:
        cookie = request.cookies.get(self.COOKIE_NAME)
        if cookie:
            return cookie
        authz = request.headers.get("authorization")
        if authz:
            parts = authz.split(None, 1)
            if len(parts) == 2 and parts[0].lower() == "bearer":
                return parts[1].strip()
        return None

    async def resolve(self, request: "Request") -> Principal | None:
        raw = self._present_session(request)
        if not raw:
            return None
        claims = verify_session(raw)
        if claims is None:
            return None
        try:
            # Read authorization state live.  A signed cookie proves who logged
            # in; it does not freeze their role, enabled state, credential
            # generation, or the continued existence of that login method.
            from ..config import config_store
            from .users import user_store

            auth = config_store.cfg().auth
            methods = set(auth.methods_effective() or ())
            live_revoked = set(auth.revoked_jti or ())
        except Exception:  # noqa: BLE001 - identity/config uncertainty denies
            return None
        jti = claims.get("jti")
        if jti is not None and (jti in self._revoked or jti in live_revoked):
            return None  # revoked -> fail closed
        try:
            floor = max(self._min_epoch, int(auth.session_epoch or 0))
            if int(claims.get("epoch", 0)) < floor:
                return None
        except (TypeError, ValueError):
            return None  # malformed epoch claim -> fail closed

        authn = claims.get("authn")
        role: str | None = None
        email: str | None = None
        if authn == "local":
            if "local" not in methods:
                return None
            subject = claims.get("sub")
            account_epoch = claims.get("account_epoch")
            if not isinstance(subject, str) or not subject:
                return None
            try:
                account_epoch = int(account_epoch)
            except (TypeError, ValueError):
                return None
            user = user_store.get(subject)
            if (user is None or not user.enabled or not user.password_hash
                    or user.session_epoch != account_epoch):
                return None
            role = user.role
            email = user.email
        elif authn == "google":
            if "google" not in methods:
                return None
            claimed_email = claims.get("email")
            if not isinstance(claimed_email, str) or not claimed_email.strip():
                return None
            email = claimed_email.strip().casefold()
            user = user_store.get_by_email(email)
            subject = claims.get("sub")
            if user is not None:
                try:
                    account_epoch = int(claims.get("account_epoch"))
                except (TypeError, ValueError):
                    return None
                if (not user.enabled or subject != user.id
                        or user.session_epoch != account_epoch):
                    return None
                role = user.role
                email = user.login_email
            else:
                # A formerly store-backed account was deleted.  It must not
                # fall through to a broad legacy allowlist/default role.
                if subject is not None:
                    return None
                allow = auth.role_allowlist or {}
                role = allow.get(email, auth.default_role)
        elif authn == "admin_token":
            configured = (auth.admin_token or "").strip()
            tag = claims.get("credential_tag")
            if not configured or not isinstance(tag, str):
                return None
            try:
                expected = credential_fingerprint(configured)
            except Exception:  # noqa: BLE001 - key material unavailable => deny
                return None
            import hmac as _hmac
            if not _hmac.compare_digest(tag, expected):
                return None
            role = "admin"
        else:
            # Upgrade boundary: legacy cookies did not bind their issuer or
            # account generation and therefore cannot be safely re-authorized.
            return None

        caps = caps_for_role(role)
        if not caps:
            return None  # unknown role holds nothing -> fail closed
        return Principal(role=role, email=email,
                         caps=caps, jti=jti if isinstance(jti, str) else None)


class LocalAuthProvider:
    """Local username+password method. Resolves an already-minted session cookie.

    Local LOGIN (``POST /auth/local``) verifies a username/password against the
    ``UserStore`` and mints the SAME ``ad_session`` cookie every other method
    uses (via ``sign_session``); there is NO separate local resolution path. So
    on a normal request this provider simply delegates to ``SessionCookieProvider``
    -- a local-minted and a google-minted cookie resolve identically. Fail-closed:
    no/invalid/revoked cookie -> None (never admin).

    The username/password verification + cookie minting live in the local login
    route (``auth/users_routes.py``); this provider only carries the per-request
    *resolution* under the ``MultiAuthProvider`` composition.
    """

    name = "local"

    def __init__(self, *, revoked_jti: frozenset[str] | None = None,
                 min_epoch: int = 0):
        self._session = SessionCookieProvider(revoked_jti=revoked_jti,
                                              min_epoch=min_epoch)

    async def resolve(self, request: "Request") -> Principal | None:
        return await self._session.resolve(request)


class MultiAuthProvider:
    """Compose the local + google methods into ONE resolution seam.

    The pinned chain (first non-None wins, else fail-closed):
      1. break-glass ``admin_token``/``ASTRODECK_TOKEN`` -> admin (ALWAYS, method
         independent) -- only present when a token is configured;
      2. the ``ad_session`` cookie via ``SessionCookieProvider`` (resolves BOTH
         local- and google-minted cookies, since both call ``sign_session``);
      3. none -> None (401) when at least one method is enabled.

    ``name`` is ``"local"`` / ``"google"`` / ``"local+google"`` (the enabled
    methods joined) -- NEVER ``"none"``, so the W3 ``remote && name=="none"``
    hard-deny interlock keeps treating a multi-method server as a real (not
    open-default) provider. When ``methods`` is empty the caller builds a
    ``NoneAuthProvider`` instead, NOT this one.
    """

    def __init__(self, methods: list[str], *,
                 admin_token: str = "",
                 role_allowlist: dict[str, str] | None = None,
                 default_role: str | None = None, hd: str = "",
                 revoked_jti: frozenset[str] | None = None,
                 min_epoch: int = 0):
        # Preserve a stable, deduped method order: local before google.
        ordered = [m for m in ("local", "google") if m in (methods or [])]
        self._methods = ordered
        self.name = "+".join(ordered) if ordered else "multi"
        self._revoked = frozenset(revoked_jti or ())
        # Step 1: break-glass token (only if configured).
        token = (admin_token or "").strip()
        self._token_provider = TokenAdminProvider(token) if token else None
        # Step 2: the shared session-cookie resolver (local + google cookies).
        # ``min_epoch`` rejects sessions minted before auth was (re)enabled.
        self._session = SessionCookieProvider(revoked_jti=self._revoked,
                                              min_epoch=min_epoch)
        # Google metadata retained so the login route / allowlist mapping can
        # read it back off the active provider (re-evaluated per request there).
        self.role_allowlist = dict(role_allowlist or {})
        self.default_role = default_role
        self.hd = hd or ""

    @property
    def methods(self) -> list[str]:
        return list(self._methods)

    async def resolve(self, request: "Request") -> Principal | None:
        # 1) break-glass token -> admin (always wins when present)
        if self._token_provider is not None:
            p = await self._token_provider.resolve(request)
            if p is not None:
                return p
        # 2) signed session cookie (local- or google-minted)
        return await self._session.resolve(request)


class GoogleAuthProvider:
    """Google OIDC provider (W2.4 Stage C). SEAM pinned now, NOT built here.

    Plan (pinned): Authorization-Code + PKCE; verify the ID token
    (iss/aud/sig/exp); require ``email_verified == true``; pin ``hd`` when set;
    map email->role via ``AuthConfig.role_allowlist`` re-evaluated EVERY
    request, else ``default_role`` or deny. On non-login requests it reads the
    AstroDeck session cookie (delegated to ``SessionCookieProvider``) and checks
    ``jti`` is not revoked.

    Until the OIDC dance + ID-token verification land (auth/routes.py), this
    provider only resolves an already-minted session cookie and is otherwise
    fail-closed (returns None) -- it NEVER yields admin on its own.
    """

    name = "google"

    def __init__(self, *, role_allowlist: dict[str, str] | None = None,
                 default_role: str | None = None, hd: str = "",
                 revoked_jti: frozenset[str] | None = None,
                 min_epoch: int = 0):
        self.role_allowlist = dict(role_allowlist or {})
        self.default_role = default_role
        self.hd = hd or ""
        self._session = SessionCookieProvider(revoked_jti=revoked_jti,
                                              min_epoch=min_epoch)

    def role_for_email(self, email: str) -> str | None:
        """email -> role via the allowlist (re-evaluated EVERY request), else
        ``default_role`` (or None = deny). Pinned mapping for the callback."""
        if email in self.role_allowlist:
            return self.role_allowlist[email]
        return self.default_role

    async def resolve(self, request: "Request") -> Principal | None:
        # On a normal request, trust only an already-minted, verified session
        # cookie. The OIDC code/token exchange happens in auth/routes.py (W2.4-C)
        # and mints that cookie; it is not performed inline here.
        return await self._session.resolve(request)


# The default active provider: open admin (byte-for-byte today's behavior).
DEFAULT_PROVIDER: AuthProvider = NoneAuthProvider()
