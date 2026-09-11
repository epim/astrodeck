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

# The SECOND site-derived node, and it sits at the top level as a sibling of
# ``mount`` rather than inside it, which is exactly how it survived the audit
# that closed alt/az. ``hub._compute_meridian`` builds the countdown from the
# hour angle: ``lst = lst_hours(site["longitude"])``, ``ha = LST - RA``,
# ``hours_to_flip = -ha``. So a caller who holds ``mount.ra_hours`` (which is
# not site data) recovers ``LST = ra_hours - hours_to_flip`` and from it
# ``longitude = (LST - GMST(t)) * 15``. The value is rounded to 4 decimal
# hours = 0.36 s of hour angle = ~120 m of longitude, so the rounding is not a
# defence.
#
# The STATUS word leaks too, coarsely, and had to be collapsed with it:
# ``n_a_over_pole`` is ``flip_unnecessary_over_pole(dec, site["latitude"])``, a
# latitude-dependent predicate a caller can sweep by pointing the mount, and
# ``counting`` / ``due`` is the SIGN of that same hour angle. The three below
# are the site-derived verdicts; ``n_a_fork`` (pier side), ``flip_disabled``
# (the plan) and ``unknown`` are not, and stay.
#
# ``flip_owed`` joins it for the same reason ``due`` did. It is true only once
# the target has crossed, so it is another reading of the SIGN of that hour
# angle -- weaker, because it also needs the mount to have failed to flip, but
# a caller who can point the mount can arrange that condition and then sweep
# the boolean. It is NULLED rather than set False: false is a claim that no
# flip is owed, and this seam must never answer a safety question on behalf of
# a caller it is withholding the answer from.
_MERIDIAN_DERIVED_KEYS = ("hours_to_flip", "flip_owed")
_MERIDIAN_DERIVED_STATUSES = frozenset({"counting", "due", "n_a_over_pole"})
#: What a collapsed status becomes. Both UI consumers already render this as
#: "unknown"/"the mount does not report a flip" rather than crashing, and
#: ``flip_enabled``/``pier_side`` still ride the block, so a non-holder keeps
#: the two facts that are about the MOUNT rather than about where it stands.
_MERIDIAN_UNKNOWN = "unknown"


def _strip_mount_derived(mount: dict, container: dict | None = None) -> None:
    """Remove the site-derived pointing values from a mount block IN PLACE.

    RA/Dec stay: they say where the telescope looks, not where it stands.
    Alt/az are the same fact expressed in the OBSERVER's frame, which is the
    part that localizes them.

    ``container`` is the payload the node came out of, passed to EVERY stripper
    so a rule that depends on a SIBLING node can be written (see
    ``_strip_camera_dew``). Unused here."""
    for k in _MOUNT_DERIVED_KEYS:
        mount.pop(k, None)


def _strip_meridian_derived(meridian: dict, container: dict | None = None) -> None:
    """Remove the site-derived flip timing from a meridian block IN PLACE.

    ``hours_to_flip`` becomes NULL rather than absent (the field is typed
    ``number | null`` on every client and already has a "no countdown" render),
    and a site-derived ``status`` collapses to ``unknown``. ``flip_owed`` goes
    null with it -- see the key list for why null and not False. ``flip_enabled``,
    ``pier_side`` and its ``pier_side_source``/``pier_side_age_s`` are
    properties of the mount and the loaded plan, not of the observer's
    position, so they stay."""
    for k in _MERIDIAN_DERIVED_KEYS:
        if k in meridian:
            meridian[k] = None
    if meridian.get("status") in _MERIDIAN_DERIVED_STATUSES:
        meridian["status"] = _MERIDIAN_UNKNOWN


#: node key -> the in-place stripper for it. ONE table, so a third derived node
#: is added in one place and both the REST seam and the WS seam get it.
_DERIVED_NODES = (("mount", _strip_mount_derived),
                  ("meridian", _strip_meridian_derived))

