"""Shared site-precision redaction helpers + the WS re-auth cadence constant.

``view.site_precise`` (admin-only; EXCLUDED from viewer/operator) is the
access-control decision for the observatory's EXACT GPS fix. The serving
payloads (poll_status / summary / redacted config) are built without a
principal, so we STRIP at the seam: any principal LACKING the cap has the
four precise-site keys (name, latitude, longitude, elevation_m) REMOVED
(absent, not nulled) while is_default/horizon_min_deg are retained (the UI
needs both and neither reveals location), and a holder gets the full block.
This is the ONLY place the cap is enforced, so every precise-site surface
(REST status/summary/config + the WS hello frame and status pushes) must
route through here.

``view.weather`` (operator + admin; 2026-07-17 decisions wave I2) is the
SEPARATE access-control decision for the WS ``weather`` event -- split off
``view.site_precise`` so an operator (who never holds view.site_precise) can
still see the forecast/Sky Conditions/radar surface. This module enforces
BOTH caps independently: a weather event is gated on view.weather, every
other event's precise-site keys are gated on view.site_precise.

These helpers were originally nested closures inside ``api.app.create_app``.
They are extracted here -- module-level and IMPORT-LIGHT (only ``..auth`` for
``Principal``/``CAP_VIEW_SITE_PRECISE``/``CAP_VIEW_WEATHER`` + stdlib) -- so
BOTH the on-LAN /ws handler (``api.app``) AND the relay-tunneled /ws handler
(``remote.relay_client``) share ONE implementation. ``api.app`` already imports
``remote.relay_client``, so relay_client importing the helpers back out of
``api.app`` would be circular; a neutral module breaks the cycle.
"""
from __future__ import annotations

from ..auth.capabilities import (CAP_CONFIG_BACKEND, CAP_VIEW_SITE_DERIVED,
                                 CAP_VIEW_SITE_PRECISE, CAP_VIEW_WEATHER)
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
# The exact set of precise-site keys removed for a principal lacking
# view.site_precise (spec §2/§8). is_default + horizon_min_deg are NOT here --
# they are retained (default-site nudge + alt-limit display; neither is a
# geolocator).
_SITE_STRIP_KEYS = ("name", "latitude", "longitude", "elevation_m")

# ---------------------------------------------- values DERIVED from the site
# Stripping the four keys above is necessary and NOT sufficient, and that was
# the hole: this seam was a key-name filter while a dozen surfaces returned
# f(latitude, longitude). Given the mount's RA/Dec — which a viewer holds, it is
# not site data — an ALTITUDE pins the observer to a circle on the Earth, and a
# second sample hours later, or an azimuth alongside it, collapses that circle
# to a point. Recovered to 2.9 km from three authorized requests in the audit.
#
# Quantizing is not an available answer: averaging many coarse samples recovers
# the underlying value, which is exactly how that 2.9 km figure was obtained.
# So the derived values are ABSENT for a non-holder, the same as the raw ones.
_MOUNT_DERIVED_KEYS = ("alt", "az")


def _strip_mount_derived(mount: dict) -> None:
    """Remove the site-derived pointing values from a mount block IN PLACE.

    RA/Dec stay: they say where the telescope looks, not where it stands.
    Alt/az are the same fact expressed in the OBSERVER's frame, which is the
    part that localizes them."""
    for k in _MOUNT_DERIVED_KEYS:
        mount.pop(k, None)


def _scrub_derived_node(container: dict) -> None:
    """Make ``container['mount']`` safe for a non-holder IN PLACE, fail-CLOSED
    on an unexpected shape — same rule as :func:`_scrub_site_node`."""
    if "mount" not in container:
        return
    if isinstance(container.get("mount"), dict):
        _strip_mount_derived(container["mount"])
    else:
        container.pop("mount", None)


def _strip_site(site: dict) -> None:
    """Delete the four precise-site keys from a site dict IN PLACE (safe: every
    caller hands us a freshly-built/copied dict, never shared/persisted state).
    Keys are made ABSENT, not nulled (spec §2). is_default/horizon_min_deg are
    left untouched."""
    for k in _SITE_STRIP_KEYS:
        site.pop(k, None)


def _scrub_site_node(container: dict) -> None:
    """Make ``container['site']`` SAFE for a non-holder IN PLACE, FAIL-CLOSED on
    an unexpected shape.

    A plain-``dict`` site has the four precise keys key-stripped (the normal
    path, spec §2). A site of ANY OTHER shape -- a model, a list, a scalar, some
    future nesting we cannot key-strip -- is REMOVED WHOLESALE. We never leave a
    ``site`` node we could not positively strip, because a coordinate hidden in
    an unexpected shape MUST fail CLOSED (absent) and never fail OPEN (leak):
    the payload/serializer shape has grown before and will again, and the
    security posture (this is the ONLY site-precision enforcement seam) demands
    that drift default to stripping, not leaking. No-op when ``site`` is absent."""
    if "site" not in container:
        return
    if isinstance(container.get("site"), dict):
        _strip_site(container["site"])
    else:
        container.pop("site", None)  # unexpected shape -> fail CLOSED


