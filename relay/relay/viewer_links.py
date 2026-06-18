"""Viewer-link issuance + the revocation registry (W3.3.5).

A viewer link is a scoped, expiring URL (``GET /share/{token}``) that mints a
viewer-ONLY session for a friend with no Google account. The link's ``jti`` lives
in this server-side allow/deny registry so a LEAKED ``/share`` link is revocable
BEFORE its ``exp`` -- independent of any signing key (rotating the viewer-link
key logs EVERYONE out; revoking a ``jti`` drops just the one link). Also enforces
``max_viewers`` per link and per-link audit logging.

The token itself is minted by ``principal.mint_viewer_link_token`` with the
SEPARATE viewer-link key. This module is the stateful side: which ``jti`` are
live, how many viewers each holds, and the audit trail. The HOME ALSO carries a
``jti`` allow/deny registry and re-checks -- a revoke must propagate to both
(the relay drops the per-``ws_id`` projection; the home rejects new requests).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from .principal import PrincipalSigner, ViewerLinkSpec, mint_viewer_link_token


@dataclass
class _LinkState:
    """Live state for one issued viewer link."""

    jti: str
    spec: ViewerLinkSpec
    issued_at: float
    exp: float
    revoked: bool = False
    active_viewers: set = field(default_factory=set)   # ws_ids currently using it


class ViewerLinkRegistry:
    """Issues viewer links and tracks/revokes them by ``jti``.

    ``audit`` is an injected sink ``(event: str, fields: dict) -> None`` so the
    per-link audit trail is testable (a recording fake) and the production relay
    points it at its log/append-only store."""

    def __init__(self, signer: PrincipalSigner, *,
                 audit: Optional[Callable[[str, dict], None]] = None,
                 clock: Callable[[], float] = time.time):
        self._signer = signer
        self._links: dict[str, _LinkState] = {}
        self._clock = clock
        self._audit = audit or (lambda event, fields: None)

    # -- issuance -------------------------------------------------------------

    def issue(self, spec: ViewerLinkSpec) -> tuple[str, str]:
        """Mint + register a viewer link. Returns ``(token, jti)``. The token's
        cap set is EXPLICIT (viewer read-only + any opt-in), enforced at mint
        time by ``mint_viewer_link_token`` (forbidden caps raise)."""
        now = self._clock()
        token, jti = mint_viewer_link_token(self._signer, spec, now=now)
        self._links[jti] = _LinkState(
            jti=jti, spec=spec, issued_at=now, exp=now + spec.ttl_s,
        )
        self._audit("viewer_link.issue",
                    {"jti": jti, "label": spec.label, "ttl_s": spec.ttl_s,
                     "max_viewers": spec.max_viewers,
                     "extra_caps": list(spec.extra_caps)})
        return token, jti

    # -- revocation -----------------------------------------------------------

    def revoke(self, jti: str) -> bool:
        """Revoke a link by ``jti`` so it cannot be used again (before ``exp``).
        Returns True if a live link was revoked. The caller pushes a ``REVOKE``
        control frame down each affected tunnel so the home + the relay's
        per-``ws_id`` projections drop within the bounded window (<=30 s)."""
        link = self._links.get(jti)
        if link is None or link.revoked:
            return False
        link.revoked = True
        self._audit("viewer_link.revoke", {"jti": jti})
        return True

    def is_revoked(self, jti: str) -> bool:
        """True iff the link is unknown, revoked, or expired (fail-closed)."""
        link = self._links.get(jti)
        if link is None:
            return True
        if link.revoked:
            return True
        return self._clock() >= link.exp

    # -- admission control ----------------------------------------------------

    def admit(self, jti: str, ws_id: str) -> bool:
        """Admit a new viewer (``ws_id``) under link ``jti``, enforcing
        ``max_viewers``. Returns True if admitted. Records an audit entry either
        way. A revoked/expired link is refused."""
        link = self._links.get(jti)
        if link is None or self.is_revoked(jti):
            self._audit("viewer_link.admit_denied",
                        {"jti": jti, "ws_id": ws_id, "reason": "revoked_or_unknown"})
            return False
        if len(link.active_viewers) >= link.spec.max_viewers:
            self._audit("viewer_link.admit_denied",
                        {"jti": jti, "ws_id": ws_id, "reason": "max_viewers"})
            return False
        link.active_viewers.add(ws_id)
        self._audit("viewer_link.admit",
                    {"jti": jti, "ws_id": ws_id,
                     "active": len(link.active_viewers)})
        return True

    def release(self, jti: str, ws_id: str) -> None:
        """A viewer disconnected -> free its slot under ``jti``."""
        link = self._links.get(jti)
        if link is not None:
            link.active_viewers.discard(ws_id)
            self._audit("viewer_link.release",
                        {"jti": jti, "ws_id": ws_id,
                         "active": len(link.active_viewers)})

    def renew(self, jti: str) -> bool:
        """Renew-while-connected: extend ``exp`` by the link's TTL for a
        multi-hour imaging session. No-op if the link forbids renew or is
        revoked. Returns True if extended. (A NEW token is minted by the caller
        if it needs a fresh ``exp`` claim in the cookie; this extends the
        server-side validity window.)"""
        link = self._links.get(jti)
        if link is None or link.revoked or not link.spec.renew_while_connected:
            return False
        link.exp = self._clock() + link.spec.ttl_s
        self._audit("viewer_link.renew", {"jti": jti, "new_exp": link.exp})
        return True

    def active_count(self, jti: str) -> int:
        """Current concurrent-viewer count for a link (0 if unknown)."""
        link = self._links.get(jti)
        return len(link.active_viewers) if link else 0
