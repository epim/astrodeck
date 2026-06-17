"""API tests for POST /api/profiles/{id}/activate (Stage B, api/app.py).

Activating a profile sets it active AND connects its rig through the pinned hub
``connect_profile_id`` path (spawned as the ``profile`` named task, streamed over
the WS). These tests assert the happy path (connect + active pointer + the
profile is marked active in the list), the 404 for a missing id, and the 409
guard when a run is in progress without ``force``.

Repo convention: ``TestClient`` against a temp ConfigStore + tmp profiles dir, no
real rig (sim is hostless), no ``unittest.mock``. Boot auto-connect is OFF.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
from astrodeck.config import ConfigStore
from astrodeck.profiles import Profile


class _FakeRunningTask:
    """A stand-in task that reports as still-running, so ``engine.running`` /
    ``hub.looping`` flip True without launching a real coroutine."""

    def done(self) -> bool:
        return False


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
            yield c, temp_store, profile_lib
        finally:
            try:
                c.post("/api/disconnect")
            except Exception:
                pass


def _save_sim_profile(profile_lib, name="Sim Rig") -> str:
    """Persist an empty sim profile (primary_backend defaults to sim) and return
    its id."""
    prof = Profile(name=name, primary_backend="sim")
    profile_lib.save(prof)
    return prof.id


def _wait_active(store, pid, c) -> None:
    """The activate route spawns the connect as the ``profile`` named task (it runs
    on the TestClient's event loop). Poll the persisted active pointer until the
    task has written it, driving the loop by issuing cheap requests so the
    background task gets scheduled."""
    import time
    for _ in range(200):
        if store.cfg().active_profile_id == pid:
            return
        # a cheap request gives the TestClient loop a chance to run the task.
        c.get("/api/status")
        time.sleep(0.02)


def test_activate_connects_and_sets_active(client):
    c, store, profile_lib = client
    pid = _save_sim_profile(profile_lib)

    r = c.post(f"/api/profiles/{pid}/activate")
    assert r.status_code == 200, r.text
    assert r.json().get("started") == "profile"

    _wait_active(store, pid, c)

    # active pointer persisted by connect_profile_id after a successful connect.
    assert store.cfg().active_profile_id == pid
    # the profile list marks it active.
    rows = c.get("/api/profiles").json()
    row = next(p for p in rows if p["id"] == pid)
    assert row.get("active") is True
    # a sim rig actually came up.
    assert c.get("/api/status").json().get("mode") == "sim"


def test_activate_missing_profile_is_404(client):
    c, _, _ = client
    r = c.post("/api/profiles/does-not-exist/activate")
    assert r.status_code == 404, r.text


def test_activate_while_running_without_force_is_409(client, monkeypatch):
    c, _, profile_lib = client
    pid = _save_sim_profile(profile_lib)
    # flip the engine into a "running" state without launching a real sequence.
    monkeypatch.setattr(app_module.engine, "_task", _FakeRunningTask())
    assert app_module.engine.running is True

    r = c.post(f"/api/profiles/{pid}/activate")
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "running"
