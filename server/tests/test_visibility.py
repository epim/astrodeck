"""Visibility ephemeris (Owner D — Sky Atlas).

These exercise ``astrodeck.catalog.visibility`` directly (the pure ``compute_night``
+ the ``/api/visibility*`` router on a minimal app) so they are independent of
the survey/framing lanes that also register routers on the full ``create_app``.

Coverage (per the sub-batch brief):
  * a mid-latitude target produces a non-empty dark window + a finite transit;
  * a high-latitude June date falls back (``darkness_kind != "astronomical"``)
    and ``best_window`` is never null-with-a-blank-window;
  * ``ra_deg`` uses ``*15`` — a target at RA 12h / Dec 0 transits at LST ≈ 12h,
    i.e. near local solar midnight (a 15× slip would land a random field).
"""
from __future__ import annotations

import datetime as _dt
import math

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from astrodeck.catalog import visibility as vis
from astrodeck.catalog.coords import lst_hours
from astrodeck.config import ConfigStore, Site

# Deterministic sites (never the default, so the math is trusted).
MID_SITE = {"name": "Mid", "latitude": 40.0, "longitude": -74.0,
            "elevation_m": 0.0, "is_default": False, "horizon_min_deg": 15.0}
EQ_SITE = {"name": "Eq", "latitude": 40.0, "longitude": 0.0,
           "elevation_m": 0.0, "is_default": False, "horizon_min_deg": 15.0}
