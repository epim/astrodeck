# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""CONTINUE against ResumeArm: one starter per session, and no lost frames
(#189 S1, spec 5.9 "The critical section"; task S1-13).

Run CONTINUE and ResumeArm can both start the same dormant session. Today's
``patch_session`` loads, checks and saves in separate ``to_thread`` calls, and
ResumeArm can start the session in between; the save then puts a stale frame
list over the file of a session that is now running - the one-starter race
recorded on 2026-09-18. CONTINUE must not inherit it, so it never saves the
session itself, and it re-reads, checks dormant, replaces the plan and calls
``engine.start`` in one synchronous section under the store's write lock.

THE INTERLEAVING IS FORCED, NOT HOPED FOR. The route's first read of the
session runs in a worker thread (``asyncio.to_thread``), which is exactly the
await the race needs. A wrapper on that read holds the worker at a
``threading.Barrier`` after it has read and signalled the loop; ResumeArm's
real ``tick`` then runs on the loop, and releases the barrier when it is done.
No sleep anywhere: every party reaches the barrier without needing the loop,
and the barrier's timeout is a deadlock guard, never a wait.

The harness is ``test_flows_continue.py``'s: the real app over ASGI on this
loop, and a real ``SequenceEngine`` whose imaging loop alone is replaced. So
``engine.start``'s "already running" refusal, the ledger write
(``_record_session_frame``) and the night's ending (``_finalize_report``) are
the engine's own. ResumeArm's ``tick`` is real, with the three steps that read
the sky or move the mount pinned (``_window_open``, ``_devices_ready``,
``_recover``), the same pins ``test_resume_arm_name_warning_once.py`` uses.

Each mutant was applied to a byte-for-byte backup of the file it changes, in
a copy of ``server/`` so no other suite saw it, and the file was restored
byte-identical (SHA-256 compared) afterwards. The failures are quoted as
observed.
"""
from __future__ import annotations

import asyncio
import threading

import pytest

from astrodeck.sequence.resume_arm import ResumeArm
from astrodeck.sequence.session import SessionStore, session_store
from test_flows_continue import LR, _file_frames, rig  # noqa: F401 (fixture)

#: A deadlock guard. Every party reaches the barrier without the event loop's
#: help, so a healthy run never waits on this.
BARRIER_TIMEOUT_S = 30.0


@pytest.fixture(autouse=True)
def _the_store_singleton_is_left_as_found():
    """No test here may leave anything on the process-wide ``session_store``.

    THE WRAPPERS BELOW PATCH ``SessionStore``, THE CLASS, NOT THE INSTANCE.
    ``monkeypatch.setattr(session_store, "newest_for_flow", w)`` reads the old
    value with ``getattr``, which is the BOUND method, and its undo puts that
    bound method back with ``setattr``: into the instance's ``__dict__``,
    where there was nothing before. It stays there for the rest of the
    worker process and shadows the class, so every later test that patches
    ``SessionStore.newest_for_flow`` patches something nobody calls. That is
    how this file broke another: with the gate patched on the instance, run
    ``tests/test_flows_continue_race.py tests/test_flows_progress_route.py``
    in one process (``-n0``) and the route test's lookup spy never fires
    (observed 2026-09-24, verbatim):

        FAILED tests/test_flows_progress_route.py::TestPlumbing::test_the_compile_the_lookup_and_the_count_run_off_the_loop
        E       AssertionError: assert {'flow_progre..._plan': False} == {'flow_progre..._plan': False}
        E         Omitting 3 identical items, use -vv to show
        E         Right contains 1 more item:
        E         {'newest_for_flow': False}

    Under xdist that pairing depends on which worker draws which file, so the
    suite went red or green by scheduling.

    This fixture sees the leak in the file that makes it. It compares the
    instance's own attributes before and after each test, by identity, so an
    entry some OTHER file leaked earlier in the process is not blamed here.

    RED under mutant "gate patched on the instance" (``_gate_the_first_read``
    patching ``session_store`` with a bound-method wrapper, as it first did),
    at the teardown of each of the two tests that use it (observed,
    verbatim):

        _ ERROR at teardown of test_resume_arm_starts_first_and_every_frame_it_banks_stays _
        E       AssertionError: this test left ['newest_for_flow'] on the
        process-wide session_store instance; patch SessionStore (the class)
        instead, or every later SessionStore patch in this worker is shadowed
        E       assert not ['newest_for_flow']

    and the leak it reports is not hypothetical: the same run's
    ``test_a_session_deleted_before_the_lock_is_not_recreated``, whose
    wrapper is on the class, never reached its wrapper, so the session was not
    deleted and CONTINUE started it:

        E       assert 200 == 409

    Controls: the fixed file alone and paired with the route test, 21 passed.
    """
    before = dict(vars(session_store))
    yield
    after = dict(vars(session_store))
    left = sorted(k for k in after
                  if k not in before or after[k] is not before[k])
    assert not left, (
        f"this test left {left} on the process-wide session_store instance; "
        f"patch SessionStore (the class) instead, or every later "
        f"SessionStore patch in this worker is shadowed")


class _ArmHub:
    """What ``ResumeArm.tick`` reads of the hub once its sky and device steps
    are pinned: no dusk preparation, and a camera that is there."""
    site: dict = {}
    dusk_arm = None

    def require(self, role):
        return object()


@pytest.fixture
def arm(rig, monkeypatch):
    """A real ResumeArm on the rig's engine. ``hold`` is awaited inside the
    pinned ``_recover``, the ladder that would move the mount, so a test can
    park the tick after it has read the armed session."""
    hooks: dict = {"hold": None}

    async def recover(self, session):
        if hooks["hold"] is not None:
            await hooks["hold"]()
        return None

    monkeypatch.setattr(ResumeArm, "_window_open", lambda self, s, t: True)
    monkeypatch.setattr(ResumeArm, "_devices_ready", lambda self: True)
    monkeypatch.setattr(ResumeArm, "_recover", recover)
    a = ResumeArm(rig.engine, _ArmHub())
    a.hooks = hooks
    return a


def _gate_the_first_read(monkeypatch, barrier: threading.Barrier,
                         read: asyncio.Event) -> None:
    """Hold the route's first lookup - in its worker thread, AFTER it has
    read - until the other party lets it go.

    On the CLASS (``_the_store_singleton_is_left_as_found`` says why): the
    route calls it on the ``session_store`` instance, which finds it there."""
    loop = asyncio.get_running_loop()
    real = SessionStore.newest_for_flow
    first = [True]

    def gated(self, flow_id, statuses):
        found = real(self, flow_id, statuses)
        if first:
            first.clear()
            loop.call_soon_threadsafe(read.set)
            barrier.wait()
        return found

    monkeypatch.setattr(SessionStore, "newest_for_flow", gated)


def _release(barrier: threading.Barrier) -> None:
    try:
        barrier.wait()
    except threading.BrokenBarrierError:
        pass


def _missing(session_id: str, frame_ids) -> list[str]:
    on_disk = {fid for fid, _t, _s in _file_frames(session_id)}
    return [f for f in frame_ids if f not in on_disk]


async def test_resume_arm_starts_first_and_every_frame_it_banks_stays(
        rig, arm, monkeypatch):
    """ResumeArm starts the session between CONTINUE's read and its lock, and
    its run banks two frames. CONTINUE must find the session active under the
    lock and back off, and the file must hold every frame.

    RED under mutant "save before start, outside the lock" (in ``run_flow``,
    ``latest.plan = plan`` and ``await asyncio.to_thread(session_store.save,
    latest)`` ahead of ``_continue_flow_session``, the shape of
    ``patch_session`` plus resume): the stale copy lands on the file of the
    running session, and ``engine.start`` then refuses "already running" with
    the damage done (verbatim):

        _________ test_resume_arm_starts_first_and_every_frame_it_banks_stays _________
        E   AssertionError: the session file lost 2 frame(s) the winning run banked
        E   assert ['cde73506b27...327690eb4af6'] == []
        E
        E     Left contains 2 more items, first extra item: 'cde73506b27d46c9ad583bb2b153e127'
        E     Use -v to get more diff

        Under mutant "no re-read inside the lock" this test keeps its frames
        - ``engine.start``'s refusal holds them - and fails on the answer,
        which is the engine's refusal instead of the section's own:

        AssertionError: CONTINUE did not see, under the lock, that the
        session had been started: 'a sequence is already running'
    """
    fid = await rig.save_flow(LR)
    one = await rig.night_one(fid, [0, 1])
    assert session_store.armed().id == one.id, "premise: armed for ResumeArm"
    barrier = threading.Barrier(2, timeout=BARRIER_TIMEOUT_S)
    read = asyncio.Event()
    _gate_the_first_read(monkeypatch, barrier, read)
    banked: list[str] = []

    async def resume_arm_fires() -> None:
        await asyncio.wait_for(read.wait(), BARRIER_TIMEOUT_S)  # CONTINUE read
        try:
            await arm.tick()
            assert rig.engine.running, "premise: ResumeArm's start won"
            banked.extend(f.id for f in rig.bank([0, 0]))
        finally:
            _release(barrier)

    r, _ = await asyncio.gather(rig.run(fid), resume_arm_fires())

    lost = _missing(one.id, [*(f.id for f in one.frames), *banked])
    assert lost == [], (
        f"the session file lost {len(lost)} frame(s) the winning run banked")
    race = rig.starts[1:]
    assert [(s.won, s.session_id) for s in race if s.won] == [(True, one.id)], (
        f"not exactly one start won: {race}")
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert isinstance(detail, dict) and detail.get("code") == "session_changed", (
        f"CONTINUE did not see, under the lock, that the session had been "
        f"started: {detail!r}")
    assert detail["status"] == "active"
    assert session_store.load(one.id).status == "active"


async def test_a_resume_arm_run_that_ends_in_the_gap_is_continued_whole(
        rig, arm, monkeypatch):
    """ResumeArm starts the session, its run banks two frames and ENDS, all
    between CONTINUE's read and its lock. The session is dormant again, so
    CONTINUE takes it - and must take the file's copy, with those frames, not
    the one it read before the await.

    RED under mutant "no re-read inside the lock" (``s =
    session_store.load(first_read.id)`` -> ``s = first_read``): the run starts
    from the stale ledger, and ``engine.start``'s own save writes it:

        AssertionError: the session file lost 2 frame(s) the ResumeArm run banked
        assert ['8e7aa62adc1...7ec120dd2e0d'] == []
          Left contains 2 more items, first extra item: '8e7aa62adc1c460784d7cf30534bec57'

    RED under mutant "save before start, outside the lock", for the same
    reason by the other door:

        AssertionError: the session file lost 2 frame(s) the ResumeArm run banked
        assert ['87b0c98f489...f10fbf1d4efc'] == []
          Left contains 2 more items, first extra item: '87b0c98f489041bab81876b7b918cd2f'

    DELIBERATE PIN CHANGE (S7 integration, #430, S7 orchestrator ruling 7):
    the last line used to pin ``night == 3``, the run count after night one,
    the ResumeArm run and CONTINUE, all on the real clock seconds apart.
    CONTINUE's ``night`` is the observing night since S7, and unpinned the
    case went red with ``assert 1 == 3``. Night one now starts on the evening
    of night 1 and the race on the evening of night 2 (``Rig.on_night``):
    the ResumeArm run and the CONTINUE that races it fall in one gap of one
    evening, so CONTINUE is the night that run already opened, night 2, and
    never a third night after two.

    RED under mutant "tonight always adds one" (``Session.night_at``
    answering ``len(nights) + 1``, a run count by another name), observed in
    the integration's private copy:

        AssertionError: CONTINUE on the evening a ResumeArm run already
        started is not another night
        assert 3 == 2
    """
    fid = await rig.save_flow(LR)
    rig.on_night(1)
    one = await rig.night_one(fid, [0])
    rig.on_night(2)
    barrier = threading.Barrier(2, timeout=BARRIER_TIMEOUT_S)
    read = asyncio.Event()
    _gate_the_first_read(monkeypatch, barrier, read)
    banked: list[str] = []

    async def resume_arm_run_comes_and_goes() -> None:
        await asyncio.wait_for(read.wait(), BARRIER_TIMEOUT_S)
        try:
            await arm.tick()
            assert rig.engine.running, "premise: ResumeArm's start won"
            banked.extend(f.id for f in rig.bank([0, 1]))
            await rig.end_night("incomplete")
            assert session_store.load(one.id).status == "dormant", (
                "premise: the ResumeArm run left the session dormant")
        finally:
            _release(barrier)

    r, _ = await asyncio.gather(rig.run(fid), resume_arm_run_comes_and_goes())

    everything = [*(f.id for f in one.frames), *banked]
    lost = _missing(one.id, everything)
    assert lost == [], (
        f"the session file lost {len(lost)} frame(s) the ResumeArm run banked")
    assert r.status_code == 200, r.text
    race = rig.starts[1:]
    assert [(s.won, s.session_id) for s in race] == [(True, one.id),
                                                     (True, one.id)], race
    assert race[1].frame_ids == everything, (
        "CONTINUE started from a ledger without the ResumeArm run's frames")
    assert len(session_store.load(one.id).nights) == 3, "premise: three runs"
    assert r.json()["session"]["night"] == 2, (
        "CONTINUE on the evening a ResumeArm run already started is not "
        "another night")


async def test_control_continue_starts_first_and_resume_arm_is_refused(
        rig, arm, bus_lines):
    """CONTROL, the other order. ResumeArm reads the armed session and is
    parked inside its ladder; CONTINUE takes the session and its run banks;
    then ResumeArm's ladder returns holding its stale copy. One winner, and
    nothing of the loser's reaches the file.

    UPDATED FOR #211 (mosaic slice H1, task T4), which changed what the loser
    does. This test used to expect ResumeArm's stale start to REACH
    ``engine.start`` and be refused "already running": two starts, the second
    lost. That refusal kept the frames, but ResumeArm then logged a healthy
    run as a failed start and armed a ten-minute backoff over it. T4 makes
    the tick re-read the session under the store's write lock after the
    ladder and stand down, with one info line, when a run has started; it no
    longer calls ``engine.start`` at all here. So the expectation is now one
    start (CONTINUE's), no hold and no backoff on the arm, and one stand-down
    line that names the run. The old expectation failed against T4's tick,
    observed verbatim:

        AssertionError: [Start(won=True, session_id='43cb92df7ac140b391952407a22aaaf4', ...
        assert [(True, '43cb...407a22aaaf4')] == [(True, '43cb...407a22aaaf4')]
          Right contains one more item: (False, '43cb92df7ac140b391952407a22aaaf4')

    Mutants run in a copy of ``server/``, each from a byte backup of the file
    it changes and restored byte-identical (SHA-256) after, observed verbatim:

    RED under mutant "no re-check" (``_still_startable`` returns ``armed, ""``
    at once: the pre-T4 tick, which starts the copy it read before the
    ladder). The engine's refusal still keeps the frames; the tick is what is
    wrong:

        AssertionError: ResumeArm's stale copy reached engine.start:
        [Start(won=True, session_id='0693973b638840caba45a9ef5424c73b', [...]
        error=''), Start(won=False, session_id='0693973b638840caba45a9ef5424c73b',
        [...] error='a sequence is already running')]
        assert [(True, '0693...9ef5424c73b')] == [(True, '0693...9ef5424c73b')]
          Left contains one more item: (False, '0693973b638840caba45a9ef5424c73b')

    RED under mutant "no running re-check" (the ``if self.engine.running``
    branch of ``_still_startable`` removed). The status check then stands the
    tick down, so no start is made, but for the wrong reason: the line must
    name the run, not the status:

        AssertionError: ["auto-resume stood down for 'continue me': it is active now, not dormant"]
        assert (1 == 1 and 'a run started' in "auto-resume stood down for 'continue me': it is active now, not dormant")

    GREEN under mutant "engine.start without its already-running refusal"
    (``engine.py``: the ``if self.running: raise`` removed), which was this
    test's recorded mutant before T4. It no longer reaches that refusal: the
    tick's stand-down, not the engine, is what holds this interleaving now.
    """
    fid = await rig.save_flow(LR)
    one = await rig.night_one(fid, [0])
    barrier = threading.Barrier(2, timeout=BARRIER_TIMEOUT_S)
    inside = asyncio.Event()

    async def hold() -> None:
        inside.set()
        await asyncio.to_thread(barrier.wait)

    arm.hooks["hold"] = hold
    tick = asyncio.create_task(arm.tick())
    try:
        await inside.wait()               # ResumeArm holds its copy
        r = await rig.run(fid)
        assert r.status_code == 200, r.text
        banked = [f.id for f in rig.bank([0, 1])]
    finally:
        await asyncio.to_thread(_release, barrier)
        await tick

    lost = _missing(one.id, [*(f.id for f in one.frames), *banked])
    assert lost == [], (
        f"the session file lost {len(lost)} frame(s) the winning run banked")
    race = rig.starts[1:]
    assert [(s.won, s.session_id) for s in race] == [(True, one.id)], (
        f"ResumeArm's stale copy reached engine.start: {race}")
    assert (arm._retry_at, arm.hold) == (0.0, None), (
        "a run that is going is not a refusal to back off from")
    stood_down = [m for lvl, m, _src in bus_lines
                  if lvl == "info" and "stood down" in m]
    assert len(stood_down) == 1 and "a run started" in stood_down[0], (
        stood_down)


async def test_a_session_deleted_before_the_lock_is_not_recreated(
        rig, monkeypatch):
    """The session is deleted between CONTINUE's lookup and its lock - here
    inside the lookup's own worker thread, after it has read, which is where
    ``DELETE /api/sessions/{id}``'s ``to_thread(session_store.delete)`` would
    land. The in-lock re-read finds no file, and CONTINUE must refuse with
    ``session_changed`` (status None) rather than start the copy it read:
    ``engine.start``'s save would put a deleted ledger back on disk, running.

    RED under mutant "a gone session continues" (``except (KeyError,
    SessionUnreadable): s = None`` -> ``s = first_read``), observed by the
    verifier:

        AssertionError: {"started":true,"flow_id":"9edcb6ffa11d415aaf25fca1114658d5",
        "frames":5,"unmapped":[],"session":{"id":"f78eeec7b24c45709d698bc547f24ee7",
        "night":2,"continued":true,"kept":2,"new":0,"dropped":0}}
        assert 200 == 409
         +  where 200 = <Response [200 OK]>.status_code
    """
    fid = await rig.save_flow(LR)
    one = await rig.night_one(fid, [0])
    real = SessionStore.newest_for_flow

    def read_then_deleted(self, flow_id, statuses):
        found = real(self, flow_id, statuses)
        if found is not None and self._path(found.id).exists():
            self.delete(found.id)
        return found

    # On the class, like the gate above, so nothing is left on the instance.
    monkeypatch.setattr(SessionStore, "newest_for_flow", read_then_deleted)

    r = await rig.run(fid)

    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert (detail["code"], detail["status"], detail["session_id"]) == (
        "session_changed", None, one.id), detail
    assert rig.starts[1:] == [], "a deleted session reached engine.start"
    assert not session_store._path(one.id).exists(), (
        "CONTINUE put a deleted session back on disk")


async def test_continue_starts_the_engine_inside_the_write_lock(rig,
                                                                monkeypatch):
    """The tests above all run on the event loop's one thread, where the
    section's having no ``await`` is what keeps ResumeArm out; none of them
    can see the lock. The lock is for the writers that are NOT on that thread:
    a route's ``asyncio.to_thread(session_store.save, ...)``. So probe it from
    another thread at the moment CONTINUE calls ``engine.start``: the store's
    write lock - the one every ``save`` takes - must be held.

    RED under mutant "CONTINUE starts outside the lock" (``with
    session_store.write_locked():`` -> ``if True:`` in
    ``_continue_flow_session``):

        AssertionError: another thread could take the store's write lock while
        CONTINUE started the engine: [(True, True)]
        assert [(True, True)] == [(True, False)]

    RED under mutant "write_locked without the lock" (``session.py``), the
    same failure:

        AssertionError: another thread could take the store's write lock while
        CONTINUE started the engine: [(True, True)]
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
    r = await rig.run(fid)
    assert r.status_code == 200, r.text
    assert probes == [(True, False)], (
        "another thread could take the store's write lock while CONTINUE "
        f"started the engine: {probes}")
