"""API tests for /api/config/survey + /api/survey/pack* (offline-pack spec §5)."""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    from astrodeck.config import config_store
    monkeypatch.setattr(config_store, "_path", tmp_path / "astrodeck.json")
    monkeypatch.setattr(config_store, "_cfg", None)
    import astrodeck.catalog.survey_pack as sp
    monkeypatch.setattr(sp, "PACK_ROOT", tmp_path / "_survey_pack")
    sp.fetch_state.finish()
    from astrodeck.api import app as app_module
    app = app_module.create_app()
    with TestClient(app) as c:
        yield c


def test_get_pack_status_shape_absent(client):
    r = client.get("/api/survey/pack")
    assert r.status_code == 200
    body = r.json()
    assert body["present"] is False and body["fetching"] is None
    assert body["survey"] == "CDS/P/DSS2/color" and body["slug"] == "dss2color"


def test_config_survey_roundtrip(client):
    assert client.get("/api/config").json()["survey"] == {"online_fetch": False}
    r = client.post("/api/config/survey", json={"online_fetch": True})
    assert r.status_code == 200
    assert client.get("/api/config").json()["survey"] == {"online_fetch": True}


def test_fetch_starts_and_reports_already(client, monkeypatch):
    import astrodeck.catalog.survey_pack as sp
    started = {}
    def fake_start(order=4, slug="dss2color"):
        if started:
            raise sp.FetchAlreadyRunning()
        started["order"] = order
    monkeypatch.setattr(sp, "start_fetch", fake_start)
    r = client.post("/api/survey/pack/fetch", json={"order": 3})
    assert r.status_code == 202 and r.json() == {"started": True}
    assert started["order"] == 3
    r = client.post("/api/survey/pack/fetch", json={"order": 3})
    assert r.status_code == 200 and r.json() == {"started": False, "already": True}


def test_fetch_order_clamped(client, monkeypatch):
    import astrodeck.catalog.survey_pack as sp
    seen = {}
    monkeypatch.setattr(sp, "start_fetch",
                        lambda order=4, slug="dss2color": seen.setdefault("o", order))
    client.post("/api/survey/pack/fetch", json={"order": 99})
    assert seen["o"] == 6


def test_fetch_507_on_insufficient_space(client, monkeypatch):
    import astrodeck.catalog.survey_pack as sp
    def no_space(order=4, slug="dss2color"):
        raise sp.InsufficientSpace(free=1000, required=9999)
    monkeypatch.setattr(sp, "start_fetch", no_space)
    r = client.post("/api/survey/pack/fetch", json={})
    assert r.status_code == 507
    assert r.json()["detail"] == {"detail": "insufficient disk space",
                                  "free_bytes": 1000, "required_bytes": 9999}


def test_delete_pack_and_409_while_running(client, monkeypatch):
    import astrodeck.catalog.survey_pack as sp
    r = client.delete("/api/survey/pack")
    assert r.status_code == 200 and r.json() == {"deleted": False}
    assert sp.fetch_state.try_start(5)
    try:
        r = client.delete("/api/survey/pack")
        assert r.status_code == 409
    finally:
        sp.fetch_state.finish()
