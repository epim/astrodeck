"""`GET /api/cloudmap/at` reads the mount itself (#520).

The panels used to build `/api/cloudmap/at?alt=&az=` from the mount's alt/az,
so the Fly relay's access log held the live pointing several times a minute. A
pointing at a known time is a function of the site, and at park its altitude
is the latitude (#140). Now the GET takes only `ahead_s`: the server reads the
connected mount, works out where it points, and answers `at_payload` for that
direction. A picked direction - somebody tapping the dome - is a POST body.

What this file holds, on the real service over the synthetic sky that
`test_cloudmap_routes` builds, with a SIMULATED mount in the hub:

  * the telescope answer IS `at_payload` at the mount's pointing, at every
    rung of the look-ahead ladder, and follows the mount when it moves;
  * it does not echo the pointing back;
  * a GET still carrying alt or az is refused with 422, naming the
    parameter and never its value;
  * no mount, a disconnected one, one that will not answer and one below the
    horizon are each a 200 with basis `no_pointing` - never a 500 and never a
    400 the caller did nothing to earn;
  * a switched-off map does not read the mount at all;
  * the control: a POSTed direction is answered for that direction.

MUTATIONS RUN, 2026-09-29, each in a private scratch copy of server/
(scratchpad/H4-PRIV-mut in the session scratchpad) from a byte backup restored
with its sha256 checked. Output verbatim, cut at "[...]" where a payload runs
on.

  M1 "the route ignores the mount and answers a fixed direction" -
  `telescope_payload` calls `at_payload(alt_deg=45.0, az_deg=180.0, ...)`
  instead of the mount's pointing. 1 failed,
  test_the_answer_is_at_payload_at_the_mounts_pointing:
      AssertionError: the telescope answer at ahead_s=0 is not at_payload at
      the mount's pointing: [...]
      Differing items:
      {'downrange_km': 3.996237134906181} != {'downrange_km': 2.3077107895821443}
      [...]
      {'beam_m': 166.02494856950034} != {'beam_m': 135.58714017351178}...

  M2 "accept and ignore alt" - the `_refuse_site_query` call deleted from
  `get_cloudmap_at`. 1 failed, test_a_get_still_carrying_alt_or_az_is_refused:
      AssertionError: alt=12.345 -> 200
      assert 200 == 422

  M3 "echo the pointing" - the `del out["alt_deg"], out["az_deg"]` deleted.
  2 failed: test_the_answer_is_at_payload_at_the_mounts_pointing ("Left
  contains 2 more items: {'alt_deg': [...], 'az_deg': [...]}") and
  test_the_answer_does_not_echo_the_pointing:
      AssertionError: ['ahead_s', 'alt_deg', 'az_deg', 'basis', 'beam_m',
      'cell_km', ...]

  M4 "the mount read is not guarded" - the try/except around
  `telescope.get_position()` removed. 1 failed,
  test_a_mount_that_will_not_answer_is_a_200_not_a_500:
      AssertionError: Internal Server Error
      assert 500 == 200

  M5 "a disconnected mount is still read" - the `connected` test dropped from
  `_mount_pointing`. 1 failed, test_a_disconnected_mount_is_no_mount:
      assert 'crossing' == 'no_pointing'

  M6 "below the horizon is asked anyway" - the `if not alt > 0.0` return
  deleted. 1 failed, test_a_mount_below_the_horizon_is_a_200_not_a_400:
      AssertionError: {"detail":"alt must satisfy 0 < alt <= 90, got -52.7
      degrees"}
      assert 400 == 200

  M7 "the mount is read before the sky is checked" - `_mount_pointing` awaited
  ahead of `_no_sky`. 2 failed, test_a_switched_off_map_does_not_read_the_mount:
      AssertionError: a switched-off cloud map read the mount
      assert 1 == 0
  and test_a_mount_that_will_not_answer_is_a_200_not_a_500 (`assert 2 == 1`
  on the ask count), which pins one read per request.

Added by the H4-PRIV verifier, 2026-09-29, after both mutants below SURVIVED
every cloud-map, RBAC and #19 leak test (test_cloudmap_routes,
test_rbac_enforcement, this file, test_no_route_leaks_the_site_coordinates).
Run in a private scratch copy of server/ (scratchpad/H4-PRIV-verify-mut) from
a byte backup restored with its sha256 checked. Output verbatim.

  M8 "the POST is gated by view.status" - `post_cloudmap_at` declared and
  required under CAP_VIEW_STATUS. 1 failed,
  test_a_role_without_view_weather_is_refused_on_both_forms:
      AssertionError: viewer: POST /api/cloudmap/at -> 200
      assert 200 == 403

  M9 "the GET is gated by view.status" - the same change on
  `get_cloudmap_at`. 1 failed, the same case:
      AssertionError: viewer: GET /api/cloudmap/at -> 200
      assert 200 == 403
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
from astrodeck.catalog.coords import altaz, lst_hours
from astrodeck.devices.sim import SimRig, SimTelescope

from test_cloudmap_routes import PICKED, _make
from test_cloudmap_service import BASE, SITE_LAT, SITE_LON

LADDER = (0, 900, 1800)


def _sim_mount(ha_hours: float, dec_deg: float) -> SimTelescope:
    """A connected simulated mount pointing at hour angle ``ha_hours`` and
    declination ``dec_deg`` at the synthetic sky's instant."""
    rig = SimRig()
    rig.ra_hours = (lst_hours(SITE_LON, BASE) - ha_hours) % 24.0
    rig.dec_deg = dec_deg
    tel = SimTelescope(rig)
    asyncio.run(tel.connect())
    return tel


