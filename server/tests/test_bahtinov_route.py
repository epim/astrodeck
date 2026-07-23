"""NOV-12 API: POST /api/focuser/bahtinov/{start,stop} arm/disarm the aid and
flip status.bahtinov_active; start 409s when a sequence is running. Mirrors the
in-process app + sim-rig harness from test_autofocus_route.py."""
import time

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
from astrodeck.config import ConfigStore


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv(app_module.NO_AUTOCONNECT_ENV_VAR, "1")
    temp_store = ConfigStore(path=tmp_path / "astrodeck.json")
    import astrodeck.config as config_mod
    import astrodeck.hub as hub_mod
    from astrodeck.profiles import profiles as profile_lib
    monkeypatch.setattr(config_mod, "config_store", temp_store)
    monkeypatch.setattr(hub_mod, "config_store", temp_store)
    monkeypatch.setattr(app_module, "config_store", temp_store)
    monkeypatch.setattr(hub_mod, "CAPTURE_DIR", tmp_path / "captures")
    monkeypatch.setattr(profile_lib, "_dir", tmp_path / "profiles")

    app = app_module.create_app()
    with TestClient(app) as c:
        try:
            yield c
        finally:
            try:
                c.post("/api/disconnect")
            except Exception:
                pass


def test_start_arms_and_stop_disarms(client):
    assert client.post("/api/connect/sim").status_code == 200
    r = client.post("/api/focuser/bahtinov/start",
                    json={"exposure_s": 1.0, "gain": 100, "binning": 1, "tol_px": 1.5})
    assert r.status_code == 200, r.text
    assert r.json()["active"] is True
    assert client.get("/api/status").json()["bahtinov_active"] is True

    r = client.post("/api/focuser/bahtinov/stop")
    assert r.status_code == 200, r.text
    assert r.json()["active"] is False
    assert client.get("/api/status").json()["bahtinov_active"] is False


def test_start_conflicts_with_running_sequence(client, monkeypatch):
    assert client.post("/api/connect/sim").status_code == 200
    # Force the sequence engine to look "running" so the camera-busy guard fires.
    monkeypatch.setattr(type(app_module.engine), "running",
                        property(lambda self: True))
    r = client.post("/api/focuser/bahtinov/start", json={})
    assert r.status_code == 409, r.text
