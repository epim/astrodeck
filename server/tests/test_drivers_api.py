"""Driver API routes (equipment-drivers spec §3.1/§3.2): CRUD + probe + the
merged describe surface. In-process via TestClient against a temp ConfigStore
(repo convention — see test_connect_api.py)."""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from astrodeck.config import config_store
    monkeypatch.setattr(config_store, "_path", tmp_path / "astrodeck.json")
    monkeypatch.setattr(config_store, "_cfg", None)   # force fresh load from tmp
    import astrodeck.drivers as drv
    drv.invalidate()
    from astrodeck.api import app as app_module
    app = app_module.create_app()
    with TestClient(app) as c:
        yield c


def test_drivers_crud_roundtrip(client):
    r = client.post("/api/config/drivers",
                    json={"type": "nina", "host": "astrotown.lan"})
    assert r.status_code == 200
    d = r.json()["driver"]
    assert d["id"].startswith("nina-") and d["port"] == 1888

    r = client.patch(f"/api/config/drivers/{d['id']}", json={"enabled": False})
    assert r.status_code == 200 and r.json()["driver"]["enabled"] is False

    r = client.delete(f"/api/config/drivers/{d['id']}")
    assert r.status_code == 200 and r.json()["deleted"] == d["id"]


def test_drivers_validation_and_404(client):
    assert client.post("/api/config/drivers",
                       json={"type": "asiair", "host": "h"}).status_code == 422
    assert client.post("/api/config/drivers",
                       json={"type": "nina", "host": "  "}).status_code == 422
    assert client.patch("/api/config/drivers/nope",
                        json={"port": 2}).status_code == 404
    assert client.delete("/api/config/drivers/nope").status_code == 404
    assert client.post("/api/drivers/nope/probe").status_code == 404


def test_get_drivers_returns_roles_and_implicit_rows(client):
    r = client.get("/api/drivers")
    assert r.status_code == 200
    body = r.json()
    assert "camera" in body["roles"]
    ids = {d["id"] for d in body["drivers"]}
    assert {"sim", "astrodeck", "astap"} <= ids
    sim = next(d for d in body["drivers"] if d["id"] == "sim")
    assert sim["implicit"] is True and sim["status"]["reachable"] is True


def test_probe_route_accepts_implicit_ids(client):
    assert client.post("/api/drivers/sim/probe").status_code == 200
