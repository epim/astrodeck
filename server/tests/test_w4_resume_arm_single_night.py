"""#195: DUSK WINDOW's "Single night" used to change nothing about resuming -
`engine.start` arms `auto_resume` unconditionally, and `ResumeArm.tick` asked
only whether tonight's window was open, never how many nights the session had
already run. `SequencePlan.resume_across_nights` (models.py) closes that: when
False, `ResumeArm.tick` (resume_arm.py) still resumes a crash or a reboot on
the SAME night (continuity within a night is a separate promise) but refuses
and disarms a dormant session whose window has reopened on a LATER night.

Same harness and precedent as `test_resume_arm.py`
(`test_tick_resumes_when_window_open`, `test_give_up_when_window_closes_mid_
retry`): a real sim hub and engine, `_window_open` monkeypatched True so the
tick never has to ask the sky, and an injected clock on `ResumeArm` alone -
`Session.observing_nights()` reads the REAL wall clock (the report id's own
stamp, `session.report_night`), so "a different night" is driven by moving
the injected clock two days ahead of real time, never by faking the report
id itself.
"""
from __future__ import annotations

import asyncio
import time

import pytest

import astrodeck.hub as hub_module
from astrodeck.hub import Hub
from astrodeck.sequence import SequenceEngine, SequencePlan
from astrodeck.sequence.models import ExposureStep, Target
from astrodeck.sequence.resume_arm import ResumeArm
from astrodeck.sequence.session import session_store

#: A whole day on the real wall clock, past any noon-rollover boundary twice
#: over, so "a different night" never depends on what time of day the suite
#: happens to run.
TWO_DAYS_S = 2 * 86400.0


@pytest.fixture
async def sim_hub(tmp_path, monkeypatch):
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    monkeypatch.setattr(hub_module.config_store.cfg().safety,
                        "solar_avoidance", False)
    h = Hub()
    await h.connect_sim()
    yield h
    await h.disconnect_all()


async def wait_for(predicate, timeout=30.0):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.05)
    return False


def _plan(*, resume_across_nights: bool, count: int = 6) -> SequencePlan:
    return SequencePlan(
        name="single-night", guide=False, dither_every=0, autofocus_every=0,
        meridian_flip=False, resume_across_nights=resume_across_nights,
        targets=[Target(name="M42", ra_hours=5.5881, dec_deg=-5.3911,
                        center=False, autofocus_first=False,
                        steps=[ExposureStep(filter="L", exposure_s=0.05,
                                            count=count)])])


async def _dormant_armed(engine, *, resume_across_nights: bool) -> str:
    """A real dormant, armed session with one owed frame still left, one
    observing night recorded on the real wall clock: start, bank one frame,
    abort, arm. Mirrors `test_resume_arm.py`'s `_dormant_armed`, with the
    plan's `resume_across_nights` as the one free variable this file tests."""
    engine.start(_plan(resume_across_nights=resume_across_nights, count=6))
    sid = engine._session.id
    assert await wait_for(lambda: engine._frames_done >= 1)
    await engine.abort()
    s = session_store.load(sid)
    assert s.status == "dormant"
    assert s.observing_nights(), "premise: the real run left a night behind"
    s.auto_resume = True
    session_store.save(s)
    return sid


async def test_single_night_plan_still_resumes_the_same_night(sim_hub,
                                                               monkeypatch):
    """Continuity within a night is a SEPARATE promise from "Single night",
    and this fix must not withdraw it: a crash or a reboot minutes later, on
    the night the session already ran, still resumes."""
    engine = SequenceEngine(sim_hub)
    sid = await _dormant_armed(engine, resume_across_nights=False)
    now = {"t": time.time()}               # same real night as the run above
    arm = ResumeArm(engine, sim_hub, clock=lambda: now["t"])
    monkeypatch.setattr(ResumeArm, "_window_open", lambda self, s, t: True)
    await arm.tick()
    assert await wait_for(lambda: engine.state.get("state") == "complete")
    assert session_store.load(sid).status == "complete", (
        "a same-night resume must still finish the plan")


async def test_single_night_plan_refuses_a_later_night(sim_hub, monkeypatch,
                                                        bus_lines):
    """The window reopens two real days later: a 'Single night' session must
    not quietly finish itself like a campaign would.

    MUTANT "no night check" (`if False and not armed.plan.resume_across_
    nights:`, disabling the whole block this fix adds) turned this red, run
    from a byte backup and restored and sha256-verified afterwards:

        AssertionError: a single-night session must not start on a later
        night at all
            [info] sequence 'single-night' started: 6 frames, 0 min
            integration
            ...
            [info] auto-resume: 'single-night' resumed
        assert not True
         +  where True = <SequenceEngine object>.running

    The tick ran the full recovery ladder - autofocus, plate solve,
    re-centre - and resumed the plan exactly as a cross-night campaign
    should, which is precisely the bug: a 'Single night' session has no
    gate left to stop it."""
    engine = SequenceEngine(sim_hub)
    sid = await _dormant_armed(engine, resume_across_nights=False)
    first_night = session_store.load(sid).observing_nights()[0]
    now = {"t": time.time() + TWO_DAYS_S}
    arm = ResumeArm(engine, sim_hub, clock=lambda: now["t"])
    monkeypatch.setattr(ResumeArm, "_window_open", lambda self, s, t: True)
    await arm.tick()
    assert not engine.running, (
        "a single-night session must not start on a later night at all\n"
        + "\n".join(f"  [{lv}] {msg}" for lv, msg, _src in bus_lines))
    after = session_store.load(sid)
    assert after.status == "dormant", (
        f"the session changed status to {after.status!r} instead of staying "
        f"dormant and disarmed")
    assert after.auto_resume is False, (
        "a single-night session on a later night must be DISARMED, not left "
        "armed for the next tick to try again")
    assert any("single night" in msg and first_night in msg
              for _lv, msg, _src in bus_lines), (
        "the operator is told why, naming the night the flow actually ran:\n"
        + "\n".join(f"  [{lv}] {msg}" for lv, msg, _src in bus_lines))


async def test_default_plan_still_resumes_across_nights(sim_hub, monkeypatch):
    """The missing-key default, and an explicit True, must reproduce TODAY's
    behaviour byte-identically: a plan that never opted out of cross-night
    resume still comes back two real days later. (`test_campaign_across_
    nights.py` already covers this end to end for a campaign; this is the
    same guarantee from `ResumeArm.tick`'s own new branch, which must not
    fire at all when the flag is True.)"""
    engine = SequenceEngine(sim_hub)
    sid = await _dormant_armed(engine, resume_across_nights=True)
    now = {"t": time.time() + TWO_DAYS_S}
    arm = ResumeArm(engine, sim_hub, clock=lambda: now["t"])
    monkeypatch.setattr(ResumeArm, "_window_open", lambda self, s, t: True)
    await arm.tick()
    assert await wait_for(lambda: engine.state.get("state") == "complete")
    assert session_store.load(sid).status == "complete", (
        "a plan that did not ask for 'Single night' must still resume on a "
        "later night")