# WHY SATELLITES ARE NOT IN THAT TABLE, and are gated whole instead (D-SKY-1).
# Every entry above works by DEGRADING a node: mount.alt/az come off and RA/Dec
# stay; meridian's countdown goes null and the pier side stays. The planets take
# the same shape one level down -- ``solar_system._observer(site_derived)``
# hands a non-holder the GEOCENTRIC observer, so the row is computed from the
# centre of the Earth and no field of it can carry the site, at a cost of at
# most 12.8 arcsec of position. That trick works because a planet is far away.
# It does not transfer to a satellite: at 400 km, topocentric parallax is TENS
# OF DEGREES, so the geocentric answer is not a coarser version of the
# topocentric one, it is a different part of the sky. There is nothing to
# degrade to. So ``GET /api/satellites/passes`` is gated WHOLE on
# ``view.site_derived`` (like ``GET /api/catalog/tonight``), and the satellite
# rows inside ``GET /api/catalog`` are withheld inside ``objects.search()``
# BEFORE the propagator runs -- the Moon's rule, for a much larger number. A
# stripper here would have been the wrong tool twice over: it would have had to
# remove every field of the row, and it would have run after the answer already
# existed.

# ------------------------------------------- values DERIVED from the WEATHER
# The dew-heater node, carried here for the dew-control lane (which does not own
# this file). Gated on ``view.weather``, the cap that already decides whether a
# principal sees the forecast at all -- and it has to be gated, because a heater
# power driven from the dew margin IS THE DEW MARGIN RE-ENCODED. The same shape
# as ``mount.alt`` being latitude: the number does not carry the word.
#
# What stays is the SETTINGS (is the heater on, is it following the dew point,
# is there a manual override running, which ports exist); what goes is the
# READINGS (the margin, the air temperature, the dew point, and the duty cycle
# computed from them).
#
# AND THE DUTY CYCLE HAS THREE CARRIERS, not one. That is what the first pass
# missed: nulling ``dew.power_pct`` and the dew node's own port rows left the
# SAME number reaching a viewer as ``camera.dew_heater`` (the register the loop
# writes) and as the ``value`` of a ``follow_dew`` port on
# ``GET /api/switch/ports`` (the port the loop writes). Each is gated here, on
# ``view.weather``, under one predicate - ``dew_is_following`` - so the three
# can never answer differently:
#
#   dew.power_pct + dew.ports[].value  ->  _strip_dew
#   camera.dew_heater                  ->  _strip_camera_dew
#   GET /api/switch/ports  value       ->  _redact_switch_ports_for
_WEATHER_DERIVED_KEYS = ("margin_c", "temp_c", "dewpoint_c")


def dew_is_following(dew: object) -> bool:
    """Is the dew loop DRIVING the heaters right now?

    The ONE predicate behind every "this heater level is a weather reading"
    decision, so the status node, the WS push and ``GET /api/switch/ports``
    cannot answer it three different ways. ``enabled and following`` is the
    only combination in which a level on a heater was CHOSEN BY THE RAMP; with
    the loop off, or paused by a manual override, the number on the register is
    the one a human put there and says nothing about the air.

    Total: anything that is not a dew snapshot with both flags true is False
    (the loop has not ticked, the build has no dew controller, the node came
    back in a shape we do not recognise)."""
    return bool(isinstance(dew, dict)
                and dew.get("enabled") and dew.get("following"))


def _strip_dew(dew: dict, container: dict | None = None) -> None:
    """Remove the dew-point READINGS from a dew block IN PLACE.

    ``margin_c``/``temp_c``/``dewpoint_c`` are made ABSENT and ``power_pct``
    becomes NULL (the field is typed ``number | null`` on the client and already
    renders a "not reporting" state). ``power_pct`` is collapsed rather than
    kept because a heater following the dew point is a continuous function of
    the margin: publishing the duty cycle publishes the margin at whatever
    resolution the caller cares to sample, which is the same finding that closed
    ``mount.alt``.

    ``enabled``/``following``/``override_until_ts``/``reason``/``ports`` stay:
    they are settings and identities, not measurements of the air -- but a
    FOLLOWING port's ``value`` is the same leak as ``power_pct`` wearing a
    port id, because it IS the duty cycle this loop just wrote to that port.
    So the row survives (the id, the name and the fact that it follows are
    equipment) and its level is nulled. A port that does NOT follow keeps its
    value: nobody derived it from the air."""
    for k in _WEATHER_DERIVED_KEYS:
        dew.pop(k, None)
    if "power_pct" in dew:
        dew["power_pct"] = None
    rows = dew.get("ports")
    if isinstance(rows, list):
        # A NEW LIST OF NEW ROWS, never an in-place edit of the caller's. The
        # WS seam hands us a SHALLOW copy of the dew node (_redact_ws_event),
        # so the list and its dicts are still the shared bus event's -- writing
        # through them would take the level out of the admin's copy too, which
        # is the same bug the copy above exists to prevent.
        #
        # `follow_dew` MISSING is treated as following: fail-closed on a row
        # whose shape we do not recognise.
        dew["ports"] = [
            {**row, "value": None}
            if isinstance(row, dict) and row.get("follow_dew", True)
            and "value" in row else row
            for row in rows]