def _expected_pointing(tel: SimTelescope) -> tuple[float, float]:
    """Where the mount points, worked out independently of the route: the raw
    position through `altaz` at the synthetic sky's instant, exactly as the
    status frame's `mount.alt`/`az` are."""
    alt, az = altaz(tel.rig.ra_hours, tel.rig.dec_deg, SITE_LAT, SITE_LON, BASE)
    return alt, az


def _telescope_answer(svc, alt: float, az: float, ahead_s: float) -> dict:
    out = svc.at_payload(alt_deg=alt, az_deg=az, ahead_s=ahead_s, now=BASE)
    del out["alt_deg"], out["az_deg"]
    return out


@pytest.fixture
def no_mount(monkeypatch):
    """The hub is a module singleton; whatever a previous test left in it must
    not be what this one reads."""
    monkeypatch.delitem(app_module.hub.devices, "telescope", raising=False)


def _install(monkeypatch, tel) -> None:
    monkeypatch.setitem(app_module.hub.devices, "telescope", tel)


# ============================================ the answer is the mount's


def test_the_answer_is_at_payload_at_the_mounts_pointing(
        tmp_path, monkeypatch, no_mount):
    """Two pointings, the whole ladder at each, compared field for field with
    `at_payload` asked directly about the direction the mount holds.

    Two and not one, because a route answering one FIXED direction would match
    a mount that happened to point there. The two answers must also differ
    from each other, or the comparison could not tell a fixed direction from a
    followed one."""
    _store, app, svc = _make(tmp_path, monkeypatch)
    south = _sim_mount(0.0, SITE_LAT - 30.0)      # on the meridian, ~60 up
    west = _sim_mount(2.5, SITE_LAT - 5.0)        # past it, lower and west
    answers = []
    with TestClient(app) as c:
        for tel in (south, west):
            _install(monkeypatch, tel)
            alt, az = _expected_pointing(tel)
            assert 20.0 < alt < 89.0, "the fixture no longer points at the sky"
            for ahead_s in LADDER:
                r = c.get(f"/api/cloudmap/at?ahead_s={ahead_s}")
                assert r.status_code == 200, r.text
                got = r.json()
                want = _telescope_answer(svc, alt, az, ahead_s)
                assert got == want, (
                    f"the telescope answer at ahead_s={ahead_s} is not "
                    f"at_payload at the mount's pointing:\n got  {got}\n want "
                    f"{want}")
            answers.append(c.get("/api/cloudmap/at").json())
    assert answers[0]["basis"] in ("crossing", "mask_only"), answers[0]
    assert answers[0] != answers[1], (
        "two different pointings gave the same answer, so this case cannot "
        "tell a route that follows the mount from one that does not")


