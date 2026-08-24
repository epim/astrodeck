"""An abort is a decision, not a fault -- auto-resume must not undo it.

Observed on the rig 2026-08-24 00:30-00:40 PDT (0.3.7): three consecutive
aborts each returned {"aborted": true} and published state "aborted", and
auto-resume restarted the same session within ~45 s every time. The run could
not be stopped through any documented control, which also made the box
undeployable -- deploy_037.ps1 refuses to restart the server under a running
sequence, and the sequence would not stay stopped.

The loop had no exit: `engine.start` arms `session.auto_resume` unconditionally
(engine.py, "ARMED BY DEFAULT"), nothing disarmed it on an abort, and the
resume tick's `session_store.armed()` is satisfied by exactly
dormant + auto_resume. So abort -> dormant -> armed -> resume -> re-armed.

NOT covered by the existing abort tests, and that is the point: none of them
run the resume tick, so every one of them passed all night while the rig was
resurrecting itself. test_resume_arm.py's `_dormant_armed` helper aborts and
then explicitly sets `auto_resume = True`, which models an operator arming it
from the UI -- a different case, and still correct after this change.
"""
import asyncio

import pytest

import astrodeck.hub as hub_module
from astrodeck.hub import Hub
from astrodeck.sequence import SequenceEngine, SequencePlan
from astrodeck.sequence.models import ExposureStep, Target
from astrodeck.sequence.resume_arm import ResumeArm
from astrodeck.sequence.session import session_store


@pytest.fixture
async def sim_hub(tmp_path, monkeypatch):
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    monkeypatch.setattr(hub_module.config_store.cfg().safety, "solar_avoidance", False)
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


def _plan() -> SequencePlan:
    """Long enough that the run is still going when the abort lands -- a plan
    that self-completes would test the complete path, not the abort path."""
    return SequencePlan(name="abort-me", guide=False, dither_every=0,
                        autofocus_every=0, meridian_flip=False, targets=[Target(
                            name="M42", ra_hours=5.5881, dec_deg=-5.3911,
                            center=False, autofocus_first=False,
                            steps=[ExposureStep(filter="L", exposure_s=0.05,
                                                count=400)])])


async def _run_then(engine, stop):
    """Start, get a frame on disk, then apply `stop`. Returns the session id."""
    engine.start(_plan())
    sid = engine._session.id
    assert await wait_for(lambda: engine._frames_done >= 1), "no frame was taken"
    await stop()
    return sid


async def test_operator_abort_disarms_auto_resume(sim_hub):
    engine = SequenceEngine(sim_hub)
    sid = await _run_then(engine, engine.abort)

    s = session_store.load(sid)
    assert s.status == "dormant", "an aborted run still owes frames"
    assert s.auto_resume is False, (
        "the operator stopped this run; leaving it armed is what let "
        "auto-resume restart it three times on 2026-08-24")


async def test_an_aborted_run_survives_the_resume_tick(sim_hub, monkeypatch):
    """The property the shipped abort tests never checked: run the tick."""
    engine = SequenceEngine(sim_hub)
    await _run_then(engine, engine.abort)
    state_after_abort = engine.state.get("state")
    assert state_after_abort == "aborted"

    arm = ResumeArm(engine, sim_hub, clock=lambda: 1_700_000_000.0)
    # Window wide open and every other gate satisfied -- if anything is going
    # to restart it, this is the tick that does.
    monkeypatch.setattr(ResumeArm, "_window_open", lambda self, s, t: True)
    for _ in range(3):
        await arm.tick()

    assert not engine.running, "the resume tick restarted a deliberate abort"
    assert engine.state.get("state") == "aborted", (
        f"state moved off 'aborted' to {engine.state.get('state')!r}")


async def test_a_cancellation_that_is_not_an_operator_abort_stays_armed(sim_hub):
    """Resume-after-restart must keep working.

    A process teardown cancels the run task WITHOUT going through `abort()`
    (no caller of `engine.abort()` is a shutdown path -- the four are
    /api/sequence/abort, /api/disconnect and profile apply/activate, all
    deliberate human actions). That cancellation reaches the same
    `except asyncio.CancelledError -> _finalize_report("aborted")` arm, so
    keying the disarm on the reason string alone would silently retire the
    feature that exists for the 2am reboot. `_aborting` is the discriminator.
    """
    engine = SequenceEngine(sim_hub)

    async def cancel_like_a_shutdown():
        engine._task.cancel()
        try:
            await engine._task
        except (asyncio.CancelledError, Exception):
            pass

    sid = await _run_then(engine, cancel_like_a_shutdown)

    s = session_store.load(sid)
    assert s.status == "dormant"
    assert s.auto_resume is True, (
        "a cancelled task is not an operator abort -- disarming here would "
        "break resume-after-restart, which is the whole point of the feature")
