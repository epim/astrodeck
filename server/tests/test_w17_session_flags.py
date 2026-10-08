# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Wave 17 integration: the session flags the queue (#598) left loose.

THREE FINDINGS, ALL FOUND BY WP-133 AND CLOSED HERE.

#837 (a) ARMING ENDS THE WAIT. ``PATCH {auto_resume: true}`` on a session
that waits behind another set the flag and left ``queued_behind`` on the file,
so the session was armed AND queued: it would start by itself and then be armed
a second time when the run it was waiting for completed. ``engine.start``
clears the marker on every start path for the same reason; the route now clears
it where it arms.

#837 (b) ONE SINGLETON LOOP. ``engine.start`` and ``PATCH auto_resume`` each
ran their own copy of "arm this session, disarm every other armed one", and
the queue's promotion needed a third. WP-133 wrote it once
(``SequenceEngine._arm_exclusively``); the other two call it now. What is
pinned here is that ALL THREE reach it, so a fourth private copy (or a revert
of either) is a red test rather than a code-review catch. The behaviour the
copies shared is pinned where it always was: ``test_sessions_api`` (the route's
warning and ``disarmed`` list), ``test_w4_disarm_visible`` (``start``'s),
``test_resume_ladder_stops`` (the route's ladder stop), and
``test_w17_queued_next_session`` (the promotion's).

    ONE DIFFERENCE IS DELIBERATE and is the only one: the copies named EVERY
    session they switched off, a finished session's leftover flag included,
    where the shared loop names only a dormant or live one ("hygiene, not
    news", WP-133's own rule for the promotion). The flag is still switched
    off on the finished session, and it is pinned below.

#838 A FINISHED SESSION IS NOT ARMED. ``start()`` arms every run, so a run that
finished the plan left a COMPLETE session carrying ``auto_resume``: ``armed()``
never returns it (it asks for dormant), but the card drew an "armed without a
safety monitor" chip on a night that was over and the singleton named it when a
later start switched it off. ``_finalize_report`` clears the flag where it
decides the status is complete, and ONLY there: every other ending still owes
frames and keeps the arming that carries it through a crash or a restart.

HARNESS: ``test_w17_queued_next_session``'s (the real app and engine over a
simulator hub, and ``_finalize_report`` driven directly for the endings a run
cannot be made to reach on demand).

NAMED MUTANTS (each run from a byte backup inside this worktree, restored
byte-identically with sha256 compared; the failing assertion is quoted in the
docstring of the test that catches it).
"""
from __future__ import annotations

import pytest

from astrodeck.sequence import SequenceEngine
from astrodeck.sequence.session import session_store
from test_w17_queued_next_session import (  # noqa: F401  (rig is a fixture)
    _finish, _plan, _stored, rig, wait_for,
)


# ------------------------------------------------- #837 (a): arming ends a wait

async def test_arming_a_queued_session_clears_its_wait(rig):
    """B waits behind the armed A. Arming B in its own right ends the wait on
    the file, disarms A (the singleton), and says so.

    MUTANT "arming keeps the marker" (``s.queued_behind = None`` removed from
    the route's ``if body.auto_resume:`` branch) turned this red, observed:

        AssertionError: arming a session ends its wait, on disk
        assert '<the id of the session it waited behind>' is None
    """
    a = _stored("tonight", auto_resume=True)
    b = _stored("mosaic", queued_behind=a.id)
    r = await rig.patch(b.id, auto_resume=True)
    assert r.status_code == 200, r.text
    stored = session_store.load(b.id)
    assert stored.auto_resume is True
    assert stored.queued_behind is None, "arming a session ends its wait, on disk"
    assert session_store.load(a.id).auto_resume is False, "the singleton held"
    assert r.json()["disarmed"] == [{"id": a.id, "name": "tonight"}]
    armed = session_store.armed()
    assert armed is not None and armed.id == b.id


async def test_only_arming_clears_the_wait(rig):
    """The control for the case above: the marker goes because the session was
    ARMED, not because the request was an auto_resume one. Disarming an
    unarmed queued session (a no-op the UI can send) leaves it waiting, and so
    does a plan edit.

    MUTANT "every auto_resume request clears the marker" (the clear moved out
    of the ``if body.auto_resume:`` branch) turned this red, observed:

        AssertionError: disarming a session that was never armed must not end
        its wait
        assert None == '<the id of the session it waits behind>'
    """
    a = _stored("tonight", auto_resume=True)
    b = _stored("mosaic", queued_behind=a.id)
    r = await rig.patch(b.id, auto_resume=False)
    assert r.status_code == 200, r.text
    assert session_store.load(b.id).queued_behind == a.id, (
        "disarming a session that was never armed must not end its wait")
    assert session_store.load(a.id).auto_resume is True


# --------------------------------------- #837 (b): the three callers, one loop

@pytest.fixture
def arm_spy(monkeypatch):
    """Every call of ``SequenceEngine._arm_exclusively``: which session it
    armed, and whether the caller handed it a per-session hook."""
    calls: list[tuple[str, bool]] = []
    real = SequenceEngine._arm_exclusively

    def spy(session, on_disarm=None):
        calls.append((session.id, on_disarm is not None))
        return real(session, on_disarm)

    monkeypatch.setattr(SequenceEngine, "_arm_exclusively", staticmethod(spy))
    return calls


async def test_start_arms_through_the_shared_loop(rig, arm_spy):
    """A run start arms its session through ``_arm_exclusively``, with no hook
    (no ladder can be working while a run starts), and the session it armed is
    the one the loop was asked about.

    MUTANT "start has its own copy again" (``disarmed = self._arm_exclusively(
    session)`` made ``disarmed = []`` in ``start``) turned this red, observed:

        AssertionError: ('start did not arm through the shared singleton
        loop', [])
        assert [] == [('<the session id>', False)]
    """
    rig.engine.start(_plan("tonight", count=60))
    sid = rig.engine._session.id
    try:
        assert arm_spy == [(sid, False)], (
            "start did not arm through the shared singleton loop", arm_spy)
    finally:
        await rig.engine.abort()


async def test_the_patch_route_arms_through_the_shared_loop_with_a_ladder_hook(
        rig, arm_spy):
    """``PATCH {auto_resume: true}`` arms through ``_arm_exclusively``, and
    hands it the hook that stops a recovery ladder working on a session about
    to lose its switch (#220): the one thing the route's copy did that
    ``start``'s did not.

    MUTANT "the route has its own copy again" (``engine._arm_exclusively(``
    made ``(lambda *a, **k: [])(`` in ``patch_session``) turned this red,
    observed:

        AssertionError: ('PATCH did not arm through the shared singleton
        loop', [])
        assert [] == [('<the session id>', True)]
    """
    a = _stored("tonight", auto_resume=True)
    b = _stored("mosaic")
    r = await rig.patch(b.id, auto_resume=True)
    assert r.status_code == 200, r.text
    assert arm_spy == [(b.id, True)], (
        "PATCH did not arm through the shared singleton loop", arm_spy)
    assert session_store.load(a.id).auto_resume is False


async def test_the_promotion_arms_through_the_shared_loop(rig, arm_spy):
    """The promotion is the loop's first caller and keeps its shape: one call,
    no hook."""
    a = _stored("tonight", count=3, done=3, auto_resume=True)
    b = _stored("mosaic", queued_behind=a.id)
    _finish(rig, a, "complete")
    assert arm_spy == [(b.id, False)], arm_spy


async def test_a_finished_sessions_leftover_flag_is_switched_off_but_not_named(
        rig, bus_lines):
    """THE ONE DELIBERATE DIFFERENCE from the copies the loop replaced: a
    COMPLETE session still carrying ``auto_resume`` (a file from before #838)
    is switched off by an arming PATCH and by a start, and NEITHER names it:
    ``armed()`` never returned it, so nothing was lost.

    MUTANT "the loop names finished sessions" (``_arm_exclusively``'s
    ``if other.status in ("dormant", "active"):`` made ``if True:``) turned
    this red, observed:

        AssertionError: a finished session's leftover flag was named as news
        assert [{'id': '<the id>', 'name': 'old night'}] == []
    """
    old = _stored("old night", status="complete", count=3, done=3,
                  auto_resume=True)
    b = _stored("mosaic")
    r = await rig.patch(b.id, auto_resume=True)
    assert r.status_code == 200, r.text
    assert session_store.load(old.id).auto_resume is False, (
        "the leftover flag was not switched off")
    assert r.json().get("disarmed", []) == [], (
        "a finished session's leftover flag was named as news")
    assert not any("old night" in m for lvl, m, _s in bus_lines
                   if lvl == "warning"), bus_lines


# ----------------------------------------- #838: a finished session is unarmed

async def test_a_run_that_finishes_the_plan_leaves_the_session_unarmed(rig):
    """A REAL RUN, end to end: ``start()`` arms it, the plan is shot to the
    last frame, and the stored session is complete and NOT armed.

    MUTANT "a complete session keeps the flag" (the ``if self._session.status
    == "complete": self._session.auto_resume = False`` block removed from
    ``_finalize_report``) turned this red, observed:

        AssertionError: a finished session is still armed
        assert True is False
    """
    rig.engine.start(_plan("tonight", count=2))
    sid = rig.engine._session.id
    assert rig.engine._session.auto_resume is True, (
        "premise: start() arms every run, so the clear below is the only "
        "thing that can leave it unarmed")
    assert await wait_for(lambda: session_store.load(sid).status == "complete")
    assert session_store.load(sid).auto_resume is False, (
        "a finished session is still armed")
    assert await wait_for(lambda: not rig.engine.running)


async def test_a_complete_finish_clears_the_flag(rig):
    """The direct harness, over a stored armed session with nothing owed:
    finalized 'complete', it is complete and unarmed.

    MUTANT "a complete session keeps the flag": as above, observed:

        AssertionError: assert True is False
    """
    a = _stored("tonight", count=3, done=3, auto_resume=True)
    _finish(rig, a, "complete")
    stored = session_store.load(a.id)
    assert stored.status == "complete"
    assert stored.auto_resume is False


@pytest.mark.parametrize("reason, done", [
    ("complete", 1),            # the run finished its pass but frames are owed
    ("dawn_cutoff", 1), ("incomplete", 1), ("unsafe", 1), ("error", 1),
    ("quality", 1), ("cooling_skip", 1), ("shutdown", 1),
    # Nothing owed, but the run did not END 'complete': dormant by the
    # engine's own rule, and the arming that carries a crash through stays.
    ("dawn_cutoff", 3),
])
async def test_every_other_ending_keeps_the_arming(rig, reason, done):
    """A dormant session owes frames (or ended on something other than a
    completion) and keeps the arming that carries it through a crash or a
    restart: only a COMPLETE finish clears it.

    MUTANT "every ending clears the flag" (the clear's ``if status ==
    "complete"`` condition made ``if True:``) turned all nine cases red,
    observed:

        AssertionError: a 'unsafe' ending dropped the arming that carries it
        through a restart
        assert False is True
    """
    a = _stored("tonight", count=3, done=done, auto_resume=True)
    _finish(rig, a, reason)
    stored = session_store.load(a.id)
    assert stored.status == "dormant"
    assert stored.auto_resume is True, (
        f"a {reason!r} ending dropped the arming that carries it through a "
        f"restart")