def test_the_answer_does_not_echo_the_pointing(tmp_path, monkeypatch, no_mount):
    """Nobody asked about a direction, so none comes back: the ladder draws its
    marker from the status frame. Dropped, not nulled, so a reader that wants
    them fails loudly instead of drawing a marker at zero."""
    _store, app, _svc = _make(tmp_path, monkeypatch)
    tel = _sim_mount(0.0, SITE_LAT - 30.0)
    alt, az = _expected_pointing(tel)
    with TestClient(app) as c:
        _install(monkeypatch, tel)
        r = c.get("/api/cloudmap/at")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["basis"] in ("crossing", "mask_only"), body
    assert "alt_deg" not in body and "az_deg" not in body, sorted(body)
    for value in (alt, az):
        for form in (repr(value), f"{value:.3f}", f"{value:.2f}"):
            assert form not in r.text, (
                "the telescope answer carries a rendering of the mount's "
                "pointing")


# ===================================== an old client fails, and says why


def test_a_get_still_carrying_alt_or_az_is_refused(tmp_path, monkeypatch,
                                                    no_mount):
    """Refused, not ignored: FastAPI drops an undeclared query parameter in
    silence, so an old tab would go on writing the pointing into every log on
    the way while getting a good answer back. The refusal names the parameter
    and never its value, because the body goes back through the same relay."""
    _store, app, _svc = _make(tmp_path, monkeypatch)
    with TestClient(app) as c:
        _install(monkeypatch, _sim_mount(0.0, SITE_LAT - 30.0))
        for query in ("alt=12.345", "az=67.891", "alt=12.345&az=67.891",
                      "alt=12.345&az=67.891&ahead_s=900", "lat=12.345",
                      "lon=67.891", "site=12.345"):
            r = c.get("/api/cloudmap/at?" + query)
            assert r.status_code == 422, query + " -> " + str(r.status_code)
            assert r.json()["detail"]["code"] == "site_query_refused", r.text
            assert "12.345" not in r.text and "67.891" not in r.text, (
                "the refusal echoed the value it refused: " + query)
        # Control: the lead time alone is the route's whole vocabulary.
        assert c.get("/api/cloudmap/at?ahead_s=900").status_code == 200


# ============================== no pointing is a state, not an error


def test_no_mount_is_a_stated_no_pointing(tmp_path, monkeypatch, no_mount):
    _store, app, _svc = _make(tmp_path, monkeypatch)
    with TestClient(app) as c:
        r = c.get("/api/cloudmap/at")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["basis"] == "no_pointing", body
    assert body["probability"] is None and body["pierce_lat_deg"] is None
    assert "no mount" in body["reason"], body["reason"]
    assert body["enabled"] is True and body["observed_at"] is not None


def test_a_disconnected_mount_is_no_mount(tmp_path, monkeypatch, no_mount):
    """The sim mount still answers `get_position` once disconnected, so this
    is the case that proves the route asks `connected` rather than relying on
    a dead driver to raise."""
    _store, app, _svc = _make(tmp_path, monkeypatch)
    tel = _sim_mount(0.0, SITE_LAT - 30.0)
    asyncio.run(tel.disconnect())
    with TestClient(app) as c:
        _install(monkeypatch, tel)
        r = c.get("/api/cloudmap/at")
    assert r.status_code == 200, r.text
    assert r.json()["basis"] == "no_pointing", r.json()
    assert "no mount" in r.json()["reason"]


class _Mute:
    """A connected mount whose position read fails, and counts its asks."""

    connected = True

    def __init__(self, exc: BaseException | None = None):
        self.exc = exc or RuntimeError("serial port wedged at 12.345 67.891")
        self.asked = 0

    async def get_position(self):
        self.asked += 1
        raise self.exc


def test_a_mount_that_will_not_answer_is_a_200_not_a_500(
        tmp_path, monkeypatch, no_mount):
    """The exception is named by its type and its TEXT is not echoed: a driver
    message can carry anything, including a position."""
    _store, app, _svc = _make(tmp_path, monkeypatch)
    mute = _Mute()
    with TestClient(app, raise_server_exceptions=False) as c:
        _install(monkeypatch, mute)
        r = c.get("/api/cloudmap/at")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["basis"] == "no_pointing", body
    assert "RuntimeError" in body["reason"], body["reason"]
    assert "12.345" not in r.text and "wedged" not in r.text, body["reason"]
    assert mute.asked == 1


