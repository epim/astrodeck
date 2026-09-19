"""Regression coverage for #21, #26, #29, #30, #33, #34, #43 and #60.

All hardware is synthetic; no live rig endpoints or site configuration.
"""
import asyncio
from types import SimpleNamespace

import pytest

from astrodeck.guide.native import NativeGuider
from astrodeck.sequence.engine import SequenceEngine
from astrodeck.sequence.models import ExposureStep, SequencePlan, Target


def engine():
    return SequenceEngine(SimpleNamespace(devices={}, guider=None, site={}))


@pytest.mark.parametrize("old", ["dawn_cutoff", "unsafe", "quality"])
async def test_operator_abort_does_not_inherit_previous_run_reason(old):
    eng = engine()
    eng._set_state(state="complete", end_reason=old)
    eng._set_state(state="running")
    assert "end_reason" not in eng.state
    await eng.abort()
    assert eng.state["end_reason"] == "aborted"


async def test_stop_before_new_run_gets_its_first_task_turn():
    eng = engine()
    eng._set_state(state="complete", end_reason="dawn_cutoff")
    eng.start(SequencePlan(name="Synthetic immediate stop", guide=False, targets=[
        Target(name="Synthetic field", ra_hours=1, dec_deg=20,
               steps=[ExposureStep(exposure_s=60, count=1)])]))
    assert eng.state["state"] not in {"complete", "aborted", "error"}
    assert "end_reason" not in eng.state
    session_id = eng._session.id
    await eng.abort()
    assert eng.state["state"] == eng.state["end_reason"] == "aborted"
    from astrodeck.sequence.session import session_store
    stored = session_store.load(session_id)
    assert stored.status == "dormant"
    assert stored.auto_resume is False
    assert eng._report_finalized


@pytest.mark.parametrize("reason", ["unsafe", "quality", "dawn_cutoff", "error"])
async def test_abort_during_terminal_teardown_preserves_its_reason(reason):
    eng = engine()
    eng._set_state(state="aborted", end_reason=reason)
    await eng.abort()
    assert eng.state["end_reason"] == reason


def test_error_and_completion_overwrite_previous_terminal_causes():
    eng = engine()
    eng._set_state(state="aborted", end_reason="unsafe")
    eng._set_state(state="error", detail="camera is busy (plate solve)")
    assert eng.state["end_reason"] == "error"
    eng._set_state(state="complete")
    assert eng.state["end_reason"] == "complete"


def test_sky_api_explains_settled_verdict_and_identifies_latest_frame(monkeypatch):
    import astrodeck.sequence.engine as mod
    monkeypatch.setattr(mod.time, "time", lambda: 1200)
    eng = engine()
    eng._clouds.observe(True, 1000, score=.8, reason="cloudy")
    eng._clouds.observe(True, 1100, score=.8, reason="cloudy")
    eng._clouds.observe(False, 1200, score=.2, reason="clear (189 bright stars)")
    sky = eng._sky_state()
    assert sky["cloudy"] is True
    assert sky["reason"].startswith("cloudy,")
    assert "1 of 2" in sky["reason"]
    assert sky["latest_frame"] == {
        "cloudy": False, "score": .2, "reason": "clear (189 bright stars)"}


@pytest.mark.parametrize("exposure", [5, 60, 180, 600])
async def test_cloud_probe_keeps_science_settings_without_saving(exposure):
    eng = engine()
    calls = []
    async def capture(*args, **kwargs):
        calls.append((args, kwargs))
        return {"cloud": {"cloudy": False, "score": .1, "reason": "clear"}}
    eng.hub.capture = capture
    eng._hold_step = ExposureStep(exposure_s=exposure, gain=125, offset=30,
                                 binning=2, filter="Ha", count=3)
    target = Target(name="Synthetic field", ra_hours=1, dec_deg=20)
    await eng._cloud_probe(target)
    assert calls == [((float(exposure), 125, 30, 2),
                     {"save": False, "target": "Synthetic field", "frame_type": "Light"})]
    assert eng._frames_done == 0


