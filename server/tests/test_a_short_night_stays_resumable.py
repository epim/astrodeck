"""#252: a run that ends with frames owed must stay resumable.

Every test here drives the REAL engine. The defect these cover shipped behind
a correct pure function (``Session.remaining()``) whose only caller asked it
the wrong question, and a pure-function test passes against it happily.

Rig evidence, 2026-08-16: session 18572134 ended ``complete`` with 117 of 175
frames and ``auto_resume`` set - armed, finished, and unreachable forever,
because ``session_store.armed()`` only ever returns a DORMANT session.
"""
import asyncio
import time

import pytest

import astrodeck.hub as hub_module
from astrodeck.hub import Hub
from astrodeck.sequence import SequenceEngine, SequencePlan
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
        await asyncio.sleep(0.05)
    return False


def _target(name, count, schedule=None) -> Target:
    t = Target(name=name, ra_hours=5.5881, dec_deg=-5.3911, center=False,
               autofocus_first=False,
               steps=[ExposureStep(filter="L", exposure_s=0.05, count=count)])
    if schedule is not None:
        t.schedule = schedule
    return t


def _plan(name, targets) -> SequencePlan:
    return SequencePlan(name=name, guide=False, dither_every=0,
                        autofocus_every=0, meridian_flip=False,
                        targets=targets)


def _close_the_window(eng, target) -> None:
    """Dawn arrives UNDER a running frame loop.

    The scheduler froze this target's window at run start and will never
    re-select it, so a closure that happens while the step is shooting can only
    be seen by the per-frame boundary check. That is the shape of every real
    single-target night on this rig, and the shape no existing test had: the
    two tests that guard this behaviour both close the window in the PAST, so
    the target is rejected at SELECTION and reaches a different branch.
    """
    start, _stop = eng._frozen.get(id(target)) or (time.time() - 100, None)
    eng._frozen[id(target)] = (start, time.time() - 1)


async def test_a_dawn_cut_mid_target_leaves_the_session_resumable(sim_hub):
    plan = _plan("dawncut", [_target("A", 8)])
    eng = SequenceEngine(sim_hub)
    eng.start(plan)
    sid = eng._session.id
    assert await wait_for(lambda: eng._frames_done >= 2)
    _close_the_window(eng, plan.targets[0])
    assert await wait_for(lambda: eng.state.get("state") == "complete")

    s = session_store.load(sid)
    assert s.owed() == 8 - len(s.frames)
    assert 0 < s.owed() < 8, "the cut has to land mid-target for this to test anything"
    assert s.status == "dormant", (
        "a night cut short owes frames, and 'complete' is the one status that "
        "makes them unreachable")
    armed = session_store.armed()
    assert armed is not None and armed.id == sid, (
        "armed() only ever returns a dormant session - this is the whole "
        "reason the status matters")


async def test_a_finished_run_still_completes(sim_hub):
    """The other direction. Over-correcting into false dormancy would re-arm
    every finished session and re-shoot it at the next dusk."""
    plan = _plan("short", [_target("A", 2)])
    eng = SequenceEngine(sim_hub)
    eng.start(plan)
    sid = eng._session.id
    assert await wait_for(lambda: eng.state.get("state") == "complete")

    s = session_store.load(sid)
    assert s.owed() == 0
    assert s.status == "complete"
    assert session_store.armed() is None


async def test_a_calibration_only_plan_still_completes(sim_hub):
    """Calibration frames reach the same ledger as lights (the rig's
    'Calib 2026-08-11' session holds 94 of them), so the ledger-truth rule must
    not strand a dark set in a nightly resume loop."""
    darks = Target(name="darks", ra_hours=0.0, dec_deg=0.0, calibration=True,
                   center=False, autofocus_first=False,
                   steps=[ExposureStep(filter=None, exposure_s=0.05, count=2,
                                       frame_type="Dark")])
    eng = SequenceEngine(sim_hub)
    eng.start(_plan("calib", [darks]))
    sid = eng._session.id
    assert await wait_for(lambda: eng.state.get("state") == "complete")

    s = session_store.load(sid)
    assert s.owed() == 0
    assert s.status == "complete"
