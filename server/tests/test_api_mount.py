"""POST /api/mount/tracking_rate + hub status wiring (multi-rate mount
tracking, 2026-07-21, plan Task 4).

Route tests mirror test_move_watchdog.py's pattern: a real ``create_app()`` +
TestClient, with ``hub.require`` monkeypatched to a tiny fake telescope --
avoids standing up a whole sim/connect flow for a route-shape test. The
viewer-403 check mirrors test_rbac_enforcement.py's local FakeAuthProvider
(no unittest.mock, no real OIDC)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
from astrodeck.auth import (Principal, principal_for_role, reset_active_provider,
                            set_active_provider)
from astrodeck.devices.base import DeviceError


@pytest.fixture(autouse=True)
def _reset_provider_after():
    """Every test ends with the open (admin) default restored."""
    yield
    reset_active_provider()


@pytest.fixture
def client():
    app = app_module.create_app()
    with TestClient(app) as c:
        yield c


class FakeAuthProvider:
    """Returns a FIXED principal for every request. Installed via
    ``set_active_provider`` (mirrors test_rbac_enforcement.py)."""

    name = "fake"

    def __init__(self, principal: "Principal | None"):
        self._principal = principal

    async def resolve(self, request):
        return self._principal


class _Tel:
    """Minimal fake telescope: records set_tracking_rate calls."""

    connected = True

    def __init__(self):
        self.set_calls: list[str] = []

    async def set_tracking_rate(self, rate: str) -> None:
        self.set_calls.append(rate)


class _RefusingTel(_Tel):
    """A mount whose set_tracking_rate always fails device-side."""

    async def set_tracking_rate(self, rate: str) -> None:
        raise DeviceError("mount refused the rate change")


def test_valid_rate_calls_device_and_returns_rate(client, monkeypatch):
    tel = _Tel()
    monkeypatch.setattr(app_module.hub, "require", lambda role: tel)
    r = client.post("/api/mount/tracking_rate", params={"rate": "lunar"})
    assert r.status_code == 200, r.text
    assert r.json() == {"tracking_rate": "lunar"}
    assert tel.set_calls == ["lunar"]


def test_unknown_rate_is_422_and_never_reaches_device(client, monkeypatch):
    tel = _Tel()
    monkeypatch.setattr(app_module.hub, "require", lambda role: tel)
    r = client.post("/api/mount/tracking_rate", params={"rate": "king"})
    assert r.status_code == 422
    assert tel.set_calls == []


def test_device_error_maps_to_409(client, monkeypatch):
    monkeypatch.setattr(app_module.hub, "require", lambda role: _RefusingTel())
    r = client.post("/api/mount/tracking_rate", params={"rate": "solar"})
    assert r.status_code == 409


def test_route_declares_reaches_and_cap():
    """The endpoint carries the RBAC markers the boot assertion + this plan
    require: gated by control.mount and declared to reach
    Telescope.set_tracking_rate (mirrors the existing /api/mount/tracking
    route's markers)."""
    from astrodeck.auth import CAP_CONTROL_MOUNT
    from astrodeck.auth.rbac import CAP_ATTR, REACHES_ATTR

    app = app_module.create_app()
    route = next(r for r in app.routes
                if getattr(r, "path", None) == "/api/mount/tracking_rate")
    assert "POST" in route.methods
    caps = getattr(route.endpoint, CAP_ATTR)
    reaches = getattr(route.endpoint, REACHES_ATTR)
    assert caps == {CAP_CONTROL_MOUNT}
    assert reaches == {"Telescope.set_tracking_rate"}


def test_viewer_principal_is_403(client):
    set_active_provider(FakeAuthProvider(principal_for_role("viewer")))
    r = client.post("/api/mount/tracking_rate", params={"rate": "lunar"})
    assert r.status_code == 403


# ------------------------------------------------------------- hub status

async def test_hub_status_includes_tracking_rate_when_connected():
    from astrodeck.devices.sim import SimRig, SimTelescope
    from astrodeck.hub import Hub

    hub = Hub()
    tel = SimTelescope(SimRig())
    await tel.connect()
    hub.devices["telescope"] = tel
    status = await hub.poll_status()
    assert status["mount"]["tracking_rate"] == "sidereal"
    assert status["mount"]["can_set_tracking_rate"] is True
    await hub.disconnect_all()