async def test_probe_failure_stays_unknown_and_cancellation_propagates():
    eng = engine()
    eng._hold_step = ExposureStep(exposure_s=60, count=1)
    async def failed(*a, **kw):
        raise RuntimeError("synthetic camera failure")
    eng.hub.capture = failed
    assert await eng._cloud_probe(None) is None
    async def cancelled(*a, **kw):
        raise asyncio.CancelledError
    eng.hub.capture = cancelled
    with pytest.raises(asyncio.CancelledError):
        await eng._cloud_probe(None)


async def test_cloud_probe_does_not_write_fits_or_consume_science_number(tmp_path, monkeypatch):
    import json
    import astrodeck.hub as hub_module
    from astrodeck.devices.sim import SimCamera, SimRig
    from astrodeck.gallery import scan
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    hub = hub_module.Hub()
    cam = SimCamera(SimRig())
    await cam.connect()
    hub.devices["camera"] = cam
    eng = SequenceEngine(hub)
    eng._hold_step = ExposureStep(exposure_s=.01, gain=100, count=1)
    target = Target(name="Synthetic field", ra_hours=1, dec_deg=20)
    try:
        await eng._cloud_probe(target)
        assert not list(tmp_path.rglob("*.fits"))
        assert not hub._counter_file().exists()
        await eng._capture(eng._hold_step, target)
        assert len(list(tmp_path.rglob("*.fits"))) == 1
        assert list(json.loads(hub._counter_file().read_text()).values()) == [1]
        rows, truncated = scan(tmp_path)
        assert len(rows) == 1 and not truncated
    finally:
        await cam.disconnect()


def test_asymmetric_rates_size_for_slower_axis_and_extend_capped_budget():
    cfg = {"image_scale_arcsec": 5.5, "image_scale_known": True}
    free = NativeGuider(None, SimpleNamespace(), config=cfg)
    slow = free._build_engine_config((.004178, .002089))
    fast = free._build_engine_config((.004178, .004178))
    assert slow["calibration_duration_ms"] >= fast["calibration_duration_ms"] * 1.99
    capped = NativeGuider(None, SimpleNamespace(max_pulse_ms=1000), config=cfg)
    got = capped._build_engine_config((.004178, .002089))
    assert got["calibration_duration_ms"] == 1000
    assert got["max_steps"] > 60
    assert got["max_ra_duration_ms"] == got["max_dec_duration_ms"] == 1000


def test_pinned_duration_preserved_and_pinned_distance_used():
    g = NativeGuider(None, SimpleNamespace(), config={
        "image_scale_arcsec": 5.5, "calibration_duration_ms": 750})
    assert g._build_engine_config((.004, .002))["calibration_duration_ms"] == 750
    g.config.pop("calibration_duration_ms")
    before = g._build_engine_config((.004, .002))["calibration_duration_ms"]
    g.config["calibration_distance"] = 50
    assert g._build_engine_config((.004, .002))["calibration_duration_ms"] == min(2500, int(before * 2) + 1)


@pytest.mark.parametrize("direction,arcsec", [("north", "7.5"), ("west", "15.0")])
def test_am5_cap_message_uses_the_axis_rate(monkeypatch, direction, arcsec):
    import astrodeck.devices.backends.zwo_am5 as am5
    messages = []
    monkeypatch.setattr(am5, "_cap_warn_last", float("-inf"))
    monkeypatch.setattr(am5.bus, "log", lambda *args: messages.append(args))
    telescope = object.__new__(am5.ZwoAm5Telescope)
    telescope.name = "Synthetic AM5"
    assert telescope._capped_ms(direction, 2000) == 1000
    assert f"~{arcsec} arcsec" in messages[0][1]


@pytest.mark.parametrize("known,scale", [(False, 1.0), (True, 5.5)])
def test_calibration_scale_visible_before_first_frame_and_logged(monkeypatch, known, scale):
    from astrodeck.events import bus
    logs = []
    monkeypatch.setattr(bus, "log", lambda *args: logs.append(args))
    g = NativeGuider(None, SimpleNamespace(), config={
        "image_scale_arcsec": scale, "image_scale_known": known})
    status = g.stats()
    assert status.calibration_image_scale == scale
    assert status.image_scale_known is known
    g._build_engine_config(None)
    assert logs[0][0] == ("info" if known else "warning")
    assert ("configured optics" if known else "assumed") in logs[0][1]