#: The camera node's dew-heater register. Its own constant because it is the
#: only key of that node this module has any opinion about.
_CAMERA_DEW_KEY = "dew_heater"


def _strip_camera_dew(camera: dict, container: dict | None = None) -> None:
    """Remove ``camera.dew_heater`` IN PLACE **while the dew loop is driving it**.

    THE SECOND CARRIER OF THE SAME NUMBER, and it is the one that survived the
    first pass. ``_strip_dew`` nulls ``dew.power_pct`` because a heater power
    that follows the dew point IS the dew margin re-encoded - and then the very
    same duty cycle was published one node over as ``camera.dew_heater``,
    unstripped, to any holder of ``view.status``. Inverting the ramp
    (``dew.ramp_power``, four config numbers a viewer reads off ``GET
    /api/config``) recovers the margin from it to about 0.2 C, which is the
    whole quantity ``view.weather`` exists to withhold.

    CONDITIONAL, because the original argument for publishing it is still
    right when the loop is NOT driving. This is a DEVICE READOUT of a register
    an operator sets by hand from the Capture bench, and it exists so that
    slider shows the level the heater is actually at instead of its own last
    write (the bug that reading it fixed). With the loop off or paused, the
    number on the register is the one a human put there: it is 60% whether the
    dew point is -10 C or 14 C, and withholding it would break a control an
    operator has always had in order to hide something that is not a
    measurement. With the loop FOLLOWING, the same field is the ramp's output
    and nothing else.

    ``container`` is the payload the camera node came out of, and the sibling
    ``dew`` node in it is what says which of the two it is. A payload with no
    ``dew`` node at all (the loop has not ticked yet, or this build has no dew
    controller) is "not following" - nothing has commanded that register, so
    there is no weather in it.

    ABSENT, not nulled, and only this one key: ``camera`` is the biggest node
    on the status frame and every other field of it (temperature, cooler,
    binning, the video capabilities) is about the camera."""
    if not dew_is_following((container or {}).get("dew")):
        return
    camera.pop(_CAMERA_DEW_KEY, None)


#: node key -> stripper, for nodes gated on ``view.weather`` rather than
#: ``view.site_derived``. A separate table because it is a separate cap: an
#: operator holds weather and not site_precise, a syncer holds neither.
#:
#: ``camera`` is in the WEATHER table and not the site one, and it is stripped
#: PARTIALLY (one key) rather than wholesale - see ``_strip_camera_dew``.
_WEATHER_DERIVED_NODES = (("dew", _strip_dew), ("camera", _strip_camera_dew))


def _scrub_derived_node(container: dict, table=_DERIVED_NODES) -> None:
    """Make every DERIVED node in ``container`` safe for a non-holder,
    fail-CLOSED on an unexpected shape - same rule as :func:`_scrub_site_node`.

    ``table`` is which family of derived nodes to strip: ``_DERIVED_NODES``
    (site) or ``_WEATHER_DERIVED_NODES``. One implementation, two caps, so a
    node added to either table gets the fail-closed behaviour for free.

    EVERY NODE IS COPIED BEFORE IT IS STRIPPED, and that is a correctness fix
    rather than tidiness. ``_redact_site_for``'s docstring promised it was
    handed "a fresh per-call dict", and for the top level it is - but the NODES
    inside it are not always fresh. ``hub.poll_status`` stashes the meridian
    block it just built as ``hub.last_meridian`` and puts THE SAME OBJECT on
    the payload, so stripping it in place blanked ``hours_to_flip`` in the
    hub's own copy: one viewer's ``GET /api/status`` dropped the flip ETA for
    the ENGINE (which reads ``last_meridian`` to window-gate the flip cost) and
    for every later reader, until the next poll rebuilt it. The WS seam already
    copied for the same reason; now both do."""
    for key, strip in table:
        if key not in container:
            continue
        node = container.get(key)
        if isinstance(node, dict):
            new_node = dict(node)
            strip(new_node, container)
            container[key] = new_node
        else:
            container.pop(key, None)


