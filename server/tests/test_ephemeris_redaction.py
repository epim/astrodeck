"""What a caller who cannot see site-derived data gets, and when they get it.

TWO SEPARATE PROPERTIES, and the second is the one that is easy to lose:

* A viewer gets NO satellite rows and a NOTE saying why. Not an empty list -- an
  empty list reads as "there are no satellites", which is false and unactionable.
* The refusal happens BEFORE the propagator runs. A withheld row that was
  computed and then dropped is a withheld row whose LATENCY still answers the
  question, and this codebase has closed that shape of hole before (the audit
  that recovered the observatory to 2.9 km used three requests a plain viewer
  was entitled to make). So the propagator is monkeypatched to explode, and a
  viewer still gets a clean note.

WHY SATELLITES ARE GATED AND PLANETS ARE NOT. ``solar_system._observer`` hands a
non-holder the GEOCENTRIC observer, so a planet row is computed from the centre
of the Earth and no field of it -- named, or added next month -- can carry the
site. That costs at most 12.8 arcsec. It does not transfer to a satellite: at
400 km the topocentric parallax is tens of DEGREES, so there is no degraded
answer to give, only a different part of the sky.

The dew-heater half of this file is the same argument in a different currency: a
heater duty cycle that FOLLOWS the dew point is the dew margin re-encoded, so it
rides ``view.weather`` rather than being published beside the settings that
control it.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
from astrodeck.auth import (principal_for_role, reset_active_provider,
                            set_active_provider)
from astrodeck.catalog.ephemeris import elements as el
from astrodeck.catalog.ephemeris import satellites as sats
from astrodeck.catalog.ephemeris.routes import router as ephemeris_router
from astrodeck.config import ConfigStore

WHEN = 1_789_012_800.0

ISS_ROW = {
    "name": "ISS (ZARYA)", "norad_id": 25544,
    "line1": "1 25544U 98067A   26253.14350205  .00005262  00000+0  "
             "10337-3 0  9999",
    "line2": "2 25544  51.6301 239.6211 0004991 124.3002 235.8459 "
             "15.49065630584920",
}


def _wire(app):
    """Register the ephemeris router where ``app.py`` will register it.

    ``create_app()`` ends with a catch-all ``GET /{path:path}`` that serves the
    SPA, and FastAPI matches in registration order -- so a router added AFTER
    ``create_app()`` returns is shadowed and every request 404s. This splices it
    into the same position the one line in ``app.py`` will, and is a NO-OP once
    that line lands, so it cannot mask its absence.

    ``iter_app_routes`` rather than a flat walk of ``app.router.routes``:
    FastAPI 0.141 makes ``include_router`` append one lazy marker instead of
    copying the child routes up, so on a fresh install the real route is
    invisible behind it and a naive check splices a second copy in."""
    from astrodeck.auth.rbac import iter_app_routes

    if any(getattr(r, "path", "") == "/api/satellites/passes"
           for r in iter_app_routes(app)):
        return False
    routes = app.router.routes
    mark = len(routes)
    app.include_router(ephemeris_router)
    added = routes[mark:]
    del routes[mark:]
    idx = next((i for i, r in enumerate(routes)
                if getattr(r, "path", "") == "/{path:path}"), len(routes))
    routes[idx:idx] = added
    return True


class _FixedPrincipal:
    name = "fake"

    def __init__(self, principal):
        self._principal = principal

    async def resolve(self, request):
        return self._principal


@pytest.fixture
def client(tmp_path, monkeypatch):
    import astrodeck.config as config_mod
    import astrodeck.hub as hub_mod

    store = ConfigStore(path=tmp_path / "astrodeck.json")
    cfg = store.cfg()
    cfg.site = cfg.site.model_copy(update={
        "name": "Test", "latitude": 37.5, "longitude": -122.3,
        "elevation_m": 20.0, "is_default": False})
    monkeypatch.setattr(config_mod, "config_store", store)
    monkeypatch.setattr(hub_mod, "config_store", store)
    monkeypatch.setattr(app_module, "config_store", store)
    monkeypatch.setattr(hub_mod, "CAPTURE_DIR", tmp_path / "captures")
    monkeypatch.setattr(el, "ELEMENTS_DIR", tmp_path / "ephemeris")
    monkeypatch.setattr(el, "SATELLITE_FILE",
                        tmp_path / "ephemeris" / "satellites.json")
    monkeypatch.setattr(el, "COMET_FILE", tmp_path / "ephemeris" / "comets.json")
    monkeypatch.setattr(app_module, "configure_provider_from_auth",
                        lambda _auth: app_module.get_active_provider())
    el.write_envelope(el.SATELLITE_FILE, "test", [ISS_ROW], WHEN)
    reset_active_provider()
    app = app_module.create_app()
    _wire(app)
    with TestClient(app) as c:
        yield c
    reset_active_provider()


@pytest.fixture
def as_role():
    def _set(role: str):
        set_active_provider(_FixedPrincipal(principal_for_role(role)))
    yield _set
    reset_active_provider()


# ================================================ the search, per capability

def test_an_operator_searching_for_iss_gets_the_satellite(client, as_role):
    """The control. Without this the withholding test below could pass because
    satellites never work at all."""
    as_role("operator")
    r = client.get("/api/catalog", params={"q": "iss", "explain": 1})
    assert r.status_code == 200
    rows = [x for x in r.json()["results"] if x.get("kind") == "satellite"]
    assert rows, f"an operator got no satellite: {r.json()}"
    assert rows[0]["id"] == "ISS (ZARYA)"
    assert rows[0]["norad_id"] == 25544
    assert rows[0]["mag"] is None


def test_the_route_does_not_recompute_a_satellites_alt_az(client, as_role):
    """``GET /api/catalog`` fills in alt/az for a ``view.site_derived`` caller by
    running ``altaz`` over each row's RA/Dec. A satellite must be SKIPPED, and
    the reason is that its two coordinate pairs are in different frames:
    ``ra_hours``/``dec_deg`` are GEOCENTRIC (the direction from the centre of
    the Earth) while ``alt``/``az`` came from the TOPOCENTRIC vector at this
    site (satellites.py). For a body 400 km up those are not the same
    direction, so the recompute replaced a right answer with one tens of
    degrees out - and the row still looked perfectly well-formed, which is why
    nothing downstream could have caught it.

    Graded against what the ephemeris itself produced, not against a constant:
    a hard-coded pair would go stale the day the fixture's epoch moves.

    Sabotage: drop the ``kind == "satellite"`` guard in the route."""
    from astrodeck.catalog import altaz, round_az_deg
    import astrodeck.api.app as _app

    as_role("operator")
    r = client.get("/api/catalog", params={"q": "iss"})
    assert r.status_code == 200
    row = next(x for x in r.json() if x.get("kind") == "satellite")

    recomputed_alt, recomputed_az = altaz(row["ra_hours"], row["dec_deg"],
                                          _app.hub.site["latitude"],
                                          _app.hub.site["longitude"])
    assert row["alt"] is not None and row["az"] is not None
    # The two frames must actually DISAGREE here, or this test would pass with
    # the guard removed and prove nothing.
    assert abs(row["alt"] - round(recomputed_alt, 1)) > 1.0         or abs(row["az"] - round_az_deg(recomputed_az)) > 1.0, (
        "the geocentric recompute happens to match: pick a fixture epoch where "
        "the ISS is not near the horizon-crossing where the two frames agree")


def test_a_viewer_searching_for_iss_gets_no_rows_and_the_reason(client,
                                                                as_role):
    """An empty result set is not an answer. "There are no satellites" and
    "your role may not be told where they are" look identical on a screen, and
    only one of them is something the user can act on."""
    as_role("viewer")
    r = client.get("/api/catalog", params={"q": "iss", "explain": 1})
    assert r.status_code == 200
    body = r.json()
    assert [x for x in body["results"] if x.get("kind") == "satellite"] == []
    assert any("Satellites are not offered here" in n for n in body["notes"]), (
        f"a viewer got no satellites and no explanation: {body['notes']}")
    assert any("tens of degrees apart from two towns" in n
               for n in body["notes"])


def test_the_withheld_path_never_evaluates_the_ephemeris(client, as_role,
                                                         monkeypatch):
    """THE TIMING HOLE. A row that is computed and then dropped is still an
    oracle: the request takes longer when the answer exists, and a caller with a
    stopwatch reads the difference. So the capability check happens before a
    single element set is propagated -- proved here by making the propagator
    itself fail the test if it is ever reached."""
    calls: list[int] = []

    def _explode(*a, **kw):
        calls.append(1)
        raise AssertionError(
            "the propagator ran for a caller who may not see satellites")

    monkeypatch.setattr(sats, "propagate_teme", _explode)
    as_role("viewer")
    r = client.get("/api/catalog", params={"q": "iss", "explain": 1})
    assert r.status_code == 200
    assert calls == [], "the ephemeris was evaluated on the withheld path"
    assert any("Satellites are not offered here" in n
               for n in r.json()["notes"]), (
        "the viewer got neither a row nor a clean note")


def test_a_viewer_still_gets_the_deep_sky_rows(client, as_role):
    """The refusal is scoped. A viewer loses the satellites and keeps the
    catalogue -- withholding more than the finding requires is its own defect."""
    as_role("viewer")
    r = client.get("/api/catalog", params={"q": "M31", "explain": 1})
    assert r.status_code == 200
    assert any(x["id"] == "M31" for x in r.json()["results"])


# ============================================== the passes route, gated whole

def test_a_viewers_passes_request_is_403(client, as_role):
    """Gated WHOLE, like ``GET /api/catalog/tonight``. "The ISS clears your tree
    line at 21:04 in the north-west" is a solution for latitude and longitude,
    and there is nothing left of the route once that is removed."""
    as_role("viewer")
    assert client.get("/api/satellites/passes",
                      params={"hours": 1}).status_code == 403


def test_an_operator_may_ask_for_passes(client, as_role):
    as_role("operator")
    r = client.get("/api/satellites/passes", params={"hours": 1})
    assert r.status_code == 200
    body = r.json()
    assert "passes" in body and "horizon_source" in body
    assert "elements" in body


def test_the_status_route_reports_the_cache_and_no_positions(client, as_role):
    """``view.status`` is right for this one BECAUSE it carries no position: how
    old a downloaded file is says nothing about where the rig stands."""
    as_role("viewer")
    r = client.get("/api/ephemeris/status")
    assert r.status_code == 200
    body = r.json()
    assert body["satellites"]["present"] is True
    assert body["satellites"]["count"] == 1
    text = repr(body)
    for leak in ("ra_hours", "dec_deg", "alt", "az", "latitude", "longitude"):
        assert leak not in text, f"the status payload carries {leak}"


def test_a_viewer_may_not_make_this_server_dial_celestrak(client, as_role):
    """``POST /api/ephemeris/refresh`` is gated on ``config.site_optics`` rather
    than a view capability because it makes the SERVER make an outbound request.
    That is also why ``/api/ephemeris`` belongs on
    ``_REMOTE_LOCAL_ONLY_MUTATION_PREFIXES``: the same argument that put
    ``/api/survey/pack`` there."""
    as_role("viewer")
    assert client.post("/api/ephemeris/refresh",
                       json={"which": "all"}).status_code == 403
    as_role("operator")
    assert client.post("/api/ephemeris/refresh",
                       json={"which": "all"}).status_code == 403


def test_a_refresh_of_an_unknown_kind_is_a_422(client, as_role):
    as_role("admin")
    assert client.post("/api/ephemeris/refresh",
                       json={"which": "asteroids"}).status_code == 422


# ================================================= comets take the other path

def test_a_viewer_gets_comets_geocentrically_rather_than_not_at_all(client,
                                                                    as_role):
    """The planets' rule. A comet at 1.5 AU shifts by well under an arcsecond
    between two sites, so the row is not a location oracle and there is nothing
    to withhold -- the viewer gets it, computed from the centre of the Earth,
    and the row says that is what happened."""
    from astrodeck.catalog.ephemeris import comets as C

    line = ("0002P         2027 02 10.2279  0.338618  0.847314  187.2867  "
            "334.0194   11.3479  20260910  14.3  4.0  2P/Encke               "
            "                                  MPC xxxxx")
    el.write_envelope(el.COMET_FILE, "mpc-cometels",
                      [C.parse_comet_line(line)], WHEN)

    as_role("viewer")
    rows = [x for x in client.get("/api/catalog",
                                  params={"q": "encke", "explain": 1}).json()
            ["results"] if x.get("kind") == "comet"]
    assert rows, "a comet was withheld from a viewer; it is not site-derived"
    assert rows[0]["topocentric"] is False
    assert rows[0]["geocentric_reason"] == "not_permitted"
    assert "alt" not in rows[0] and "az" not in rows[0]

    as_role("operator")
    rows = [x for x in client.get("/api/catalog",
                                  params={"q": "encke", "explain": 1}).json()
            ["results"] if x.get("kind") == "comet"]
    assert rows and rows[0]["topocentric"] is True
    assert "alt" in rows[0]


# ============================================ the dew node rides view.weather

_DEW = {"enabled": True, "following": True, "override_until_ts": None,
        "margin_c": 2.4, "temp_c": 11.8, "dewpoint_c": 9.4, "power_pct": 38,
        "reason": "following the dew point",
        "ports": [{"id": "p1", "name": "Dew band", "follow_dew": True,
                   "value": 38},
                  {"id": "p2", "name": "Bench light", "follow_dew": False,
                   "value": 1}]}


def _dew() -> dict:
    """A DEEP copy of the fixture.

    ``_dew()`` is shallow, so the ports list and its rows are the module
    fixture's own. A stripper that edits a row in place would then be graded
    against a fixture it had already rewritten -- the assertion passes because
    both sides moved, and the next test in the file inherits the damage."""
    return {**_DEW, "ports": [dict(row) for row in _DEW["ports"]]}


def test_the_dew_readings_are_stripped_for_a_non_weather_principal():
    """A heater power driven from the dew margin IS the dew margin re-encoded.
    The margin, the air temperature and the dew point come off; the duty cycle
    collapses to null; the SETTINGS stay, because they are about the equipment
    rather than about the air."""
    from astrodeck.api.redact import _redact_site_for

    out = _redact_site_for({"dew": _dew()}, principal_for_role("viewer"))
    dew = out["dew"]
    for key in ("margin_c", "temp_c", "dewpoint_c"):
        assert key not in dew, f"{key} reached a caller without view.weather"
    assert dew["power_pct"] is None, (
        "the heater duty cycle is a continuous function of the dew margin; "
        "publishing it publishes the margin")
    assert dew["enabled"] is True and dew["following"] is True
    assert dew["reason"] == "following the dew point"
    # THE ROW SURVIVES, ITS LEVEL DOES NOT. A following port's value IS the
    # duty cycle this loop just wrote to it, so it is power_pct wearing a port
    # id -- the same continuous function of the margin, at whatever resolution
    # the caller cares to sample. The id, the name and the fact that it follows
    # are equipment, and they stay.
    following, other = dew["ports"]
    assert following["id"] == "p1" and following["name"] == "Dew band"
    assert following["follow_dew"] is True
    assert following["value"] is None, (
        "a dew-following port's level is the margin re-encoded")
    # A port that does NOT follow keeps its value: nobody derived it from the
    # air, and blanking it would hide a bench light for no reason.
    assert other["value"] == 1
    assert _DEW["ports"][0]["value"] == 38, "the fixture was mutated in place"


def test_an_operator_keeps_the_dew_readings():
    """Operators hold ``view.weather`` (the 2026-07-17 owner decision that split
    it off ``view.site_precise``), so this must not be keyed on the wrong cap."""
    from astrodeck.api.redact import _redact_site_for

    out = _redact_site_for({"dew": _dew()}, principal_for_role("operator"))
    assert out["dew"]["margin_c"] == 2.4
    assert out["dew"]["power_pct"] == 38


def test_a_syncer_loses_the_dew_readings_too():
    """The role that exists to download frames holds neither weather nor
    site_derived, and a background process that ships every frame off-site is
    exactly the principal this rule is for."""
    from astrodeck.api.redact import _redact_site_for

    out = _redact_site_for({"dew": _dew()}, principal_for_role("syncer"))
    assert "margin_c" not in out["dew"] and out["dew"]["power_pct"] is None


def test_the_ws_push_strips_dew_without_mutating_the_shared_event():
    """``Event.data`` is shared across every subscriber, so stripping in place
    would take the readings out of the ADMIN's copy too -- the same reason the
    site node and the mount node are copied before they are scrubbed."""
    from astrodeck.api.redact import _redact_ws_event

    ev = {"type": "status", "data": {"dew": _dew()}}
    viewer = _redact_ws_event(ev, principal_for_role("viewer"))
    assert "margin_c" not in viewer["data"]["dew"]
    assert viewer["data"]["dew"]["power_pct"] is None
    assert ev["data"]["dew"]["margin_c"] == 2.4, "the shared event was mutated"
    # The ports list is one level DEEPER than the copy the seam makes (it copies
    # the dew dict, not its rows), so a stripper that wrote through a row would
    # take the level out of the admin's copy as well and nothing above would
    # notice. This is the assertion that says it did not.
    assert ev["data"]["dew"]["ports"][0]["value"] == 38, (
        "the shared event's port rows were mutated")
    admin = _redact_ws_event(ev, principal_for_role("admin"))
    assert admin["data"]["dew"]["margin_c"] == 2.4
    assert admin["data"]["dew"]["ports"][0]["value"] == 38


def test_a_dew_node_of_an_unexpected_shape_fails_closed():
    """Fail CLOSED on drift, the rule the whole seam is held to: a shape we
    cannot positively strip is removed wholesale rather than forwarded."""
    from astrodeck.api.redact import _redact_site_for, _redact_ws_event

    assert "dew" not in _redact_site_for({"dew": [2.4]},
                                         principal_for_role("viewer"))
    ev = _redact_ws_event({"type": "status", "data": {"dew": 2.4}},
                          principal_for_role("viewer"))
    assert "dew" not in ev["data"]


def test_a_principal_holding_site_caps_but_not_weather_still_loses_dew():
    """The early return in both redaction seams tests all THREE caps. No role
    holds precise+derived without weather today, which is exactly why leaving
    weather out of that test would have been inert -- and would have stopped
    being inert the day somebody added a role."""
    from astrodeck.api.redact import _redact_site_for, _redact_ws_event
    from astrodeck.auth import (CAP_VIEW_SITE_DERIVED, CAP_VIEW_SITE_PRECISE,
                                CAP_VIEW_STATUS, Principal)

    p = Principal(role="custom", caps=frozenset({
        CAP_VIEW_STATUS, CAP_VIEW_SITE_PRECISE, CAP_VIEW_SITE_DERIVED}))
    out = _redact_site_for({"dew": _dew()}, p)
    assert "margin_c" not in out["dew"] and out["dew"]["power_pct"] is None
    ev = _redact_ws_event({"type": "status", "data": {"dew": _dew()}}, p)
    assert "margin_c" not in ev["data"]["dew"]


# =========================== the OTHER TWO carriers of the same duty cycle
# Nulling ``dew.power_pct`` was necessary and not sufficient. The level the
# ramp computes is WRITTEN to two registers, and each of them is published on
# its own:
#
#   camera.dew_heater              the camera window heater, on /api/status
#   GET /api/switch/ports  value   a follow_dew port, on its own route
#
# Both are the dew margin re-encoded for exactly as long as the loop is
# driving them -- ``dew.ramp_power`` is a published function of four config
# numbers a viewer reads off /api/config, so inverting it recovers the margin
# to about 0.2 C. Both stop being readings the moment the loop is off or
# paused, because then the number is the one a human put there. One predicate,
# ``redact.dew_is_following``, decides for all three.

def _following(**over) -> dict:
    """A dew snapshot with the loop actually driving."""
    return {**_dew(), **over}


def _paused() -> dict:
    """The same loop, paused by a manual override: the registers now carry
    whatever a human set, so nothing about them is a weather reading."""
    return _following(following=False, power_pct=None)


class TestTheCameraWindowHeater:
    def test_a_viewer_loses_the_level_while_the_loop_is_following(self):
        from astrodeck.api.redact import _redact_site_for

        out = _redact_site_for(
            {"dew": _following(), "camera": {"temperature": -10.0,
                                             "has_dew_heater": True,
                                             "dew_heater": 38}},
            principal_for_role("viewer"))
        assert "dew_heater" not in out["camera"], (
            "camera.dew_heater IS dew.power_pct on the register the loop just "
            "wrote it to; inverting the ramp recovers the margin from it")
        # ...and ONLY that key. The camera block is the biggest node on the
        # status frame and every other field of it is about the camera.
        assert out["camera"]["temperature"] == -10.0
        assert out["camera"]["has_dew_heater"] is True

    def test_an_operator_keeps_it(self):
        from astrodeck.api.redact import _redact_site_for

        out = _redact_site_for({"dew": _following(),
                                "camera": {"dew_heater": 38}},
                               principal_for_role("operator"))
        assert out["camera"]["dew_heater"] == 38

    def test_a_paused_loop_leaves_it_alone(self):
        """The argument for publishing it at all, and it is still right half
        the time. With the loop paused the register holds the level an operator
        set by hand from the Capture bench - it is 60% whether the dew point is
        -10 C or 14 C - and the readout exists precisely so that slider shows
        the heater instead of its own last write."""
        from astrodeck.api.redact import _redact_site_for

        out = _redact_site_for({"dew": _paused(), "camera": {"dew_heater": 60}},
                               principal_for_role("viewer"))
        assert out["camera"]["dew_heater"] == 60

    def test_a_disabled_loop_leaves_it_alone(self):
        from astrodeck.api.redact import _redact_site_for

        out = _redact_site_for(
            {"dew": _following(enabled=False, following=False),
             "camera": {"dew_heater": 60}}, principal_for_role("viewer"))
        assert out["camera"]["dew_heater"] == 60

    def test_no_dew_node_at_all_leaves_it_alone(self):
        """A build with no dew controller, or one whose first tick has not
        happened: nothing has commanded that register, so there is no weather
        in it."""
        from astrodeck.api.redact import _redact_site_for

        out = _redact_site_for({"camera": {"dew_heater": 60}},
                               principal_for_role("viewer"))
        assert out["camera"]["dew_heater"] == 60

    def test_the_ws_push_strips_it_without_mutating_the_shared_event(self):
        """``Event.data`` is shared across every subscriber - the same reason
        the site, mount and dew nodes are copied before they are scrubbed."""
        from astrodeck.api.redact import _redact_ws_event

        ev = {"type": "status",
              "data": {"dew": _following(), "camera": {"dew_heater": 38}}}
        viewer = _redact_ws_event(ev, principal_for_role("viewer"))
        assert "dew_heater" not in viewer["data"]["camera"]
        assert ev["data"]["camera"]["dew_heater"] == 38, (
            "the shared event's camera node was mutated")
        admin = _redact_ws_event(ev, principal_for_role("admin"))
        assert admin["data"]["camera"]["dew_heater"] == 38


class TestAFollowingPortsLevel:
    """``GET /api/switch/ports`` publishes the same number wearing a port id."""

    def _rows(self):
        return [{"id": 3, "name": "Dew Strap C", "follow_dew": True,
                 "value": 97.0, "protected_now": False},
                {"id": 0, "name": "Mount", "follow_dew": False, "value": 1,
                 "protected_now": True}]

    def test_a_viewer_loses_a_following_ports_level(self):
        from astrodeck.api.redact import _redact_switch_ports_for

        out = _redact_switch_ports_for(self._rows(),
                                       principal_for_role("viewer"),
                                       _following())
        dew_row, mount_row = out
        assert dew_row["value"] is None
        # The ROW survives: the Power sheet cannot draw a port at all without
        # the id, the name and the protection policy, and none of the three is
        # a weather reading.
        assert dew_row["id"] == 3 and dew_row["name"] == "Dew Strap C"
        assert dew_row["follow_dew"] is True
        assert dew_row["protected_now"] is False
        # A port that does NOT follow keeps its value: nobody derived it from
        # the air, and blanking it would hide a mount relay for no reason.
        assert mount_row["value"] == 1

    def test_an_operator_keeps_it(self):
        from astrodeck.api.redact import _redact_switch_ports_for

        out = _redact_switch_ports_for(self._rows(),
                                       principal_for_role("operator"),
                                       _following())
        assert out[0]["value"] == 97.0

    def test_a_paused_loop_leaves_every_level_alone(self):
        from astrodeck.api.redact import _redact_switch_ports_for

        out = _redact_switch_ports_for(self._rows(),
                                       principal_for_role("viewer"), _paused())
        assert out[0]["value"] == 97.0

    def test_a_row_of_an_unrecognised_shape_is_treated_as_following(self):
        """Fail CLOSED, the same reading ``_strip_dew`` takes: a row with no
        ``follow_dew`` is one whose shape we do not recognise, and the safe
        answer for a level we cannot classify is to withhold it."""
        from astrodeck.api.redact import _redact_switch_ports_for

        out = _redact_switch_ports_for([{"id": 9, "value": 44}],
                                       principal_for_role("viewer"),
                                       _following())
        assert out[0]["value"] is None

    def test_the_caller_s_rows_are_not_mutated(self):
        from astrodeck.api.redact import _redact_switch_ports_for

        rows = self._rows()
        _redact_switch_ports_for(rows, principal_for_role("viewer"),
                                 _following())
        assert rows[0]["value"] == 97.0


# ------------------------------------------- and the same thing over the wire
# The unit tests above grade the RULE. This grades that the two surfaces that
# carry the number are actually wired to it: a rule nothing calls withholds
# nothing, and ``GET /api/switch/ports`` in particular had no principal at all
# until this fix, so there was no seam for the rule to live at.

@pytest.fixture
def dew_rig(client, monkeypatch):
    """A rig with the loop FOLLOWING and both registers at what it commanded.

    The camera reads its heater back (``_ReadableCamera``, the shape every
    backend that can be asked has); the sim power box's slot 3 is a dew strap
    flagged ``follow_dew``; and the controller's snapshot says the loop is
    driving. Yields the client.
    """
    from astrodeck import power_guard
    from astrodeck.devices.sim import SimCamera, SimRig, SimSwitch

    class _ReadableCamera(SimCamera):
        async def get_dew_heater(self) -> int | None:
            return self._dew_power

    cam = _ReadableCamera(SimRig())
    cam.connected = True
    cam._dew_power = 38
    sw = SimSwitch()
    sw.connected = True
    # Slot 3 is the sim's 0..255 dew strap; put it where a 38% ramp output
    # would land so the value under test is a real number and not a default 0.
    sw._ports[3].value = 97.0
    monkeypatch.setitem(app_module.hub.devices, "camera", cam)
    monkeypatch.setitem(app_module.hub.devices, "switch", sw)
    power_guard.set_port_settings(3, follow_dew=True)
    monkeypatch.setattr(app_module.dew_controller, "_snap", _following())
    try:
        yield client
    finally:
        # The port store is session-scoped (conftest), so this flag would
        # otherwise be the next test's starting condition.
        power_guard.set_port_settings(3, follow_dew=False)


def test_an_admin_reads_both_levels(dew_rig, as_role):
    """THE PRECONDITION, and without it the withholding test below could pass
    against a rig that publishes neither number to anybody."""
    as_role("admin")
    cam = dew_rig.get("/api/status").json()["camera"]
    assert cam["dew_heater"] == 38
    rows = dew_rig.get("/api/switch/ports").json()
    strap = next(r for r in rows if r["id"] == 3)
    assert strap["follow_dew"] is True
    assert strap["value"] == 97.0, (
        "the sim dew strap is a 0..255 port sitting at 97; if this moved, the "
        "assertion below stops proving anything")


def test_a_viewer_reads_neither(dew_rig, as_role):
    """SABOTAGE (run red, restored): drop ``camera`` from
    ``_WEATHER_DERIVED_NODES``, or drop the ``_redact_switch_ports_for`` call
    from the ports route. Either one alone puts the duty cycle back on the
    wire, and inverting ``dew.ramp_power`` (four numbers on /api/config)
    recovers the dew margin from it to about 0.2 C."""
    as_role("viewer")
    cam = dew_rig.get("/api/status").json()["camera"]
    assert "dew_heater" not in cam, (
        "the camera window heater's level reached a principal without "
        "view.weather while the dew loop was driving it")
    # ...and the camera node is still there, with everything that is about the
    # camera rather than about the air.
    assert "temperature" in cam and cam["has_dew_heater"] is True
    rows = dew_rig.get("/api/switch/ports").json()
    strap = next(r for r in rows if r["id"] == 3)
    assert strap["value"] is None, (
        "a dew-following port's level is dew.power_pct wearing a port id")
    assert strap["name"] == "Dew Strap C", "the row itself is equipment; it stays"
    # A port the loop does not drive is untouched: nobody derived it from the
    # air, and blanking it would hide the mount relay for no reason.
    assert next(r for r in rows if r["id"] == 0)["value"] == 1


def test_a_viewer_keeps_both_once_the_loop_is_paused(dew_rig, as_role,
                                                     monkeypatch):
    """The conditional is the whole design. A manual override means the level
    on either register is the one a human set, and withholding it would break
    the Capture bench's slider for a viewer to hide a number that is not a
    measurement."""
    as_role("viewer")
    monkeypatch.setattr(app_module.dew_controller, "_snap", _paused())
    assert dew_rig.get("/api/status").json()["camera"]["dew_heater"] == 38
    rows = dew_rig.get("/api/switch/ports").json()
    assert next(r for r in rows if r["id"] == 3)["value"] == 97.0
