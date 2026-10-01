"""The site picker's read-back is a POST; the GET is the stored site only (#520).

`/api/site/sky` answers the sun's altitude, tonight's dark window, a coarse
place label and the local sidereal time. Its no-parameter GET is what the
Weather conditions panel and the Monitor's vitals band read, and it stays
exactly as it was. The site picker (Settings > Site, and the Sky hub's Sites
sheet) used to ask the same route about the coordinates being TYPED, as
`GET /api/site/sky?lat=&lon=` - so every hop's access log wrote down the place
an admin was about to save. That question is a POST body now.

What this file holds:

  * the GET refuses lat/lon with 422, naming the parameters and never their
    values, even for an admin who may hold them;
  * the POST answers for the TYPED place, not the stored one;
  * the POST keeps the holder-only rule: an operator and a viewer are 403;
  * a NaN is a 422 at the body, not a confident answer for nowhere;
  * the control: the no-parameter GET still answers the stored site.

MUTATIONS RUN, 2026-09-29, each in a private scratch copy of server/
(scratchpad/H4-PRIV-mut in the session scratchpad) from a byte backup restored
with its sha256 checked. Output verbatim.

  M1 "accept and ignore lat/lon" - the `_refuse_site_query` call deleted from
  `site_sky`. 1 failed, test_the_get_refuses_named_coordinates_even_for_an_admin:
      AssertionError: lat=-33.9&lon=151.2 -> 200
      assert 200 == 422

  M2 "the preview answers for the stored site" - `site_sky_preview` passes
  the stored site's coordinates instead of the body's. 1 failed,
  test_the_post_answers_for_the_typed_place:
      AssertionError: the picker's read-back described some other place: N
      hemisphere · W longitude · ~N. America

  M3 "the preview is gated by view.status" - `site_sky_preview` declared and
  required under CAP_VIEW_STATUS. 1 failed,
  test_the_post_is_for_holders_of_view_site_precise:
      AssertionError: operator -> 200
      assert 200 == 403

  M4 "a NaN reaches the arithmetic" - `allow_inf_nan=False` dropped from
  `SiteSkyPreviewBody`. 1 failed, test_a_nan_is_a_422_not_a_500:
      AssertionError: {"lat":NaN,"lon":151.2} -> 200
      assert 200 == 422
  The 200 was not an error page: probed under the same mutant, the body was
  the Sun at 90.0 degrees and "S hemisphere · E longitude", a confident
  read-back for a place that does not exist.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
from astrodeck.auth import principal_for_role, reset_active_provider, set_active_provider

from test_the_display_consumers_need_a_site import _Fixed, _store

#: Not the stored site, and on the far side of the world from it, so an answer
#: computed from the wrong one cannot pass for the right one.
TYPED = {"lat": -33.9, "lon": 151.2}


def _client(tmp_path, monkeypatch, role: str, *, sited: bool = True,
            raise_server_exceptions: bool = True) -> TestClient:
    _store(tmp_path, monkeypatch, sited=sited)
    set_active_provider(_Fixed(principal_for_role(role)))
    monkeypatch.setattr(app_module, "configure_provider_from_auth",
                        lambda _auth: app_module.get_active_provider())
    return TestClient(app_module.create_app(),
                      raise_server_exceptions=raise_server_exceptions)


def teardown_function(_fn):
    reset_active_provider()


def test_the_get_refuses_named_coordinates_even_for_an_admin(tmp_path,
                                                             monkeypatch):
    """An admin may hold the site, and is refused anyway: the problem is not
    who is asking but that the question is written into a URL."""
    with _client(tmp_path, monkeypatch, "admin") as c:
        for query in ("lat=-33.9&lon=151.2", "lat=-33.9", "lon=151.2"):
            r = c.get("/api/site/sky?" + query)
            assert r.status_code == 422, query + " -> " + str(r.status_code)
            assert r.json()["detail"]["code"] == "site_query_refused", r.text
            assert "33.9" not in r.text and "151.2" not in r.text, (
                "the refusal echoed the coordinates it refused: " + query)


def test_the_get_without_parameters_answers_the_stored_site(tmp_path,
                                                            monkeypatch):
    """The control. SkyConditionsPanel and VitalsBand read exactly this."""
    with _client(tmp_path, monkeypatch, "admin") as c:
        body = c.get("/api/site/sky").json()
    for key in ("sun_alt_deg", "dark_window", "place_hint", "lst_str"):
        assert key in body, (key, body)
    assert body["place_hint"].endswith("~N. America"), body["place_hint"]


def test_the_post_answers_for_the_typed_place(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch, "admin") as c:
        r = c.post("/api/site/sky", json=TYPED)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["place_hint"] == "S hemisphere · E longitude · ~Australia / Oceania", (
        "the picker's read-back described some other place: "
        + body["place_hint"])
    assert "sun_alt_deg" in body and "lst_str" in body, body


def test_the_post_is_for_holders_of_view_site_precise(tmp_path, monkeypatch):
    """Naming a place and reading the sun back is a geolocation oracle: sweep
    candidates, keep whichever reproduces what you already see."""
    for role in ("operator", "viewer"):
        with _client(tmp_path, monkeypatch, role) as c:
            r = c.post("/api/site/sky", json=TYPED)
        assert r.status_code == 403, role + " -> " + str(r.status_code)
        reset_active_provider()


def test_a_nan_is_a_422_not_a_500(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch, "admin",
                 raise_server_exceptions=False) as c:
        for body in ('{"lat":NaN,"lon":151.2}', '{"lat":-33.9,"lon":Infinity}'):
            r = c.post("/api/site/sky", content=body,
                       headers={"content-type": "application/json"})
            assert r.status_code == 422, body + " -> " + str(r.status_code)
