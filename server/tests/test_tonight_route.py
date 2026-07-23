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
    import astrodeck.config as config_mod
    import astrodeck.hub as hub_mod
    monkeypatch.setattr(config_mod, "config_store", store)
    monkeypatch.setattr(hub_mod, "config_store", store)
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
