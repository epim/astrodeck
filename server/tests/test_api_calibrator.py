"""POST /api/calibrator/* manual flat-panel control (PRO-5). Mirrors
test_api_mount.py: a real create_app() + TestClient with hub.require
monkeypatched to a tiny fake calibrator (no full sim connect for a route-shape
test)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
from astrodeck.auth import reset_active_provider
from astrodeck.devices.base import DeviceError


@pytest.fixture(autouse=True)
def _reset_provider_after():
    yield
    reset_active_provider()


@pytest.fixture
def client():
    app = app_module.create_app()
    with TestClient(app) as c:
        yield c


class _Panel:
    """Minimal fake CoverCalibrator: records on/off/cover calls."""

    connected = True
    has_cover = True

    def __init__(self):
        self.calls: list[str] = []

    async def calibrator_on(self, brightness: int) -> None:
        self.calls.append(f"on:{brightness}")

    async def calibrator_off(self) -> None:
        self.calls.append("off")

    async def open_cover(self) -> None:
        self.calls.append("open")

    async def close_cover(self) -> None:
        self.calls.append("close")


class _RefusingPanel(_Panel):
    async def calibrator_on(self, brightness: int) -> None:
        raise DeviceError("no calibrator connected")


def test_calibrator_routes(client, monkeypatch):
    panel = _Panel()
    monkeypatch.setattr(app_module.hub, "require", lambda role: panel)
    assert client.post("/api/calibrator/on", json={"brightness": 100}).status_code == 200
    assert client.post("/api/calibrator/off").status_code == 200
    assert client.post("/api/calibrator/cover", json={"open": True}).status_code == 200
    assert panel.calls == ["on:100", "off", "open"]


def test_calibrator_device_error_maps_to_409(client, monkeypatch):
    monkeypatch.setattr(app_module.hub, "require", lambda role: _RefusingPanel())
    r = client.post("/api/calibrator/on", json={"brightness": 50})
    assert r.status_code == 409
