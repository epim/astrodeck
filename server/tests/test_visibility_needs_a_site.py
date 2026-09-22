"""Tonight's visibility is not computed for a site nobody saved (#24).

Every number `catalog/visibility.py` produces - dark window, altitude curve,
transit, best window, moon separation - is f(site). At the 0,0 default each was
a confident answer for the Gulf of Guinea, in a response shaped exactly like a
correct one: `/api/visibility` drew an altitude chart for somewhere else,
`/api/visibility/order` ordered a plan by it, and the mosaic stamped each panel
with a transit altitude nobody could see was invented.

There is no degraded form worth returning - strip the site-derived fields and
nothing is left - so the module raises `NoSite`, the two routes answer 409
`no_site` (the same refusal `/api/catalog/tonight` gives), and the mosaic, which
already reports a failure per panel, says so per panel.

MUTATIONS RUN, and what each printed:

  M1, delete the `if latlon is None: raise NoSite` in `compute_night` AND the
  one in `_site_location` - back to reading the dict raw. 4 failed: the
  pure-compute case (a night came back), both routes (200), the mosaic (a
  transit_alt was stamped).

  M2, delete only the check in `compute_night`. 0 failed - `_site_location`,
  called two lines later, refuses too, and that is by design: it is the base
  every computation is built on. Recorded because it means the check in
  `compute_night` is belt-and-braces for the longitude read above it, not the
  load-bearing one.

  M3, delete the `except NoSite` in `get_visibility`. 1 failed: the route
  case, with a 500 instead of a 409.

  M4, delete the `except NoSite` in `post_order`. 1 failed: the order case,
  500.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from astrodeck.catalog import visibility as vis
from astrodeck.config import ConfigStore, Site

DEFAULT_SITE = {"name": "", "latitude": 0.0, "longitude": 0.0,
                "elevation_m": 0.0, "is_default": True}
MID_SITE = {"name": "Mid", "latitude": 40.0, "longitude": -74.0,
            "elevation_m": 0.0, "is_default": False}


def _store(tmp_path, monkeypatch, *, sited: bool):
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    if sited:
        store.set_site(Site(name="Mid", latitude=40.0, longitude=-74.0),
                       expected_version=None)
    else:
        store.cfg().site = Site(name="", latitude=0.0, longitude=0.0,
                                elevation_m=0.0, is_default=True)
    import astrodeck.config as config_mod
    import astrodeck.hub as hub_mod
    monkeypatch.setattr(config_mod, "config_store", store)
    monkeypatch.setattr(hub_mod, "config_store", store)
    assert store.cfg().site.is_default is (not sited), "fixture missed its state"
    return store


def _client(app_router):
    app = FastAPI()
    app.include_router(app_router)
    return TestClient(app)


# ---------------------------------------------------------------- the module


def test_no_night_is_computed_for_the_default_site():
    with pytest.raises(vis.NoSite, match="no observing site"):
        vis.compute_night(0.71, 41.27, date="2026-01-15", site=DEFAULT_SITE)


def test_a_saved_site_still_gets_its_night():
    """The control: the refusal is about the default, not about the call."""
    night = vis.compute_night(0.71, 41.27, date="2026-01-15", site=MID_SITE)
    assert night["transit_alt"] > 0


# ---------------------------------------------------------------- the routes


def test_the_visibility_route_refuses(tmp_path, monkeypatch):
    _store(tmp_path, monkeypatch, sited=False)
    with _client(vis.router) as c:
        r = c.get("/api/visibility", params={"ra": 0.71, "dec": 41.27})
    assert r.status_code == 409, (r.status_code, r.text[:200])
    detail = r.json()["detail"]
    assert detail["code"] == "no_site", detail
    assert "Settings" in detail["detail"], detail


def test_the_order_route_refuses(tmp_path, monkeypatch):
    _store(tmp_path, monkeypatch, sited=False)
    with _client(vis.router) as c:
        r = c.post("/api/visibility/order", json={"targets": [
            {"name": "M31", "ra_hours": 0.71, "dec_deg": 41.27}]})
    assert r.status_code == 409, (r.status_code, r.text[:200])
    assert r.json()["detail"]["code"] == "no_site"


def test_the_routes_answer_at_a_saved_site(tmp_path, monkeypatch):
    """The control for both refusals."""
    _store(tmp_path, monkeypatch, sited=True)
    with _client(vis.router) as c:
        assert c.get("/api/visibility",
                     params={"ra": 0.71, "dec": 41.27}).status_code == 200
        assert c.post("/api/visibility/order", json={"targets": [
            {"name": "M31", "ra_hours": 0.71, "dec_deg": 41.27}]}
        ).status_code == 200


# ---------------------------------------------------------------- the mosaic


def test_the_mosaic_says_why_a_panel_has_no_altitude(tmp_path, monkeypatch):
    """The mosaic already reports a failure per panel rather than dropping it
    (framing.py's own history), so the refusal arrives as a reason a person
    can act on rather than as a missing number."""
    from astrodeck.catalog import framing
    _store(tmp_path, monkeypatch, sited=False)
    with _client(framing.router) as c:
        r = c.post("/api/framing/mosaic", json={
            "ra_hours": 0.71, "dec_deg": 41.27, "rows": 1, "cols": 2,
            "overlap": 0.2, "rotation_deg": 0.0,
            "fov_x_deg": 1.0, "fov_y_deg": 0.7, "date": "2026-01-15"})
    assert r.status_code == 200, r.text[:200]
    for p in r.json()["panels"]:
        assert "transit_alt" not in p or p["transit_alt"] is None, p
        assert "no observing site" in p.get("transit_alt_error", ""), p
