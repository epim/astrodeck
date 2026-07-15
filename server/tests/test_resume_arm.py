"""Task 8: ResumeArm auto-resume service (sessions spec §5) — injected clock
(monkeypatched time source / _window_open, schedule-test precedent), real sim
hub + engine (no FakeHub)."""
import asyncio

import pytest

import astrodeck.hub as hub_module
from astrodeck.devices.base import DeviceError
from astrodeck.hub import Hub
from astrodeck.sequence import SequenceEngine, SequencePlan
from astrodeck.sequence.models import ExposureStep, Target
from astrodeck.sequence.resume_arm import RETRY_INTERVAL_S, ResumeArm
from astrodeck.sequence.session import Session, session_store


@pytest.fixture
async def sim_hub(tmp_path, monkeypatch):
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    _safety = hub_module.config_store.cfg().safety
    monkeypatch.setattr(_safety, "solar_avoidance", False)
    h = Hub()
    await h.connect_sim()
    yield h
    await h.disconnect_all()


async def wait_for(predicate, timeout=30.0):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.1)
    return False


def _plan(count=6) -> SequencePlan:
    return SequencePlan(name="arm", guide=False, dither_every=0,
                        autofocus_every=0, meridian_flip=False, targets=[Target(
                            name="M42", ra_hours=5.5881, dec_deg=-5.3911,
                            center=False, autofocus_first=False,
                            steps=[ExposureStep(filter="L", exposure_s=0.05,
                                                count=count)])])


async def _dormant_armed(sim_hub, engine) -> str:
    """Real dormant session: start, get a frame in, abort, arm."""
    engine.start(_plan())
    sid = engine._session.id
    assert await wait_for(lambda: engine._frames_done >= 1)
    await engine.abort()
    s = session_store.load(sid)
    assert s.status == "dormant"
    s.auto_resume = True
    session_store.save(s)
    return sid


async def test_tick_noop_when_window_closed(sim_hub, monkeypatch):
    engine = SequenceEngine(sim_hub)
    await _dormant_armed(sim_hub, engine)
    now = {"t": 1_700_000_000.0}
    arm = ResumeArm(engine, sim_hub, clock=lambda: now["t"])
    monkeypatch.setattr(ResumeArm, "_window_open", lambda self, s, t: False)
    await arm.tick()
    assert not engine.running


async def test_tick_resumes_when_window_open(sim_hub, monkeypatch):
    engine = SequenceEngine(sim_hub)
    sid = await _dormant_armed(sim_hub, engine)
    now = {"t": 1_700_000_000.0}
    arm = ResumeArm(engine, sim_hub, clock=lambda: now["t"])
    monkeypatch.setattr(ResumeArm, "_window_open", lambda self, s, t: True)
    await arm.tick()
    assert engine.running
    assert await wait_for(lambda: engine.state.get("state") == "complete")
    assert session_store.load(sid).status == "complete"


async def test_refusal_retries_after_10_minutes(sim_hub, monkeypatch):
    engine = SequenceEngine(sim_hub)
    await _dormant_armed(sim_hub, engine)
    now = {"t": 1_700_000_000.0}
    arm = ResumeArm(engine, sim_hub, clock=lambda: now["t"])
    monkeypatch.setattr(ResumeArm, "_window_open", lambda self, s, t: True)
    calls = {"n": 0}

    def refuse(role):
        calls["n"] += 1
        raise DeviceError("no camera")
    monkeypatch.setattr(sim_hub, "require", refuse)
    await arm.tick()                                  # refusal
    assert calls["n"] == 1 and not engine.running
    assert arm._retry_at == now["t"] + RETRY_INTERVAL_S
    now["t"] += 300
    await arm.tick()                                  # still backing off
    assert calls["n"] == 1
    now["t"] += 301                                   # past the 10-min mark
    await arm.tick()
    assert calls["n"] == 2


async def test_give_up_when_window_closes_mid_retry(sim_hub, monkeypatch):
    engine = SequenceEngine(sim_hub)
    sid = await _dormant_armed(sim_hub, engine)
    now = {"t": 1_700_000_000.0}
    arm = ResumeArm(engine, sim_hub, clock=lambda: now["t"])
    window = {"open": True}
    monkeypatch.setattr(ResumeArm, "_window_open",
                        lambda self, s, t: window["open"])
    monkeypatch.setattr(sim_hub, "require",
                        lambda role: (_ for _ in ()).throw(DeviceError("no cam")))
    await arm.tick()                                  # refusal -> backoff armed
    window["open"] = False                            # dawn passed
    now["t"] += RETRY_INTERVAL_S + 1
    await arm.tick()
    assert arm._gave_up_for == sid                    # one give-up, no attempt
    assert not engine.running


async def test_disarm_and_running_engine_stop_interest(sim_hub, monkeypatch):
    engine = SequenceEngine(sim_hub)
    sid = await _dormant_armed(sim_hub, engine)
    s = session_store.load(sid)
    s.auto_resume = False                             # UI disarm
    session_store.save(s)
    now = {"t": 1_700_000_000.0}
    arm = ResumeArm(engine, sim_hub, clock=lambda: now["t"])
    monkeypatch.setattr(ResumeArm, "_window_open", lambda self, s, t: True)
    await arm.tick()
    assert not engine.running                         # nothing armed -> no-op


async def test_quota_unbounded_refused(sim_hub, monkeypatch):
    """HARD REQUIREMENT (Task 4 review carry-in): ResumeArm is a THIRD
    engine.start path and engine.start is deliberately unguarded, so ResumeArm
    MUST call quota_unbounded() itself and refuse (alert + backoff, stay
    dormant) an accepted-quota plan that could run forever under persistent
    rejects. The route gates never see this path."""
    engine = SequenceEngine(sim_hub)
    plan = _plan()
    plan.count_mode = "accepted"          # unbounded: accepted mode ...
    plan.max_consecutive_rejects = 0      # ... both reject guards off ...
    plan.max_consecutive_rejects_night = 0
    # ... and a non-calibration target with no stop boundary (default schedule).
    s = Session(name="unbounded", status="dormant", plan=plan, auto_resume=True)
    session_store.save(s)
    now = {"t": 1_700_000_000.0}
    arm = ResumeArm(engine, sim_hub, clock=lambda: now["t"])
    monkeypatch.setattr(ResumeArm, "_window_open", lambda self, s, t: True)
    # require would succeed (real camera) — the refusal must come from the guard.
    await arm.tick()
    assert not engine.running                         # refused before start
    assert arm._retry_at == now["t"] + RETRY_INTERVAL_S
    assert session_store.load(s.id).status == "dormant"
