"""AstroDeck auth package -- RBAC core (W2).

Public surface (import from ``astrodeck.auth``):

  Capabilities / roles:
    CAP_* strings, ALL_CAPS, DESTRUCTIVE_CAPS, VIEWER_LINK_CAPS,
    ROLES, ROLES_CAP, caps_for_role, has_capability, role_rank

  Identity:
    Principal, admin_principal, principal_for_role

  Providers:
    AuthProvider, NoneAuthProvider, TokenAdminProvider,
    SessionCookieProvider, GoogleAuthProvider, LocalAuthProvider,
    MultiAuthProvider

  Local users / passwords:
    User, UserStore, user_store, hash_password, verify_password,
    PasswordTooLongError

  Sessions:
    sign_session, verify_session, decode_session, session_secret,
    secret_is_default, SECRET_ENV_VAR

  Enforcement / DI:
    require, requires, get_principal, resolve_principal,
    set_active_provider, get_active_provider, reset_active_provider,
    set_trust_loopback, get_trust_loopback (G4 loopback-trust test mode),
    build_provider, configure_provider_from_auth

Kept import-light: no ``api.app`` / ``hub`` / ``config`` import at package
import time, so this package can be pulled into the boot assertion and tests
without an import cycle.
"""
from __future__ import annotations

from .capabilities import (ALL_CAPS, CAP_ADMIN_USERS, CAP_CONFIG_ALERTS,
                           CAP_CONFIG_BACKEND, CAP_CONFIG_SAFETY,
                           CAP_CONFIG_SITE_OPTICS, CAP_CONFIG_SOLAR_OVERRIDE,
                           CAP_CONTROL_CAPTURE, CAP_CONTROL_GUIDE,
                           CAP_CONTROL_MOUNT, CAP_CONTROL_POWER,
                           CAP_SYSTEM_UPDATE, CAP_VIEW_MEDIA, CAP_VIEW_PREVIEW,
                           CAP_VIEW_SITE_PRECISE, CAP_VIEW_STATUS,
                           CAP_VIEW_WEATHER,
                           DESTRUCTIVE_CAPS, RETIRED_CAPS, ROLES, ROLES_CAP,
                           VIEWER_LINK_CAPS, caps_for_role, has_capability,
                           role_rank)
from .deps import (_scope_is_remote, build_provider,
                   configure_provider_from_auth, get_active_provider,
                   get_principal, get_trust_loopback, require, requires,
                   reset_active_provider, resolve_principal,
                   set_active_provider, set_trust_loopback)
from .passwords import (PasswordTooLongError, hash_password, verify_password)
from .principal import Principal, admin_principal, principal_for_role
from .providers import (AuthProvider, DEFAULT_PROVIDER, GoogleAuthProvider,
                        LocalAuthProvider, MultiAuthProvider, NoneAuthProvider,
                        SessionCookieProvider, TokenAdminProvider)
from .session import (DEV_DEFAULT_SECRET, SECRET_ENV_VAR, SessionError,
                      decode_session, secret_is_default, session_secret,
                      sign_session, verify_session)
from .users import User, UserStore, user_store

__all__ = [
    # capabilities
    "CAP_VIEW_STATUS", "CAP_VIEW_PREVIEW", "CAP_VIEW_MEDIA",
    "CAP_VIEW_SITE_PRECISE", "CAP_VIEW_WEATHER",
    "CAP_CONTROL_CAPTURE", "CAP_CONTROL_MOUNT",
    "CAP_CONTROL_GUIDE", "CAP_CONTROL_POWER", "CAP_CONFIG_SAFETY",
    "CAP_CONFIG_SOLAR_OVERRIDE", "CAP_CONFIG_BACKEND", "CAP_CONFIG_SITE_OPTICS",
    "CAP_CONFIG_ALERTS", "CAP_ADMIN_USERS", "CAP_SYSTEM_UPDATE",
    "ALL_CAPS", "DESTRUCTIVE_CAPS",
    "RETIRED_CAPS", "VIEWER_LINK_CAPS", "ROLES", "ROLES_CAP",
    "caps_for_role", "has_capability", "role_rank",
    # principal
    "Principal", "admin_principal", "principal_for_role",
    # providers
    "AuthProvider", "NoneAuthProvider", "TokenAdminProvider",
    "SessionCookieProvider", "GoogleAuthProvider", "LocalAuthProvider",
    "MultiAuthProvider", "DEFAULT_PROVIDER",
    # local users / passwords
    "User", "UserStore", "user_store",
    "hash_password", "verify_password", "PasswordTooLongError",
    # session
    "sign_session", "verify_session", "decode_session", "session_secret",
    "secret_is_default", "SECRET_ENV_VAR", "DEV_DEFAULT_SECRET", "SessionError",
    # deps / enforcement
    "require", "requires", "get_principal", "resolve_principal",
    "_scope_is_remote",
    "set_active_provider", "get_active_provider", "reset_active_provider",
    "set_trust_loopback", "get_trust_loopback",
    "build_provider", "configure_provider_from_auth",
]
