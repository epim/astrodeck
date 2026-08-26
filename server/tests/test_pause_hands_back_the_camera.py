"""Pausing a run must hand the camera back, and must stay paused.

Reported from the rig 2026-08-26: "when I paused the flow it just auto resumed
itself and I couldn't manually trigger a capture."

Both halves are real and they are different bugs.

THE CAMERA. ``engine.running`` is ``_task is not None and not _task.done()``,
which is still True for a PAUSED run -- the task exists, it is parked on
``_checkpoint``'s ``await self._paused.wait()``. Four routes refuse with
"a sequence is running" on that flag: /api/capture, /api/capture/loop,
/api/capture/livestack/start and /api/focuser/bahtinov/start. Every one of them
is the thing an operator pauses in order to do. Nothing is exposing while
paused -- the gate is a FRAME BOUNDARY -- so the camera is genuinely free and
the refusal is about a flag rather than about the hardware.

THE FLAG. ``pause()`` clears the event and ``resume()`` sets it, but
``abort()`` never touches it, and both pause() and resume() early-return while
``_aborting``. So aborting a paused run leaves ``paused`` True with no run at
all. Read off the rig at 00:08 while writing this:

    state: aborted   running: False   paused: True

A rig that has stopped, telling every client it is merely paused, with Resume
lit on nothing.

THE PAUSE ITSELF. ``start()`` sets the event unconditionally, so ANY restart
silently discards a pause -- including one auto-resume issues. That is how a
pause "resumes itself": the operator's decision lives only in an in-memory
event that the next start overwrites without a word.
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
    return SequencePlan(name="pause-me", guide=False, dither_every=0,
                        autofocus_every=0, meridian_flip=False, targets=[Target(
                            name="M42", ra_hours=5.5881, dec_deg=-5.3911,
                            center=False, autofocus_first=False,
                            steps=[ExposureStep(filter="L", exposure_s=0.05,
                                                count=400)])])


async def test_a_running_run_owns_the_camera(sim_hub):
    """The control. Without this the next test passes on a broken predicate."""
    engine = SequenceEngine(sim_hub)
    engine.start(_plan())
    try:
        assert await wait_for(lambda: engine._frames_done >= 1)
        assert engine.owns_camera is True
    finally:
        await engine.abort()


async def test_pausing_hands_the_camera_back(sim_hub):
    """The operator's complaint, as a property.

    The pause gate is a frame boundary, so a paused run is not mid-exposure and
    the camera is free. Refusing a manual frame here refuses it on the strength
    of a flag, not of the hardware.
    """
    engine = SequenceEngine(sim_hub)
    engine.start(_plan())
    try:
        assert await wait_for(lambda: engine._frames_done >= 1)
        engine.pause()
        assert await wait_for(lambda: engine.paused)
        assert engine.running is True, (
            "precondition: the task is alive, which is why `running` refused")
        assert engine.owns_camera is False, (
            "a paused run must release the camera -- taking a frame is what an "
            "operator pauses in order to do")
    finally:
        engine.resume()
        await engine.abort()


async def test_resuming_takes_the_camera_back(sim_hub):
    engine = SequenceEngine(sim_hub)
    engine.start(_plan())
    try:
        assert await wait_for(lambda: engine._frames_done >= 1)
        engine.pause()
        assert await wait_for(lambda: engine.paused)
        engine.resume()
        assert await wait_for(lambda: not engine.paused)
        assert engine.owns_camera is True
    finally:
        await engine.abort()


async def test_an_aborted_run_is_not_still_paused(sim_hub):
    """The stuck flag, read off the rig verbatim.

    `pause()` and `resume()` both early-return while `_aborting`, so nothing
    inside the teardown could clear it either.
    """
    engine = SequenceEngine(sim_hub)
    engine.start(_plan())
    assert await wait_for(lambda: engine._frames_done >= 1)
    engine.pause()
    assert await wait_for(lambda: engine.paused)

    await engine.abort()

    assert engine.running is False
    assert engine.paused is False, (
        "a stopped run is not a paused one: the rig published "
        "state=aborted running=False paused=True and lit Resume on nothing")
    assert engine.owns_camera is False


async def test_a_pause_is_not_silently_undone_by_auto_resume(sim_hub, monkeypatch):
    """"I paused the flow and it just auto resumed itself."

    An operator pause is a decision, exactly as an abort is. If the run then
    ends -- dawn, weather, an error -- the session goes dormant with
    auto_resume still armed, the tick restarts it, and `start()` sets the pause
    event on the way in. The pause is gone and nothing ever said so.
    """
    engine = SequenceEngine(sim_hub)
    engine.start(_plan())
    sid = engine._session.id
    assert await wait_for(lambda: engine._frames_done >= 1)
    engine.pause()
    assert await wait_for(lambda: engine.paused)
    await engine.abort()

    arm = ResumeArm(engine, sim_hub, clock=lambda: 1_700_000_000.0)
    monkeypatch.setattr(ResumeArm, "_window_open", lambda self, s, t: True)
    for _ in range(3):
        await arm.tick()

    assert not engine.running, "auto-resume restarted a run the operator paused"
    assert session_store.load(sid).auto_resume is False