def test_a_mount_below_the_horizon_is_a_200_not_a_400(tmp_path, monkeypatch,
                                                      no_mount):
    """Stage 4's domain is (0, 90]. The caller sent no altitude, so a mount
    parked low must not come back as the caller's domain error."""
    _store, app, _svc = _make(tmp_path, monkeypatch)
    tel = _sim_mount(12.0, 0.0)                # the anti-meridian, well down
    alt, _az = _expected_pointing(tel)
    assert alt < -10.0, "the fixture is no longer below the horizon"
    with TestClient(app) as c:
        _install(monkeypatch, tel)
        r = c.get("/api/cloudmap/at")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["basis"] == "no_pointing", body
    assert "below the horizon" in body["reason"], body["reason"]


def test_a_switched_off_map_does_not_read_the_mount(tmp_path, monkeypatch,
                                                    no_mount):
    """A feature that is off must not cost the serial link a transaction. The
    answer is the switched-off one, and the mount is never asked."""
    _store, app, _svc = _make(tmp_path, monkeypatch, enabled=False,
                              populate=False)
    mute = _Mute()
    with TestClient(app, raise_server_exceptions=False) as c:
        _install(monkeypatch, mute)
        r = c.get("/api/cloudmap/at")
    assert r.status_code == 200, r.text
    assert r.json()["basis"] == "no_data", r.json()
    assert r.json()["reason"] == "the cloud map is switched off"
    assert mute.asked == 0, "a switched-off cloud map read the mount"


# =========================================== the gate, on both forms


def test_a_role_without_view_weather_is_refused_on_both_forms(
        tmp_path, monkeypatch, no_mount):
    """The pierce point sits within 30 km of the rig, which is why every
    cloud-map route is behind view.weather and a viewer reaches none of them
    (the comment above the routes in app.py). The POST is a new door onto the
    same answer and the GET now spends a mount read, so both are held here: a
    viewer is refused on each and the mount is never asked, and an operator,
    the control, is answered on each.

    Nothing else held either gate. `test_no_route_here_is_reachable_without_a_
    session` refuses an ANONYMOUS caller, which a gate on view.status refuses
    just as well, and a viewer holds view.status.
    """
    from astrodeck.auth import (principal_for_role, reset_active_provider,
                                set_active_provider)
    from test_the_display_consumers_need_a_site import _Fixed

    _store, app, _svc = _make(tmp_path, monkeypatch)
    monkeypatch.setattr(app_module, "configure_provider_from_auth",
                        lambda _auth: app_module.get_active_provider())
    try:
        for role, want in (("viewer", 403), ("operator", 200)):
            set_active_provider(_Fixed(principal_for_role(role)))
            mute = _Mute()
            with TestClient(app, raise_server_exceptions=False) as c:
                _install(monkeypatch, mute)
                got = c.get("/api/cloudmap/at").status_code
                assert got == want, f"{role}: GET /api/cloudmap/at -> {got}"
                got = c.post("/api/cloudmap/at", json=PICKED).status_code
                assert got == want, f"{role}: POST /api/cloudmap/at -> {got}"
            assert mute.asked == (0 if want == 403 else 1), (
                f"{role}: the mount was asked {mute.asked} times")
    finally:
        reset_active_provider()


# ============================================================ the control


def test_a_posted_direction_is_answered_for_that_direction(
        tmp_path, monkeypatch, no_mount):
    """The picked-point form answers the direction in its body, echoes it (the
    caller chose it), and does not care where the mount is."""
    _store, app, svc = _make(tmp_path, monkeypatch)
    with TestClient(app) as c:
        _install(monkeypatch, _sim_mount(0.0, SITE_LAT - 30.0))
        for picked in (PICKED, {"alt": 30, "az": 90, "ahead_s": 900}):
            r = c.post("/api/cloudmap/at", json=picked)
            assert r.status_code == 200, r.text
            want = svc.at_payload(alt_deg=picked["alt"], az_deg=picked["az"],
                                  ahead_s=picked.get("ahead_s", 0), now=BASE)
            assert r.json() == want, r.json()
            assert r.json()["alt_deg"] == picked["alt"]
