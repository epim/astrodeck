"""Rotator route contracts: 409 without device, exposure refusal, reverse
gating, range-mapped move response. Uses the drivers-api client fixture and
manipulates the app module's hub directly (routes read the module-global hub
at call time)."""
import pytest
from fastapi.testclient import TestClient

from astrodeck.devices.sim import SimCamera, SimRig, SimRotator


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from astrodeck.config import config_store
    monkeypatch.setattr(config_store, "_path", tmp_path / "astrodeck.json")
    monkeypatch.setattr(config_store, "_cfg", None)
    import astrodeck.drivers as drv
    drv.invalidate()
    from astrodeck.api import app as app_module
    app = app_module.create_app()
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def hub():
    from astrodeck.api import app as app_module
    return app_module.hub   # adjust to the module's actual hub symbol if named differently


@pytest.fixture()
def rot(hub):
    rig = SimRig()
    r = SimRotator(rig)
    r.connected = True          # SimRotator.connect() only flips this flag
    hub.devices["rotator"] = r
    # rotate-to-pa requires BOTH rotator and camera (it solves to discover PA),
    # so a connected sim camera must be present too or hub.require("camera")
    # 409s before the route ever reaches hub.rotate_to_pa.
    cam = SimCamera(rig)
    cam.connected = True
    hub.devices["camera"] = cam
    yield r
    hub.devices.pop("rotator", None)
    hub.devices.pop("camera", None)
    hub._busy.pop("rotator", None)
    hub._busy.pop("rotate_to_pa", None)


def test_move_409_without_rotator(client):
    r = client.post("/api/rotator/move", json={"position_deg": 90.0})
    assert r.status_code == 409
    assert "rotator" in r.json()["detail"]


def test_move_refused_while_exposing(client, hub, rot, monkeypatch):
    class Locked:
        def locked(self):
            return True
    monkeypatch.setattr(hub, "_capture_lock", Locked())
    monkeypatch.setattr(hub, "_capture_busy", "sequence exposure", raising=False)
    r = client.post("/api/rotator/move", json={"position_deg": 90.0})
    assert r.status_code == 409
    assert "busy" in r.json()["detail"]


def test_move_returns_range_mapped_target(client, hub, rot):
    from astrodeck.config import RotatorConfig, config_store
    config_store.set_rotator(RotatorConfig(range_type="quarter",
                                           range_start_deg=0.0,
                                           tolerance_deg=1.0))
    r = client.post("/api/rotator/move", json={"position_deg": 100.0})
    assert r.status_code == 200
    body = r.json()
    assert body["adjusted"] is True
    assert body["target_deg"] == pytest.approx(10.0)   # QUARTER map: 100→10
    assert body["started"] == "rotator"


def test_reverse_400_when_unsupported(client, hub, rot):
    r = client.post("/api/rotator/reverse", json={"reverse": True})
    assert r.status_code == 400


def test_halt_ok(client, hub, rot):
    r = client.post("/api/rotator/halt")
    assert r.status_code == 200


def test_rotate_to_pa_spawns(client, hub, rot, monkeypatch):
    async def instant(self, *a, **k):
        return {"rotated": True}
    monkeypatch.setattr(type(hub), "rotate_to_pa", instant)
    r = client.post("/api/rotator/rotate-to-pa", json={"target_pa_deg": 120.0})
    assert r.status_code == 200
    assert r.json() == {"started": "rotate_to_pa"}
