"""A campaign is not one night, and the machinery for that already exists.

`to_plan` used to report a campaign flow at DANGER weight, saying the run
"images ONE night and stops at dawn", that "the capture cursor is not
persisted", that "no target is marked done" and that the flow "will not re-arm
at the next dusk". Three of those four were false by the time they were written.

Over-reporting a loss is the same defect as hiding one, and it is worse here
than it sounds: an operator told their month-long campaign will not work does
not run it, so the feature that does work goes unused and nobody ever finds out.

This file is the evidence the corrected note rests on. It proves the ONE thing
the existing session tests do not state in campaign terms: a pool member that
finished on night one is skipped on night two, and the next member gets the
night - which is what "the pool advances" means when nothing in the engine has
ever heard of a pool.
"""
from __future__ import annotations

import asyncio

import pytest

import astrodeck.hub as hub_module
from astrodeck.hub import Hub
from astrodeck.sequence import SequenceEngine, SequencePlan
from astrodeck.sequence.models import ExposureStep, Schedule, Target
from astrodeck.sequence.session import session_store


@pytest.fixture
async def sim_hub(tmp_path, monkeypatch):
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    monkeypatch.setattr(hub_module.config_store.cfg().safety,
                        "solar_avoidance", False)
    h = Hub()
    await h.connect_sim()
    yield h
    await h.disconnect_all()


async def _wait(predicate, timeout=40.0) -> bool:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.05)
    return False


def _member(name: str, count: int, schedule: Schedule | None = None) -> Target:
    """One pool candidate. `on_missed="skip"` is what `to_plan` gives every
    pool member, so the shape here is the shape a compiled pool arrives in."""
    t = Target(name=name, ra_hours=5.5881, dec_deg=-5.3911, center=False,
               autofocus_first=False,
               steps=[ExposureStep(filter="L", exposure_s=0.05, count=count)])
    t.schedule = schedule or Schedule(on_missed="skip")
    return t


async def test_a_campaign_advances_across_nights(sim_hub):
    """Night one finishes member A and never reaches B. Night two must skip A
    and spend itself on B.

    If the cursor were lost, A would be reshot from zero every night and B
    would never be reached at all - a campaign that runs forever on its first
    candidate, which is precisely what the old note described."""
    import time as _time
    past = _time.strftime("%H:%M", _time.localtime(_time.time() - 3600))
    plan = SequencePlan(
        name="campaign", guide=False, dither_every=0, autofocus_every=0,
        meridian_flip=False,
        targets=[_member("A", 2),
                 # B's window shut an hour ago: night one cannot have it.
                 _member("B", 2, Schedule(start_mode="now", stop_mode="time",
                                          stop_time=past, on_missed="skip"))])
    a_step, b_step = plan.targets[0].steps[0], plan.targets[1].steps[0]

    eng = SequenceEngine(sim_hub)
    eng.start(plan)
    sid = eng._session.id
    assert await _wait(lambda: eng.state.get("state") == "complete")

    night_one = session_store.load(sid)
    assert night_one.status == "dormant", "unmet work must stay resumable"
    assert night_one.auto_resume is True, (
        "the session has to be ARMED or nothing brings the campaign back")
    assert night_one.accepted(a_step.id) == 2
    assert night_one.accepted(b_step.id) == 0

    # Night two: B's window is open. Same session, same ledger.
    night_one.plan.targets[1].schedule = Schedule(on_missed="skip")
    session_store.save(night_one)
    eng2 = SequenceEngine(sim_hub)
    eng2.start(night_one.plan, session=night_one)
    assert await _wait(lambda: eng2.state.get("state") == "complete")

    done = session_store.load(sid)
    assert done.accepted(a_step.id) == 2, (
        "A was already finished and must NOT be reshot - that is the pool "
        f"advancing; got {done.accepted(a_step.id)}")
    assert done.accepted(b_step.id) == 2, "B gets the night A no longer needs"
    assert len(done.nights) == 2, "each night is its own immutable report"
    assert done.status == "complete", (
        "every target has its frames, so the campaign is over and auto-resume "
        "will not pick it up again")


async def test_the_finished_campaign_is_no_longer_armed(sim_hub):
    """The other end of it. `ResumeArm.armed()` only considers DORMANT sessions,
    so a completed campaign stops coming back on its own. Without this a
    finished month-long campaign would re-arm every dusk forever."""
    plan = SequencePlan(name="short", guide=False, dither_every=0,
                        autofocus_every=0, meridian_flip=False,
                        targets=[_member("A", 1)])
    eng = SequenceEngine(sim_hub)
    eng.start(plan)
    sid = eng._session.id
    assert await _wait(lambda: eng.state.get("state") == "complete")
    assert session_store.load(sid).status == "complete"
    armed = session_store.armed()
    assert armed is None or armed.id != sid, (
        "a finished campaign must not stay armed for the next dusk")
