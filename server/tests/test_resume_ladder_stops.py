# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Abort and disarm stop ResumeArm's recovery ladder, and the resume-arm
route says it is recovering (#220; mosaic slice H2 task T3; spec 5.9 "Starts
are refused while auto-resume re-centres", 6.15 "Operator STOP").

After a restart the ladder reads the safety monitor, may autofocus,
blind-solves and re-centres the mount, and it is minutes long. H1 made every
start route refuse while it runs (409 ``resume_recovering``) and made the
ladder stop once a run had started, but nothing an operator could do stopped
it. Abort had no run to abort, so the ladder slewed on and started the
session a moment later. A disarm was read only after the ladder, so the
mount was re-centred for a session nobody wanted any more. And no route said
the ladder was running, so the 409 told the operator to wait for something
they could not see.

Now ``ResumeArm.stop_recovery`` asks the ladder to stop. It raises a flag the
ladder reads at the same between-step points ``_a_run_took_over`` guards, and
it cancels the step the ladder is awaiting. A ladder stopped that way is
neither a refusal nor a crash: no backoff, no crash counted, one stand-down
line. ``POST /api/sequence/abort`` stops it and disarms the session it was
recovering, as Abort disarms a running one. ``PATCH /api/sessions/{id}``
that disarms or abandons the session being recovered stops it, and so does
arming another session (the singleton disarms this one) and ``DELETE`` of it.
``GET /api/sequence/resume-arm`` reports ``recovering`` and the step in words.

THE HARNESS is ``test_flows_continue.py``'s: the real app over ASGI on this
loop and a real ``SequenceEngine`` whose imaging loop alone is replaced, with
a recorder on ``engine.start``. ResumeArm is real, and so is its ``tick`` and
its ``_recover``: only the sky window and the plate solver's presence are
pinned. It runs on a recording hub whose steps can each be PARKED on an
``asyncio.Event`` (the H1 T9 ladder harness in
``test_resume_arm_stale_start.py``, with every step made holdable), on the
rig's own engine, and it is installed as ``app_module.resume_arm``, the one
instance the routes read. Every party is on the event loop, so waits are
counted in turns of the loop, never in seconds, and the one timeout is a
deadlock guard.

Each mutant was applied to a byte-for-byte backup of the file it changes, in
a copy of ``server/`` so no other suite saw it, and the file was restored
byte-identical (SHA-256 compared) afterwards. The failures are quoted as
observed (``--tb=short``).
"""
from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

import astrodeck.api.app as app_module
import astrodeck.sequence.resume_arm as resume_arm_module
from astrodeck.devices import fingerprint as fingerprint_module
from astrodeck.sequence.models import ExposureStep, SequencePlan, Target
from astrodeck.sequence.resume_arm import LADDER_STEPS, ResumeArm
from astrodeck.sequence.session import Session, session_store
from test_flows_continue import rig  # noqa: F401 (fixture)

#: A deadlock guard for waits a healthy run satisfies at once.
DEADLOCK_GUARD_S = 30.0

#: Turns of the loop a stopped ladder gets to unwind in. A cancelled step
#: needs two or three; this is a cap, not a wait.
TURN_CAP = 1000

FOCUS_POSITION = 9935

#: The ladder steps the route test parks on, one case each.
ROUTE_STEPS = ("safety", "focus", "autofocus", "solve", "limits", "recentre")


class _Step:
    """One ladder step the test can hold.

    ``hold`` parks the step until ``release`` is set; ``entered`` says the
    ladder reached it. ``ended`` records how it ended: "released", or
    "cancelled" when a stop cancelled the await. ``stubborn`` makes the step
    swallow a cancel and wait for ``release`` anyway, then return normally:
    a driver call that runs to its end whatever its caller does, which is the
    case only the ladder's between-step flag can stop."""

    def __init__(self) -> None:
        self.hold = False
        self.stubborn = False
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.ended: str | None = None

    async def __call__(self) -> None:
        if not self.hold:
            return
        self.entered.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            if not self.stubborn:
                self.ended = "cancelled"
                raise
            await self.release.wait()
            self.ended = "ran to its end"
            return
        self.ended = "released"


class _Connected:
    connected = True


class _Focuser:
    connected = True

    def __init__(self, step: _Step) -> None:
        self._step = step

    async def get_position(self) -> int:
        await self._step()
        return FOCUS_POSITION


class _ParkedHub:
    """Records what the ladder asked of the rig; nothing physical happens.
    ``calls`` holds the invasive steps in order: autofocus, the solve and
    the re-centring goto."""
    site: dict = {}
    dusk_arm = None

    def __init__(self) -> None:
        self.steps = {name: _Step() for name in
                      ("safety", "focus", "autofocus", "solve", "limits",
                       "recentre")}
        self.focuser = _Focuser(self.steps["focus"])
        self.devices = {"camera": _Connected(), "telescope": _Connected(),
                        "focuser": self.focuser}
        self.calls: list[str] = []

    def require(self, role):
        return self.focuser if role == "focuser" else object()

    async def solve_and_sync(self, exposure_s: float = 3.0, *,
                             blind: bool = False,
                             refusal_level: str = "warning"):
        self.calls.append("solve")
        await self.steps["solve"]()
        return {"ok": True}

    async def goto_and_center(self, ra, dec, *a, **k) -> None:
        self.calls.append("goto")
        await self.steps["recentre"]()


def _plan(name: str) -> SequencePlan:
    return SequencePlan(name=name, guide=False, dither_every=0,
                        meridian_flip=False, targets=[Target(
                            name="M42", ra_hours=5.5881, dec_deg=-5.3911,
                            steps=[ExposureStep(filter="L", exposure_s=1.0,
                                                count=2)])])


def _dormant(name: str, *, armed: bool) -> Session:
    s = Session(name=name, status="dormant", plan=_plan(name),
                auto_resume=armed)
    session_store.save(s)
    return s


def _trust_the_focuser() -> None:
    """A record from before this process, matching what the focuser reads:
    focus is trusted and the ladder skips autofocus."""
    fingerprint_module.record(focuser_position=FOCUS_POSITION, filter_slot=0,
                              ra_hours=1.0, dec_deg=2.0, parked=False,
                              tracking=True)
    fingerprint_module.reset_for_tests()


@pytest.fixture
def ladder(rig, tmp_path, monkeypatch):
    """A real ResumeArm on the rig's engine and a parked hub, installed as
    the app's ``resume_arm``, with one armed dormant session for it to
    recover. The engine's slew-limit check and safety read are the hub's
    parkable steps. Focus is trusted unless a test says otherwise."""
    monkeypatch.setattr(fingerprint_module, "_PATH", tmp_path / "fp.json")
    fingerprint_module.reset_for_tests()
    hub = _ParkedHub()

    async def check_slew_limits(target, *, cfg=None, plan=None,
                                projected=True) -> None:
        await hub.steps["limits"]()

    async def current_safety():
        await hub.steps["safety"]()
        return SimpleNamespace(stale=False, is_safe=True, reason="")

    async def autofocus() -> None:
        hub.calls.append("autofocus")
        await hub.steps["autofocus"]()

    monkeypatch.setattr(rig.engine, "check_slew_limits", check_slew_limits)
    monkeypatch.setattr(rig.engine, "current_safety", current_safety)
    arm = ResumeArm(rig.engine, hub)
    monkeypatch.setattr(arm, "_window_open", lambda s, t: True)
    monkeypatch.setattr(arm, "_can_solve", lambda: True)
    monkeypatch.setattr(arm, "_can_autofocus", lambda: True)
    monkeypatch.setattr(arm, "_autofocus", autofocus)
    monkeypatch.setattr(app_module, "resume_arm", arm)
    _trust_the_focuser()
    session = _dormant("recover me", armed=True)
    assert session_store.armed().id == session.id, "premise: armed"
    yield SimpleNamespace(arm=arm, hub=hub, session=session, rig=rig)
    fingerprint_module.reset_for_tests()


async def _park(lad, where: str) -> asyncio.Task:
    """Start a tick and return once its ladder is parked in step ``where``.
    A tick that finishes without reaching it fails here, by name."""
    step = lad.hub.steps[where]
    step.hold = True
    tick = asyncio.create_task(lad.arm.tick())
    waiter = asyncio.ensure_future(step.entered.wait())
    done, _ = await asyncio.wait({waiter, tick}, timeout=DEADLOCK_GUARD_S,
                                 return_when=asyncio.FIRST_COMPLETED)
    if waiter not in done:
        waiter.cancel()
        if tick.done():
            tick.result()
        raise AssertionError(f"the tick never reached the {where} step")
    assert lad.arm.recovering, "premise: the ladder is running"
    return tick


async def _unwound(task: asyncio.Future) -> bool:
    """Give a stopped ladder (or a stopping service) the loop to unwind on,
    counted in turns: True when ``task`` finished on its own, without any
    step being released."""
    for _ in range(TURN_CAP):
        if task.done():
            return True
        await asyncio.sleep(0)
    return task.done()


async def _finish(lad, tick: asyncio.Task) -> None:
    """Release every step and wait for the tick: whatever the code under
    test did, the test ends with no tick left running."""
    for step in lad.hub.steps.values():
        step.release.set()
    await asyncio.wait_for(tick, DEADLOCK_GUARD_S)


def _stood_down(bus_lines) -> list[str]:
    return [m for lvl, m, _src in bus_lines
            if lvl == "info" and "stood down" in m]


# ------------------------------------------------------------------ Abort

@pytest.mark.parametrize("where", ["focus", "solve", "limits", "recentre",
                                   "solve_stubborn"])
async def test_abort_mid_ladder_stops_it_before_its_next_step(
        ladder, bus_lines, where):
    """``POST /api/sequence/abort`` while the ladder is parked in a step. The
    ladder must take no step after the abort: no solve and no goto that it
    had not already begun. The step it was awaiting is cancelled, so the
    tick finishes without that step being let go. The session it was
    recovering is disarmed, with the reason in the log, and the stop is not
    a refusal (no backoff, no hold) or a crash (``crash_resumes`` untouched).
    No start follows, on this tick or the next.

    ``focus``: parked reading the focuser, so the solve is next (focus is
    trusted). ``solve``: parked in the solve, so the limit check and the
    goto are next. ``limits``: parked in the slew-limit check, the await
    right before the goto. ``recentre``: parked in the goto itself, which
    the stop cancels: the ladder does not wait out the slew, does not record
    the mount as re-centred, and starts nothing (what a cancelled goto does
    on the mount is the driver's; ``stop_recovery`` says what the contract
    promises). ``solve_stubborn``: the solve swallows the cancel and runs to
    its end when released, so only the between-step flag can keep the goto
    from following it.

    RED under mutant "abort ignores the ladder" (``sequence_abort``'s
    ``resume_arm.stop_recovery`` call removed), every case, and the goto
    still happens. Observed verbatim:

        [focus]
        E   AssertionError: the ladder went on after the abort: ['solve', 'goto']
        E   assert ['solve', 'goto'] == []
        [solve]
        E   AssertionError: the ladder went on after the abort: ['solve', 'goto']
        E   assert ['solve', 'goto'] == ['solve']
        [limits] the same as [solve]
        [solve_stubborn] the same as [solve]
        [recentre]
        E   AssertionError: a stopped ladder recorded the mount as re-centred
        E   assert Target(id='375dbc8ffcac42f8ade10e955836a1bd', name='M42', ra_hours=5.5881, dec_deg=-5.3911, center=True, autofocus_fir...m_pct=0.0, max_hour_angle_h=0.0, stop_mode='none', stop_offset_min=0, stop_time=None, max_run_min=0, on_missed='wait')) is None

    RED under mutant "flag only, no cancel" (``stop_recovery``'s
    ``ladder.cancel()`` replaced by ``pass``), every case. In the three
    cancellable ones the flag still keeps the goto back once the step is
    let go, but the step itself was never cut short; the stubborn solve got
    no cancel to swallow, so its premise fails. Observed verbatim:

        [focus] [solve] [limits]
        E   AssertionError: the abort did not cancel the step the ladder was awaiting
        E   assert False is True
        [recentre] the goto ran to its end once let go:
        E   AssertionError: a stopped ladder recorded the mount as re-centred
        [solve_stubborn]
        E   AssertionError: premise: the stubborn solve ran to its end
        E   assert 'released' == 'ran to its end'

    RED under mutant "cancel only, no flag" (``_must_stop`` returns
    ``self._a_run_took_over()`` alone), ``solve_stubborn`` only, the other
    four staying green:

        [solve_stubborn]
        E   AssertionError: the ladder went on after the abort: ['solve', 'goto']
        E   assert ['solve', 'goto'] == ['solve']

    RED under mutant "abort stops but does not disarm" (``disarm=True``
    dropped from the route's call), every case, at the stored session:

        [focus] [solve] [limits] [recentre] [solve_stubborn]
        E   AssertionError: the aborted session is still armed
        E   assert True is False

    RED under mutant "a stop is a refusal" (the tick's stand-down branch
    arms ``_retry_at`` and ``_set_hold`` as the refusal branch does),
    every case:

        [solve]
        E   AssertionError: a stopped ladder is not a refusal: no backoff, no hold
        E   assert (1790294859.2...ab1225', ...}) == (0.0, None)
        [focus] [limits] [recentre] [solve_stubborn] the same two lines
    """
    lad = ladder
    park_at = "solve" if where == "solve_stubborn" else where
    if where == "solve_stubborn":
        lad.hub.steps["solve"].stubborn = True
    before_starts = len(lad.rig.starts)
    tick = await _park(lad, park_at)
    try:
        r = await lad.rig.client.post("/api/sequence/abort")
        if where == "solve_stubborn":
            lad.hub.steps["solve"].release.set()
        unwound = await _unwound(tick)
    finally:
        await _finish(lad, tick)

    assert r.status_code == 200, r.text
    expected = {"focus": [], "solve": ["solve"], "limits": ["solve"],
                "recentre": ["solve", "goto"],
                "solve_stubborn": ["solve"]}[where]
    assert lad.hub.calls == expected, (
        f"the ladder went on after the abort: {lad.hub.calls}")
    assert lad.arm._recentred is None, (
        "a stopped ladder recorded the mount as re-centred")
    if where == "solve_stubborn":
        assert lad.hub.steps["solve"].ended == "ran to its end", (
            "premise: the stubborn solve ran to its end")
    else:
        assert unwound is True, (
            "the abort did not cancel the step the ladder was awaiting")
        assert lad.hub.steps[park_at].ended == "cancelled", (
            f"the {park_at} step was not cancelled: "
            f"{lad.hub.steps[park_at].ended}")
    assert lad.rig.starts[before_starts:] == [], (
        f"a start followed the abort: {lad.rig.starts[before_starts:]}")
    assert (lad.arm._retry_at, lad.arm.hold) == (0.0, None), (
        "a stopped ladder is not a refusal: no backoff, no hold")
    assert lad.arm.recovering is False
    stored = session_store.load(lad.session.id)
    assert stored.auto_resume is False, "the aborted session is still armed"
    assert (stored.status, stored.crash_resumes) == ("dormant", 0), (
        "a stopped ladder is not a crash")
    said = [m for lvl, m, _src in bus_lines
            if lvl == "info" and "stopped by hand" in m]
    assert len(said) == 1 and "'recover me'" in said[0] \
        and "disarmed" in said[0], said
    lines = _stood_down(bus_lines)
    assert len(lines) == 1 and "Abort" in lines[0], lines

    # The next tick, the one that used to restart it 60 s later.
    calls_before = list(lad.hub.calls)
    for step in lad.hub.steps.values():
        step.hold = False
    await lad.arm.tick()
    assert lad.hub.calls == calls_before, (
        f"the next tick ran the ladder again: {lad.hub.calls}")
    assert lad.rig.starts[before_starts:] == [], (
        f"the next tick started the aborted session: "
        f"{lad.rig.starts[before_starts:]}")


async def test_a_step_that_finishes_during_the_abort_s_await_is_not_followed_by_a_goto(
        ladder, bus_lines, monkeypatch):
    """#189 item 10 (b): ``sequence_abort`` stops the ladder BEFORE its first
    await, and this is the case that says why the order matters.

    The ladder is parked in a stubborn slew-limit check (it swallows the
    cancel and runs to its end once released), the await right before the
    goto. ``engine.abort``, the route's first await, is made to release that
    step and then give the loop turns enough for a ladder that has not been
    told to stop to reach its goto. With the stop made before the await, the
    step runs to its end, the ladder finds the flag at its next between-step
    point and returns: no goto, and no start. With the stop made after, the
    step finished into a ladder nobody had stopped yet.

    The test above cannot see the order: every case in it lets the step go
    only after the route has returned, by which time the stop has been made
    whichever side of the await it was on.

    RED under mutant "stop_recovery moved after the route's first await"
    (``resume_arm.stop_recovery(...)`` placed after ``await
    engine.abort()``), observed verbatim:

        E   AssertionError: a goto followed a step that finished during the abort's await: ['solve', 'goto']
        E   assert ['solve', 'goto'] == ['solve']
    """
    lad = ladder
    limits = lad.hub.steps["limits"]
    limits.stubborn = True
    before_starts = len(lad.rig.starts)
    tick = await _park(lad, "limits")
    real_abort = lad.rig.engine.abort

    async def abort_that_lets_the_step_finish() -> None:
        limits.release.set()
        for _ in range(TURN_CAP // 10):
            await asyncio.sleep(0)
        await real_abort()

    monkeypatch.setattr(lad.rig.engine, "abort",
                        abort_that_lets_the_step_finish)
    try:
        r = await lad.rig.client.post("/api/sequence/abort")
        await _unwound(tick)
    finally:
        await _finish(lad, tick)

    assert r.status_code == 200, r.text
    assert lad.hub.calls == ["solve"], (
        f"a goto followed a step that finished during the abort's await: "
        f"{lad.hub.calls}")
    assert limits.ended == "ran to its end", (
        "premise: the slew-limit check ran to its end inside the abort's "
        f"await: {limits.ended}")
    assert lad.arm._recentred is None
    assert lad.rig.starts[before_starts:] == [], (
        f"a start followed the abort: {lad.rig.starts[before_starts:]}")
    assert session_store.load(lad.session.id).auto_resume is False


async def test_control_abort_with_no_ladder_leaves_the_armed_session_alone(
        ladder, bus_lines):
    """CONTROL: Abort with no ladder running changes nothing about auto-
    resume. The armed session stays armed and the next tick recovers and
    starts it as before. An abort disarms what it stops, not whatever
    happens to be armed.

    RED under mutant "stop ignores recovering" (``stop_recovery`` takes
    ``self._ladder_session or session_store.armed()`` and no longer asks
    ``self._recovering``), observed verbatim:

        E   AssertionError: an abort with no ladder disarmed the armed session
        E   assert False is True
    """
    lad = ladder
    before_starts = len(lad.rig.starts)
    r = await lad.rig.client.post("/api/sequence/abort")
    assert r.status_code == 200, r.text
    assert session_store.load(lad.session.id).auto_resume is True, (
        "an abort with no ladder disarmed the armed session")
    await lad.arm.tick()
    assert lad.hub.calls == ["solve", "goto"], lad.hub.calls
    assert [(s.won, s.session_id) for s in lad.rig.starts[before_starts:]] \
        == [(True, lad.session.id)], lad.rig.starts[before_starts:]
    assert _stood_down(bus_lines) == []


# --------------------------------------------------------------- Shutdown

async def _service_parked(lad, where: str = "solve") -> None:
    """Start the service's own loop and return once its first tick is
    parked in step ``where``."""
    step = lad.hub.steps[where]
    step.hold = True
    lad.arm.start()
    await asyncio.wait_for(step.entered.wait(), DEADLOCK_GUARD_S)
    assert lad.arm.recovering, "premise: the ladder is running"


@pytest.mark.parametrize("with_operator_stop", [False, True])
async def test_the_service_stopping_mid_ladder_stops_and_starts_nothing(
        ladder, bus_lines, with_operator_stop):
    """The app shuts down (``ResumeArm.stop``) while the ladder is parked in
    the solve. The ladder now runs as its own task, and the tick absorbs a
    cancel of THAT task when an operator asked for it; a cancel of the tick
    itself must still propagate, or the service would not stop. And a
    shutdown is not an abort: the session stays armed, because
    resume-after-restart is exactly the feature a shutdown must leave
    intact, and no start is made on the way down.

    ``with_operator_stop``: an operator's stop lands in the same turn as the
    shutdown, so the tick sees both a stop request and its own cancel.

    RED under mutant "every ladder cancel is absorbed" (the tick's
    ``current_task()`` / ``if ... : raise`` lines removed, so a
    CancelledError from the ladder always reads as a stop), both cases.
    Without an operator stop the tick carried on from the cancelled ladder
    and started the session; with one it stood down; either way the
    service loop went on to its 60 s sleep. Observed verbatim:

        [False]
        E   AssertionError: the shutdown started the session: [Start(won=True, session_id='ad31f5f7bef14889b112f7089e957afd', frames=[], frame_ids=[], step_ids=['87b03d32a20a4b3b9bd6d855d29f0bba'], count_mode='attempts', cool_to=None, error='')]
        E   assert [Start(won=Tr...ne, error='')] == []
        [True]
        E   AssertionError: the service did not stop: its loop went on past the cancel
        E   assert False is True

    RED under mutant "cancelling() not consulted" (``if self._stop_why is
    None: raise``), the case with an operator stop only: the tick took the
    shutdown's cancel for the operator's, stood down, and the service loop
    went on, so ``stop`` never returned. Observed verbatim:

        [True]
        E   AssertionError: the service did not stop: its loop went on past the cancel
        E   assert False is True

    Both mutants stayed GREEN against this test's first draft, which
    bounded ``arm.stop()`` with ``asyncio.wait_for``: see the comment at
    the call.
    """
    lad = ladder
    before_starts = len(lad.rig.starts)
    await _service_parked(lad)
    service = lad.arm._task
    stopper: asyncio.Future | None = None
    try:
        if with_operator_stop:
            lad.arm.stop_recovery("the operator pressed Abort")
        # NOT ``wait_for(arm.stop(), ...)``. ``stop`` swallows a
        # CancelledError, including the one ``wait_for``'s timeout sends it,
        # so ``wait_for`` returned normally after its timeout and a service
        # that never stopped passed as stopped: both mutants below stayed
        # green that way. So ``stop`` runs as its own task and the test
        # counts turns of the loop until it is done, which a healthy stop is
        # within a handful of.
        stopper = asyncio.ensure_future(lad.arm.stop())
        stopped = await _unwound(stopper)
    finally:
        for step in lad.hub.steps.values():
            step.release.set()
        service.cancel()
        await asyncio.wait([t for t in (service, stopper) if t is not None],
                           timeout=DEADLOCK_GUARD_S)

    assert lad.rig.starts[before_starts:] == [], (
        f"the shutdown started the session: {lad.rig.starts[before_starts:]}")
    assert stopped is True, (
        "the service did not stop: its loop went on past the cancel")
    assert service.cancelled(), (
        "the service loop ended some other way than by its cancel")
    assert lad.hub.calls == ["solve"], lad.hub.calls
    assert lad.hub.steps["solve"].ended == "cancelled"
    assert lad.arm.recovering is False
    assert lad.arm._task is None, "the service is still running"
    assert session_store.load(lad.session.id).auto_resume is True, (
        "a shutdown disarmed the session it interrupted")


# ----------------------------------------------------------------- Disarm

async def _disarm(lad, how: str, session_id: str):
    c = lad.rig.client
    if how == "auto_resume_false":
        return await c.patch(f"/api/sessions/{session_id}",
                             json={"auto_resume": False})
    if how == "abandoned":
        return await c.patch(f"/api/sessions/{session_id}",
                             json={"status": "abandoned"})
    if how == "delete":
        return await c.delete(f"/api/sessions/{session_id}")
    raise AssertionError(how)


@pytest.mark.parametrize("how", ["auto_resume_false", "abandoned", "delete",
                                 "arm_another"])
async def test_disarming_the_recovered_session_stops_the_ladder(
        ladder, bus_lines, how):
    """The operator withdraws the session the ladder is recovering while it
    is parked in the solve. The ladder must stop before its next step (no
    goto), the solve it was awaiting is cancelled, no start follows, and the
    stop is neither a refusal nor a crash.

    ``auto_resume_false`` and ``abandoned`` are the PATCH bodies that disarm.
    ``delete`` is ``DELETE /api/sessions/{id}``: the same class, decided in
    ``delete_session``. ``arm_another`` is ``PATCH {"auto_resume": true}`` on
    a different dormant session, whose singleton disarms the one being
    recovered.

    RED under mutant "disarm read only after the ladder" (``patch_session``
    and ``delete_session`` no longer call ``resume_arm.stop_recovery``, so
    the ladder learns of the disarm only in ``_still_startable``, after it),
    every case, and the goto still happens. Observed verbatim:

        [auto_resume_false]
        E   AssertionError: the ladder went on after the disarm: ['solve', 'goto']
        E   assert ['solve', 'goto'] == ['solve']
        [abandoned] [delete] [arm_another] the same line

    RED under mutant "the singleton does not stop the ladder" (the
    ``stop_recovery`` call in PATCH's singleton loop removed; since #837 that
    loop is ``SequenceEngine._arm_exclusively`` and the call is the
    ``on_disarm`` hook the route hands it, replaced by a no-op in the
    re-run of 2026-10-07), ``arm_another`` only, the same two lines. Under "delete does not stop
    the ladder" (the call in ``delete_session`` removed), ``delete`` only,
    and under "abandon does not stop" (PATCH's condition reduced to
    ``body.auto_resume is False``), ``abandoned`` only, the same two lines
    each time.

    RED under mutant "flag only, no cancel", every case, observed verbatim:

        [auto_resume_false] [abandoned] [delete] [arm_another]
        E   AssertionError: the disarm did not cancel the step the ladder was awaiting
        E   assert (False is True)

    and under "a stop is a refusal", every case, with the no-backoff line
    quoted in the abort test above.
    """
    lad = ladder
    other = _dormant("the other one", armed=False)
    before_starts = len(lad.rig.starts)
    tick = await _park(lad, "solve")
    try:
        if how == "arm_another":
            r = await lad.rig.client.patch(f"/api/sessions/{other.id}",
                                           json={"auto_resume": True})
        else:
            r = await _disarm(lad, how, lad.session.id)
        unwound = await _unwound(tick)
    finally:
        await _finish(lad, tick)

    assert r.status_code == 200, r.text
    assert lad.hub.calls == ["solve"], (
        f"the ladder went on after the disarm: {lad.hub.calls}")
    assert unwound is True and lad.hub.steps["solve"].ended == "cancelled", (
        "the disarm did not cancel the step the ladder was awaiting")
    assert lad.rig.starts[before_starts:] == [], (
        f"a start followed the disarm: {lad.rig.starts[before_starts:]}")
    assert (lad.arm._retry_at, lad.arm.hold) == (0.0, None), (
        "a stopped ladder is not a refusal: no backoff, no hold")
    assert lad.arm.recovering is False
    lines = _stood_down(bus_lines)
    assert len(lines) == 1 and "'recover me'" in lines[0], lines
    if how == "delete":
        assert all(s.id != lad.session.id for s in session_store.load_all())
        return
    stored = session_store.load(lad.session.id)
    assert stored.auto_resume is False and stored.crash_resumes == 0, stored
    if how == "arm_another":
        assert session_store.armed().id == other.id
        return
    # No start on the next tick either: nothing is armed.
    for step in lad.hub.steps.values():
        step.hold = False
    await lad.arm.tick()
    assert lad.hub.calls == ["solve"], (
        f"the next tick ran the ladder again: {lad.hub.calls}")
    assert lad.rig.starts[before_starts:] == []


@pytest.mark.parametrize("how", ["auto_resume_false", "abandoned", "delete"])
async def test_control_disarming_another_session_leaves_the_ladder_running(
        ladder, bus_lines, how):
    """CONTROL: the same requests on a DIFFERENT session do not stop the
    ladder. The other session is armed as well, written straight to the
    store the way a hand-edited file would carry it, older than the one
    being recovered, so ``armed()`` picks the recovered one and the route's
    stop request names a session the ladder is not recovering. Released,
    the ladder re-centres and ResumeArm starts its session.

    RED under mutant "stop ignores the session id" (``stop_recovery``'s
    ``session_id`` comparison removed), every case: the request for the
    other session stopped this ladder, whose tick then finished with no
    step released. Observed verbatim:

        [auto_resume_false] [abandoned] [delete]
        E   AssertionError: disarming another session ended the tick
        E   assert False
    """
    lad = ladder
    other = _dormant("the other one", armed=True)
    # Saved again AFTER the other one, so it is the newest armed session and
    # the one ``armed()`` hands the tick.
    session_store.save(lad.session)
    assert session_store.load(other.id).auto_resume is True, "premise"
    assert session_store.armed().id == lad.session.id, "premise"
    before_starts = len(lad.rig.starts)
    tick = await _park(lad, "solve")
    try:
        r = await _disarm(lad, how, other.id)
        still_parked = not await _unwound(tick)
    finally:
        await _finish(lad, tick)

    assert r.status_code == 200, r.text
    assert still_parked, "disarming another session ended the tick"
    assert lad.hub.calls == ["solve", "goto"], (
        f"disarming another session stopped the ladder: {lad.hub.calls}")
    assert [(s.won, s.session_id) for s in lad.rig.starts[before_starts:]] \
        == [(True, lad.session.id)], lad.rig.starts[before_starts:]
    assert _stood_down(bus_lines) == []


@pytest.mark.parametrize("how", ["rearm", "plan_edit"])
async def test_control_a_patch_that_keeps_the_session_armed_leaves_the_ladder_running(
        ladder, bus_lines, how):
    """CONTROL: a PATCH of the session being recovered that neither disarms
    nor abandons it does not stop the ladder. ``rearm`` sends
    ``{"auto_resume": true}`` to the session that is already armed;
    ``plan_edit`` sends the session's own plan back, a dormant plan edit in
    which ``auto_resume`` is absent. Only ``auto_resume: false`` and
    ``status: abandoned`` withdraw the session, so the stop must not ride on
    every PATCH of it, and an absent ``auto_resume`` (None) is not a disarm.
    Released, the ladder re-centres and ResumeArm starts the session.

    The control above cannot see this: it sends its requests to ANOTHER
    session, whose id ``stop_recovery`` refuses whatever the condition in
    front of the call says. Both mutants below stayed green against every
    other test in this file and in test_sessions_api.py,
    test_start_refused_while_resume_recovers.py,
    test_resume_arm_stale_start.py and test_resume_after_restart.py, until
    this test was added.

    RED under mutant "patch stops on any request" (``patch_session``'s
    ``if body.status == "abandoned" or body.auto_resume is False:`` becomes
    ``if True:``), both cases, and under mutant "patch stops when
    auto_resume is absent" (``... or not body.auto_resume:``), ``plan_edit``
    only. Observed verbatim, the same two lines in each failing case:

        _ test_control_a_patch_that_keeps_the_session_armed_leaves_the_ladder_running[plan_edit] _
        E   AssertionError: a PATCH that kept the session armed ended the tick
        E   assert False

    and under "patch stops on any request" the same two lines for
    ``[rearm]`` as well.
    """
    lad = ladder
    body = ({"auto_resume": True} if how == "rearm"
            else {"plan": lad.session.plan.model_dump(mode="json")})
    before_starts = len(lad.rig.starts)
    tick = await _park(lad, "solve")
    try:
        r = await lad.rig.client.patch(f"/api/sessions/{lad.session.id}",
                                       json=body)
        still_parked = not await _unwound(tick)
    finally:
        await _finish(lad, tick)

    assert r.status_code == 200, r.text
    assert still_parked, "a PATCH that kept the session armed ended the tick"
    assert lad.hub.calls == ["solve", "goto"], (
        f"a PATCH that kept the session armed stopped the ladder: "
        f"{lad.hub.calls}")
    assert [(s.won, s.session_id) for s in lad.rig.starts[before_starts:]] \
        == [(True, lad.session.id)], lad.rig.starts[before_starts:]
    assert _stood_down(bus_lines) == []


# ------------------------------------------------------------------ Route

@pytest.mark.parametrize("step", ROUTE_STEPS)
async def test_the_route_says_the_ladder_is_recovering_and_on_which_step(
        ladder, step, monkeypatch):
    """``GET /api/sequence/resume-arm`` while the ladder is parked in each
    step: ``recovering`` is true and ``recovery`` names the step in words
    and the session by id and name, and nothing else. After the ladder it
    reports false and null.

    WORDS ONLY. The route is CAP_VIEW_STATUS, which a viewer holds, and an
    altitude or a mount position here would hand that viewer the site
    (#140). So the block's keys are pinned exactly and the step must be one
    of ``LADDER_STEPS``.

    ``safety`` needs a safety monitor on the hub for the ladder to read;
    ``autofocus`` needs focus untrusted, so the ladder autofocuses.

    RED under mutant "route omits recovering" (``recovering`` and
    ``recovery`` dropped from the route's answer), every case and the
    control below, observed verbatim:

        [safety] [focus] [autofocus] [solve] [limits] [recentre]
        E   KeyError: 'recovering'

    RED under mutant "the step is never named" (the six ``self._ladder_step
    = ...`` lines inside ``_recover`` removed, so the tick's "starting"
    stands for the whole ladder), every case:

        [solve]
        E   AssertionError: assert {'session_id'...': 'starting'} == {'session_id'...tep': 'solve'}
        E     Differing items:
        E     {'step': 'starting'} != {'step': 'solve'}
        and the same for each other step, naming it

    RED under mutant "recovery outlives the ladder" (the ``finally`` no
    longer clears ``_ladder_session``, and ``recovery`` no longer asks
    ``_recovering``), every case, after the release:

        [solve]
        E   AssertionError: assert {'session_id': '584395b0223c4d4a8d324572f376ec38', 'session_name': 'recover me', 'step': None} is None

    RED under mutant "recovering always true" (the route answers
    ``"recovering": True``), every case, after the release:

        E   AssertionError: {'armed': None, 'hold': None, 'recovering': True, 'recovery': None}
        E   assert True is False
    """
    lad = ladder
    if step == "safety":
        lad.hub.devices["safety"] = _Connected()
    if step == "autofocus":
        # A record-less fingerprint: the focuser's reading is a default,
        # not a measurement, so the ladder autofocuses.
        monkeypatch.setattr(fingerprint_module, "_PATH",
                            fingerprint_module._PATH.parent / "none.json")
        fingerprint_module.reset_for_tests()
    tick = await _park(lad, step)
    try:
        during = (await lad.rig.client.get("/api/sequence/resume-arm")).json()
    finally:
        await _finish(lad, tick)
    after = (await lad.rig.client.get("/api/sequence/resume-arm")).json()

    assert during["recovering"] is True, during
    assert during["recovery"] == {"step": step,
                                  "session_id": lad.session.id,
                                  "session_name": "recover me"}
    assert during["recovery"]["step"] in LADDER_STEPS
    assert after["recovering"] is False, after
    assert after["recovery"] is None
    assert lad.rig.engine.running, "premise: the released ladder started it"


async def test_control_the_route_with_no_ladder_says_not_recovering(ladder):
    """CONTROL: no tick, no ladder. The route answers ``recovering`` false
    and ``recovery`` null, alongside the armed session and hold it always
    carried.

    RED under mutant "recovering always true" (the route answers
    ``"recovering": True``), observed verbatim:

        E   AssertionError: {'armed': {'accepted': 0, 'id': '43bab242d2d7426d8f94dccffe152b55', 'name': 'recover me', 'origin': '', ...}, 'hold': None, 'recovering': True, 'recovery': None}
        E   assert True is False
    """
    body = (await ladder.rig.client.get("/api/sequence/resume-arm")).json()
    assert body["recovering"] is False, body
    assert body["recovery"] is None, body
    assert body["armed"]["id"] == ladder.session.id
    assert "hold" in body


def test_every_ladder_step_is_a_word_the_route_test_parks_on():
    """The vocabulary the route may report: words, so no step can smuggle a
    number (an altitude, a position) to a viewer, and every one of them
    parked on by the route test above, so a step added to the ladder
    without being shown on the route fails here. "starting" is the tick's,
    before the ladder's first step, and is not a step to park on.

    RED under mutant "an unexercised step" (``LADDER_STEPS`` gains
    "floor"), observed verbatim:

        E   AssertionError: a ladder step the route test never parks on: ['floor']
        E   assert {'autofocus',...'safety', ...} == {'autofocus',...ety', 'solve'}
        E     Extra items in the left set:
        E     'floor'
    """
    assert all(w.isalpha() and w.islower() for w in LADDER_STEPS), (
        LADDER_STEPS)
    assert set(LADDER_STEPS) - {"starting"} == set(ROUTE_STEPS), (
        "a ladder step the route test never parks on: "
        f"{sorted(set(LADDER_STEPS) - {'starting'} - set(ROUTE_STEPS))}")


def _ladder_step_writes() -> list[ast.expr]:
    """Every value ``resume_arm.py`` assigns to ``self._ladder_step``, read
    from the module's source."""
    tree = ast.parse(Path(resume_arm_module.__file__).read_text(
        encoding="utf-8"))
    values: list[ast.expr] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            targets = [node.target]
        else:
            continue
        for t in targets:
            if (isinstance(t, ast.Attribute) and t.attr == "_ladder_step"
                    and isinstance(t.value, ast.Name)
                    and t.value.id == "self" and node.value is not None):
                values.append(node.value)
    return values


def test_every_step_the_ladder_writes_is_a_word_from_the_vocabulary():
    """The test above checks ``LADDER_STEPS``; this checks that the ladder
    writes nothing else. Every assignment to ``self._ladder_step`` in
    ``resume_arm.py`` must be a literal: None, or a word from
    ``LADDER_STEPS``. The route test only parks on the steps it knows, so a
    step written between two of them, or a step that formats a number into
    its word, would reach a viewer through ``recovery`` with no test
    watching it (#140). Together with the test above: a step added to the
    ladder fails here until its word is in ``LADDER_STEPS``, and fails there
    until the route test parks on it.

    The premise is checked too: the scan finds the six steps ``_recover``
    names and the tick's "starting", so a scan that finds nothing cannot
    pass.

    RED under mutant "a new step that is not in LADDER_STEPS" (a line
    ``self._ladder_step = "settling"`` above the ``"recentre"`` one), which
    stayed green against every other test in this file, observed verbatim:

        E   AssertionError: a ladder step that is not in LADDER_STEPS: ['settling']
        E   assert ['settling'] == []

    RED under mutant "a step that carries a number" (the ``"recentre"``
    line becomes ``f"recentre to {tgt.dec_deg:.1f}"``), observed verbatim:

        E   AssertionError: a ladder step that is not a literal word: ["f'recentre to {tgt.dec_deg:.1f}'"]
        E   assert ["f'recentre ...ec_deg:.1f}'"] == []

    (the route test's ``[recentre]`` case goes red under it as well, with
    ``{'step': 'recentre to -5.4'} != {'step': 'recentre'}``: the number
    lands on a step it parks on. The mutant above lands on one it does not.)
    """
    values = _ladder_step_writes()
    not_literal = [ast.unparse(v) for v in values
                   if not isinstance(v, ast.Constant)]
    assert not_literal == [], (
        f"a ladder step that is not a literal word: {not_literal}")
    words = {v.value for v in values if v.value is not None}
    assert words >= set(ROUTE_STEPS) | {"starting"}, (
        f"premise: the scan found the ladder's steps: {sorted(words)}")
    assert sorted(words - set(LADDER_STEPS)) == [], (
        "a ladder step that is not in LADDER_STEPS: "
        f"{sorted(words - set(LADDER_STEPS))}")
