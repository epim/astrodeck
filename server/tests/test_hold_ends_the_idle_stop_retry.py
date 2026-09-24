"""A cloud hold that turns tracking back on ends the idle-stop retry first.

The idle park-hold reads its stop back, and a stop the mount did not confirm
is asked again by a task of its own (`_idle_stop_retry`, #189 A3) until the
spell ends. `_setup_target` cancels it, and waits for it, before it restores
tracking, so that the retry's ``set_tracking(False)`` can never land on the
next target.

A CLOUD HOLD GETS THERE FIRST. `_setup_target` runs its pre-slew safety gate
BEFORE its cancel, and on a rig with safety armed and no monitor assigned
(this rig's configuration) that gate opens the frames' own cloud hold, with
the new target, while the retry of the last spell's stop is still alive. The
hold (#203, #205) then turns tracking on by three routes of its own before the
setup ever reaches its cancel:

* `_hold_repoint` slews to the target and tracks it, when the mount is not on
  the held target or has left the zenith keep-out;
* `_enforce_tracking` resumes a mount that stopped on the held target;
* `_maybe_meridian_flip` takes a flip whose latch is armed.

With the retry left running, its next ask lands a ``set_tracking(False)``
after (or in the middle of) whatever the hold just did, and the hold has the
mount stopped on a target it has just said it is tracking. Each route now
cancels and awaits the retry before it turns tracking on.

THE HARNESS is `test_idle_stop_retry_clock`'s direct one: the real
`_idle_stop_retry` task on the simulator's mount, with the interval cut to a
fraction of a second, and a mount double on which the inline first stop is
not taken (so the retry is handed the stop) while the retry's own stop lands.
Each case then calls the route under test directly, as the hold does, and
waits several retry intervals.
"""
from __future__ import annotations

import asyncio
import time

import astrodeck.sequence.engine as engine_mod
from astrodeck.sequence import ExposureStep, SequencePlan, Target
from astrodeck.sequence import schedule

from test_idle_park_hold import (  # noqa: F401 (fixtures and harness)
    _ra_at, sim_hub, temp_store)

#: The retry interval here, in real seconds. Long against what each route
#: takes on the simulator's mount (milliseconds under the fast path), short
#: against a test.
RETRY = 0.2
#: How long each case waits after the route under test, in retry intervals.
AFTER = 4


def _target(name: str, ra: float, dec: float) -> Target:
    return Target(name=name, ra_hours=ra, dec_deg=dec, center=False,
                  autofocus_first=False,
                  steps=[ExposureStep(filter="L", exposure_s=60.0, gain=100,
                                      count=5)])


class _Mount:
    """The simulator's mount, with every ``set_tracking`` recorded as (what
    took effect, which task) and the inline first stop not taken, so the idle
    park-hold hands the stop to the retry task. The retry's own stop lands."""

    def __init__(self, hub, monkeypatch):
        self.tel = hub.devices["telescope"]
        self._real = self.tel.set_tracking
        self.effects: list[tuple[str, str]] = []
        monkeypatch.setattr(self.tel, "set_tracking", self._set_tracking)

    async def _set_tracking(self, on):
        task = asyncio.current_task()
        who = ("retry" if task is not None
               and task.get_name() == "idle-stop-retry" else "engine")
        if not on and who != "retry":
            raise asyncio.TimeoutError()     # the first attempt: not taken
        await self._real(bool(on))
        self.effects.append(("on" if on else "off", who))

    async def track(self, on: bool) -> None:
        """The mount's own state, set behind the engine's back."""
        await self._real(on)

    def tracking(self) -> bool:
        return bool(self.tel.rig.tracking)

    def after(self, i: int) -> list[tuple[str, str]]:
        return self.effects[i:]


async def _a_retry_alive(engine, mount: _Mount) -> asyncio.Task:
    """The idle park-hold's stop, not taken, handed to the retry task."""
    await mount.track(True)
    await engine._idle_park_hold("the next target is a long wait away")
    task = engine._idle_stop_task
    assert task is not None and not task.done(), "premise: a retry is alive"
    assert mount.tracking(), "premise: the first stop was not taken"
    return task


