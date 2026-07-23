"""PRO-1 calibration API: build/list/delete masters + the preflight coverage
fold-in (a ``no_calibration`` warning appended to the EXISTING plan pre-flight
surface — NOT a new route). Isolated app: config + CAPTURE_DIR to tmp."""
from __future__ import annotations

import numpy as np
import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
from astrodeck.config import ConfigStore
from astrodeck.devices.base import CameraFrame
from astrodeck.imaging.fitsio import save_fits


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv(app_module.NO_AUTOCONNECT_ENV_VAR, "1")
    temp_store = ConfigStore(path=tmp_path / "astrodeck.json")
    import astrodeck.config as config_mod
    import astrodeck.hub as hub_mod
    monkeypatch.setattr(config_mod, "config_store", temp_store)
    monkeypatch.setattr(hub_mod, "config_store", temp_store)
    monkeypatch.setattr(app_module, "config_store", temp_store)
    cap = tmp_path / "captures"
    monkeypatch.setattr(hub_mod, "CAPTURE_DIR", cap)
    app = app_module.create_app()
    with TestClient(app) as c:
        yield c, cap


def _dark(cap, i, exp=300.0, temp=-10.0):
    f = CameraFrame(data=np.full((16, 16), 100 + i, np.uint16), exposure_s=exp,
                    gain=100, offset=30, binning=1, bayer_pattern=None,
                    temperature_c=temp, timestamp=1_772_000_000.0 + i)
    return save_fits(f, cap / f"dark_{i}.fits", frame_type="Dark")


def _light_plan() -> dict:
    return {
        "name": "n", "cool_to": -10.0,
        "targets": [{
            "name": "M31", "ra_hours": 0.7, "dec_deg": 41.0,
            "steps": [{"filter": "Ha", "exposure_s": 300.0, "gain": 100,
                       "offset": 30, "binning": 1, "count": 10}],
        }],
    }


def test_masters_empty_then_build(env):
    c, cap = env
    assert c.get("/api/calibration/masters").json() == []
    for i in range(3):
        _dark(cap, i)
    r = c.post("/api/calibration/build")
    assert r.status_code == 200, r.text
    assert r.json()["masters_built"] == 1
    assert len(c.get("/api/calibration/masters").json()) == 1


def test_preflight_warns_missing_calibration(env):
    c, cap = env
    # A dark master exists but no flat -> a filtered light step warns.
    for i in range(3):
        _dark(cap, i)
    assert c.post("/api/calibration/build").status_code == 200
    r = c.post("/api/sequence/preflight", json=_light_plan())
    assert r.status_code == 200, r.text
    kinds = [w["kind"] for w in r.json()["warnings"]]
    assert "no_calibration" in kinds


def test_preflight_empty_library_never_nags(env):
    c, _ = env
    # Zero masters -> coverage() returns [] -> no no_calibration warning (D4).
    r = c.post("/api/sequence/preflight", json=_light_plan())
    assert r.status_code == 200
    kinds = [w["kind"] for w in r.json()["warnings"]]
    assert "no_calibration" not in kinds


def test_delete_removes_master(env):
    c, cap = env
    for i in range(3):
        _dark(cap, i)
    assert c.post("/api/calibration/build").status_code == 200
    mid = c.get("/api/calibration/masters").json()[0]["id"]
    r = c.delete(f"/api/calibration/masters/{mid}")
    assert r.status_code == 200 and r.json()["deleted"] == mid
    assert c.get("/api/calibration/masters").json() == []


def test_delete_bad_id_is_404(env):
    c, _ = env
    # A drive-prefixed id ("C:...") is a single valid path segment (so it reaches
    # the handler) that safe_id_path refuses on ANY platform -> KeyError -> 404.
    # No file outside the masters dir is ever touched.
    r = c.delete("/api/calibration/masters/C:evil")
    assert r.status_code == 404, r.text
