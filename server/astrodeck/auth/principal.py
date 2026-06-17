"""The resolved caller identity (the ``Principal``).

A ``Principal`` is what every auth provider yields and what ``require()``
checks. ``caps`` is the resolved capability set -- for a viewer-LINK this is an
EXPLICIT per-link list (NOT necessarily ``ROLES_CAP[role]``), so structural
read-only survives a role->cap drift (W2.5/W3.3). For a server-side role it is
normally ``caps_for_role(role)``.

Import-light: depends only on ``capabilities`` within the package.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .capabilities import ALL_CAPS, caps_for_role


@dataclass(frozen=True)
class Principal:
    """An authenticated (or open-default) caller.

    Frozen so a resolved principal can't be mutated mid-request to widen its
    own capabilities. ``email``/``jti`` are None under the ``none`` provider.
    """
    role: str                                  # "viewer" | "operator" | "admin"
    email: str | None = None                   # None under the "none" provider
    caps: frozenset[str] = field(default_factory=frozenset)  # resolved cap set
    jti: str | None = None                     # session/link id, for the revoke registry

    def has(self, cap: str) -> bool:
        """True iff this principal holds ``cap``. The sole authorization check."""
        return cap in self.caps

    def to_public(self) -> dict:
        """A non-secret, JSON-safe view for ``GET /api/me`` and the UI.

        ``caps`` is sorted for stable output. The signing material / jti are not
        secret-bearing here, but jti is omitted from the public shape (it is a
        session identifier, surfaced only to the revoke registry)."""
        return {
            "role": self.role,
            "email": self.email,
            "caps": sorted(self.caps),
        }


def admin_principal(email: str | None = None, jti: str | None = None) -> Principal:
    """The open-default / token-bearer principal: admin holding ALL_CAPS.

    Returned by the ``none`` provider for every caller (open default) and by the
    admin-token path. Centralized so there is exactly ONE place that mints the
    all-capabilities principal."""
    return Principal(role="admin", email=email, caps=ALL_CAPS, jti=jti)


def principal_for_role(role: str, email: str | None = None,
                       jti: str | None = None) -> Principal:
    """Build a Principal whose caps are the canonical set for ``role``.

    Unknown role -> empty caps (fail-closed). Use this for server-side roles;
    use an explicit ``caps=`` for viewer-LINKs that must pin a frozen set."""
    return Principal(role=role, email=email, caps=caps_for_role(role), jti=jti)