def _engine(hub, monkeypatch):
    monkeypatch.setattr(engine_mod, "IDLE_STOP_RETRY_S", RETRY)
    # `_tracking_now` confirms a False over four real seconds; nothing here
    # is about the confirm.
    monkeypatch.setattr(engine_mod, "TRACKING_CONFIRM_S", 0.0)
    return engine_mod.SequenceEngine(hub)


# ------------------------------------------------------------- the re-point

async def test_a_hold_repoint_ends_the_retry_before_it_slews(
        sim_hub, monkeypatch):
    """A hold opened by Bravo's pre-slew gate finds the mount stopped where
    Alpha left it and points it at Bravo (`_hold_repoint`, elsewhere), while
    the retry of Alpha's unconfirmed stop is still alive. Several retry
    intervals later the mount is still tracking Bravo, and no retry stop
    landed after the re-point turned tracking on.

    Mutant "no cancel in the re-point" (the `_cancel_idle_stop_retry` call in
    `_hold_repoint` deleted): RED (observed) -
        AssertionError: the retry of the last spell's stop landed after the
        hold pointed the mount at Bravo and tracked it, so the hold holds a
        stopped mount: effects after the re-point [('on', 'engine'), ('off',
        'retry')]
    """
    engine = _engine(sim_hub, monkeypatch)
    mount = _Mount(sim_hub, monkeypatch)
    task = await _a_retry_alive(engine, mount)
    bravo = _target("Bravo", _ra_at(-2.0, time.time()), 60.0)
    try:
        mark = len(mount.effects)
        await engine._hold_repoint(bravo, elsewhere=True)
        assert ("on", "engine") in mount.after(mark), (
            f"premise: the re-point turned tracking on: {mount.after(mark)}")
        assert engine._tracked_target is bravo, "premise: the mount is Bravo's"
        await asyncio.sleep(AFTER * RETRY)
        assert mount.tracking() and ("off", "retry") not in mount.after(mark), (
            f"the retry of the last spell's stop landed after the hold "
            f"pointed the mount at Bravo and tracked it, so the hold holds a "
            f"stopped mount: effects after the re-point {mount.after(mark)}")
        assert task.done() and engine._idle_stop_task is None
    finally:
        await engine._cancel_idle_stop_retry()


# ---------------------------------------------------------------- the resume

async def test_a_hold_resume_ends_the_retry_before_tracking_comes_back(
        sim_hub, monkeypatch):
    """The mount has stopped on the held target, and the hold resumes it in
    place (`_enforce_tracking`, the #205 branch), while the retry of the last
    spell's stop is still alive. Several retry intervals later it is still
    tracking.

    Mutant "no cancel in the resume" (the `_cancel_idle_stop_retry` call in
    `_enforce_tracking` deleted): RED (observed) -
        AssertionError: the retry of the last spell's stop landed after the
        hold resumed tracking on Alpha: effects after the resume [('on',
        'engine'), ('off', 'retry')]
    """
    engine = _engine(sim_hub, monkeypatch)
    mount = _Mount(sim_hub, monkeypatch)
    task = await _a_retry_alive(engine, mount)
    alpha = _target("Alpha", _ra_at(-2.0, time.time()), 60.0)
    await mount.track(False)            # the mount stops on its own
    try:
        mark = len(mount.effects)
        await engine._enforce_tracking(None, alpha)
        assert mount.after(mark)[:1] == [("on", "engine")], (
            f"premise: the resume turned tracking on: {mount.after(mark)}")
        await asyncio.sleep(AFTER * RETRY)
        assert mount.tracking() and ("off", "retry") not in mount.after(mark), (
            f"the retry of the last spell's stop landed after the hold "
            f"resumed tracking on Alpha: effects after the resume "
            f"{mount.after(mark)}")
        assert task.done() and engine._idle_stop_task is None
    finally:
        await engine._cancel_idle_stop_retry()


