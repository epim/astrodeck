"""Task 3: engine <-> session ledger integration (sessions spec §2/§4).

Real sim hub (repo convention: NO FakeHub), CAPTURE_DIR monkeypatched, state
polled via the bounded wait_for helper."""
import asyncio

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
import astrodeck.hub as hub_module
from astrodeck.hub import Hub
from astrodeck.sequence import SequenceEngine, SequencePlan
from astrodeck.sequence.models import ExposureStep, Target
from astrodeck.sequence.session import Session, SessionFrame, session_store


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


def _plan(count=4) -> SequencePlan:
    return SequencePlan(name="ledger", guide=False, dither_every=0,
                        autofocus_every=0, meridian_flip=False, targets=[Target(
                            name="M42", ra_hours=5.5881, dec_deg=-5.3911,
                            center=False, autofocus_first=False,
                            steps=[ExposureStep(filter="L", exposure_s=0.05,
                                                count=count)])])


async def test_run_produces_session_ledger(sim_hub):
    plan = _plan(3)
    engine = SequenceEngine(sim_hub)
    engine.start(plan)
    sid = engine._session.id
    assert await wait_for(lambda: engine.state.get("state") == "complete")
    s = session_store.load(sid)
    assert s.status == "complete"
    assert len(s.nights) == 1                       # one report for the night
    assert len(s.frames) == 3
    step = plan.targets[0].steps[0]
    assert s.accepted(step.id) == 3
    assert all(f.target_id == plan.targets[0].id for f in s.frames)
    assert all(f.night == s.nights[0] for f in s.frames)
    # metrics are floats-only, absent when unmeasured (spec §2). NB: the sim's
    # 0.05s frames yield no detectable stars (hfr unmeasured -> absent) while
    # guide_rms/sensor_temp_c ARE measured — hfr-absence != empty metrics.
    assert all(isinstance(v, float) for f in s.frames for v in f.metrics.values())
    # the retired single-slot resume file must never come back
    assert not (hub_module.CAPTURE_DIR / ".sequence_resume.json").exists()


async def test_abort_leaves_dormant_session_and_active_on_disk_midrun(sim_hub):
    plan = _plan(6)
    engine = SequenceEngine(sim_hub)
    engine.start(plan)
    sid = engine._session.id
    assert await wait_for(lambda: engine._frames_done >= 2)
    assert session_store.load(sid).status == "active"   # crash would orphan this
    await engine.abort()
    s = session_store.load(sid)
    assert s.status == "dormant"
    assert 1 <= len(s.frames) < 6


async def test_remaining_capture_s_reflects_id_keyed_done_counts(sim_hub):
    """Regression (Task 3 review, Critical): ``_done`` is keyed
    "<target.id>:<step.id>" (id-keyed — see class docstring / _target_complete),
    but ``_remaining_capture_s`` looked it up by the retired positional
    "ti:si" key. That lookup always misses, so completed frames were never
    subtracted and remaining/ETA silently overcounted by every captured frame
    for the whole run."""
    plan = SequencePlan(name="ledger", guide=False, dither_every=0,
                        autofocus_every=0, meridian_flip=False, targets=[Target(
                            name="M42", ra_hours=5.5881, dec_deg=-5.3911,
                            center=False, autofocus_first=False,
                            steps=[ExposureStep(filter="L", exposure_s=0.3,
                                                count=4)])])
    engine = SequenceEngine(sim_hub)
    engine.start(plan)
    # let at least 2 of the 4 frames actually record before sampling
    assert await wait_for(lambda: engine._frames_done >= 2)
    remaining = engine._remaining_capture_s()
    # with 2+ of 4 frames already recorded, at most 2 frames' worth of pure
    # capture time can still be owed — a positional-key lookup that always
    # misses (the bug) reports ~3-4 frames' worth instead.
    assert remaining <= 2 * 0.3 + 1e-6, remaining
    await engine.abort()


async def test_recover_routes_read_session_store(tmp_path, monkeypatch):
    # isolated app (mirrors tests/test_rbac_enforcement.py::_make_client)
    from astrodeck.config import ConfigStore
    import astrodeck.config as config_mod
    temp_store = ConfigStore(path=tmp_path / "astrodeck.json")
    monkeypatch.setattr(config_mod, "config_store", temp_store)
    monkeypatch.setattr(hub_module, "config_store", temp_store)
    monkeypatch.setattr(app_module, "config_store", temp_store)
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path / "captures")
    monkeypatch.delenv(app_module.AUTH_ENV_VAR, raising=False)
    app = app_module.create_app()
    with TestClient(app) as c:
        assert c.get("/api/sequence/recoverable").json() == {"recoverable": False}
        plan = _plan(4)
        s = Session(name="ledger", created_ts=1.0, status="dormant", plan=plan)
        s.frames.append(SessionFrame(ts=1.0, night="n1",
                                     target_id=plan.targets[0].id,
                                     step_id=plan.targets[0].steps[0].id,
                                     auto_accepted=True))
        session_store.save(s)
        r = c.get("/api/sequence/recoverable").json()
        assert r["recoverable"] is True
        assert r["session_id"] == s.id and r["name"] == "ledger"
        assert r["frames_done"] == 1 and r["frames_total"] == 4
        # recover with no camera connected -> DeviceError -> 4xx, session intact
        rr = c.post("/api/sequence/recover")
        assert rr.status_code >= 400
        assert session_store.load(s.id).status == "dormant"