def _panic_scrub_weather(container: dict) -> None:
    """Last-resort scrub of the weather-gated nodes, for the ``except`` arms.

    NOT a plain ``pop`` of every key in the table, which is what the site and
    site-derived arms do. ``camera`` is the whole camera block - temperature,
    cooler, binning, video capabilities - and only ONE key of it rides
    ``view.weather``. Dropping the node would take a viewer's entire camera
    panel off the wire to hide one integer, which is a worse failure than the
    one the guard exists for.

    So the panic path drops the ``dew`` node wholesale (all of it is readings)
    and takes exactly ``dew_heater`` off a copy of the camera node -
    UNCONDITIONALLY here, because the condition is the thing we just failed to
    evaluate, and an unevaluated condition fails closed."""
    container.pop("dew", None)
    cam = container.get("camera")
    if isinstance(cam, dict):
        cam = dict(cam)
        cam.pop(_CAMERA_DEW_KEY, None)
        container["camera"] = cam
    elif "camera" in container:
        container.pop("camera", None)   # unexpected shape -> fail CLOSED


def _redact_switch_ports_for(rows: list, principal: Principal | None,
                             dew: object) -> list:
    """``GET /api/switch/ports`` rows, with a dew-FOLLOWING port's ``value``
    nulled for a principal lacking ``view.weather``.

    THE THIRD CARRIER, and the one furthest from the word "weather". The dew
    loop writes the ramp's output onto every port flagged ``follow_dew``, so
    that port's reported value IS ``dew.power_pct`` wearing a port id - the
    same number ``_strip_dew`` already nulls on the dew node's own ``ports``
    rows, published again by the route that lists the power box. A non-holder
    who could not read it at ``/api/status`` could read it at
    ``/api/switch/ports``.

    Same rule, same shape as ``_strip_dew``: the ROW survives (the id, the
    name, the protection policy and the fact that it follows are equipment,
    and the Power sheet needs all four to render a port at all) and only the
    LEVEL goes. A port that does not follow keeps its value - nobody derived it
    from the air - and every port keeps it while the loop is off or paused,
    because then the level is whatever a human last set.

    ``follow_dew`` MISSING is treated as following, the same fail-closed
    reading ``_strip_dew`` takes on a row whose shape it does not recognise.

    Copies each row it changes; ``power_guard.annotate`` already hands us
    copies, but this helper is also the seam a future caller will reach for
    with rows it did not build."""
    if principal is not None and principal.has(CAP_VIEW_WEATHER):
        return rows
    if not isinstance(rows, list) or not dew_is_following(dew):
        return rows
    out = []
    for row in rows:
        if (isinstance(row, dict) and row.get("follow_dew", True)
                and "value" in row):
            row = {**row, "value": None}
        out.append(row)
    return out


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
    # THREE caps now, and the early return has to name all three. Every role
    # that holds precise+derived today also holds weather, so leaving `weather`
    # out of this test would be inert -- and that is exactly the kind of
    # inertness that stops being inert the day somebody adds a role. Fail
    # closed: only a holder of everything skips the work.
    has_weather = principal is not None and principal.has(CAP_VIEW_WEATHER)
    if has_precise and has_derived and has_weather:
        return payload  # holder of all three: untouched
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
        if not has_weather:
            _scrub_derived_node(payload, _WEATHER_DERIVED_NODES)
    except Exception:  # noqa: BLE001 - never 500 a surface: fail CLOSED
        if not has_precise:
            payload.pop("site", None)
            cfg = payload.get("config")
            if isinstance(cfg, dict):
                cfg.pop("site", None)
        if not has_derived:
            for key, _strip in _DERIVED_NODES:
                payload.pop(key, None)
        if not has_weather:
            _panic_scrub_weather(payload)
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
    stripped here too (``mount.alt``/``az``, and ``meridian``'s flip timing —
    a top-level sibling of ``mount``, which is how it outlived the alt/az fix),
    and a surface that exists to answer a site-relative question — visibility,
    framing, the sky panel — is gated on
    ``view.site_precise`` rather than redacted, because there is nothing left of
    it once the answer is removed. Adding a derived value to a viewer-visible
    payload is a capability decision, not a formatting one."""
    if ev_json.get("type") == "weather":
        if principal is not None and principal.has(CAP_VIEW_WEATHER):
            return ev_json  # holder (operator or admin): verbatim, unstripped
        return None          # non-holder (viewer): dropped entirely
    # Three caps, independent — see _redact_site_for. An operator holds derived
    # and weather but not precise, so no one of them alone decides this event.
    has_precise = principal is not None and principal.has(CAP_VIEW_SITE_PRECISE)
    has_derived = principal is not None and principal.has(CAP_VIEW_SITE_DERIVED)
    has_weather = principal is not None and principal.has(CAP_VIEW_WEATHER)
    if has_precise and has_derived and has_weather:
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
        # Site-DERIVED nodes (mount.alt/az; meridian's flip timing). Copied
        # before stripping for the same reason the site node is: Event.data is
        # shared across every subscriber, so mutating it in place would strip
        # the values from the holder's copy too.
        # The dew node rides ``view.weather`` rather than ``view.site_derived``
        # (a heater duty cycle following the dew point IS the dew margin), so
        # the two tables are walked under their own caps but through the same
        # copy-before-strip loop -- the shared ``Event.data`` must not be
        # mutated for anybody else's subscriber.
        for gated, table in ((has_derived, _DERIVED_NODES),
                             (has_weather, _WEATHER_DERIVED_NODES)):
            if gated:
                continue
            for key, strip in table:
                if key not in data:
                    continue
                node = data.get(key)
                if isinstance(node, dict):
                    new_node = dict(node)
                    # ``data`` and not ``new_data``: the strippers that consult
                    # a sibling (``_strip_camera_dew`` reads ``dew``) want the
                    # UNredacted one, and no stripper touches the two flags it
                    # reads, so the two are the same answer either way.
                    strip(new_node, data)
                    if new_node != node:
                        new_data = new_data if new_data is not None else dict(data)
                        new_data[key] = new_node
                else:
                    new_data = new_data if new_data is not None else dict(data)
                    new_data.pop(key, None)   # unexpected shape -> fail CLOSED
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
            for key, _strip in _DERIVED_NODES:
                safe.pop(key, None)
        if not has_weather:
            _panic_scrub_weather(safe)
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


# ------------------------------------------------------ profile-row redaction
# A device row's addressing, plus the two top-level rig endpoints a profile
# carries outside ``devices[]``. Same split as the driver rule: the LEFT column
# is where a device is reachable, the right-hand keys we keep (role/backend/
# name/dev_type/dev_num/transport/driver_id) are which device it is.
_PROFILE_DEVICE_ADDRESS_KEYS = ("host", "port", "port_path", "extra")
_PROFILE_ADDRESS_KEYS = ("nina_host", "nina_port", "phd2_host", "phd2_port")


def _redact_profile_for(payload: dict, principal: Principal | None) -> dict:
    """Scrub the addressing out of a profile record for a caller lacking
    ``config.backend``, and the ``site_name`` for one lacking
    ``view.site_precise``.

    A profile is a saved CONNECTION INTENT, so it is a driver list by another
    name: every ``devices[]`` row carries the LAN ``host``/``port`` (or the
    serial ``port_path``) of a real box, and the record carries the NINA and
    PHD2 endpoints at its top level. ``GET /api/profiles/{id}`` is gated on
    ``view.status``, which a VIEWER and a relay viewer-link both hold, and the
    only redaction it ran was ``profiles.redact_profile`` -- a key-NAME filter
    over ``devices[].extra``, which blanks ``password``-ish keys and has nothing
    to say about a host:port. So the same endpoint detail ``_redact_drivers_for``
    strips off /api/drivers was readable one route over. ``extra`` goes whole,
    for the same reason it does there: it is backend-specific connection options
    and a name filter is not a containment argument.

    TWO CAPS, EVALUATED INDEPENDENTLY (see ``_redact_site_for``): the addressing
    is gated on ``config.backend`` (the cap that can WRITE these endpoints) and
    ``site_name`` on ``view.site_precise``. A saved site NAME is PRECISE-tier,
    not derived-tier -- "Ridge Road Pad" geolocates the rig as well as the
    coordinates do -- so it belongs with ``_SITE_STRIP_KEYS``' ``name``, and an
    operator (site_derived, never site_precise) does not get it.

    Copies the record and each device row; never mutates ``payload`` in place.
    Used for BOTH the detail payload and each picker row from
    ``ProfileLibrary.list`` (a row has ``site_name`` and no ``devices``, so the
    device branch is a no-op there)."""
    has_backend = principal is not None and principal.has(CAP_CONFIG_BACKEND)
    has_precise = principal is not None and principal.has(CAP_VIEW_SITE_PRECISE)
    if has_backend and has_precise:
        return payload  # holder of both: the full record, untouched
    if not isinstance(payload, dict):
        return payload
    out = dict(payload)
    if not has_precise:
        out.pop("site_name", None)
    if not has_backend:
        for key in _PROFILE_ADDRESS_KEYS:
            out.pop(key, None)
        devices = out.get("devices")
        if isinstance(devices, list):
            scrubbed = []
            for row in devices:
                if not isinstance(row, dict):
                    scrubbed.append(row)
                    continue
                row = dict(row)
                for key in _PROFILE_DEVICE_ADDRESS_KEYS:
                    row.pop(key, None)
                scrubbed.append(row)
            out["devices"] = scrubbed
    return out


# ------------------------------------------------------- frame-path externalizing
#
# THE RULE, and it is not a cap: **no absolute path leaves this process, for
# anybody.** Not a viewer, not an operator, not an admin, not a syncer.
#
# It used to be a holder rule — ``config.backend`` saw the real on-disk location
# and everyone else saw the field removed. That is the wrong shape twice over.
# It treats the observatory's filesystem layout (account name, drive, directory
# scheme) as a privilege to be granted rather than an implementation detail
# nobody outside needs; and it left the two halves of one idea — a session's
# ``path`` and a report's ``saved_path`` — as two functions that had already
# drifted once, with the report side handing the layout to a plain viewer for
# months.
#
# So both now CONVERT rather than gate: every caller gets the capture-root-
# relative path, which is the only form any client can use anyway (it is what
# ``/api/gallery/file`` and the sync manifest accept; the absolute one is
# rejected everywhere). A frame outside this box's library — a NINA save on the
# imaging host — has no relative path, so the field is ABSENT. That is a true
# statement; a fabricated relpath would not be.


def _externalize_frame_paths(payload: dict, key: str) -> dict:
    """Rewrite ``key`` on every frame row to a capture-root-relative path,
    dropping it where no such path exists. Copies rows; never mutates in place.

    ``principal`` is deliberately NOT a parameter. There is no caller for whom
    the absolute path is the right answer, so there is no branch here to get
    wrong later.
    """
    if not isinstance(payload, dict):
        return payload
    frames = payload.get("frames")
    if not isinstance(frames, list):
        return payload
    from .. import gallery as _gallery
    scrubbed = []
    for row in frames:
        if isinstance(row, dict) and key in row:
            row = dict(row)
            rel = _gallery.relpath_under_capture(row.get(key))
            if rel is None:
                row.pop(key, None)
            else:
                row[key] = rel
        scrubbed.append(row)
    return {**payload, "frames": scrubbed}


def _redact_session_for(payload: dict, principal: Principal | None) -> dict:
    """Session-ledger frames: ``path`` becomes capture-root-relative (sessions
    spec §8, tightened — see the block comment above)."""
    return _externalize_frame_paths(payload, "path")


def _redact_report_for(payload: dict, principal: Principal | None) -> dict:
    """Session REPORT frames: ``saved_path`` becomes capture-root-relative.

    ``FrameRecord`` calls the field ``saved_path`` rather than ``path`` and
    carried the absolute on-disk location, so ``GET /api/reports/{id}`` handed
    the observatory's filesystem layout to any holder of ``view.status`` — a
    plain viewer — while the session endpoint serving the same frames stripped
    it. One name, two answers. Now one answer, and it is relative."""
    return _externalize_frame_paths(payload, "saved_path")


def report_csv_columns(cols: list[str], principal: Principal | None) -> list[str]:
    """Column list for the frames CSV. ``saved_path`` STAYS for every caller —
    the value written under it is relative (the route externalizes each row the
    same way the JSON route does), so the column is no longer a disclosure and
    dropping it would only make the CSV less useful than the JSON."""
    return list(cols)


__all__ = [
    "WS_AUTH_RECHECK_S",
    "dew_is_following",
    "_redact_switch_ports_for",
    "_redact_site_for",
    "_redact_ws_event",
    "_redact_drivers_for",
    "_redact_profile_for",
    "_redact_session_for",
    "_redact_report_for",
    "report_csv_columns",
    "_strip_site",
    "_strip_dew",
    "_strip_camera_dew",
    "_scrub_site_node",
    "_SITE_STRIP_KEYS",
    "_WEATHER_DERIVED_NODES",
]
