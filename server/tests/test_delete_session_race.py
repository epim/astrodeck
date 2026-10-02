# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""DELETE /api/sessions/{id} decides and unlinks in one locked section (#212).

The route used to load the session in a worker thread, refuse an ``active``
one, await ``engine.drain_thumbs_for_session``, and only then unlink, through
``asyncio.to_thread(session_store.delete, ...)``. Those are separate awaits,
and ``SessionStore.delete`` took no lock, so any starter could take the session
between the status check and the unlink: ResumeArm, ``/resume``, Run CONTINUE.
The unlink then removed the file and thumbnails of a RUNNING session and
answered 200, and the engine's next ledger write put the JSON back from memory.

Now the thumb drain stays first (it must: a render's trailing save would
resurrect the file), and then one synchronous section under
``session_store.write_locked()`` re-reads the session, refuses unless it is
dormant, complete or abandoned and the engine is not running it, and unlinks.
Nothing on the event loop runs inside that section, and ``delete`` now takes
the store's write lock itself, so a worker-thread delete cannot interleave
with a ``write_locked`` caller either.

THE INTERLEAVING IS FORCED. ``engine.drain_thumbs_for_session`` is the await
the race needs, so the test pins it on an Event: the route parks there after
its first load, the test acts (starts the session, or changes the file), and
then releases it. The harness is ``test_flows_continue.py``'s ``rig``: the real
app over ASGI on this loop, and a real ``SequenceEngine`` whose imaging loop
alone is replaced, so ``engine.start``'s status flip and save are the
engine's own.

