"""Shared site-precision redaction helpers + the WS re-auth cadence constant.

``view.site_precise`` (admin-only; EXCLUDED from viewer/operator) is the
access-control decision for the observatory's EXACT GPS fix. The serving
payloads (poll_status / summary / redacted config) are built without a
principal, so we coarsen at the seam: any principal LACKING the cap sees
lat/lon rounded to ~0.1 deg (~11 km -- enough to place the sky region for
altaz sanity, but not the operator's home), while a holder gets full
precision. This is the ONLY place the cap is enforced, so every precise-site
surface (REST status/summary/config + the WS hello frame and status pushes)
must route through here.

These helpers were originally nested closures inside ``api.app.create_app``.
They are extracted here -- module-level and IMPORT-LIGHT (only ``..auth`` for
``Principal``/``CAP_VIEW_SITE_PRECISE`` + stdlib) -- so BOTH the on-LAN /ws
handler (``api.app``) AND the relay-tunneled /ws handler
(``remote.relay_client``) share ONE implementation. ``api.app`` already imports
``remote.relay_client``, so relay_client importing the helpers back out of
``api.app`` would be circular; a neutral module breaks the cycle.
"""
from __future__ import annotations

from ..auth.capabilities import CAP_CONFIG_BACKEND, CAP_VIEW_SITE_PRECISE
from ..auth.principal import Principal

# How often the long-lived /ws socket RE-authenticates its principal (seconds).
# Auth is otherwise only checked at accept, so a revoked jti (POST
# /api/auth/revoke) or an expired session would keep streaming for the whole
# all-night run. Both the on-LAN handler and the relay-tunneled handler
# re-resolve at least this often and close 4401 the moment the principal no
# longer resolves or loses view.status. Module-level so a test can shrink it;
# ONE constant shared by both handlers.
WS_AUTH_RECHECK_S = 60.0

# ---------------------------------------------------- site-precision redaction
_SITE_LATLON_KEYS = ("latitude", "longitude")


def _coarsen_latlon(site: dict) -> None:
    """Round a site dict's lat/lon to ~0.1 deg IN PLACE (safe: every caller
    hands us a freshly-built dict, never shared/persisted state)."""
    for k in _SITE_LATLON_KEYS:
        v = site.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            site[k] = round(float(v), 1)


def _redact_site_for(payload: dict, principal: Principal | None) -> dict:
    """Coarsen precise site coords in ``payload`` unless ``principal`` holds
    ``view.site_precise``. Handles the top-level ``site`` block AND the
    duplicate copy inside an embedded ``config`` block (summary/hello frame).
    Mutates + returns ``payload`` (which is always a fresh per-call dict)."""
    if principal is not None and principal.has(CAP_VIEW_SITE_PRECISE):
        return payload  # holder: full precision, untouched
    if isinstance(payload, dict):
        site = payload.get("site")
        if isinstance(site, dict):
            _coarsen_latlon(site)
        cfg = payload.get("config")
        if isinstance(cfg, dict):
            cfg_site = cfg.get("site")
            if isinstance(cfg_site, dict):
                _coarsen_latlon(cfg_site)
    return payload


def _redact_ws_event(ev_json: dict, principal: Principal | None) -> dict:
    """Coarsen precise site coords in a broadcast WS event for a principal
    lacking ``view.site_precise``. The bus ``Event.data`` is SHARED across
    every subscriber, so we must NEVER mutate it in place -- we copy only the
    nodes we change (status carries ``data.site``; config carries
    ``data.config.site``). A holder sees the event verbatim (no copy)."""
    if principal is not None and principal.has(CAP_VIEW_SITE_PRECISE):
        return ev_json
    data = ev_json.get("data")
    if not isinstance(data, dict):
        return ev_json
    new_data: dict | None = None
    site = data.get("site")
    if isinstance(site, dict) and any(k in site for k in _SITE_LATLON_KEYS):
        new_data = dict(data)
        new_site = dict(site)
        _coarsen_latlon(new_site)
        new_data["site"] = new_site
    cfg = data.get("config")
    if isinstance(cfg, dict) and isinstance(cfg.get("site"), dict):
        base = new_data if new_data is not None else dict(data)
        new_cfg = dict(cfg)
        new_cfg_site = dict(cfg["site"])
        _coarsen_latlon(new_cfg_site)
        new_cfg["site"] = new_cfg_site
        base["config"] = new_cfg
        new_data = base
    if new_data is None:
        return ev_json  # nothing site-bearing in this event
    return {**ev_json, "data": new_data}


# ------------------------------------------------------ driver-row redaction
def _redact_drivers_for(payload: dict, principal: Principal | None) -> dict:
    """Scrub ``host``/``port``/``extra`` from every driver row in a ``GET
    /api/drivers`` payload unless ``principal`` holds ``config.backend`` (the
    same cap that can WRITE a driver's endpoint). Without this, a
    CAP_VIEW_STATUS-only (viewer) caller could read every configured driver's
    LAN host/port/DDNS straight off a read-only status surface.

    ``status``/``offers`` and the top-level ``roles`` list are untouched — the
    redaction is purely endpoint-identity, not availability.

    Copies each row (never mutates ``payload`` in place): ``describe_all()``
    rows can alias the probe TTL cache (see ``drivers._probe_configured``), so
    an in-place ``del`` here would be a second way to poison that cache."""
    if principal is not None and principal.has(CAP_CONFIG_BACKEND):
        return payload  # holder: full detail, untouched
    if not isinstance(payload, dict):
        return payload
    drivers = payload.get("drivers")
    if not isinstance(drivers, list):
        return payload
    scrubbed = []
    for row in drivers:
        if not isinstance(row, dict):
            scrubbed.append(row)
            continue
        row = dict(row)
        row.pop("host", None)
        row.pop("port", None)
        row.pop("extra", None)
        scrubbed.append(row)
    return {**payload, "drivers": scrubbed}


# ---------------------------------------------------- session frame-path redaction
def _redact_session_for(payload: dict, principal: Principal | None) -> dict:
    """Strip the filesystem ``path`` from every session-ledger frame unless the
    caller holds ``config.backend`` (sessions spec §8) — the same holder rule
    as ``_redact_drivers_for``: endpoint identity == filesystem identity.
    Copies rows; never mutates ``payload`` in place."""
    if principal is not None and principal.has(CAP_CONFIG_BACKEND):
        return payload
    if not isinstance(payload, dict):
        return payload
    frames = payload.get("frames")
    if not isinstance(frames, list):
        return payload
    scrubbed = []
    for row in frames:
        if isinstance(row, dict):
            row = dict(row)
            row.pop("path", None)
        scrubbed.append(row)
    return {**payload, "frames": scrubbed}


__all__ = [
    "WS_AUTH_RECHECK_S",
    "_redact_site_for",
    "_redact_ws_event",
    "_redact_drivers_for",
    "_redact_session_for",
    "_coarsen_latlon",
    "_SITE_LATLON_KEYS",
]