def _redact_site_for(payload: dict, principal: Principal | None) -> dict:
    """Strip precise site keys in ``payload`` unless ``principal`` holds
    ``view.site_precise``. Handles the top-level ``site`` block AND the
    duplicate copy inside an embedded ``config`` block (summary/hello frame).
    Mutates + returns ``payload`` (which is always a fresh per-call dict).

    FAIL-CLOSED on shape drift (whole-branch security review): an unexpected
    ``site`` shape is stripped WHOLESALE (see ``_scrub_site_node``), and the
    whole routine is wrapped so redaction can NEVER raise out and 500 a surface
    -- on any unforeseen error it removes the site node(s) and returns a safe
    (coordinate-free) payload rather than propagate."""
    # TWO CAPS, EVALUATED INDEPENDENTLY. An operator holds site_derived and NOT
    # site_precise, so a single early return on either one would be wrong in a
    # different direction for each role: keyed on precise it strips an
    # operator's alt/az, keyed on derived it hands a viewer the coordinates.
    has_precise = principal is not None and principal.has(CAP_VIEW_SITE_PRECISE)
    has_derived = principal is not None and principal.has(CAP_VIEW_SITE_DERIVED)
    if has_precise and has_derived:
        return payload  # holder of both: untouched
    if not isinstance(payload, dict):
        return payload
    try:
        if not has_precise:
            _scrub_site_node(payload)
            cfg = payload.get("config")
            if isinstance(cfg, dict):
                _scrub_site_node(cfg)
        if not has_derived:
            _scrub_derived_node(payload)
    except Exception:  # noqa: BLE001 - never 500 a surface: fail CLOSED
        if not has_precise:
            payload.pop("site", None)
            cfg = payload.get("config")
            if isinstance(cfg, dict):
                cfg.pop("site", None)
        if not has_derived:
            payload.pop("mount", None)
    return payload


def _redact_ws_event(ev_json: dict, principal: Principal | None) -> dict | None:
    """Strip precise site keys in a broadcast WS event for a principal lacking
    ``view.site_precise``. The bus ``Event.data`` is SHARED across every
    subscriber, so we must NEVER mutate it in place -- we copy only the nodes we
    change (status carries ``data.site``; config carries ``data.config.site``).
    A holder sees the event verbatim (no copy).

    Weather events (sub-project C, weather spec §8; gate split off in the
    2026-07-17 decisions wave I2) are DROPPED ENTIRELY (not stripped) for a
    principal lacking ``view.weather`` — this returns ``None`` and BOTH WS
    lanes (the LAN /ws handler in api/app.py and the relay ``_run_ws`` in
    remote/relay_client.py) skip the send on None. This is a SEPARATE cap
    from view.site_precise: operators hold view.weather (product-owner
    decision — full weather, radar map included, is visible to operators;
    the owner accepts that the radar map's tile coordinates disclose the
    site region to an operator) but do NOT hold view.site_precise, so a
    weather event is checked FIRST and independently of the site-precision
    branch below (which still gates every OTHER coordinate-bearing event on
    view.site_precise, unchanged). A viewer holds neither cap, so weather
    stays dropped for it.

    CONTRACT (spec §8), RESTATED — the original wording is what let this leak.
    It said site COORDINATES must live at ``data.site`` / ``data.config.site``,
    and every author obeyed it. But the rule that has to hold is stronger:

        NO VALUE COMPUTED FROM THE SITE MAY REACH A NON-HOLDER.

    A key-name filter cannot enforce that, because ``f(latitude, longitude)``
    carries the coordinate without carrying the key. Alt/az, airmass, an
    hour-angle, a sun altitude, a dark-window boundary, a rise/set time, a
    horizon verdict — each of these is the site, re-encoded. The audit recovered
    the observatory to 2.9 km from three requests a plain viewer is entitled to
    make, none of which contained the word "latitude".

    So: coordinates go at ``data.site``/``data.config.site``, DERIVED values are
    stripped here too (``mount.alt``/``az``), and a surface that exists to answer
    a site-relative question — visibility, framing, the sky panel — is gated on
    ``view.site_precise`` rather than redacted, because there is nothing left of
    it once the answer is removed. Adding a derived value to a viewer-visible
    payload is a capability decision, not a formatting one."""
    if ev_json.get("type") == "weather":
        if principal is not None and principal.has(CAP_VIEW_WEATHER):
            return ev_json  # holder (operator or admin): verbatim, unstripped
        return None          # non-holder (viewer): dropped entirely
    # Two caps, independent — see _redact_site_for. An operator holds derived
    # and not precise, so neither one alone decides this event.
    has_precise = principal is not None and principal.has(CAP_VIEW_SITE_PRECISE)
    has_derived = principal is not None and principal.has(CAP_VIEW_SITE_DERIVED)
    if has_precise and has_derived:
        return ev_json
    data = ev_json.get("data")
    if not isinstance(data, dict):
        return ev_json
    try:
        new_data: dict | None = None
        if not has_precise and "site" in data:
            site = data.get("site")
            if isinstance(site, dict):
                if any(k in site for k in _SITE_STRIP_KEYS):
                    new_data = dict(data)
                    new_site = dict(site)
                    _strip_site(new_site)
                    new_data["site"] = new_site
            else:
                # unexpected shape -> fail CLOSED: drop the whole site node
                # (a coordinate we cannot key-strip must never leak).
                new_data = dict(data)
                new_data.pop("site", None)
        # Site-DERIVED pointing (mount.alt/az). Copied before popping for the
        # same reason the site node is: Event.data is shared across every
        # subscriber, so mutating it in place would strip the values from the
        # holder's copy too.
        if not has_derived and "mount" in data:
            mount = data.get("mount")
            if isinstance(mount, dict):
                if any(k in mount for k in _MOUNT_DERIVED_KEYS):
                    new_data = new_data if new_data is not None else dict(data)
                    new_mount = dict(mount)
                    _strip_mount_derived(new_mount)
                    new_data["mount"] = new_mount
            else:
                new_data = new_data if new_data is not None else dict(data)
                new_data.pop("mount", None)   # unexpected shape -> fail CLOSED
        cfg = data.get("config")
        if not has_precise and isinstance(cfg, dict) and "site" in cfg:
            base = new_data if new_data is not None else dict(data)
            new_cfg = dict(cfg)
            cfg_site = cfg.get("site")
            if isinstance(cfg_site, dict):
                new_cfg_site = dict(cfg_site)
                _strip_site(new_cfg_site)
                new_cfg["site"] = new_cfg_site
            else:
                new_cfg.pop("site", None)  # unexpected shape -> fail CLOSED
            base["config"] = new_cfg
            new_data = base
        if new_data is None:
            return ev_json  # nothing site-bearing in this event
        return {**ev_json, "data": new_data}
    except Exception:  # noqa: BLE001 - never crash the ws lane: fail CLOSED
        # Rebuild data with every site-bearing node removed (absent, never
        # leaked) and forward it, so a shape we did not anticipate degrades to a
        # coordinate-free event rather than propagating an exception that would
        # tear down the whole /ws telemetry stream.
        safe = dict(data)
        if not has_precise:
            safe.pop("site", None)
        if not has_derived:
            safe.pop("mount", None)
        cfg = safe.get("config")
        if isinstance(cfg, dict):
            cfg = dict(cfg)
            cfg.pop("site", None)
            safe["config"] = cfg
        return {**ev_json, "data": safe}


