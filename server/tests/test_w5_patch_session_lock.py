"""PATCH /api/sessions/{id} decides and saves in one locked section (#167).

The route used to load the session, run its refusals and mutations, and save
it back through SEPARATE ``asyncio.to_thread`` calls -- each its own await,
with nothing holding the gap between them. ResumeArm starts a dormant session
on its own tick INSIDE its own ``session_store.write_locked()`` section
(``ResumeArm.tick``), and a start landing in that gap left this route's later
save overwriting a run that had JUST begun: the frames it had already banked,
and its ``active`` status, both went back to whatever this route's stale copy
said, and the request still answered 200.

Now the whole thing -- load, every refusal, every mutation, the save -- is one
synchronous section under ``session_store.write_locked()``, run on a worker
thread (``asyncio.to_thread``) so the event loop is never blocked by a
session's file I/O. ``write_locked()``'s ``RLock`` is what closes the race:
whichever side asks for the lock first -- this route, or ResumeArm's own
section -- runs to completion before the other is let in.

THE INTERLEAVING IS FORCED WITHOUT TIMING, the same way
``test_delete_session_race.py`` forces it for DELETE, reusing its
``_WatchedLock`` probe: the test holds ``write_locked()`` itself (standing in
for ResumeArm's own section, mid-way through starting the session) and starts
the PATCH request; the probe reports the moment the route's worker thread
asks for the lock, which is as soon as it can ask, so the test needs no sleep
to know it is waiting rather than racing ahead.

Each mutant was applied to a byte-for-byte backup of the file it changes and
the file was restored byte-identical (SHA-256 compared) afterwards. The
failure is quoted as observed.
"""
from __future__ import annotations

import asyncio
import threading
import time

from astrodeck.sequence.models import ExposureStep, SequencePlan, Target
from astrodeck.sequence.session import Session, SessionStore, session_store
from test_delete_session_race import _WatchedLock  # noqa: reused lock probe
from test_flows_continue import rig  # noqa: F401 (fixture)

#: A deadlock guard for waits a healthy run satisfies at once.
WAIT_TIMEOUT_S = 30.0


def _plan(name: str = "patchlock") -> SequencePlan:
    return SequencePlan(name=name, targets=[Target(
        name="M42", ra_hours=5.5881, dec_deg=-5.3911,
        steps=[ExposureStep(filter="L", exposure_s=1.0, count=3)])])


def _stored(status: str = "dormant", name: str = "patchlock") -> Session:
    s = Session(name=name, created_ts=time.time(), status=status,
                plan=_plan(name))
    session_store.save(s)
    return s


async def test_a_session_started_while_patch_waits_is_not_overwritten(
        rig, monkeypatch):
    """ResumeArm's own locked section starts the dormant session (here stood
    in for directly: the session is flipped to ``active`` and saved, inside a
    ``write_locked()`` section the test holds) WHILE a plan-edit PATCH of the
    same session is asking for the same lock. The PATCH must wait for the
    section to end, then refuse on what it finds: a plan edit requires a
    dormant session, and this one no longer is.

    Before #167's fix this could not happen -- the route read ``status``
    BEFORE any lock existed to wait on, so its answer belonged to a session
    that was dormant a moment earlier and was not any more.

    RED under mutant "write_locked without the lock" (``patch_session``'s
    nested ``_locked``: ``with session_store.write_locked():`` -> ``if
    True:``), observed verbatim:

        _________ test_a_session_started_while_patch_waits_is_not_overwritten _________
        tests\\test_w5_patch_session_lock.py:99: in test_a_session_started_while_patch_waits_is_not_overwritten
            assert await asyncio.to_thread(arrived.wait, WAIT_TIMEOUT_S), (
        E   AssertionError: the route's worker thread never asked for the write lock
        E   assert False

    (the probe's ``arrived`` event is never set: the route's worker thread
    never asks for the lock at all, so it races ahead of the held section
    instead of waiting for it, and the plan edit would land on the session
    this test just set ``active`` -- the #167 race, reproduced.)
    """
    s = _stored()                      # dormant
    arrived = threading.Event()
    monkeypatch.setattr(SessionStore, "_write_lock",
                        _WatchedLock(arrived.set))

    with session_store.write_locked():
        # Stands in for ResumeArm's own section: by the time anyone else is
        # let into the lock, the session it started is no longer dormant.
        started = session_store.load(s.id)
        started.status = "active"
        session_store.save(started)

        task = asyncio.create_task(rig.client.patch(
            f"/api/sessions/{s.id}", json={"plan": _plan("edited").model_dump()}))
        assert await asyncio.to_thread(arrived.wait, WAIT_TIMEOUT_S), (
            "the route's worker thread never asked for the write lock")
        assert not task.done(), (
            "the PATCH finished without waiting for the held section")

    r = await asyncio.wait_for(task, WAIT_TIMEOUT_S)

    assert r.status_code == 409, r.text
    assert r.json()["detail"] == "plan edits require a dormant session", r.text
    # Nothing the route decided before it saw the fresh status was ever
    # written: the file still says exactly what the held section left it as.
    fresh = session_store.load(s.id)
    assert fresh.status == "active"
    assert fresh.plan.name == "patchlock", "the stale plan edit was saved"


async def test_control_an_unraced_plan_edit_still_succeeds(rig):
    """CONTROL: nobody else touches the session. The locked section changes
    nothing about an ordinary PATCH's outcome -- it still succeeds, 200, with
    the usual id-merge report."""
    s = _stored()

    r = await rig.client.patch(f"/api/sessions/{s.id}",
                               json={"plan": _plan("edited").model_dump()})

    assert r.status_code == 200, r.text
    assert session_store.load(s.id).plan.name == "edited"
