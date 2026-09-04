"""/api/catalog/tonight route (NOV-3)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from astrodeck.api.app import create_app
from astrodeck.config import ConfigStore, Site


@pytest.fixture
def client(tmp_path, monkeypatch):
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    store.set_site(Site(name="Mid", latitude=40.0, longitude=-74.0),
                   expected_version=None)
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
