"""Why nothing is happening has to leave the process.

On 2026-08-16 a session sat armed and weather-held with 58 frames owed, and
Monitor said "No run active - Plan a session to start capturing". Following
that instruction is how you lose the frames: `engine.start` disarms every other
session, so planning a fresh run strands the armed one.

The reason existed. ResumeArm computed a human-readable refusal on every held
tick, formatted it into a log line, and kept nothing. `bus.log_history` is
~40 minutes of a ten-hour night, and the standing-by branch latches per session
and logs EXACTLY ONCE - so a UI built on scraping the ring would have worked
for the weather veto (which re-logs every 10 min) and failed silently for the
more common hold. That is the failure mode this file exists to prevent.
"""
import time

import pytest

from astrodeck.sequence.resume_arm import ResumeArm
from astrodeck.sequence import SequencePlan
from astrodeck.sequence.models import ExposureStep, Target
from astrodeck.sequence.session import Session


class _Engine:
    running = False


class _Hub:
    devices: dict = {}
    site = {"latitude": 37.3, "longitude": -121.8, "elevation_m": 0,
            "name": "t", "is_default": False, "horizon_min_deg": 20}


def _arm(**kw):
    a = ResumeArm(_Engine(), _Hub(), clock=lambda: 1000.0, **kw)
    return a


def _session(name="night", owed=5):
    """A real Session that really owes `owed` frames.

    `owed()` is derived from the frozen plan, and pydantic will not accept a
    stubbed method - which is the right answer: stubbing it would have let the
    tests pass against a Session whose arithmetic was broken.
    """
    return Session(
        name=name, status="dormant", auto_resume=True,
        plan=SequencePlan(
            name=name,
            targets=[Target(name="A", ra_hours=1.0, dec_deg=1.0,
                            steps=[ExposureStep(filter="L", exposure_s=1.0,
                                                count=owed)])]))


def test_nothing_is_holding_by_default():
    assert _arm().hold is None


def test_a_hold_records_the_reason_and_the_session():
    a = _arm()
    a._set_hold(_session("NGC 7129", owed=58), "cloud cover 100%", retry_at=1600.0)
    h = a.hold
    assert h["reason"] == "cloud cover 100%"
    assert h["session_name"] == "NGC 7129"
    assert h["owed"] == 58
    assert h["retry_at"] == 1600.0
    assert h["since"] == 1000.0


def test_since_survives_a_repeated_reason():
    """The weather veto re-fires every ten minutes for hours. If `since` were
    re-stamped each time, the UI could never say how long it had been waiting -
    it would always read "just now"."""
    ticks = iter([1000.0, 1600.0, 2200.0])
    a = ResumeArm(_Engine(), _Hub(), clock=lambda: next(ticks))
    s = _session()
    a._set_hold(s, "cloud cover 100%")
    a._set_hold(s, "cloud cover 100%")
    assert a.hold["since"] == 1000.0


def test_a_different_reason_restarts_the_clock():
    ticks = iter([1000.0, 1600.0])
    a = ResumeArm(_Engine(), _Hub(), clock=lambda: next(ticks))
    s = _session()
    a._set_hold(s, "cloud cover 100%")
    a._set_hold(s, "the safety monitor says it is not safe to observe")
    assert a.hold["since"] == 1600.0


def test_a_live_run_is_not_a_hold():
    a = _arm()
    a._set_hold(_session(), "cloud")
    a.engine.running = True

    import asyncio
    asyncio.run(a.tick())
    assert a.hold is None, "a running engine must clear the hold, not keep it"


@pytest.mark.asyncio
async def test_the_route_serves_it(tmp_path, monkeypatch):
    """The whole point is that it leaves the process."""
    from fastapi.testclient import TestClient
    import astrodeck.api.app as app_module

    monkeypatch.setattr(app_module.hub_module, "CAPTURE_DIR", tmp_path)
    with TestClient(app_module.create_app()) as c:
        r = c.get("/api/sequence/resume-arm")
        assert r.status_code == 200
        body = r.json()
        assert "armed" in body and "hold" in body

        app_module.resume_arm._set_hold(_session("NGC 7129", owed=58),
                                        "cloud cover 100% forecast")
        body = c.get("/api/sequence/resume-arm").json()
        assert body["hold"]["reason"] == "cloud cover 100% forecast"
        assert body["hold"]["owed"] == 58
