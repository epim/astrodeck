# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The last display-side site consumers ask whether there is a site (#24).

Four functions that computed from the site without asking. Two were already
unreachable at a default site through their only caller and are guarded again
because the next caller may not refuse first; two were live:

  * `/api/site/sky` (LIVE) - with no overrides, an operator or admin at an
    unset site was served the sun's altitude and tonight's dark window for
    0,0. The Weather and Monitor hubs draw the dark band from it. Now withheld,
    which is exactly what a viewer already receives, so every consumer handles
    it. Named coordinates (the site picker, admin-only) still answer: that is a
    question about a place asked on purpose.
  * `comets.row` (LIVE on the batch path) - `position()` goes geocentric at an
    unset site so the alt/az branch is skipped, but the batch path passes its
    own `_reason`, and a None there stamped an alt/az for 0,0.
  * `guided.sky_context` and `CloudmapService._refresh` - both callers refuse
    a default site first.

MUTATIONS RUN, and what each printed:

  M1, delete the `if lat is None and lon is None and not site_is_set(s)`
  return in `site_sky`. 2 failed: the unsited operator and admin both got
  `sun_alt_deg` and `dark_window` for 0,0. (Since #520 the GET takes no
  coordinates and the line is `if not site_is_set(s)`; the picker's named
  point is `site_sky_preview`, a POST. Re-run against the new line on
  2026-09-29, in a scratch copy: the same 2 failed, the unsited operator on
  `assert ('sun_alt_deg' not in {'dark_window': {...}, 'sun_alt_deg': ...})`.)
  M2, delete the `if latlon is not None` in `comets.row` (compute from the
  raw site). 1 failed: `alt` was present.
  M3, delete the `raise ValueError` in `sky_context`. 1 failed: a sun
  altitude came back.
  M4, delete the `return` in `_refresh`. 1 failed: the fetch stub was called.

THE CLOUDMAP CONTROL WAS VACUOUS ON ITS FIRST RUN. `Site(...)` defaults
`is_default` to True, so the "real site" it built was an unsaved one and the
refresh returned at the new guard - the control failed, which is what a
control is for. Every hand-built `Site` in a test that means a saved site has
to say `is_default=False`.
"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
from astrodeck.auth import principal_for_role, reset_active_provider, set_active_provider
from astrodeck.config import ConfigStore, Site

DEFAULT_SITE = {"latitude": 0.0, "longitude": 0.0, "elevation_m": 0.0,
                "is_default": True}


class _Fixed:
    name = "fake"

    def __init__(self, p):
        self._p = p

    async def resolve(self, request):
        return self._p


def _store(tmp_path, monkeypatch, *, sited):
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    if sited:
        store.set_site(Site(name="Somewhere", latitude=40.0, longitude=-74.0,
                            elevation_m=10.0), expected_version=None)
    else:
        store.cfg().site = Site(name="", latitude=0.0, longitude=0.0,
                                elevation_m=0.0, is_default=True)
    import astrodeck.config as config_mod
    import astrodeck.hub as hub_mod
    for mod in (config_mod, hub_mod, app_module):
        monkeypatch.setattr(mod, "config_store", store)
    monkeypatch.setattr(app_module, "configure_provider_from_auth",
                        lambda _auth: app_module.get_active_provider())
    return store


@pytest.fixture
def sky(tmp_path, monkeypatch):
    """``named`` is the site picker's typed point.

    RE-PINNED FOR #520, DELIBERATELY. It used to be a ``query`` string appended
    to the GET (``?lat=40&lon=-74``); a URL is written down by every hop it
    crosses, so the picker's question is a POST body now. What each case below
    asserts about the answer is unchanged."""
    def _get(role, *, sited, named=None):
        _store(tmp_path, monkeypatch, sited=sited)
        set_active_provider(_Fixed(principal_for_role(role)))
        try:
            with TestClient(app_module.create_app()) as c:
                if named is not None:
                    return c.post("/api/site/sky", json=named)
                return c.get("/api/site/sky")
        finally:
            reset_active_provider()
    return _get


# ------------------------------------------------------------ /api/site/sky


