"""Guiding Assistant end-to-end on the sim rig + refusal/route paths
(design 2026-07-24 §5 tests #4/#5).

* ONE sim-rig e2e: seed drift/PE, run the assistant, assert a plausible report +
  non-empty recommendations, then round-trip the recommended values into a
  persisted ``GuideConfig`` (the same map the UI ``buildApplyBody`` -> PUT
  /api/guide/settings performs).
* Refusal paths (parametrized): refuses while guiding / parked (guider level);
  409 no-guider, 400 non-native, 409 already-guiding (route level).
* The ``OutOfRoom`` edge guard halts a near-edge Phase-B walk cleanly (SAFETY
  GUARD 1).
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
import astrodeck.config as config_mod
from astrodeck.config import ConfigStore, GuideConfig, GuideAxisParams
from astrodeck.devices.base import DeviceError
from astrodeck.guide import assistant as ga
from astrodeck.providers import NATIVE_AVAILABLE

native_only = pytest.mark.skipif(not NATIVE_AVAILABLE, reason="native wheel absent")


# ------------------------------------------------------------- apply round-trip
def _apply_recs_to_guide_config(report: dict) -> GuideConfig:
    """Mirror the UI ``buildApplyBody`` field map (design §4.4) on the Python
    side: fold the NON-advanced recommendations onto a GuideConfig so the PUT
    round-trip can be asserted against the persisted config."""
    by_field = {r["field"]: r for r in report["recommendations"] if not r["advanced"]}
    ra = GuideAxisParams()
    dec = GuideAxisParams()
    if "min_move" in by_field:
        ra.min_move = by_field["min_move"]["recommended"]
        dec.min_move = by_field["min_move"]["recommended"]
    if "aggression" in by_field:
        ra.aggression = by_field["aggression"]["recommended"]
    if "hysteresis" in by_field:
        ra.hysteresis = by_field["hysteresis"]["recommended"]
    return GuideConfig(
        ra_algorithm=by_field["ra_algorithm"]["recommended"],
        dec_algorithm=by_field["dec_algorithm"]["recommended"],
        blc_pulse_ms=int(by_field["blc_pulse_ms"]["recommended"]),
        ra_params=ra, dec_params=dec)


@native_only
async def test_assistant_e2e_on_sim(tmp_path, monkeypatch):
    from astrodeck.devices.sim import build_sim_rig
    from astrodeck.guide.native import NativeGuider

    store = ConfigStore(path=tmp_path / "astrodeck.json")
    monkeypatch.setattr(config_mod, "config_store", store)

    rig = build_sim_rig()
    cam = rig["guide_camera"]
    tel = rig["telescope"]
    await cam.connect()
    await tel.connect()
    # seed a real drift + keep the default periodic error so Phase A has signal.
    rig["_rig"].guide_drift_px_s = 0.2
    # config scale MUST match the rig's guide plate scale so the declared-rate
    # backlash math is self-consistent (both 1.0"/px here).
    g = NativeGuider(cam, tel, config={"image_scale_arcsec": 1.0,
                                       "image_scale_known": True,
                                       "exposure_s": 0.1}, profile_id=None)
    await g.connect()

    report = await asyncio.wait_for(
        g.run_guiding_assistant({"duration_s": 20}), timeout=60.0)

    # --- plausible report ---
    m = report["measurements"]
    assert m["n"] >= 20
    assert m["rms_total_px"] >= 0.0
    bl = m["backlash"]
    assert bl["result_code"] in (
        ga.BL_VALID, ga.BL_TOO_FEW_NORTH, ga.BL_TOO_FEW_SOUTH,
        ga.BL_NOT_CLEARED, ga.BL_SANITY)
    assert bl["y_rate_source"] in ("declared", "calibration")
    assert report["recommendations"], "recommendations must be non-empty"
    assert report["polar"]["verdict"]
    assert report["samples"]                       # feeds GuideScatter/GuideGraph
    # the cached report is the same object the GET route serves.
    assert g.run_assistant_report() is report

    # --- apply round-trips into GuideConfig (buildApplyBody -> PUT) ---
    applied = _apply_recs_to_guide_config(report)
    out = store.set_guide(applied)
    seed = next(r for r in report["recommendations"] if r["field"] == "blc_pulse_ms")
    mm = next(r for r in report["recommendations"] if r["field"] == "min_move")
    assert out.guide.blc_pulse_ms == int(seed["recommended"])
    assert out.guide.ra_params.min_move == pytest.approx(mm["recommended"])
    assert out.guide.ra_algorithm == "hysteresis"

    await g.disconnect()


# ------------------------------------------------------------- guider refusals
@native_only
@pytest.mark.parametrize("setup, needle", [
    ("guiding", "stop guiding"),
    ("parked", "unpark"),
])
async def test_assistant_refuses_outside_exclusive_window(setup, needle):
    from astrodeck.devices.sim import build_sim_rig
    from astrodeck.guide.native import NativeGuider

    rig = build_sim_rig()
    cam = rig["guide_camera"]
    tel = rig["telescope"]
    await cam.connect()
    await tel.connect()
    g = NativeGuider(cam, tel, config={"image_scale_arcsec": 1.0}, profile_id=None)
    await g.connect()

    if setup == "guiding":
        g._active = True                           # pretend a guide loop owns us
    elif setup == "parked":
        rig["_rig"].parked = True

    with pytest.raises(DeviceError) as ei:
        await g.run_guiding_assistant({"duration_s": 20})
    assert needle in str(ei.value)
    await g.disconnect()


# ------------------------------------------------------------- edge guard (§8)
def test_backlash_edge_guard_halts_before_walking_off_frame():
    """SAFETY GUARD 1: a star already within ``margin`` px of a frame edge yields
    an immediate clean ``done`` (no north pulse that would push it further off),
    and the run is flagged ``halted`` -> compute() reports TOO_FEW_NORTH."""
    run = ga.BacklashRun(0.008, 0.0, frame_w=640, frame_h=480, margin=15)
    cmd = run.step(320.0, 472.0, 0.0)              # y=472 is within 15 of 480
    assert cmd.kind == "done"                      # never pulses toward the edge
    res = run.compute()
    assert res.halted is True
    assert res.result_code == ga.BL_TOO_FEW_NORTH


# ------------------------------------------------------------- route paths
@pytest.fixture
def client():
    app = app_module.create_app()
    with TestClient(app) as c:
        yield c


class _FakeGuider:
    connected = True

    def __init__(self, *, native: bool, active: bool = False,
                 report: dict | None = None):
        self._active = active
        self._report = report
        if native:
            self.run_guiding_assistant = self._run
            self.run_assistant_report = lambda: self._report
            self.stop_guiding_assistant = lambda: None

    async def _run(self, opts=None, on_progress=None):
        return {"ok": True}

    async def is_active(self):
        return self._active


def test_start_409_when_no_guider(client, monkeypatch):
    monkeypatch.setattr(app_module.hub, "guider", None)
    r = client.post("/api/guide/assistant/start", json={})
    assert r.status_code == 409


def test_start_400_on_non_native_guider(client, monkeypatch):
    monkeypatch.setattr(app_module.hub, "guider", _FakeGuider(native=False))
    r = client.post("/api/guide/assistant/start", json={})
    assert r.status_code == 400
    assert "native" in r.json()["detail"].lower()


def test_start_409_when_already_guiding(client, monkeypatch):
    monkeypatch.setattr(app_module.hub, "guider",
                        _FakeGuider(native=True, active=True))
    r = client.post("/api/guide/assistant/start", json={})
    assert r.status_code == 409


def test_start_spawns_on_native_idle(client, monkeypatch):
    monkeypatch.setattr(app_module.hub, "guider",
                        _FakeGuider(native=True, active=False))
    r = client.post("/api/guide/assistant/start", json={"include_backlash": True})
    assert r.status_code == 200
    assert r.json() == {"started": "guide_assistant"}


def test_report_route_returns_cached_or_null(client, monkeypatch):
    monkeypatch.setattr(app_module.hub, "guider",
                        _FakeGuider(native=True, report={"measurements": {}}))
    r = client.get("/api/guide/assistant/report")
    assert r.status_code == 200 and r.json()["report"] == {"measurements": {}}
    # non-native guider exposes no report accessor -> null (panel simply omits).
    monkeypatch.setattr(app_module.hub, "guider", _FakeGuider(native=False))
    r = client.get("/api/guide/assistant/report")
    assert r.status_code == 200 and r.json()["report"] is None
