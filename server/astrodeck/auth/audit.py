"""Redacted security audit log for authentication events (OPEN-008).

Auth endpoints (local password login, break-glass token login, first-run admin
creation) can be brute-forced or flooded when exposed, and until now a failed or
successful sign-in left no durable trace. This emits a structured line to the
dedicated ``astrodeck.audit`` logger for each security-relevant auth event,
recording WHO (username/subject), the OUTCOME, the source IP, and a short
machine reason -- and NEVER a password or token.

A deployment routes ``astrodeck.audit`` to its own sink (and the reverse-proxy
in deploy/reverse-proxy/ adds request-level logging in front of the app). We
also mirror each line to the in-app event bus so it surfaces in the live log,
at info for a success and warning for a denial.
"""
from __future__ import annotations

import logging
from typing import Optional

from ..events import bus

#: Dedicated logger so a deployment can route auth audit lines to their own sink
#: (file, syslog, SIEM) without entangling them with application logging.
audit_log = logging.getLogger("astrodeck.audit")


def client_ip(request) -> str:
    """Best-effort source IP of ``request`` for the audit line. The connecting
    peer only -- we do not trust a client-supplied ``X-Forwarded-For`` here; the
    reverse proxy in front of a public deployment is where forwarded-IP trust is
    configured."""
    try:
        client = getattr(request, "client", None)
        if client is not None and getattr(client, "host", None):
            return str(client.host)
    except Exception:  # noqa: BLE001 - audit must never break the request
        pass
    return "?"


def record(event: str, *, ok: bool, request=None,
           user: Optional[str] = None, reason: str = "") -> None:
    """Emit one audit line. ``event`` is a short verb (``login``,
    ``token_login``, ``first_run_admin``, ``revoke`` ...); ``ok`` is the
    outcome; ``user`` is the account (omit for token-only paths). No secret is
    ever passed here -- callers pass the username, never the password/token."""
    ip = client_ip(request) if request is not None else "?"
    status = "ok" if ok else "deny"
    parts = [f"auth {event} {status}", f"user={user or '-'}", f"ip={ip}"]
    if reason:
        parts.append(f"reason={reason}")
    msg = " ".join(parts)
    if ok:
        audit_log.info(msg)
        bus.log("info", msg, "audit")
    else:
        audit_log.warning(msg)
        bus.log("warning", msg, "audit")