HILAT_SITE = {"name": "Tromso", "latitude": 69.6, "longitude": 18.9,
              "elevation_m": 0.0, "is_default": False, "horizon_min_deg": 15.0}


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A minimal app mounting only the visibility router. ``hub.site`` reads
    ``config_store.cfg().site``, so point that at an isolated store seeded with a
    configured mid-latitude site (matching how the rest of the suite isolates
    config — test_app_preflight.py)."""
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    store.set_site(Site(name="Mid", latitude=MID_SITE["latitude"],
                        longitude=MID_SITE["longitude"]), expected_version=None)
    import astrodeck.config as config_mod
    import astrodeck.hub as hub_mod
    monkeypatch.setattr(config_mod, "config_store", store)
    monkeypatch.setattr(hub_mod, "config_store", store)

    app = FastAPI()
    app.include_router(vis.router)
    with TestClient(app) as c:
        yield c


# --------------------------------------------------------------- pure compute

def test_mid_latitude_has_dark_window_and_finite_transit():
    # M31 (RA 0.71h, Dec +41.27) on a winter night — a textbook target.
    night = vis.compute_night(0.71, 41.27, date="2026-01-15", site=MID_SITE)

    assert night["darkness_kind"] == "astronomical"
    assert night["dark_start_unix"] is not None
    assert night["dark_end_unix"] is not None
    assert night["dark_end_unix"] > night["dark_start_unix"]

    # transit is a finite epoch with a real altitude (M31 ~89° from lat 40).
    assert math.isfinite(night["transit_unix"])
    assert -90.0 <= night["transit_alt"] <= 90.0
    assert night["transit_alt"] > 40.0

    # a high northern target is observable: a real best window + samples.
    assert night["never_rises_above_limit"] is False
    assert night["best_window"] is not None
    assert night["best_window"]["end_unix"] > night["best_window"]["start_unix"]
    assert len(night["samples"]) > 10
    s0 = night["samples"][0]
    assert {"t_unix", "alt", "moon_alt", "sun_alt"} <= set(s0)

    # moon block is fully populated.
    moon = night["moon"]
    assert 0.0 <= moon["illumination"] <= 1.0
    assert moon["phase_name"]
    assert 0.0 <= moon["separation_deg"] <= 180.0


def test_ra_uses_times_15_transit_near_local_midnight():
    # RA 12h / Dec 0 from lon 0: transit happens when LST ≈ RA = 12h. Near the
    # spring equinox the sun's RA is ~0h, so LST≈12h falls at local solar
    # midnight. A 15× hours-vs-degrees slip would transit a different field
    # entirely (and the LST-at-transit check would fail).
    night = vis.compute_night(12.0, 0.0, date="2026-03-20", site=EQ_SITE,
                              alt_limit=20.0)
    assert night["transit_in_daylight"] is False
    lst = lst_hours(0.0, night["transit_unix"])
    # LST at transit must equal the RA (12h) to within a sample step (~0.2h).
    diff = min((lst - 12.0) % 24.0, (12.0 - lst) % 24.0)
    assert diff < 0.3, f"LST at transit {lst:.3f}h != 12h (ra*15 slip?)"


def test_high_latitude_june_falls_back_without_blank_window():
    # Tromsø in June: the sun never reaches −18° (or even −12°) → no astro/
    # nautical dark. The ladder must fall back yet still return a usable window.
    night = vis.compute_night(18.0, 30.0, date="2026-06-21", site=HILAT_SITE,
                              alt_limit=20.0)
    assert night["darkness_kind"] != "astronomical"
    # never a null-with-blank window: a darkest-interval fallback is provided.
    assert night["dark_start_unix"] is not None
    assert night["dark_end_unix"] is not None
    assert night["dark_end_unix"] > night["dark_start_unix"]
    # best_window, when present, is a real interval (never start==end).
    if night["best_window"] is not None:
        assert night["best_window"]["end_unix"] > night["best_window"]["start_unix"]


def test_below_limit_target_is_flagged_and_has_no_window():
    # A deep-southern object from a northern site never clears the alt limit.
    night = vis.compute_night(6.0, -60.0, date="2026-01-15", site=MID_SITE,
                              alt_limit=30.0)
    assert night["never_rises_above_limit"] is True
    assert night["best_window"] is None


def test_moon_phase_name_respects_waxing_sign():
    assert vis._moon_phase_name(0.3, True) == "Waxing Crescent"
    assert vis._moon_phase_name(0.3, False) == "Waning Crescent"
    assert vis._moon_phase_name(0.7, True) == "Waxing Gibbous"
    assert vis._moon_phase_name(0.7, False) == "Waning Gibbous"
    assert vis._moon_phase_name(0.005, True) == "New Moon"
    assert vis._moon_phase_name(0.995, False) == "Full Moon"


def test_moon_factor_gates_on_moon_altitude():
    # A bright moon that has SET (moon_up False) imposes no penalty.
    assert vis._moon_factor(10.0, 0.95, moon_up=False) == 1.0
    # A bright moon close by while UP penalises (but is clamped ≥0.3).
    near = vis._moon_factor(5.0, 0.95, moon_up=True)
    far = vis._moon_factor(110.0, 0.95, moon_up=True)
    assert 0.3 <= near < far <= 1.0


# --------------------------------------------------------------- routes

def test_get_visibility_route(client):
    r = client.get("/api/visibility",
                   params={"ra": 0.71, "dec": 41.27, "date": "2026-01-15"})
    assert r.status_code == 200, r.text
    night = r.json()
    assert night["alt_limit_deg"] == vis.DEFAULT_ALT_LIMIT
    assert night["dark_start_unix"] is not None
    assert math.isfinite(night["transit_unix"])
    assert night["samples"]


def test_order_route_is_group_atomic(client):
    # Two M31 panels (a group) + one single target. The group's panels must stay
    # contiguous in recommended_order and never be interleaved with the single.
    body = {
        "date": "2026-01-15",
        "targets": [
            {"name": "M31 1-1", "ra_hours": 0.71, "dec_deg": 41.27,
             "mosaic_group": "M31"},
            {"name": "Vega", "ra_hours": 18.6, "dec_deg": 38.78},
            {"name": "M31 1-2", "ra_hours": 0.75, "dec_deg": 41.27,
             "mosaic_group": "M31"},
        ],
    }
    r = client.post("/api/visibility/order", json=body)
    assert r.status_code == 200, r.text
    data = r.json()
    assert len(data["targets"]) == 3
    order = data["recommended_order"]
    assert sorted(order) == [0, 1, 2]
    # positions of the two M31 panels (original indices 0 and 2) are adjacent.
    pos = {idx: rank for rank, idx in enumerate(order)}
    assert abs(pos[0] - pos[2]) == 1, f"M31 panels interleaved: {order}"


def test_order_empty_targets(client):
    r = client.post("/api/visibility/order", json={"targets": []})
    assert r.status_code == 200, r.text
    assert r.json() == {"targets": [], "recommended_order": []}


def test_get_visibility_out_of_range_dec_is_422_not_500(client):
    # |dec|>90 must be rejected at the pydantic boundary (422), never reach
    # astropy (which raises a bare ValueError that would 500 with a trace leak).
    r = client.get("/api/visibility", params={"ra": 0.71, "dec": 9999})
    assert r.status_code == 422, r.text


def test_order_bad_target_is_422_not_500(client):
    # One out-of-range target must 422 the whole request at the model boundary,
    # not 500 the entire asyncio.gather batch.
    body = {
        "date": "2026-01-15",
        "targets": [
            {"name": "ok", "ra_hours": 0.71, "dec_deg": 41.27},
            {"name": "bad", "ra_hours": 0.71, "dec_deg": 9999},
        ],
    }
    r = client.post("/api/visibility/order", json=body)
    assert r.status_code == 422, r.text


def test_tonight_anchor_morning_resolves_to_just_passed_midnight(monkeypatch):
    # An early-morning (03:00 local) request must anchor on the midnight that just
    # passed (~3h ago), NOT the next night's midnight (~21h ahead). lon=0 so local
    # solar time == UTC; pick a "now" that is 03:00 UTC.
    now_0300 = _dt.datetime(
        2026, 6, 15, 3, 0, 0, tzinfo=_dt.timezone.utc).timestamp()
    monkeypatch.setattr(vis.time, "time", lambda: now_0300)

    anchor = vis._night_anchor_unix(None, 0.0)
    # the just-passed midnight is 3h (10800s) before now; the next one would be
    # 21h ahead. Assert we got the former.
    assert anchor == pytest.approx(now_0300 - 3 * 3600.0, abs=1.0)
    assert anchor < now_0300


@pytest.mark.parametrize("bad", [
    "not-a-date",        # not remotely a date
    "2026-13-45",        # well-formed shape, impossible month/day
    "2026-02-30",        # impossible day for a real month
    "20260724",          # fromisoformat would accept this; the wire format is dashed
    "2026-7-4",          # unpadded — not the YYYY-MM-DD the UI's date input emits
])
def test_bad_date_is_422_not_a_silent_answer_for_tonight(client, bad):
    """A malformed date used to be SWALLOWED by _night_anchor_unix's
    try/except, which fell through to the "tonight" branch — so
    ?date=2026-13-45 returned tonight's sky presented as the caller's
    requested night. An off-by-one month in any client produced entirely
    plausible numbers for the wrong date, which is worse than an error.

    Covers both surfaces this router owns: the GET query param and the POST
    body (validated by pydantic there). /api/catalog/tonight is the third
    caller-supplied-date surface and lives on the full app, so it is gated in
    test_tonight_route.py instead — this fixture mounts the visibility router
    only."""
    r = client.get("/api/visibility",
                   params={"ra": 0.71, "dec": 41.27, "date": bad})
    assert r.status_code == 422, r.text

    r = client.post("/api/visibility/order", json={
        "date": bad,
        "targets": [{"name": "ok", "ra_hours": 0.71, "dec_deg": 41.27}]})
    assert r.status_code == 422, r.text


@pytest.mark.parametrize("good", [None, "", "2026-01-15", "2026-12-31"])
def test_absent_or_valid_date_still_works(client, good):
    """The other half of the rule: omitted and empty both mean "tonight" (a
    cleared date input posts ""), and a real date is accepted unchanged. Without
    this, a stricter check could 422 the ordinary no-date request."""
    params = {"ra": 0.71, "dec": 41.27}
    if good is not None:
        params["date"] = good
    r = client.get("/api/visibility", params=params)
    assert r.status_code == 200, r.text
