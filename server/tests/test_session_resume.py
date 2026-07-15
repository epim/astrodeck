"""Task 5: graceful resume semantics (sessions spec §4).

Resume re-enters the FULL normal start path; _done seeds from the id-keyed
ledger so an edited/reordered plan never misattributes; a closed-window target
dormants the night (dawn analogue) and resumes at exact remaining counts; and
report-id minting is collision-safe across a session's nights."""
import asyncio
import time

import pytest

import astrodeck.hub as hub_module
from astrodeck.hub import Hub
from astrodeck.sequence import SequenceEngine, SequencePlan
from astrodeck.sequence.engine import _mint_report_id
from astrodeck.sequence.models import ExposureStep, Schedule, Target
from astrodeck.sequence.session import session_store


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


def _target(name, count) -> Target:
    return Target(name=name, ra_hours=5.5881, dec_deg=-5.3911, center=False,
                  autofocus_first=False,
                  steps=[ExposureStep(filter="L", exposure_s=0.05, count=count)])


def test_mint_report_id_collision_safe():
    ts = time.time()
    first = _mint_report_id("My Plan", ts, [])
    assert first.startswith("My_Plan-")
    second = _mint_report_id("My Plan", ts, [first])
    assert second == f"{first}-2"
    third = _mint_report_id("My Plan", ts, [first, second])
    assert third == f"{first}-3"


async def test_reordered_plan_resume_does_not_misattribute(sim_hub):
    plan = SequencePlan(name="re", guide=False, dither_every=0, autofocus_every=0,
                        meridian_flip=False,
                        targets=[_target("A", 3), _target("B", 3)])
    a_step = plan.targets[0].steps[0]
    b_step = plan.targets[1].steps[0]
    eng = SequenceEngine(sim_hub)
    eng.start(plan)
    sid = eng._session.id
    # let target A record at least one frame, then abort mid-run
    assert await wait_for(lambda: eng._frames_done >= 1)
    await eng.abort()
    s = session_store.load(sid)
    a_before = s.accepted(a_step.id)
    assert a_before >= 1
    # EDIT: reorder targets (B first) — ids survive the shuffle
    s.plan = SequencePlan.model_validate(
        {**s.plan.model_dump(),
         "targets": [s.plan.targets[1].model_dump(),
                     s.plan.targets[0].model_dump()]})
    session_store.save(s)
    eng2 = SequenceEngine(sim_hub)
    eng2.start(s.plan, session=s)
    assert await wait_for(lambda: eng2.state.get("state") == "complete")
    done = session_store.load(sid)
    # id-keyed: each step ends at EXACTLY its count — no double-shot, no skip
    assert done.accepted(a_step.id) == 3
    assert done.accepted(b_step.id) == 3
    assert done.status == "complete"


async def test_window_dormant_then_resume_exact_remaining(sim_hub):
    # target B's window closed an hour ago -> skipped -> dawn_cutoff -> dormant
    past = time.strftime("%H:%M", time.localtime(time.time() - 3600))
    b = _target("B", 2)
    b.schedule = Schedule(start_mode="now", stop_mode="time", stop_time=past)
    plan = SequencePlan(name="dawnish", guide=False, dither_every=0,
                        autofocus_every=0, meridian_flip=False,
                        targets=[_target("A", 2), b])
    a_step = plan.targets[0].steps[0]
    b_step = plan.targets[1].steps[0]
    eng = SequenceEngine(sim_hub)
    eng.start(plan)
    sid = eng._session.id
    assert await wait_for(lambda: eng.state.get("state") == "complete")
    assert eng.state.get("end_reason") == "dawn_cutoff"
    s = session_store.load(sid)
    assert s.status == "dormant"                    # unmet work -> resumable
    assert s.accepted(a_step.id) == 2
    assert s.accepted(b_step.id) == 0
    assert s.remaining()[b_step.id] == 2
    # night 2: open B's window (id-safe plan edit), resume
    s.plan.targets[1].schedule = Schedule()
    session_store.save(s)
    eng2 = SequenceEngine(sim_hub)
    eng2.start(s.plan, session=s)
    assert await wait_for(lambda: eng2.state.get("state") == "complete")
    done = session_store.load(sid)
    assert done.status == "complete"
    assert done.accepted(a_step.id) == 2            # A was NOT reshot
    assert done.accepted(b_step.id) == 2            # B got exactly its remainder
    assert len(done.nights) == 2 and done.nights[0] != done.nights[1]