async def test_control_a_resume_with_nothing_to_resume_leaves_the_retry_asking(
        sim_hub, monkeypatch):
    """CONTROL. The mount still reports tracking, because the stop the
    retry is asking for has not been taken. `_enforce_tracking` has nothing
    to resume and turns nothing on, so the retry is not ended: it goes on
    asking for the stop it is owed.

    Mutant "cancel at the top of `_enforce_tracking`" (the cancel moved above
    the tracking read): RED (observed) -
        AssertionError: a resume that resumed nothing ended the retry of a
        stop still owed: retry asks after it []
    """
    engine = _engine(sim_hub, monkeypatch)
    mount = _Mount(sim_hub, monkeypatch)
    task = await _a_retry_alive(engine, mount)
    alpha = _target("Alpha", _ra_at(-2.0, time.time()), 60.0)
    try:
        mark = len(mount.effects)
        await engine._enforce_tracking(None, alpha)
        assert ("on", "engine") not in mount.after(mark), (
            f"premise: nothing was resumed: {mount.after(mark)}")
        await asyncio.sleep(AFTER * RETRY)
        asks = [e for e in mount.after(mark) if e == ("off", "retry")]
        assert asks and not task.cancelled(), (
            f"a resume that resumed nothing ended the retry of a stop still "
            f"owed: retry asks after it {asks}")
    finally:
        await engine._cancel_idle_stop_retry()


# ------------------------------------------------------------------ the flip

async def test_a_hold_flip_ends_the_retry_before_its_goto(sim_hub,
                                                         monkeypatch):
    """A flip whose latch is armed is taken from a hold's look
    (`_maybe_meridian_flip`) while the retry of the last spell's stop is
    still alive. The flip's goto turns tracking on; several retry intervals
    later it is still on.

    Mutant "no cancel before the flip" (the `_cancel_idle_stop_retry` call in
    `_maybe_meridian_flip` deleted): RED (observed) -
        AssertionError: the retry of the last spell's stop landed after the
        flip's goto turned tracking on: effects after the flip [('on',
        'engine'), ('off', 'retry')]
    """
    engine = _engine(sim_hub, monkeypatch)
    mount = _Mount(sim_hub, monkeypatch)
    # No post-flip sweep, and a mount that gives no countdown of its own, so
    # the flip point is the engine's arithmetic alone.
    sim_hub.devices.pop("focuser", None)

    async def no_countdown():
        raise RuntimeError("no countdown")

    monkeypatch.setattr(mount.tel, "time_to_meridian_flip", no_countdown)

    async def meridian_flip(ra, dec):
        await mount.tel.set_tracking(True)       # a goto turns tracking on
        return {"flipped": False}

    monkeypatch.setattr(sim_hub, "meridian_flip", meridian_flip)
    lead_s = 60.0 * schedule.MERIDIAN_FLIP_LEAD_MIN
    # A minute past its flip point: the flip is due now, with no wait for it.
    alpha = _target("Alpha", _ra_at(-(lead_s - 60.0) / 3600.0, time.time()),
                    20.0)
    engine.plan = SequencePlan(name="flip", guide=False, dither_every=0,
                               autofocus_every=0, meridian_flip=True,
                               targets=[alpha])
    task = await _a_retry_alive(engine, mount)
    engine._flip_armed = True
    try:
        mark = len(mount.effects)
        await engine._maybe_meridian_flip(alpha, 0.0)
        assert ("on", "engine") in mount.after(mark), (
            f"premise: the flip was taken and turned tracking on: "
            f"{mount.after(mark)}")
        await asyncio.sleep(AFTER * RETRY)
        assert mount.tracking() and ("off", "retry") not in mount.after(mark), (
            f"the retry of the last spell's stop landed after the flip's goto "
            f"turned tracking on: effects after the flip {mount.after(mark)}")
        assert task.done() and engine._idle_stop_task is None
    finally:
        await engine._cancel_idle_stop_retry()
