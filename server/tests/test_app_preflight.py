"""API regression tests for the FIX-B backend changes.

- P1-3: a calibration-only plan (darks/bias/flats) must pass the below-horizon
  preflight at a *configured* site even when its mandatory dummy coords are
  below the horizon — it never slews, so the check is meaningless for it.
- P2-9: a malformed ``schema_version`` on import is a clean 422, not a 500.
- P0:   a client-supplied id for a NEW plan/profile is not trusted — the server
  mints its own uuid.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
from astrodeck.config import ConfigStore, Site
from astrodeck.devices.base import DeviceError


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Isolate config + plan/profile libraries to tmp so the test never touches
    # the real server/config tree.
    temp_store = ConfigStore(path=tmp_path / "astrodeck.json")
    import astrodeck.config as config_mod
    import astrodeck.hub as hub_mod
    monkeypatch.setattr(config_mod, "config_store", temp_store)
    monkeypatch.setattr(hub_mod, "config_store", temp_store)
    monkeypatch.setattr(app_module, "config_store", temp_store)

    from astrodeck.plans import PlanLibrary
    from astrodeck.profiles import ProfileLibrary
    plan_lib = PlanLibrary(directory=tmp_path / "plans")
    prof_lib = ProfileLibrary(directory=tmp_path / "profiles")
    monkeypatch.setattr(app_module, "plan_library", plan_lib)
    monkeypatch.setattr(app_module, "profiles", prof_lib)

    app = app_module.create_app()
    with TestClient(app) as c:
        yield c, temp_store


def _cal_plan_payload():
    return {
        "name": "darks",
        "guide": False,
        "targets": [{
            "name": "darks", "ra_hours": 0.0, "dec_deg": 0.0,
            "calibration": True,
            "steps": [{"exposure_s": 1.0, "count": 3, "frame_type": "Dark"}],
        }],
    }


def _light_plan_payload():
    p = _cal_plan_payload()
    p["name"] = "lights"
    p["targets"][0]["calibration"] = False
    p["targets"][0]["name"] = "M-below"
    return p


def test_calibration_plan_passes_preflight_at_configured_site(client, monkeypatch):
    c, store = client
    # Configure a real (non-default) site; set_site flips is_default off.
    store.set_site(Site(name="Backyard", latitude=51.5, longitude=-0.1),
                   expected_version=None)
    assert store.cfg().site.is_default is False

    # Force "below horizon at a configured site" deterministically: the hub's
    # own horizon check rejects the dummy (0,0) coords.
    def reject(ra, dec, *, force=False):
        raise DeviceError("target is below the visible horizon (alt -42°)")
    monkeypatch.setattr(app_module.hub, "_check_horizon", reject)

    # Capture engine.start so the preflight is exercised without running a real
    # sequence. require("camera") is also stubbed (no rig connected in the test).
    started = {"n": 0}
    monkeypatch.setattr(app_module.engine, "start",
                        lambda plan, **kw: started.__setitem__("n", started["n"] + 1))
    monkeypatch.setattr(app_module.hub, "require", lambda role: object())

    # A light-frame target with the same below-horizon coords must be 409'd —
    # proves the preflight is actually active for this site.
    r_light = c.post("/api/sequence/start", json=_light_plan_payload())
    assert r_light.status_code == 409
    assert r_light.json()["detail"]["code"] == "below_horizon"

    # The calibration-only plan must NOT be blocked — it reaches engine.start.
    r_cal = c.post("/api/sequence/start", json=_cal_plan_payload())
    assert r_cal.status_code == 200, r_cal.text
    assert r_cal.json() == {"started": True, "frames": 3}
    assert started["n"] == 1


def test_import_malformed_schema_version_is_422(client):
    c, _ = client
    for bad in ("abc", [9], {"x": 1}):
        r = c.post("/api/plans/import", json={"schema_version": bad,
                                              "name": "x", "plan": {}})
        assert r.status_code == 422, (bad, r.text)
        assert r.json()["detail"]["code"] == "invalid"


def test_save_plan_does_not_trust_client_id_for_new_record(client):
    c, _ = client
    payload = {
        "plan": {"name": "Traversal", "targets": [{
            "name": "M31", "ra_hours": 0.71, "dec_deg": 41.27,
            "steps": [{"exposure_s": 5.0, "count": 1}]}]},
        "id": "../../pwned",
    }
    r = c.post("/api/plans", json=payload)
    assert r.status_code == 200, r.text
    # the server minted a fresh uuid; the malicious id was discarded
    assert r.json()["id"] != "../../pwned"


def test_save_profile_does_not_trust_client_id_for_new_record(client):
    c, _ = client
    r = c.post("/api/profiles", json={"id": "../../pwned", "name": "Evil"})
    assert r.status_code == 200, r.text
    assert r.json()["id"] != "../../pwned"
