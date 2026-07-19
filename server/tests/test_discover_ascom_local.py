"""COM-T1: GET /api/discover/ascom-local returns the registry-enumerated
drivers (role-tagged offers). Enumeration is monkeypatched so the route is
provable on any OS (no real registry). Repo convention: in-process via
TestClient against a temp ConfigStore (see test_connect_api.py /
test_drivers_api.py) — the literal route carries
``dependencies=[Depends(require(CAP_VIEW_STATUS))]`` which passes open under
the default ``none`` auth provider used by these fixtures.
"""
import pytest
from fastapi.testclient import TestClient

import astrodeck.devices.ascom_registry as reg


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from astrodeck.config import config_store
    monkeypatch.setattr(config_store, "_path", tmp_path / "astrodeck.json")
    monkeypatch.setattr(config_store, "_cfg", None)   # force fresh load from tmp
    from astrodeck.api import app as app_module
    app = app_module.create_app()
    with TestClient(app) as c:
        yield c


def test_discover_ascom_local_returns_offers(client, monkeypatch):
    monkeypatch.setattr(reg, "enumerate_offers", lambda: [
        {"role": "camera", "name": "Sim Cam", "dev_type": "camera",
         "dev_num": 0, "progid": "ASCOM.Simulator.Camera"},
        {"role": "telescope", "name": "Sim Scope", "dev_type": "telescope",
         "dev_num": 0, "progid": "ASCOM.Simulator.Telescope"},
    ])
    r = client.get("/api/discover/ascom-local")
    assert r.status_code == 200
    body = r.json()
    assert {"role", "dev_type", "dev_num", "progid"} <= set(body[0])
    assert body[0]["progid"] == "ASCOM.Simulator.Camera"


def test_discover_ascom_local_empty_off_windows(client, monkeypatch):
    monkeypatch.setattr(reg, "enumerate_offers", lambda: [])
    r = client.get("/api/discover/ascom-local")
    assert r.status_code == 200
    assert r.json() == []
