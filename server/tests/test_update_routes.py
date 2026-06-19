"""API surface for self-update: status (read), check/apply/config (system.update)."""
import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
from astrodeck.config import ConfigStore
from astrodeck.update import github, signing
from astrodeck.update import service as SVC
from astrodeck.update.state import update_state

_, PUB = signing.generate_keypair()  # a real 32-byte base64 Ed25519 public key


@pytest.fixture(autouse=True)
def _clean_state():
    update_state.current = "0.1.0"
    update_state.set_available(None, "")
    update_state.set_phase("idle")
    update_state.set_result(None)
    SVC.reset_service()
    SVC.reset_exit_state()
    yield


@pytest.fixture
def client(tmp_path, monkeypatch):
    temp_store = ConfigStore(path=tmp_path / "astrodeck.json")
    import astrodeck.config as config_mod
    import astrodeck.hub as hub_mod
    monkeypatch.setattr(config_mod, "config_store", temp_store)
    monkeypatch.setattr(hub_mod, "config_store", temp_store)
    monkeypatch.setattr(app_module, "config_store", temp_store)
    monkeypatch.setattr(hub_mod, "CAPTURE_DIR", tmp_path / "captures")
    with TestClient(app_module.create_app()) as c:
        yield c, temp_store


def test_status_reports_unsupervised(client):
    c, _ = client
    r = c.get("/api/update/status")
    assert r.status_code == 200
    b = r.json()
    assert b["supervised"] is False
    assert b["can_apply"] is False
    assert b["current"]  # the running version


def test_apply_returns_409_when_blocked(client):
    c, _ = client
    r = c.post("/api/update/apply")
    assert r.status_code == 409  # not supervised / no update / no pubkey


def test_check_updates_status(client, monkeypatch):
    async def fake_latest(*a, **k):
        return github.ReleaseInfo("0.2.0", "v0.2.0", "release notes here",
                                  "http://a", "http://s", "http://g", False)
    monkeypatch.setattr("astrodeck.update.github.latest_release", fake_latest)
    c, _ = client
    r = c.post("/api/update/check")
    assert r.status_code == 200
    body = r.json()
    assert body["latest"] == "0.2.0"
    assert body["update_available"] is True
    assert body["notes_md"] == "release notes here"


def test_set_update_config(client):
    c, store = client
    r = c.post("/api/update/config",
               json={"channel": "prerelease", "signing_pubkey": PUB,
                     "repo": "epim/astrodeck"})
    assert r.status_code == 200
    assert r.json()["update"]["channel"] == "prerelease"
    assert store.cfg().update.signing_pubkey == PUB


def test_set_update_config_rejects_bad_channel(client):
    c, _ = client
    r = c.post("/api/update/config", json={"channel": "nope"})
    assert r.status_code == 400