Each mutant was applied to a byte-for-byte backup of the file it changes and
the file was restored byte-identical (SHA-256 compared) afterwards. The
failures are quoted as observed.
"""
from __future__ import annotations

import asyncio
import threading
import time

import pytest

import astrodeck.hub as hub_module
from astrodeck.sequence.models import ExposureStep, SequencePlan, Target
from astrodeck.sequence.session import (Session, SessionFrame, SessionStore,
                                        session_store)
from test_flows_continue import rig  # noqa: F401 (fixture)

#: A deadlock guard for waits a healthy run satisfies at once.
WAIT_TIMEOUT_S = 30.0


def _plan(name: str = "del") -> SequencePlan:
    return SequencePlan(name=name, targets=[Target(
        name="M42", ra_hours=5.5881, dec_deg=-5.3911,
        steps=[ExposureStep(filter="L", exposure_s=1.0, count=3)])])


def _stored(status: str = "dormant", name: str = "del") -> Session:
    """A stored session holding one frame, with a thumbnail on disk."""
    plan = _plan(name)
    s = Session(name=name, created_ts=time.time(), status=status, plan=plan)
    t = plan.targets[0]
    s.frames.append(SessionFrame(ts=1.0, night="n1", target_id=t.id,
                                 step_id=t.steps[0].id, path="f.fits",
                                 auto_accepted=True))
    session_store.save(s)
    thumbs = session_store.thumbs_dir(s.id)
    thumbs.mkdir(parents=True)
    (thumbs / f"{s.frames[0].id}.jpg").write_bytes(b"\xff\xd8thumb")
    return s


def _bak(session_id: str):
    path = session_store._path(session_id)
    return path.with_suffix(path.suffix + ".bak")


def _pin_the_drain(rig, monkeypatch):
    """Park the route inside its thumb drain, after its first load, until the
    returned ``release`` is set. ``entered`` says it has arrived."""
    entered, release = asyncio.Event(), asyncio.Event()
    real = rig.engine.drain_thumbs_for_session

    async def pinned(session_id: str) -> None:
        entered.set()
        await release.wait()
        await real(session_id)

    monkeypatch.setattr(rig.engine, "drain_thumbs_for_session", pinned)
    return entered, release


async def _delete_across(rig, monkeypatch, session_id: str, act):
    """DELETE ``session_id``, running ``act()`` while the route is parked in
    its drain."""
    entered, release = _pin_the_drain(rig, monkeypatch)
    task = asyncio.create_task(rig.client.delete(f"/api/sessions/{session_id}"))
    try:
        await asyncio.wait_for(entered.wait(), WAIT_TIMEOUT_S)
        act()
    finally:
        release.set()
    return await asyncio.wait_for(task, WAIT_TIMEOUT_S)


def _mark_active(session_id: str) -> None:
    """The file says active, as ``engine.start``'s save leaves it, with no
    run behind it: the status half of the in-lock check on its own."""
    s = session_store.load(session_id)
    s.status = "active"
    session_store.save(s)


@pytest.mark.parametrize("gap", ["engine_start", "file_marked_active"])
async def test_a_session_taken_during_the_drain_is_not_deleted(
        rig, monkeypatch, gap):
    """The route has loaded a dormant session and is draining its thumbs
    when the session is taken. ``engine_start`` is the race itself:
    ``engine.start(session=...)``, the call ResumeArm, ``/resume`` and
    CONTINUE all make. ``file_marked_active`` is the status half alone: the
    file says active and nothing is running, which the pre-drain check
    already refuses, so the in-lock check must refuse it too. Either way the
    answer is 409, and the file and the thumbnails are still there.

    RED under mutant "status checked only before the await" (today's code:
    ``await asyncio.to_thread(session_store.delete, session_id)`` and a
    return ahead of the locked section), both cases, observed verbatim
    (``--tb=short``; the other three race tests in this file went red with
    it, the controls stayed green):

        _____ test_a_session_taken_during_the_drain_is_not_deleted[engine_start] ______
        E   AssertionError: {"deleted":"99f24f3d3ce14f42a5fbfb6c3a7e28b9"}
        E   assert 200 == 409

    and ``[file_marked_active]`` the same, ``{"deleted":"a3c652a611934858bdb62393409fa63a"}``.

    RED under mutant "the lock checks the engine only" (both status tests
    removed from the locked section: ``if running_it:``, and the deletable
    list skipped), ``file_marked_active`` only:

        E   AssertionError: {"deleted":"32bdc82d875f4ae59e58bcf7b7b4c6e5"}
        E   assert 200 == 409

    RED under mutant "active left to the list" (``or s.status == "active"``
    removed, so the deletable list refuses it in other words),
    ``file_marked_active`` only, which is what the wording assertion is for:

        E   AssertionError: {"detail":"cannot delete a session that is active"}
        E   assert 'cannot delet...hat is active' == 'cannot delet...nning session'
    """
    s = _stored()
    thumbs = session_store.thumbs_dir(s.id)

    def act() -> None:
        if gap == "engine_start":
            rig.engine.start(s.plan, session=session_store.load(s.id))
            assert rig.engine.running, "premise: the session was started"
        else:
            _mark_active(s.id)

    r = await _delete_across(rig, monkeypatch, s.id, act)

    assert r.status_code == 409, r.text
    assert r.json()["detail"] == "cannot delete a running session", r.text
    assert session_store._path(s.id).exists(), "the session file was unlinked"
    assert thumbs.is_dir() and any(thumbs.iterdir()), (
        "the thumbnails of a taken session were deleted")
    assert session_store.load(s.id).status == "active"


async def test_a_running_session_whose_file_says_dormant_is_not_deleted(
        rig):
    """The engine is running the session and its file says dormant. That is
    the state #167's PATCH race leaves: ``patch_session`` loads a dormant
    copy, awaits, and saves it back over a session that started in between.
    The status alone would let the delete through, so the engine's own word
    that it holds the session must refuse it.

    RED under mutant "no engine check" (the ``running_it`` test removed from
    the locked section), observed verbatim:

        ________ test_a_running_session_whose_file_says_dormant_is_not_deleted ________
        E   AssertionError: {"deleted":"d075145a7e3e4477a535f6039d548b3a"}
        E   assert 200 == 409
    """
    s = _stored()
    stale = session_store.load(s.id)          # read while dormant, as PATCH does
    rig.engine.start(s.plan, session=session_store.load(s.id))
    session_store.save(stale)                 # ...and saved after the start
    assert session_store.load(s.id).status == "dormant", (
        "premise: the file says dormant")
    assert rig.engine.running, "premise: the engine is running the session"

    r = await rig.client.delete(f"/api/sessions/{s.id}")

    assert r.status_code == 409, r.text
    assert session_store._path(s.id).exists()
    assert session_store.thumbs_dir(s.id).is_dir()


async def test_a_session_deleted_during_the_drain_answers_404(rig,
                                                              monkeypatch):
    """Two deletes of one session: the other lands while this one drains.
    The re-read finds no file, and that is "not found", said as a 404, not
    a 500 from the ``KeyError``.

    RED under mutant "no KeyError at the re-read" (the ``except KeyError``
    around the in-lock load removed): the exception escapes the route, and
    the ASGI transport raises it into the test (observed verbatim):

        _____________ test_a_session_deleted_during_the_drain_answers_404 _____________
        tests\\test_delete_session_race.py:100: in _delete_across
        E   KeyError: '13317bae08774a16a27b604679184175'

    Under "status checked only before the await" it answers 200 instead:

        E   AssertionError: {"deleted":"d57c633172e248fcb47d559c9d1217af"}
        E   assert 200 == 404
    """
    s = _stored()

    r = await _delete_across(rig, monkeypatch, s.id,
                             lambda: session_store.delete(s.id))

    assert r.status_code == 404, r.text


async def test_a_session_in_a_status_the_delete_does_not_name_is_refused(
        rig):
    """``Session.status`` is a plain string, so a hand-edited file, or one a
    newer build wrote, loads with a status this build does not know. The
    delete removes only the three statuses it names and refuses anything
    else, saying which status it found; the file and thumbnails stay.

    RED under mutant "no deletable list" (the ``_DELETABLE_STATUSES`` test in
    the locked section replaced by ``if False:``), which every other test in
    this file survived, observed verbatim (``--tb=short``):

        _______ test_a_session_in_a_status_the_delete_does_not_name_is_refused ________
        E   AssertionError: {"deleted":"0a0f5f8cbb9f4114ad9692d006b18e86"}
        E   assert 200 == 409
    """
    s = _stored("paused")
    assert session_store.load(s.id).status == "paused", (
        "premise: an unknown status loads")

    r = await rig.client.delete(f"/api/sessions/{s.id}")

    assert r.status_code == 409, r.text
    assert r.json()["detail"] == "cannot delete a session that is paused", (
        r.text)
    assert session_store._path(s.id).exists(), "the session file was unlinked"
    assert session_store.thumbs_dir(s.id).is_dir()


@pytest.mark.parametrize("status", ["dormant", "complete", "abandoned"])
async def test_control_an_untouched_session_is_deleted_whole(rig, monkeypatch,
                                                             status):
    """CONTROL: nobody takes the session while the route drains. It is
    deleted, 200, and so are its thumbnails and its ADOPT ``.bak``: every
    status a session can be deleted in.

    RED under mutant "dormant only" (the deletable statuses narrowed to
    ``("dormant",)``), ``complete`` and ``abandoned``, observed verbatim:

        ________ test_control_an_untouched_session_is_deleted_whole[complete] _________
        E   AssertionError: {"detail":"cannot delete a session that is complete"}
        E   assert 409 == 200
        ________ test_control_an_untouched_session_is_deleted_whole[abandoned] ________
        E   AssertionError: {"detail":"cannot delete a session that is abandoned"}
        E   assert 409 == 200
    """
    s = _stored(status)
    session_store.backup(s.id)
    assert _bak(s.id).exists(), "premise: the backup is on disk"

    r = await _delete_across(rig, monkeypatch, s.id, lambda: None)

    assert r.status_code == 200, r.text
    assert r.json() == {"deleted": s.id}
    assert not session_store._path(s.id).exists()
    assert not session_store.thumbs_dir(s.id).parent.exists()
    assert not _bak(s.id).exists()


async def test_control_another_sessions_run_does_not_block_the_delete(
        rig, monkeypatch):
    """CONTROL: the engine is running a DIFFERENT session. The check is "the
    engine is not running THIS one", so the dormant session is deleted.

    RED under mutant "any run blocks a delete" (``running_it`` reduced to
    ``engine.running``), observed verbatim:

        _________ test_control_another_sessions_run_does_not_block_the_delete _________
        E   AssertionError: {"detail":"cannot delete a running session"}
        E   assert 409 == 200
    """
    doomed = _stored(name="doomed")
    live = _stored(name="live")
    rig.engine.start(live.plan, session=session_store.load(live.id))
    assert rig.engine.running, "premise: another session is running"

    r = await rig.client.delete(f"/api/sessions/{doomed.id}")

    assert r.status_code == 200, r.text
    assert not session_store._path(doomed.id).exists()
    assert session_store._path(live.id).exists()


# ------------------------------------------------------ the store's own lock

class _WatchedLock:
    """The store's RLock, reporting when a thread other than the one that
    built it asks for it. Every ``with self._write_lock`` in the store goes
    through ``__enter__``, so this sees a delete ask for the lock before it
    blocks on it."""

    def __init__(self, on_foreign_ask) -> None:
        self._real = threading.RLock()
        self._owner = threading.get_ident()
        self._on_foreign_ask = on_foreign_ask

    def __enter__(self):
        if threading.get_ident() != self._owner:
            self._on_foreign_ask()
        self._real.acquire()
        return self

    def __exit__(self, *exc) -> None:
        self._real.release()


def test_a_worker_thread_delete_waits_for_a_write_locked_section(
        tmp_path, monkeypatch):
    """A ``write_locked`` section on one thread (CONTINUE's, ResumeArm's,
    the DELETE route's own) and a delete on a worker thread. The delete must
    wait for the section to end: the lock is what holds every other
    worker-thread writer off, and a delete that ignored it could unlink a
    session inside a section that has just decided to start it.

    NO TIMING. The worker's delete sets ``arrived`` either when it asks for
    the lock (the fix: it then blocks) or when it has finished (the mutant:
    it never asked). Which one happened is read after ``arrived``, so the
    verdict does not depend on how fast a thread starts.

    RED under mutant "delete without the lock" (``SessionStore.delete``'s
    ``with self._write_lock:`` -> ``if True:``), observed verbatim:

        ________ test_a_worker_thread_delete_waits_for_a_write_locked_section _________
        E   AssertionError: a worker-thread delete ran inside a write_locked section
        E   assert not True
    """
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    arrived, finished = threading.Event(), threading.Event()
    monkeypatch.setattr(SessionStore, "_write_lock",
                        _WatchedLock(arrived.set))
    s = _stored()
    path = session_store._path(s.id)

    def worker() -> None:
        session_store.delete(s.id)
        finished.set()
        arrived.set()

    t = threading.Thread(target=worker, daemon=True)
    with session_store.write_locked():
        t.start()
        assert arrived.wait(WAIT_TIMEOUT_S), "the worker never ran"
        ran_inside = finished.is_set()
        still_there = path.exists()
    t.join(WAIT_TIMEOUT_S)

    assert not ran_inside, (
        "a worker-thread delete ran inside a write_locked section")
    assert still_there
    assert finished.is_set() and not path.exists(), (
        "the delete did not complete once the section ended")


def test_control_a_delete_with_no_section_open_does_not_wait(tmp_path,
                                                             monkeypatch):
    """CONTROL: with no section holding the lock, a worker-thread delete
    goes straight through, and its thumbnails and ``.bak`` go with it."""
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    s = _stored()
    session_store.backup(s.id)
    t = threading.Thread(target=session_store.delete, args=(s.id,))
    t.start()
    t.join(WAIT_TIMEOUT_S)
    assert not t.is_alive(), "a delete with no section open waited"
    assert not session_store._path(s.id).exists()
    assert not session_store.thumbs_dir(s.id).parent.exists()
    assert not _bak(s.id).exists()
