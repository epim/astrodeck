# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""ResumeArm starts the session as it is AFTER its recovery ladder, and says
when it is recovering (#211; mosaic slice H1 task T4; spec 5.9, "The critical
section" and "One starter per session").

``ResumeArm.tick`` reads the armed session once, at the top, and then awaits
the recovery ladder, which blind-solves and re-centres the mount and can take
minutes. It used to hand ``engine.start`` the copy it read before that await.
Another starter can take the same session inside the ladder, and if that run
also ENDS inside it (an abort, a safety stop, a short flow) the session is
dormant and armed again with frames the pre-ladder copy never saw.
``engine.start``'s own save then wrote the stale copy over the file, and every
frame the other run banked stopped being counted. If the other run was still
going, the start was refused "already running", which kept the frames but
logged a refusal and armed a ten-minute backoff over a healthy run, while the
ladder slewed and solved beside it.

So after the ladder, with no await, under the store's write lock, ResumeArm
reads the session again, requires it dormant, armed and the engine idle, and
starts the RE-READ copy. Anything else stands the attempt down: one info line,
no backoff, no crash counted. The ladder itself stops before its next focus,
solve or slew once a run has started. And ``ResumeArm.recovering`` says, for
exactly the length of the ladder, that a start now would race it: T9's 409 on
the start routes reads it.

THE HARNESS. The race tests are ``test_flows_continue_race.py``'s: the real
app over ASGI on this loop and a real ``SequenceEngine`` whose imaging loop
alone is replaced, so ``engine.start``'s "already running" refusal, the ledger
write (``_record_session_frame``) and the night's ending (``_finalize_report``)
are the engine's own. ResumeArm's ``tick`` is real, with its sky and device
steps pinned and ``_recover`` replaced by a hold the test releases. The other
starter calls ``engine.start(session=session_store.load(id))`` directly, the
call every start route makes, and NOT a route: T9 makes the routes refuse while
the ladder runs, which would turn these tests into tests of T9. Operator edits
(arm, disarm, delete) are the store writes the routes make, for the same
reason. Every party is on the event loop, so the barrier is an
``asyncio.Event``: no sleep anywhere, and the one timeout is a deadlock guard.

The ladder tests run the REAL ``_recover`` on a recording hub, and a run
"starts" when the hub flips the engine's ``running`` from inside the step the
ladder is awaiting.

Every test that guards a branch names the mutants it kills and quotes the
failure each produced. Each mutant was applied to a byte-for-byte backup of
``resume_arm.py`` in a copy of ``server/``, so no other suite saw it, and the
file was restored byte-identical (SHA-256 compared) afterwards.
"""
from __future__ import annotations

import asyncio
import threading

import pytest

import astrodeck.hub as hub_module
import astrodeck.sequence.resume_arm as resume_arm_module
from astrodeck.devices import fingerprint as fingerprint_module
from astrodeck.sequence.engine import SequenceEngine
from astrodeck.sequence.models import (ExposureStep, SequencePlan, Target,
                                       plan_identity_errors)
from astrodeck.sequence.resume_arm import ResumeArm
from astrodeck.sequence.session import Session, SessionStore, session_store
from test_flows_continue import LR, _file_frames, rig  # noqa: F401 (fixture)

#: A deadlock guard, never a wait: every party here is on the event loop, so a
#: healthy run reaches each point in a handful of turns.
DEADLOCK_GUARD_S = 30.0

#: The words every stand-down line carries, so a test can count them.
STOOD_DOWN = "stood down"


# ------------------------------------------------------------ race harness

class _ArmHub:
    """What ``ResumeArm.tick`` reads of the hub once its sky and device steps
    are pinned: no dusk preparation, and a camera that is there."""
    site: dict = {}
    dusk_arm = None

    def require(self, role):
        return object()


@pytest.fixture
def arm(rig, monkeypatch):
    """A real ResumeArm on the rig's engine. ``hooks["hold"]`` is awaited
    inside the pinned ``_recover``, so a test can park the tick after it has
    read the armed session; ``hooks["raises"]`` makes the ladder raise.
    ``seen`` records ``recovering`` as the ladder itself saw it."""
    hooks: dict = {"hold": None, "raises": None}
    seen: list[bool] = []

    async def recover(self, session):
        seen.append(self.recovering)
        if hooks["hold"] is not None:
            await hooks["hold"]()
        if hooks["raises"] is not None:
            raise hooks["raises"]
        return None

    monkeypatch.setattr(ResumeArm, "_window_open", lambda self, s, t: True)
    monkeypatch.setattr(ResumeArm, "_devices_ready", lambda self: True)
    monkeypatch.setattr(ResumeArm, "_recover", recover)
    a = ResumeArm(rig.engine, _ArmHub())
    a.hooks = hooks
    a.seen = seen
    return a


async def _park(arm) -> tuple[asyncio.Task, asyncio.Event]:
    """Start a tick and return once it is parked inside the ladder, with the
    event that lets it go. A tick that finishes without reaching the ladder
    fails here, by name, rather than as a timeout."""
    inside, release = asyncio.Event(), asyncio.Event()

    async def hold() -> None:
        inside.set()
        await release.wait()

    arm.hooks["hold"] = hold
    tick = asyncio.create_task(arm.tick())
    waiter = asyncio.ensure_future(inside.wait())
    done, _ = await asyncio.wait({waiter, tick}, timeout=DEADLOCK_GUARD_S,
                                 return_when=asyncio.FIRST_COMPLETED)
    if waiter not in done:
        waiter.cancel()
        if tick.done():
            tick.result()
        raise AssertionError("the tick never reached the recovery ladder")
    return tick, release


async def _let_go(tick: asyncio.Task, release: asyncio.Event) -> None:
    release.set()
    await asyncio.wait_for(tick, DEADLOCK_GUARD_S)


def _start_by_hand(rig, session_id: str) -> None:
    """Another starter takes the session: the call every start route makes,
    with the session read from the file, as CONTINUE and /resume read it."""
    s = session_store.load(session_id)
    rig.engine.start(s.plan, session=s)


def _arm_by_hand(session_id: str) -> None:
    """The PATCH route's writes for ``{"auto_resume": true}``: the singleton
    disarms every other session, then this one is armed."""
    for other in session_store.load_all():
        if other.id != session_id and other.auto_resume:
            other.auto_resume = False
            session_store.save(other)
    s = session_store.load(session_id)
    s.auto_resume = True
    session_store.save(s)


def _disarm_by_hand(session_id: str) -> None:
    """The PATCH route's write for ``{"auto_resume": false}``."""
    s = session_store.load(session_id)
    s.auto_resume = False
    session_store.save(s)


def _other_plan() -> SequencePlan:
    return SequencePlan(name="another night", targets=[Target(
        name="another", ra_hours=1.0, dec_deg=2.0, center=False,
        autofocus_first=False,
        steps=[ExposureStep(filter="L", exposure_s=1.0, count=2)])])


def _missing(session_id: str, frame_ids) -> list[str]:
    on_disk = {fid for fid, _t, _s in _file_frames(session_id)}
    return [f for f in frame_ids if f not in on_disk]


def _stood_down(bus_lines) -> list[str]:
    return [m for lvl, m, _src in bus_lines
            if lvl == "info" and STOOD_DOWN in m]


# ------------------------------------------------------------ (a) the loss

async def test_a_run_that_starts_and_ends_inside_the_ladder_keeps_its_frames(
        rig, arm):
    """The issue's outcome 2. The tick parks inside the ladder holding the
    session as it was; the same session is started by hand, banks two frames
    through the engine's ledger write and ends dormant and armed; then the
    ladder returns. ResumeArm must start the session as it is NOW, so the file
    keeps every frame and the resumed run's ledger has them.

    RED under mutant "start the pre-ladder copy" (``session=fresh`` ->
    ``session=armed`` in the start call: today's code, which starts what it
    read before the ladder), observed verbatim:

        AssertionError: the session file lost 2 frame(s) the run inside the
        ladder banked
        assert ['a9f9481f987...c0aeae166bd5'] == []
          Left contains 2 more items, first extra item: 'a9f9481f98724a00887d8316d36031ce'
    """
    fid = await rig.save_flow(LR)
    one = await rig.night_one(fid, [0])
    assert session_store.armed().id == one.id, "premise: armed for ResumeArm"
    tick, release = await _park(arm)
    try:
        _start_by_hand(rig, one.id)
        banked = [f.id for f in rig.bank([0, 1])]
        await rig.end_night("incomplete")
        after = session_store.load(one.id)
        assert (after.status, after.auto_resume) == ("dormant", True), (
            "premise: the run inside the ladder left the session dormant "
            "and armed, so ResumeArm may take it")
        before = len(rig.starts)
    finally:
        await _let_go(tick, release)

    everything = [*(f.id for f in one.frames), *banked]
    lost = _missing(one.id, everything)
    assert lost == [], (
        f"the session file lost {len(lost)} frame(s) the run inside the "
        f"ladder banked")
    mine = rig.starts[before:]
    assert [(s.won, s.session_id) for s in mine] == [(True, one.id)], mine
    assert mine[0].frame_ids == everything, (
        "ResumeArm started a ledger without the frames banked inside its "
        "ladder")


# --------------------------------------------- (b) the run is still going

async def test_the_same_session_still_running_is_not_started_again(
        rig, arm, bus_lines):
    """The issue's outcome 1. The same session is started by hand inside the
    ladder and is still running when it returns. ResumeArm makes no
    ``engine.start`` call at all: no refusal, no backoff, no hold, no crash
    counted, one info line.

    The status check and the running check both see this one, so neither
    alone is its mutant. RED under mutant "no re-check" (``_still_startable``
    returns ``armed`` at once: today's start, with no re-read and no checks),
    where the engine's refusal keeps the frames but the tick treats a healthy
    run as a failed start, observed verbatim:

        AssertionError: ResumeArm called engine.start over a live run:
        [Start(won=False, session_id='22b52f35167c487ab27e004fdc6538dd', [...]
        error='a sequence is already running')]
        assert [Start(won=Fa...ady running')] == []

    Under mutant "no running re-check" this test still makes no start,
    because the status check sees an active session; it fails only on the
    reason, which must name the run rather than the status:

        AssertionError: ["auto-resume stood down for 'continue me': it is
        active now, not dormant"]
    """
    fid = await rig.save_flow(LR)
    one = await rig.night_one(fid, [0])
    tick, release = await _park(arm)
    try:
        _start_by_hand(rig, one.id)
        banked = [f.id for f in rig.bank([0])]
        before = len(rig.starts)
    finally:
        await _let_go(tick, release)

    assert rig.starts[before:] == [], (
        f"ResumeArm called engine.start over a live run: {rig.starts[before:]}")
    assert (arm._retry_at, arm.hold) == (0.0, None), (
        "a run that is going is not a refusal to back off from")
    assert rig.engine.running and rig.engine._session.id == one.id
    assert _missing(one.id, [*(f.id for f in one.frames), *banked]) == []
    assert session_store.load(one.id).crash_resumes == one.crash_resumes
    lines = _stood_down(bus_lines)
    assert len(lines) == 1 and "a run started" in lines[0], lines


async def test_another_run_still_going_when_the_ladder_returns_is_left_alone(
        rig, arm, bus_lines):
    """The case only the running re-check sees. Another plan is started by
    hand inside the ladder (which disarms ours, the singleton), and then the
    operator arms ours again, which the PATCH route allows while a run is
    live. When the ladder returns our session is dormant AND armed, and the
    engine is busy with the other run. ResumeArm makes no ``engine.start``
    call and leaves the crash counter as it was. An earlier refusal's hold is
    seeded first, and standing down must clear it: nothing is holding now.

    RED under mutant "no running re-check" (the ``engine.running`` test in
    ``_still_startable`` removed), observed verbatim:

        AssertionError: ResumeArm called engine.start over a live run:
        [Start(won=False, session_id='ab50617e5b724697a57faf91c0ec77e4', [...]
        error='a sequence is already running')]
        assert [Start(won=Fa...ady running')] == []

    RED under mutant "stand-down keeps the hold" (the ``_clear_hold()`` in
    the stand-down branch removed), observed verbatim:

        AssertionError: standing down left an earlier refusal's hold up:
        {'reason': 'an earlier refusal', 'since': 1790273840.0890424,
        'retry_at': None, 'session_id': '5a00e81351f249c0897e1952ca929450',
        'session_name': 'continue me', 'owed': 4}
    """
    fid = await rig.save_flow(LR)
    one = await rig.night_one(fid, [0])
    # One earlier crash, so "untouched" cannot pass as "reset to zero".
    s = session_store.load(one.id)
    s.crash_resumes = 1
    session_store.save(s)
    arm._set_hold(s, "an earlier refusal")
    tick, release = await _park(arm)
    try:
        rig.engine.start(_other_plan())
        _arm_by_hand(one.id)
        ours = session_store.load(one.id)
        assert (ours.status, ours.auto_resume) == ("dormant", True), (
            "premise: ours is dormant and armed while the other run is live")
        before = len(rig.starts)
    finally:
        await _let_go(tick, release)

    assert rig.starts[before:] == [], (
        f"ResumeArm called engine.start over a live run: {rig.starts[before:]}")
    assert arm._retry_at == 0.0, (
        "a run that is going is not a refusal to back off from")
    assert arm.hold is None, (
        f"standing down left an earlier refusal's hold up: {arm.hold}")
    assert session_store.load(one.id).crash_resumes == 1, (
        "standing down touched the crash counter")
    lines = _stood_down(bus_lines)
    assert len(lines) == 1 and "a run started" in lines[0], lines


# ------------------------------------ the other ways the session stops being ours

async def test_a_session_completed_inside_the_ladder_is_not_reopened(
        rig, arm, bus_lines):
    """The run inside the ladder banks everything the plan owes and ends
    ``complete``. The file is then complete AND armed, and only the status
    check stands between ResumeArm and reopening a finished session.

    DELIBERATE PIN CHANGE (#838, wave 17 integration). This used to say
    "Completion does not disarm (only an abort does)" and take the armed flag
    from the run itself. The engine now clears ``auto_resume`` where a night
    completes (``_finalize_report``), so a real run no longer leaves that
    shape. What the case grades is the INDEPENDENT net - ``_still_startable``
    refuses a complete session whatever its flag says - and a file the engine
    did not write (from before the clear, or armed by hand) still has the
    shape. So the case asserts the engine's clear, then puts the flag back by
    hand, and the rest is as it was.

    RED under mutant "no dormant re-check" (the ``status != "dormant"`` test
    in ``_still_startable`` removed), observed verbatim:

        AssertionError: ResumeArm reopened a complete session:
        [Start(won=True, session_id='8a72fea803054b35873d2044ee573931', [...]
        error='')]
        assert [Start(won=Tr...ne, error='')] == []
    """
    fid = await rig.save_flow(LR)
    one = await rig.night_one(fid, [0])
    tick, release = await _park(arm)
    try:
        _start_by_hand(rig, one.id)
        banked = [f.id for f in rig.bank([0, 0, 1, 1])]
        await rig.end_night("complete")
        done = session_store.load(one.id)
        assert (done.status, done.auto_resume, done.owed()) == (
            "complete", False, 0), (
            "the engine left a finished session armed (#838)")
        # A file the engine did not write: complete and armed.
        done.auto_resume = True
        session_store.save(done)
        done = session_store.load(one.id)
        assert (done.status, done.auto_resume, done.owed()) == (
            "complete", True, 0), "premise: complete, still armed, owes none"
        before = len(rig.starts)
    finally:
        await _let_go(tick, release)

    assert rig.starts[before:] == [], (
        f"ResumeArm reopened a complete session: {rig.starts[before:]}")
    assert session_store.load(one.id).status == "complete"
    assert _missing(one.id, [*(f.id for f in one.frames), *banked]) == []
    assert (arm._retry_at, arm.hold) == (0.0, None)
    lines = _stood_down(bus_lines)
    assert len(lines) == 1 and "complete" in lines[0], lines


async def test_a_session_disarmed_inside_the_ladder_is_not_started(
        rig, arm, bus_lines):
    """The operator disarms the session while the ladder runs. Nothing else
    changed, so only the armed check sees it; starting it would also re-arm
    it, because ``engine.start`` arms whatever it starts.

    RED under mutant "no armed re-check" (the ``auto_resume`` test in
    ``_still_startable`` removed), observed verbatim:

        AssertionError: ResumeArm started a session the operator disarmed:
        [Start(won=True, session_id='10daddfe0c4f47acb9307f17819ed1bb', [...]
        error='')]
        assert [Start(won=Tr...ne, error='')] == []
    """
    fid = await rig.save_flow(LR)
    one = await rig.night_one(fid, [0])
    tick, release = await _park(arm)
    try:
        _disarm_by_hand(one.id)
        before = len(rig.starts)
    finally:
        await _let_go(tick, release)

    assert rig.starts[before:] == [], (
        f"ResumeArm started a session the operator disarmed: "
        f"{rig.starts[before:]}")
    s = session_store.load(one.id)
    assert (s.status, s.auto_resume) == ("dormant", False)
    assert (arm._retry_at, arm.hold) == (0.0, None)
    lines = _stood_down(bus_lines)
    assert len(lines) == 1 and "disarmed" in lines[0], lines


#: JSON that parses to a dict and fails ``Session`` validation, so the store
#: raises ``SessionUnreadable`` rather than ``KeyError``. A dict with only an
#: id would not do: every other field has a default, and it VALIDATES.
NOT_A_SESSION = '{"plan": "not a plan"}'


@pytest.mark.parametrize("how", ["deleted", "unreadable"])
async def test_a_session_gone_inside_the_ladder_is_not_put_back(
        rig, arm, bus_lines, how):
    """The session file is deleted, or replaced by one that no longer
    validates, while the ladder runs. The re-read fails; ResumeArm must stand
    down rather than start the copy it read, whose save would put a deleted
    ledger back on disk, running, or overwrite the corrupt file the operator
    needs to see.

    RED under mutant "a gone session starts the pre-ladder copy" (the
    re-read's ``except`` returns ``armed`` instead of standing down), both
    cases, observed verbatim:

        [deleted] AssertionError: ResumeArm started a session whose file was
        deleted: [Start(won=True, session_id='884d2c674b2549f5a83b74a40782281a',
        [...] error='')]
        [unreadable] AssertionError: ResumeArm started a session whose file
        was unreadable: [Start(won=True,
        session_id='c8193ed76fda4c1e89a3d39b405ac1c2', [...] error='')]

    RED under mutant "only KeyError" (``SessionUnreadable`` dropped from that
    ``except``), the unreadable case, observed verbatim:

        E   pydantic_core._pydantic_core.ValidationError: 1 validation error for Session
        E   plan
        E     Input should be a valid dictionary or instance of SequencePlan [type=model_type, input_value='not a plan', input_type=str]
        E   astrodeck.sequence.session.SessionUnreadable: session file is unreadable: d99cb483802c492ea8e0611801c98abd
    """
    fid = await rig.save_flow(LR)
    one = await rig.night_one(fid, [0])
    path = session_store._path(one.id)
    tick, release = await _park(arm)
    try:
        if how == "deleted":
            session_store.delete(one.id)
        else:
            path.write_text(NOT_A_SESSION, encoding="utf-8")
        before = len(rig.starts)
    finally:
        await _let_go(tick, release)

    assert rig.starts[before:] == [], (
        f"ResumeArm started a session whose file was {how}: "
        f"{rig.starts[before:]}")
    if how == "deleted":
        assert not path.exists(), "ResumeArm put a deleted session back"
    else:
        assert path.read_text(encoding="utf-8") == NOT_A_SESSION, (
            "ResumeArm wrote over the file it could not read")
    assert (arm._retry_at, arm.hold) == (0.0, None)
    lines = _stood_down(bus_lines)
    assert len(lines) == 1 and "deleted or became unreadable" in lines[0], (
        lines)


async def test_resume_arm_starts_the_engine_inside_the_write_lock(
        rig, arm, monkeypatch):
    """Nothing on the loop can interleave a section with no await in it, and
    every test above is on the loop. The lock is for writers that are not: a
    route's ``asyncio.to_thread(session_store.save, ...)``. So probe it from
    another thread at the moment ResumeArm calls ``engine.start``: the store's
    write lock must be held.

    RED under mutant "start outside the lock" (``with
    session_store.write_locked():`` -> ``if True:``), observed verbatim:

        AssertionError: another thread could take the store's write lock while
        ResumeArm started the engine: [(True, True)]
        assert [(True, True)] == [(True, False)]
    """
    fid = await rig.save_flow(LR)
    await rig.night_one(fid, [0])
    probes: list[tuple[bool, bool]] = []
    recorded = rig.engine.start          # the harness's recorder

    def probing_start(plan, **kw):
        def probe() -> None:
            got = SessionStore._write_lock.acquire(blocking=False)
            probes.append((kw.get("session") is not None, got))
            if got:
                SessionStore._write_lock.release()

        t = threading.Thread(target=probe)
        t.start()
        t.join()
        return recorded(plan, **kw)

    monkeypatch.setattr(rig.engine, "start", probing_start)
    await arm.tick()
    assert probes == [(True, False)], (
        "another thread could take the store's write lock while ResumeArm "
        f"started the engine: {probes}")


def _swap_plan_by_hand(session_id: str, how: str) -> SequencePlan:
    """The PATCH route's writes for ``{"plan": ...}`` on a dormant session:
    the plan is replaced and nothing gates it (``patch_session`` checks only
    that the session is dormant). ``how`` names the gate the new plan fails."""
    s = session_store.load(session_id)
    p = s.plan.model_copy(deep=True)
    if how == "duplicate_step_id":
        steps = p.targets[0].steps
        steps[1].id = steps[0].id
    else:
        p.count_mode = "accepted"
        p.max_consecutive_rejects = 0
        p.max_consecutive_rejects_night = 0
    s.plan = p
    session_store.save(s)
    return p


@pytest.mark.parametrize("how", ["duplicate_step_id", "unbounded_quota"])
async def test_a_plan_swapped_inside_the_ladder_passes_the_gates_before_it_starts(
        rig, arm, bus_lines, how):
    """Starting the RE-READ copy starts the re-read PLAN, and the identity
    (#156) and unbounded-quota gates near the top of ``tick`` read the plan as
    it was before the ladder. PATCH ``/api/sessions/{id}`` replaces a dormant
    session's plan with neither gate, and ``engine.start`` is unguarded, so a
    plan swapped in during the ladder reached the engine with no gate between.
    It must be refused on the gates' own terms instead: no start, a hold
    naming the gate, the ten-minute backoff, the crash counter untouched, and
    the operator's new plan left on disk.

    Added by the T4 verifier. RED under mutant "no re-gate" (``gate =
    self._plan_refusal(fresh)`` -> ``gate = None``), both cases, observed
    verbatim:

        [duplicate_step_id] AssertionError: ResumeArm started a plan no gate
        had seen: [Start(won=True, session_id='e8ef93de5a394dc98a8de44b12093565',
        [...]
        assert [Start(won=Tr...ne, error='')] == []
        [unbounded_quota] AssertionError: ResumeArm started a plan no gate had
        seen: [Start(won=True, session_id='1ac09eb92d0f4e069fb5bf5b38d2f88a',
        [...]
        assert [Start(won=Tr...ne, error='')] == []

    RED under mutant "re-gate the pre-ladder plan" (``_plan_refusal(fresh)``
    -> ``_plan_refusal(armed)``), both cases, with the same message.

    RED under mutant "identity only" (the ``quota_unbounded`` branch of
    ``_plan_refusal`` removed), ``unbounded_quota`` only, and under "quota
    only" (the identity branch removed), ``duplicate_step_id`` only, each
    with the same message.

    RED under mutant "the re-gate stands down" (its ``_retry_at`` and
    ``_set_hold`` lines removed), both cases, observed verbatim:

        AssertionError: a plan the gates refuse is a refusal: backoff and hold
        assert (False, False) == (True, True)

    The CONTROL (nothing swapped, the gates pass) is the test below, RED
    under "re-gate inverted" (``if gate is not None`` -> ``if gate is
    None``).
    """
    fid = await rig.save_flow(LR)
    one = await rig.night_one(fid, [0])
    tick, release = await _park(arm)
    try:
        swapped = _swap_plan_by_hand(one.id, how)
        if how == "duplicate_step_id":
            assert plan_identity_errors(swapped), "premise: identity fails"
        else:
            assert plan_identity_errors(swapped) == [], "premise: ids are fine"
            assert resume_arm_module.quota_unbounded(
                swapped, resume_arm_module.resolve_policy(
                    swapped, resume_arm_module.config_store.cfg())), (
                "premise: the swapped plan is unbounded")
        before = len(rig.starts)
    finally:
        await _let_go(tick, release)

    assert rig.starts[before:] == [], (
        f"ResumeArm started a plan no gate had seen: {rig.starts[before:]}")
    assert (arm._retry_at > 0.0, arm.hold is not None) == (True, True), (
        "a plan the gates refuse is a refusal: backoff and hold")
    assert arm.hold["retry_at"] == arm._retry_at
    expected = {"duplicate_step_id": "this plan cannot start: ",
                "unbounded_quota": "could run forever"}[how]
    assert expected in arm.hold["reason"], arm.hold
    stored = session_store.load(one.id)
    assert (stored.status, stored.auto_resume, stored.crash_resumes) == (
        "dormant", True, one.crash_resumes)
    assert stored.plan == swapped, "the operator's new plan was overwritten"
    refused = [m for lvl, m, _src in bus_lines
               if lvl == "warning" and m.startswith("auto-resume refused")]
    assert len(refused) == 1 and expected in refused[0], refused
    assert _stood_down(bus_lines) == []


async def test_control_an_auto_resume_with_nothing_racing_starts_as_before(
        rig, arm, bus_lines):
    """CONTROL. Nothing happens inside the ladder: ResumeArm starts the armed
    session once, with its whole ledger, clears its hold and backoff and says
    it resumed. The re-check must not stand down a session that is still
    exactly what it read.

    RED under mutant "dormant re-check inverted" (``!= "dormant"`` ->
    ``== "dormant"``), observed verbatim:

        AssertionError: []
        assert [] == [(True, '714e...53bc9b760a4')]
          Right contains one more item: (True, '714e29a751e94059bb85853bc9b760a4')

    RED under mutant "re-gate inverted" (the verifier's; ``if gate is not
    None`` -> ``if gate is None`` after the ladder, so a plan the gates pass
    is the one refused), observed verbatim:

        AssertionError: []
        assert [] == [(True, 'dfc0...866bfc41a30')]
    """
    fid = await rig.save_flow(LR)
    one = await rig.night_one(fid, [0])
    before = len(rig.starts)
    await arm.tick()

    mine = rig.starts[before:]
    assert [(s.won, s.session_id) for s in mine] == [(True, one.id)], mine
    assert mine[0].frame_ids == [f.id for f in one.frames]
    assert rig.engine.running and rig.engine._session.id == one.id
    assert (arm._retry_at, arm.hold, arm.recovering) == (0.0, None, False)
    assert arm.seen == [True]
    assert _stood_down(bus_lines) == []
    assert any(lvl == "info" and m == f"auto-resume: '{one.name}' resumed"
               for lvl, m, _src in bus_lines), bus_lines


# ------------------------------------------------------ (d) ``recovering``

async def test_recovering_spans_the_ladder_and_is_cleared_however_it_ends(
        rig, arm):
    """``recovering`` is False before a tick, True while the tick is inside
    the ladder (as the ladder itself sees it, and as anything else on the
    loop sees it), and False again after the ladder returns AND after it
    raises. A flag stuck high would refuse every manual start (T9) until the
    process restarted.

    RED under mutant "cleared only on success" (the ``try``/``finally``
    around ``await self._recover(armed)`` replaced by a plain assignment
    after it), observed verbatim:

        AssertionError: recovering stayed True after the ladder raised
        assert True is False
         +  where True = <astrodeck.sequence.resume_arm.ResumeArm object at 0x000001BB4B75FE90>.recovering

    RED under mutant "never raised" (``self._recovering = True`` removed),
    observed verbatim:

        AssertionError: recovering was False while the tick sat in the ladder
        assert False is True
    """
    fid = await rig.save_flow(LR)
    await rig.night_one(fid, [0])
    assert arm.recovering is False, "recovering before any tick"

    tick, release = await _park(arm)
    try:
        inside = arm.recovering
    finally:
        await _let_go(tick, release)
    assert inside is True, "recovering was False while the tick sat in the ladder"
    assert arm.recovering is False, "recovering stayed True after the ladder returned"
    assert rig.engine.running, "premise: the first tick resumed the run"

    await rig.end_night("incomplete")            # dormant and armed again
    arm.hooks["hold"] = None
    arm.hooks["raises"] = RuntimeError("the solver crashed")
    with pytest.raises(RuntimeError, match="the solver crashed"):
        await arm.tick()
    assert arm.seen == [True, True], (
        f"the ladder did not see recovering raised each time: {arm.seen}")
    assert arm.recovering is False, (
        "recovering stayed True after the ladder raised")


def test_recovering_is_read_only():
    """Public for T9 to read, and only ResumeArm may say it is recovering: a
    route that could set it could hold every other start off.

    RED under mutant "a plain attribute" (the property renamed away and
    every ``self._recovering`` written as ``self.recovering``, so the flag
    still works and anyone can set it), observed verbatim:

        Failed: DID NOT RAISE <class 'AttributeError'>
    """
    a = ResumeArm(object(), object())
    assert a.recovering is False
    with pytest.raises(AttributeError):
        a.recovering = True


async def test_no_turn_of_the_loop_falls_between_the_running_check_and_the_flag(
        rig, arm, monkeypatch):
    """What makes ``recovering`` sound for T9: it is raised in the same
    synchronous stretch as the tick's ``engine.running`` check. A route that
    reads both and starts, without an await, then lands either wholly before
    that check (and the tick finds the engine running) or wholly after the
    flag (and the route finds it raised).

    Observed from the loop: this test takes a turn every time the tick yields,
    and at each one asks whether the tick has already read ``engine.running``
    while ``recovering`` is still down. That is the gap a route would start
    in.

    RED under mutant "an await before the flag" (``await asyncio.sleep(0)``
    inserted just before ``self._recovering = True``), observed verbatim:

        AssertionError: the loop turned (turn [2]) after the tick read
        engine.running and before it raised recovering: a route could start
        in that gap
        assert [2] == []
    """
    fid = await rig.save_flow(LR)
    await rig.night_one(fid, [0])
    real = SequenceEngine.running
    reads: list[bool] = []

    def counted(self) -> bool:
        value = real.fget(self)
        reads.append(value)
        return value

    monkeypatch.setattr(SequenceEngine, "running", property(counted))
    release = asyncio.Event()

    async def hold() -> None:
        await release.wait()

    arm.hooks["hold"] = hold
    gaps: list[int] = []
    turn = 0
    tick = asyncio.create_task(arm.tick())
    while not tick.done():
        turn += 1
        if reads and not arm.recovering:
            gaps.append(turn)
            release.set()            # let it finish; the verdict is below
        elif arm.recovering:
            release.set()
        assert turn < 1000, "the tick never finished"
        await asyncio.sleep(0)
    await tick
    assert reads, "premise: the tick never read engine.running"
    assert arm.seen == [True], "premise: the tick never reached the ladder"
    assert gaps == [], (
        f"the loop turned (turn {gaps}) after the tick read engine.running "
        f"and before it raised recovering: a route could start in that gap")


# ------------------------------------------- (c) the ladder stops mid-way

class _LadderEngine:
    """The engine surface ``tick`` and the ladder use. ``running`` is a plain
    flag the hub flips from inside a step, which is what "a run started while
    the ladder awaited that step" looks like from here.

    ``start`` takes ``tracking`` because the real ``SequenceEngine.start``
    does and ResumeArm now passes it (#202, task T9: the target the ladder
    left the mount tracking). Without the keyword the tick's start raised a
    TypeError, which the tick logs as a refusal, so the ``nowhere`` control
    saw no start at all, observed verbatim:

        AssertionError: assert ([], ['solve', 'goto'], []) == ([], ['solve'...03e2844f617'])
          At index 2 diff: [] != ['9179d85a1abd49c9b8b5303e2844f617']

    What is handed over is test_resume_arm_tracking_handoff.py's to check;
    this stub only has to accept it."""

    def __init__(self) -> None:
        self.running = False
        self.starts: list[str] = []
        self.limit_checks = 0
        self.on_limit_check = None

    def start(self, plan, *, session=None, tracking=None) -> None:
        self.starts.append(session.id if session is not None else None)

    async def check_slew_limits(self, target, *, cfg=None, plan=None,
                                projected=True) -> None:
        self.limit_checks += 1
        if self.on_limit_check is not None:
            self.on_limit_check()


class _Connected:
    connected = True


class _Focuser:
    connected = True

    def __init__(self, position: int) -> None:
        self.position = position
        self.on_read = None

    async def get_position(self) -> int:
        if self.on_read is not None:
            self.on_read()
        return self.position


class _LadderHub:
    """Records what the ladder asked of the rig; nothing physical happens.
    No safety monitor, so step 0 has nothing to read."""
    site: dict = {}
    dusk_arm = None

    def __init__(self, focuser: _Focuser) -> None:
        self.focuser = focuser
        self.devices = {"camera": _Connected(), "telescope": _Connected(),
                        "focuser": focuser}
        self.calls: list[str] = []
        self.on_solve = None

    def require(self, role):
        return self.focuser if role == "focuser" else object()

    async def solve_and_sync(self, exposure_s: float = 3.0, *,
                             blind: bool = False):
        self.calls.append("solve")
        if self.on_solve is not None:
            self.on_solve()
        return {"ok": True}

    async def goto_and_center(self, ra, dec, *a, **k) -> None:
        self.calls.append("goto")


FOCUS_POSITION = 9935


@pytest.fixture
def ladder(tmp_path, monkeypatch):
    """A real ``tick`` and a real ``_recover`` on the recording hub, with the
    sky pinned open and a plate solver and autofocus available. The armed
    session's plan repeats a target name (no rule names it), so a started
    resume logs the duplicate-name warning and a stood-down one must not."""
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    monkeypatch.setattr(fingerprint_module, "_PATH", tmp_path / "fp.json")
    fingerprint_module.reset_for_tests()
    engine = _LadderEngine()
    hub = _LadderHub(_Focuser(FOCUS_POSITION))
    a = ResumeArm(engine, hub)
    focused: list[int] = []

    async def autofocus() -> None:
        focused.append(1)

    monkeypatch.setattr(a, "_window_open", lambda s, t: True)
    monkeypatch.setattr(a, "_can_solve", lambda: True)
    monkeypatch.setattr(a, "_can_autofocus", lambda: True)
    monkeypatch.setattr(a, "_autofocus", autofocus)
    plan = SequencePlan(name="ladder", guide=False, dither_every=0,
                        meridian_flip=False, targets=[
                            Target(name="M42", ra_hours=5.5, dec_deg=-5.0,
                                   steps=[ExposureStep(filter="L",
                                                       exposure_s=1.0,
                                                       count=2)])
                            for _ in range(2)])
    s = Session(name="ladder", status="dormant", plan=plan, auto_resume=True)
    session_store.save(s)
    yield a, engine, hub, focused, s
    fingerprint_module.reset_for_tests()


def _trust_the_focuser() -> None:
    """A record from before this process, matching what the focuser reads:
    focus is trusted and the ladder skips autofocus."""
    fingerprint_module.record(focuser_position=FOCUS_POSITION, filter_slot=0,
                              ra_hours=1.0, dec_deg=2.0, parked=False,
                              tracking=True)
    fingerprint_module.reset_for_tests()


def _run_starts(engine: _LadderEngine):
    def start() -> None:
        engine.running = True
    return start


@pytest.mark.parametrize("where", [
    "focus_read_untrusted", "focus_read_trusted", "solve", "limit_check",
    "nowhere"])
async def test_a_run_that_starts_mid_ladder_stops_it_before_its_next_move(
        ladder, bus_lines, where):
    """A run starts while the ladder is awaiting one of its steps. The ladder
    must not take its next invasive step - autofocus, the solve's exposure,
    the re-centring slew - over a run that now owns the rig, and its early
    exit is not a refusal: the tick's re-check stands the attempt down with
    one info line, no backoff and no start. ``nowhere`` is the CONTROL: the
    whole ladder runs, the resume starts, and the duplicate-name warning is
    logged once.

    ``focus_read_untrusted``: the run starts during the focuser read, focus
    is untrusted, so autofocus is next. ``focus_read_trusted``: the same read
    with focus trusted, so the solve is next. ``solve``: the run starts
    during the solve, so the slew is next (acceptance (c)). ``limit_check``:
    the run starts during the slew-limit check that sits between the solve
    and the slew, which pins the slew's check below that await.

    RED under mutant "no mid-ladder check" (``_a_run_took_over`` answers
    ``False``, which removes all three exits), every case but the control,
    observed verbatim:

        [focus_read_untrusted] AssertionError: autofocus ran over a run that
        had started
        assert [1] == []
        [focus_read_trusted] AssertionError: the ladder went on after a run
        started: ['solve', 'goto']
        assert ['solve', 'goto'] == []
        [solve] AssertionError: the ladder went on after a run started:
        ['solve', 'goto']
        assert ['solve', 'goto'] == ['solve']
        [limit_check] the same as [solve]

    RED under mutant "no pre-goto check", ``solve`` and ``limit_check``:

        AssertionError: the ladder went on after a run started: ['solve', 'goto']
        assert ['solve', 'goto'] == ['solve']
          Left contains one more item: 'goto'

    RED under mutant "pre-goto check above the limit check", ``limit_check``
    only:

        AssertionError: the ladder went on after a run started: ['solve', 'goto']
        assert ['solve', 'goto'] == ['solve']
          Left contains one more item: 'goto'

    RED under mutant "no pre-solve check", ``focus_read_trusted`` only:

        AssertionError: the ladder went on after a run started: ['solve']
        assert ['solve'] == []
          Left contains one more item: 'solve'

    RED under mutant "no pre-autofocus check", ``focus_read_untrusted`` only:

        AssertionError: autofocus ran over a run that had started
        assert [1] == []
          Left contains one more item: 1

    RED under mutant "the early exit is a refusal" (each exit returns a
    reason string instead of None), every case but the control:

        AssertionError: a run starting is not a refusal: no backoff, no hold
        assert (1790274114.9...efbcfa', ...}) == (0.0, None)
          At index 0 diff: 1790274114.9037795 != 0.0

    RED under mutant "warning before the re-check" (the duplicate-name
    warning logged above the locked section, on ``armed.plan``), every case
    but the control:

        AssertionError: the duplicate-name warning was logged for a resume
        that stood down
        assert ['targets sha... one of them'] == []
          Left contains one more item: "targets share a name ('M42' x2); they
          run and count separately, but an instruction could not name just
          one of them"

    The control is RED under mutant "the mid-ladder check inverted" (``return
    bool(self.engine.running)`` -> ``return not self.engine.running``):

        AssertionError: assert ([], [], ['23...4630aaccc47']) == ([], ['solve'...4630aaccc47'])
          At index 1 diff: [] != ['solve', 'goto']
    """
    arm, engine, hub, focused, session = ladder
    if where != "focus_read_untrusted":
        _trust_the_focuser()
    if where.startswith("focus_read"):
        hub.focuser.on_read = _run_starts(engine)
    elif where == "solve":
        hub.on_solve = _run_starts(engine)
    elif where == "limit_check":
        engine.on_limit_check = _run_starts(engine)

    await arm.tick()

    warned = [m for lvl, m, _src in bus_lines
              if lvl == "warning" and m.startswith("targets share a name")]
    if where == "nowhere":
        assert (focused, hub.calls, engine.starts) == (
            [], ["solve", "goto"], [session.id])
        assert (arm._retry_at, arm.hold) == (0.0, None)
        assert _stood_down(bus_lines) == []
        assert len(warned) == 1, warned
        return
    expected_calls = {"focus_read_untrusted": [], "focus_read_trusted": [],
                      "solve": ["solve"], "limit_check": ["solve"]}[where]
    assert focused == [], "autofocus ran over a run that had started"
    assert hub.calls == expected_calls, (
        f"the ladder went on after a run started: {hub.calls}")
    assert engine.starts == [], "the tick started over a live run"
    assert (arm._retry_at, arm.hold) == (0.0, None), (
        "a run starting is not a refusal: no backoff, no hold")
    lines = _stood_down(bus_lines)
    assert len(lines) == 1 and "a run started" in lines[0], lines
    assert warned == [], (
        "the duplicate-name warning was logged for a resume that stood down")
    assert arm.recovering is False
