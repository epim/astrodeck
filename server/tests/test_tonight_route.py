# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""/api/catalog/tonight route (NOV-3).

#1257: the route computes the night for the whole catalogue in one vectorised
pass and keeps it per site, night and altitude limit, in process memory. It
took 87 s on a dev machine and 106 to 109 s on CI, one ``compute_night`` per
row. The tests here hold what the route owes:

  * it never calls ``compute_night`` per row (fast form of the time bound);
  * it is not slower than a fifth of what the per-row loop would cost on THIS
    machine right now (the calibrated time bound);
  * the same question twice computes once, and each input of the night
    (altitude limit, date, catalogue) asks again;
  * a changed site returns the NEW site's list, and changing back returns the
    first;
  * its list, order and ties equal the per-row answer on a slice of the
    catalogue;
  * ``alt_limit`` is bounded (a 422 for NaN and out of range);
  * no log record, cache repr or answer carries the site.

MUTATIONS RUN (WP 7.8 report): the per-row loop restored in the route goes red
in ``test_the_route_never_calls_compute_night`` at once and in
``test_the_route_does_not_pay_per_row`` after the loop finishes; the cache key
without the site goes red in ``test_a_site_change_returns_the_new_sites_list``.
"""
from __future__ import annotations

import json
import statistics
import time

import pytest
from fastapi.testclient import TestClient

from astrodeck.api.app import create_app
from astrodeck.catalog import visibility as vis
from astrodeck.config import ConfigStore, Site

DATE = "2026-01-15"


@pytest.fixture(autouse=True)
def _cold_nights():
    with vis._nights_lock:
        vis._nights_cache.clear()
    yield
    with vis._nights_lock:
        vis._nights_cache.clear()


@pytest.fixture
def store(tmp_path):
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    store.set_site(Site(name="Mid", latitude=40.0, longitude=-74.0),
                   expected_version=None)
    return store


@pytest.fixture
def client(store, monkeypatch):
    import astrodeck.api.app as app_module
    import astrodeck.config as config_mod
    import astrodeck.hub as hub_mod
    monkeypatch.setattr(config_mod, "config_store", store)
    monkeypatch.setattr(hub_mod, "config_store", store)
    # app.py bound the singleton by name at import, so create_app() would
    # otherwise install its auth provider from the worker-shared store: any
    # earlier test that saved a real method turns every request here into
    # a 401 (seen once in a parallel run; reproduced by saving an admin_token
    # into the shared store first).
    monkeypatch.setattr(app_module, "config_store", store)
    with TestClient(create_app()) as c:
        yield c


def test_tonight_ranks_and_tags(client):
    r = client.get("/api/catalog/tonight", params={"date": "2026-01-15"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["date"] and isinstance(data["picks"], list) and data["picks"]
    # ranked best-first: scores are non-increasing.
    scores = [p["score"] for p in data["picks"]]
    assert scores == sorted(scores, reverse=True)
    # every pick carries a difficulty tag + the tonight visibility fields.
    p = data["picks"][0]
    assert p["difficulty"] in {"easy", "moderate", "hard"}
    assert {"max_alt", "transit_unix", "moon_sep_deg",
            "never_rises_above_limit", "surface_brightness"} <= set(p)
    # a curated override survives to the wire.
    horse = next(x for x in data["picks"] if x["id"] == "IC 434")
    assert horse["difficulty"] == "hard" and horse["difficulty_source"] == "curated"


@pytest.mark.parametrize("bad", ["not-a-date", "2026-13-45", "20260724"])
def test_tonight_bad_date_is_422_not_a_silent_answer_for_tonight(client, bad):
    """Third caller-supplied-date surface (siblings gated in test_visibility.py).

    A malformed date was swallowed by _night_anchor_unix's try/except and fell
    through to the "tonight" branch, so the whole ranked catalog came back for
    TONIGHT while `data["date"]` echoed the caller's requested night — a silent
    wrong answer across every pick."""
    r = client.get("/api/catalog/tonight", params={"date": bad})
    assert r.status_code == 422, r.text


# ----------------------------------------------------------------- #1257

@pytest.fixture
def small_catalogue(monkeypatch):
    """Every 45th row: about 300 rows spread over the sky, so a test that is not
    about cost does not pay for 13,370."""
    import astrodeck.catalog.objects as objects_mod
    rows = objects_mod.CATALOG[::45]
    monkeypatch.setattr(objects_mod, "CATALOG", rows)
    return rows


def _tonight(client, **params):
    r = client.get("/api/catalog/tonight", params={"date": DATE, **params})
    assert r.status_code == 200, r.text
    return r.json()


def test_the_route_never_calls_compute_night(client, small_catalogue,
                                             monkeypatch):
    """The per-row loop is the defect. Raising on the first call makes the
    restored loop fail in a second, not after its 90."""
    def refuse(*_a, **_k):
        raise AssertionError("catalog_tonight called compute_night per row")

    monkeypatch.setattr(vis, "compute_night", refuse)
    assert _tonight(client)["picks"]


def test_the_route_does_not_pay_per_row(client):
    """The calibrated time bound. A cold request (no cached night, no scaffold)
    over the whole catalogue must cost under a fifth of what the per-row loop
    would, measured on this machine at this moment from up to 30 single
    ``compute_night`` calls. A slow or loaded runner slows both numbers, so the
    ratio holds where an absolute bound would not: the vectorised route is
    about 0.015 of the loop, the loop itself is 1.0."""
    from astrodeck.catalog.objects import CATALOG
    with vis._scaffold_lock:
        vis._scaffold_cache.clear()
    t0 = time.perf_counter()
    answer = _tonight(client)
    t_route = time.perf_counter() - t0
    assert len(answer["picks"]) == len(CATALOG)

    stride = max(1, len(CATALOG) // 30)
    spent = []
    for o in CATALOG[::stride][:30]:
        t0 = time.perf_counter()
        vis.compute_night(o.ra_hours, o.dec_deg, date=DATE, step_min=20,
                          alt_limit=30.0)
        spent.append(time.perf_counter() - t0)
    loop_s = statistics.median(spent) * len(CATALOG)
    assert t_route < loop_s / 5, (
        f"a cold request took {t_route:.2f} s; the per-row loop would take "
        f"about {loop_s:.0f} s here (ratio {t_route / loop_s:.3f}, bound 0.2)")


def test_a_second_request_computes_nothing(client, small_catalogue,
                                           monkeypatch):
    builds = []
    real = vis._nights_at
    monkeypatch.setattr(vis, "_nights_at",
                        lambda *a, **k: builds.append(1) or real(*a, **k))
    first = _tonight(client)
    assert _tonight(client) == first and len(builds) == 1
    _tonight(client, alt_limit=20.0)
    assert len(builds) == 2, "another altitude limit is another answer"
    _tonight(client, date="2026-01-16")
    assert len(builds) == 3, "another night is another answer"


def test_a_site_change_returns_the_new_sites_list(client, store, small_catalogue):
    first = _tonight(client)
    # Same longitude, so the night's anchor is unchanged: only the site key can
    # tell the two lists apart.
    store.set_site(Site(name="South", latitude=-33.0, longitude=-74.0),
                   expected_version=None)
    second = _tonight(client)

    by_id = {p["id"]: p for p in first["picks"]}
    moved = [p for p in second["picks"] if p["max_alt"] != by_id[p["id"]]["max_alt"]]
    assert len(moved) > len(second["picks"]) // 2, (
        "the list after a site change is the old site's")
    assert [p["id"] for p in second["picks"]] != [p["id"] for p in first["picks"]]

    from astrodeck.hub import hub
    for p in second["picks"][:5]:
        night = vis.compute_night(p["ra_hours"], p["dec_deg"], date=DATE,
                                  step_min=20, alt_limit=30.0, site=hub.site)
        assert p["max_alt"] == night["transit_alt"]

    store.set_site(Site(name="Mid", latitude=40.0, longitude=-74.0),
                   expected_version=None)
    assert _tonight(client) == first, "going back must give the first site's list"


def test_the_list_order_and_ties_equal_the_per_row_answer(client, small_catalogue):
    """The route's whole answer against the OLD route's loop, on a slice. Equal
    scores and magnitudes keep catalogue order in both: ``rank_picks`` is a
    stable sort over picks built in catalogue order."""
    from astrodeck.catalog.difficulty import difficulty_for
    from astrodeck.catalog.objects import _TYPE_NAMES
    from astrodeck.catalog.tonight import rank_picks, tonight_score

    expected = []
    for o in small_catalogue:
        night = vis.compute_night(o.ra_hours, o.dec_deg, date=DATE,
                                  step_min=20, alt_limit=30.0)
        d = difficulty_for(o.id, o.mag, o.size_arcmin)
        bw = night["best_window"]
        expected.append({
            "id": o.id, "name": o.name, "type": _TYPE_NAMES[o.type],
            "ra_hours": o.ra_hours, "dec_deg": o.dec_deg,
            "mag": o.mag, "size_arcmin": o.size_arcmin,
            "difficulty": d["tier"], "surface_brightness": d["surface_brightness"],
            "difficulty_source": d["source"],
            "max_alt": night["transit_alt"], "transit_unix": night["transit_unix"],
            "best_window": ({"start_unix": bw["start_unix"],
                             "end_unix": bw["end_unix"]} if bw else None),
            "moon_sep_deg": night["moon"]["separation_deg"],
            "never_rises_above_limit": night["never_rises_above_limit"],
            "score": tonight_score(night),
        })
    expected = json.loads(json.dumps(rank_picks(expected)))

    answer = _tonight(client)
    ties = len({(p["score"], p["mag"]) for p in expected})
    assert ties < len(expected), "premise: the slice has ties on (score, mag)"
    assert answer["picks"] == expected
    assert answer["date"] == DATE and answer["site_is_default"] is False


@pytest.mark.parametrize("bad", ["nan", "inf", "-inf", "90.5", "-91"])
def test_alt_limit_out_of_range_is_a_422(client, small_catalogue, bad):
    r = client.get("/api/catalog/tonight", params={"date": DATE, "alt_limit": bad})
    assert r.status_code == 422, r.text


@pytest.mark.parametrize("edge", ["90", "-90", "0"])
def test_alt_limit_in_range_is_answered(client, small_catalogue, edge):
    assert _tonight(client, alt_limit=edge)["picks"]


def test_no_log_record_cache_repr_or_answer_carries_the_site(
        client, store, small_catalogue, caplog):
    import logging
    store.set_site(Site(name="Private", latitude=47.6789, longitude=-88.2468,
                        elevation_m=123.456), expected_version=None)
    caplog.set_level(logging.DEBUG)
    r = client.get("/api/catalog/tonight", params={"date": DATE})
    assert r.status_code == 200
    shown = "\n".join((caplog.text, r.text, repr(vis._nights_cache),
                       str(list(vis._nights_cache.keys()))))
    for digits in ("47.6789", "88.2468", "123.456"):
        assert digits not in shown, "the site reached a log, a repr or the answer"
