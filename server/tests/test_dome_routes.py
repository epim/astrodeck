"""PRO-4 Task 7 — /api/dome/state + /api/dome/close routes.

Mirrors test_app_route_concurrency.py's harness: a real ``create_app()`` +
TestClient over a live sim rig (which now connects a SimDome), polling the app
loop via ``/api/status`` so the spawned park-and-close background task runs.
"""
from __future__ import annotations

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
    monkeypatch.setattr(config_mod, "config_store", temp_store)
    monkeypatch.setattr(hub_mod, "config_store", temp_store)
    monkeypatch.setattr(app_module, "config_store", temp_store)
    monkeypatch.setattr(hub_mod, "CAPTURE_DIR", tmp_path / "captures")

    app = app_module.create_app()
    with TestClient(app) as c:
        try:
            yield c
        finally:
            try:
                c.post("/api/disconnect")
            except Exception:
                pass


def _wait(predicate, c, tries=400) -> bool:
    for _ in range(tries):
        if predicate():
            return True
        c.get("/api/status")     # give the app loop a chance to run bg tasks
        time.sleep(0.02)
    return predicate()


def test_state_reports_open_on_a_connected_sim_rig(client):
    assert client.post("/api/connect/sim").status_code == 200
    r = client.get("/api/dome/state")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["connected"] is True
    assert body["shutter"] == "open"
    assert body["requires_park_before_close"] is True
    assert body["can_slave"] is False


def test_state_honest_when_no_dome(client):
    # No rig connected (NO_AUTOCONNECT) → no dome in hub.devices.
    r = client.get("/api/dome/state")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["connected"] is False
    assert body["shutter"] == "unknown"


def test_close_parks_then_closes(client):
    assert client.post("/api/connect/sim").status_code == 200
    r = client.post("/api/dome/close")
    assert r.status_code == 200, r.text
    # The close moved to its OWN lane (2026-08-05). It ran in "goto" because it
    # parks the mount first, which is real mount motion — but that made the roof
    # report itself as a slew, so "Close roof now" was dead during every
    # unrelated goto. The exclusion is preserved by _LANE_SUPERSEDES; see
    # test_busy_lanes_routes.py for both halves of it.
    assert r.json().get("started") == "dome"
    # the park-and-close background task drives the shutter to closed.
    closed = _wait(
        lambda: client.get("/api/dome/state").json().get("shutter") == "closed",
        client)
    assert closed, client.get("/api/dome/state").json()


def test_close_with_no_dome_is_4xx(client):
    # No rig connected → no dome → DeviceError → 409.
    r = client.post("/api/dome/close")
    assert r.status_code == 409, r.text


def test_route_declares_reaches_and_caps():
    from astrodeck.auth import CAP_CONTROL_MOUNT, CAP_VIEW_STATUS
    from astrodeck.auth.rbac import CAP_ATTR, REACHES_ATTR

    app = app_module.create_app()
    close = next(r for r in app.routes
                 if getattr(r, "path", None) == "/api/dome/close")
    assert "POST" in close.methods
    assert getattr(close.endpoint, CAP_ATTR) == {CAP_CONTROL_MOUNT}
    assert getattr(close.endpoint, REACHES_ATTR) == {
        "Dome.close_shutter", "Telescope.park"}

    state = next(r for r in app.routes
                 if getattr(r, "path", None) == "/api/dome/state")
    assert getattr(state.endpoint, CAP_ATTR) == {CAP_VIEW_STATUS}