def test_an_unsited_operator_is_not_served_the_gulf_of_guineas_sun(sky):
    r = sky("operator", sited=False)
    assert r.status_code == 200
    body = r.json()
    assert "sun_alt_deg" not in body and "dark_window" not in body, body


def test_an_unsited_admin_is_not_served_it_either(sky):
    body = sky("admin", sited=False).json()
    for k in ("sun_alt_deg", "dark_window", "place_hint", "lst_str"):
        assert k not in body, (k, body)


def test_a_sited_operator_still_gets_the_sky(sky):
    """The control."""
    body = sky("operator", sited=True).json()
    assert "sun_alt_deg" in body and "dark_window" in body, body


def test_the_site_picker_can_still_ask_about_a_place(sky):
    """Named coordinates are a deliberate question, not a default. The picker
    is how an unsited rig GETS a site, so refusing it here would be circular."""
    body = sky("admin", sited=False, named={"lat": 40, "lon": -74}).json()
    assert "sun_alt_deg" in body, body


# ------------------------------------------------------------ comets.row


def test_a_comet_row_has_no_horizon_without_a_site(tmp_path, monkeypatch):
    from astrodeck.catalog.ephemeris import comets as C
    _store(tmp_path, monkeypatch, sited=False)
    p = {"ra_hours": 3.0, "dec_deg": 20.0, "mag": 9.0, "r_au": 1.2,
         "delta_au": 0.8, "when_unix": time.time(), "topocentric": True,
         "geocentric_reason": None}
    monkeypatch.setattr(C, "position", lambda el, when, site_derived=True: p)
    out = C.row({"name": "2P/Encke"}, time.time())
    assert "alt" not in out and "az" not in out, out


def test_a_comet_row_keeps_its_horizon_at_a_site(tmp_path, monkeypatch):
    from astrodeck.catalog.ephemeris import comets as C
    _store(tmp_path, monkeypatch, sited=True)
    p = {"ra_hours": 3.0, "dec_deg": 20.0, "mag": 9.0, "r_au": 1.2,
         "delta_au": 0.8, "when_unix": time.time(), "topocentric": True,
         "geocentric_reason": None}
    monkeypatch.setattr(C, "position", lambda el, when, site_derived=True: p)
    out = C.row({"name": "2P/Encke"}, time.time())
    assert "alt" in out and "az" in out, out


# ------------------------------------------------------------ guided


def test_guided_sky_context_refuses_the_default():
    from astrodeck.guided import sky_context
    with pytest.raises(ValueError, match="no observing site"):
        sky_context(DEFAULT_SITE, time.time())


def test_guided_sky_context_answers_at_a_site():
    from astrodeck.guided import sky_context
    ra, dec, alt = sky_context({"latitude": 40.0, "longitude": -74.0}, time.time())
    assert -90.0 <= alt <= 90.0


# ------------------------------------------------------------ cloudmap


async def test_the_cloudmap_does_not_fetch_for_the_atlantic(monkeypatch):
    from astrodeck.cloudmap import service as S
    fetched = []

    async def _latest_ref(*a, **k):
        fetched.append(a)
        return None
    monkeypatch.setattr(S, "latest_ref", _latest_ref)
    svc = S.CloudmapService()
    cfg = type("C", (), {})()
    site = Site(name="", latitude=0.0, longitude=0.0, elevation_m=0.0,
                is_default=True)
    await svc._refresh(cfg, site, time.time())
    assert fetched == [], "a refresh fetched granules for a site nobody saved"


async def test_the_cloudmap_fetches_for_a_real_site(monkeypatch):
    """The control: the same call at a saved site reaches the listing."""
    from astrodeck.cloudmap import service as S
    fetched = []

    async def _latest_ref(*a, **k):
        fetched.append(a)
        return None
    monkeypatch.setattr(S, "latest_ref", _latest_ref)
    monkeypatch.setattr(S, "_resolved_platform", lambda ccfg: "G19")
    svc = S.CloudmapService()
    site = Site(name="x", latitude=40.0, longitude=-74.0, elevation_m=10.0,
                is_default=False)      # the model defaults to True: an unsaved site
    try:
        await svc._refresh(object(), site, time.time())
    except Exception:       # noqa: BLE001 - what follows the listing is not graded
        pass
    assert fetched, "the control never reached the listing"
