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


async def test_a_live_edit_survives_the_next_frame(sim_hub):
    """A 200 from PATCH /api/sessions/<id> must still be true one exposure later.

    `patch_session` (api/app.py:4187) deliberately allows `auto_resume` edits on
    an ACTIVE session -- "arming an active session is the point, not an edge
    case" -- and does it by loading a FRESH copy from the store
    (api/app.py:4192), mutating that, and saving it.

    The running engine holds a DIFFERENT object, captured at `start()`, and
    writes the whole of it back on EVERY frame (engine.py:4107, the ledger
    write). So the operator's edit is undone within one sub, and the route
    returned 200 while meaning no.

    This is the mechanism behind "PATCH returned 200 with auto_resume False and
    did not help" on 2026-08-24. The backlog note blamed finalize; finalize is
    the second clobber, not the first.

    Stated as the engine's invariant rather than the route's, because the route
    is only one caller: the ledger write owns `frames`, and must not carry
    operator-owned fields over the top of a newer store copy.
    """
    engine = SequenceEngine(sim_hub)
    engine.start(_plan())
    sid = engine._session.id
    try:
        assert await wait_for(lambda: engine._frames_done >= 1), "no first frame"

        # exactly what the route does, on the store, while the run is live
        edited = session_store.load(sid)
        assert edited.auto_resume is True, "precondition: start() armed it"
        edited.auto_resume = False
        session_store.save(edited)

        before = engine._frames_done
        assert await wait_for(lambda: engine._frames_done >= before + 1), "no second frame"

        assert session_store.load(sid).auto_resume is False, (
            "the engine's per-frame ledger write clobbered the operator's edit; "
            "a route that answers 200 and is undone one exposure later is a "
            "broken promise, not a race")
    finally:
        await engine.abort()


def test_save_run_state_never_writes_the_callers_auto_resume():
    """The primitive, pinned directly.

    The integration test above cannot isolate a single write site: the engine
    saves the session from the ledger write AND again from each thumbnail
    render, so reverting EITHER one alone still passes -- the other repairs it
    a moment later. Measured by sabotage: breaking only the ledger write left
    all four tests green. A guard that a later writer can mask is not a guard
    for the earlier one, so the shared primitive gets its own test.
    """
    from astrodeck.sequence.models import ExposureStep, SequencePlan, Target
    from astrodeck.sequence.session import Session

    plan = SequencePlan(name="p", targets=[Target(
        name="T", ra_hours=1.0, dec_deg=1.0,
        steps=[ExposureStep(filter="L", exposure_s=1.0, count=1)])])
    s = Session(name="owned", status="active", plan=plan, auto_resume=True)
    session_store.save(s)

    # the operator disarms it through the API, on a fresh copy
    edited = session_store.load(s.id)
    edited.auto_resume = False
    session_store.save(edited)

    # the run writes its OWN stale copy back, which still says True
    assert s.auto_resume is True, "precondition: the run's copy is stale"
    s.name = "written by the run"
    session_store.save_run_state(s)

    back = session_store.load(s.id)
    assert back.auto_resume is False, "the run clobbered an operator-owned field"
    assert back.name == "written by the run", (
        "save_run_state must still write the fields the RUN owns")
    assert s.auto_resume is False, (
        "the caller's copy must stop being stale, or it diverges again on the "
        "next write")
