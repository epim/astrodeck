"""The operator-facing dusk configuration, permissions and lifecycle."""
from types import SimpleNamespace as NS

import pytest
from fastapi.testclient import TestClient

from astrodeck.auth import principal_for_role, reset_active_provider
from astrodeck.config import ConfigStore, DuskConfig
from test_rbac_enforcement import _make_client, _install
import astrodeck.api.app as app_module


@pytest.fixture
def client(tmp_path, monkeypatch):
    store, app = _make_client(tmp_path, monkeypatch)
    with TestClient(app) as client:
        yield client, store
    reset_active_provider()


def test_config_persists_and_old_client_cooling_save_keeps_target(client):
    c, store = client
    assert c.get("/api/config").json()["dusk"]["enabled"] is False
    response = c.post("/api/config", json={
        "dusk": {"enabled": True, "profile_id": "saved-test-rig", "sun_alt_deg": -6},
        "cooling": {"setpoint_c": -10},
    })
    assert response.status_code == 200, response.text
    assert response.json()["cooling"]["setpoint_c"] == -10
    assert c.get("/api/dusk/state").json()["state"] != "disabled"
    reloaded = ConfigStore(path=store._path).cfg()
    assert reloaded.dusk == DuskConfig(enabled=True, profile_id="saved-test-rig", sun_alt_deg=-6)
    assert reloaded.cooling.setpoint_c == -10
    assert c.post("/api/config", json={"cooling": {"warm_rate_c_per_min": 3}}).status_code == 200
    assert store.cfg().cooling.setpoint_c == -10


def test_status_route_and_service_lifecycle(client):
    c, _ = client
    assert c.get("/api/dusk/state").status_code == 200
    assert c.get("/api/dusk/state").json()["state"] == "disabled"
    assert app_module.dusk_arm._task is not None
    assert not app_module.dusk_arm._task.done()


@pytest.mark.parametrize("role", ["viewer", "operator"])
def test_non_admin_cannot_enable_dusk(client, role):
    c, store = client
    _install(principal_for_role(role))
    assert c.get("/api/dusk/state").status_code == 200
    response = c.post("/api/config", json={"dusk": {"enabled": True, "profile_id": "test"}})
    assert response.status_code == 403
    assert not store.cfg().dusk.enabled


def test_invalid_threshold_rejects_whole_patch(client):
    c, store = client
    response = c.post("/api/config", json={"dusk": {"sun_alt_deg": 0}, "cooling": {"setpoint_c": -5}})
    assert response.status_code == 422
    assert store.cfg().cooling.setpoint_c is None


def test_new_work_is_refused_during_dusk_connection(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(app_module.dusk_arm, "connecting", True)
    assert app_module._lane_conflict("autofocus") == "dusk preparation"
    # Emergency lanes remain available.
    assert app_module._lane_conflict("park") != "dusk preparation"
    with pytest.raises(Exception, match="409"):
        app_module._refuse_if_camera_owned()


def test_engine_cannot_start_during_connection(client, monkeypatch):
    from astrodeck.sequence.models import SequencePlan
    from astrodeck.devices.base import DeviceError
    monkeypatch.setattr(app_module.dusk_arm, "connecting", True)
    with pytest.raises(DeviceError, match="dusk preparation"):
        app_module.engine.start(SequencePlan())


@pytest.mark.asyncio
async def test_resume_waits_before_any_recovery_motion(monkeypatch):
    from astrodeck.sequence.resume_arm import ResumeArm
    from astrodeck.sequence.models import SequencePlan
    import astrodeck.sequence.resume_arm as resume_mod
    session = NS(id="test-session", name="Test session", owed=lambda: 1,
                 crash_resumes=0, plan=SequencePlan())
    monkeypatch.setattr(resume_mod.session_store, "armed", lambda: session)
    engine = NS(running=False)
    hub = NS(dusk_arm=NS(resume_veto=lambda: "Camera is cooling"))
    resume = ResumeArm(engine, hub, clock=lambda: 100)
    monkeypatch.setattr(resume, "_window_open", lambda *args: True)
    # Readiness/recovery must not even be queried until dusk preparation is done.
    def unexpected(): raise AssertionError("recovery ran before preparation")
    monkeypatch.setattr(resume, "_devices_ready", unexpected)
    await resume.tick()
    assert resume.hold["reason"] == "Camera is cooling"
    assert resume._retry_at == 0