# ------------------------------------------------------ driver-row redaction
def _redact_drivers_for(payload: dict, principal: Principal | None) -> dict:
    """Scrub ``host``/``port``/``port_path``/``extra`` from every driver row in
    a ``GET /api/drivers`` payload unless ``principal`` holds
    ``config.backend`` (the same cap that can WRITE a driver's endpoint).
    Without this, a CAP_VIEW_STATUS-only (viewer) caller could read every
    configured driver's LAN host/port/DDNS -- or a native driver's serial
    COM port -- straight off a read-only status surface. ``port_path`` is
    addressing (the serial analog of host/port), so it's stripped alongside
    them; ``transport``/``index`` are NOT addressing (a transport kind or a
    per-unit ordinal reveals nothing reachable) and are left in.

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
        row.pop("port_path", None)
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


# ---------------------------------------------------- report frame-path redaction
def _redact_report_for(payload: dict, principal: Principal | None) -> dict:
    """The same holder rule as ``_redact_session_for``, for session REPORTS.

    Reports predate that rule and were never brought under it: ``FrameRecord``
    calls the field ``saved_path`` rather than ``path`` and carries the absolute
    on-disk location, so ``GET /api/reports/{id}`` handed the observatory's
    filesystem layout to any holder of ``view.status`` — a plain viewer — while
    the session endpoint serving the same frames stripped it. One name, two
    answers. Copies rows; never mutates ``payload`` in place."""
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
            row.pop("saved_path", None)
        scrubbed.append(row)
    return {**payload, "frames": scrubbed}


def report_csv_columns(cols: list[str], principal: Principal | None) -> list[str]:
    """Column list for the frames CSV, minus ``saved_path`` for a caller without
    ``config.backend``. Same rule as ``_redact_report_for``; a CSV export is not
    a loophole around it."""
    if principal is not None and principal.has(CAP_CONFIG_BACKEND):
        return cols
    return [c for c in cols if c != "saved_path"]


__all__ = [
    "WS_AUTH_RECHECK_S",
    "_redact_site_for",
    "_redact_ws_event",
    "_redact_drivers_for",
    "_redact_session_for",
    "_redact_report_for",
    "report_csv_columns",
    "_strip_site",
    "_scrub_site_node",
    "_SITE_STRIP_KEYS",
]
