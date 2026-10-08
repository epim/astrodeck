# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Run reopens a complete flow session when the edited flow owes more, after
asking (#179, spec I-30; backlog WP-120).

``POST /api/flows/{id}/run`` continued a flow's newest session only when it
was dormant. A COMPLETE newest session started a fresh one, so raising a
finished campaign's cycles or counts could not extend it in place: its banked
subs stopped counting toward the new quota, the old session was disarmed and
left on disk, and the operator held two sessions for one flow.

The rule, as built:

* The newest session is complete, the compile shares at least one step id with
  it, and counted by tonight's plan it still owes frames
  (``continuation.reopen_report``): Run answers 409 ``reopen``, with the
  counts, and starts nothing. ``accept_reopen`` answers it and the SAME
  session continues (``session.continued`` and ``session.reopened`` true,
  its frames intact, status active).
* Run NEVER reopens silently. START OVER (``fresh``) is the other answer.
* A complete session that owes nothing, or that shares no step id (a
  re-framed single TARGET keys its steps on its geometry), starts fresh
  exactly as before, whatever ``accept_reopen`` says.
* The question is asked FIRST: before ``recount`` and ``dropped_steps``,
  because it is the larger decision, and those two still follow on the next
  press when they apply.
* The locked section re-reads the session and asks again, so a session that
  is no longer complete, or a flow that no longer owes more, answers
  ``session_changed`` and writes nothing.

THE HARNESS is ``test_flows_continue.py``'s ``rig``: the real app over ASGI on
the test's loop and a real ``SequenceEngine`` whose imaging loop alone is
replaced, so the start, the ledger write and the night's end are the engine's
own. Night one is run through the route, every owed sub is banked, and the
engine's own finalize stamps the session complete.

MUTATIONS. Each was applied to a byte backup of the one file it changes inside
the WP-120 worktree, the named tests were run (this file alone, ``-n 0``), the
file was restored byte-identically (SHA-256 compared) and the mutant text was
grepped out of the tree. Failures are quoted as observed, wrapped to fit, ids
elided as "...".

(a) "reopen_report drops the after > 0 test" (``if not rep.kept or after <=
    0:`` -> ``if not rep.kept:`` in ``continuation.py``). RED, 5 tests:
    ``test_a_complete_session_that_owes_nothing_starts_fresh``,
    ``test_accept_reopen_does_not_reopen_what_owes_nothing``,
    ``test_the_reopened_campaign_completes_and_then_an_unedited_flow_starts_fresh``,
    ``test_the_locked_section_asks_again_and_a_flow_that_owes_nothing_is_refused``
    and ``TestReopenReport::test_none_when_the_flow_owes_nothing``. The
    unedited flow's finished session is asked about:

        AssertionError: {"detail":{"code":"reopen","detail":"This flow's
        session is complete: 5 subs recorded. The flow now asks for 5, so 0
        more are owed. CONTINUE reopens that session and counts the 5 toward
        the 5. START OVER begins a new session that counts from 0.",
        "session_id":"...","recorded":5,"accepted":5,"quota":5,"owed":0}}
        assert 409 == 200

        AssertionError: assert ReopenReport(session_id='...', recorded=5,
        accepted=5, quota=5, owed=0) is None

(b) "reopen_report computes owed from the OLD plan" (``session.model_copy(
    update={"plan": plan}).owed()`` -> ``session.owed()``). RED, 7 tests: the
    three route tests that reopen, and ``TestReopenReport`` /
    ``TestReopenDetail``'s four. The old plan owes nothing, so the press
    starts fresh:

        AssertionError: a complete session whose flow owes more was not asked
        about: {"started":true,"flow_id":"...","frames":7,"unmapped":[],
        "session":{"id":"...","night":1,"continued":false,"kept":0,"new":2,
        "dropped":0}}
        assert 200 == 409

        assert None is not None

(c) "the accept_reopen check removed" (``if not body.accept_reopen:`` ->
    ``if False:`` in ``_continue_flow_session``). RED, 3 tests:
    ``test_raising_the_counts_asks_then_reopens_the_same_session``,
    ``test_the_reopened_campaign_completes_and_then_an_unedited_flow_starts_fresh``
    and ``test_the_reopen_question_comes_before_recount_and_dropped_steps``.
    The first press reopens unasked:

        AssertionError: a complete session whose flow owes more was not asked
        about: {"started":true,"flow_id":"...","frames":7,"unmapped":[],
        "session":{"id":"...","night":1,"continued":true,"kept":2,"new":0,
        "dropped":0,"reopened":true}}

        AssertionError: ['recount', 'recount', 'dropped_steps']

(d) "the locked section does not ask again" (``if reopened is None:`` ->
    ``if False:`` in ``_continue_flow_session``). RED, 1 test,
    ``test_the_locked_section_asks_again_and_a_flow_that_owes_nothing_is_refused``:
    the stale first read reopens a session whose flow owes nothing and starts
    a run:

        Failed: DID NOT RAISE <class 'fastapi.exceptions.HTTPException'>

(e) "the reopen question asked after recount" (the ``reopen`` block moved
    below the ``recount`` block of ``_continue_flow_session``). RED, 1 test,
    ``test_the_reopen_question_comes_before_recount_and_dropped_steps``:

        AssertionError: ['recount', 'recount', 'dropped_steps']

(f) "reopen_report drops the shared-step test" (``if not rep.kept or after
    <= 0:`` -> ``if after <= 0:``). RED, 2 tests:
    ``test_a_reframed_target_shares_no_step_and_starts_fresh`` (the
    re-framed flow is asked, and ``accept_reopen`` carries it on to the
    dropped-steps refusal) and ``TestReopenReport::test_none_when_no_step_id_is_shared``:

        AssertionError: {"detail":{"code":"dropped_steps","detail":"5 subs
        belong to steps this flow no longer has; they stay on disk",
        "dropped_frames":5,"session_id":"..."}}

        AssertionError: assert ReopenReport(session_id='...', recorded=5,
        accepted=5, quota=5, owed=5) is None

(g) "the sentence counts the recorded subs, not what counts" (``counts the
    {report.counted}`` -> ``counts the {report.recorded}`` in
    ``reopen_detail``). RED, 2 tests, both in ``TestReopenDetail``:

        - ... CONTINUE reopens that session and counts the 3 toward the 7.
        + ... CONTINUE reopens that session and counts the 5 toward the 7.

(h) "the locked section reopens without the route's say-so" (``(reopen and
    s.status == "complete")`` -> ``s.status == "complete"``). RED, 1 test,
    ``test_the_locked_section_asks_again_and_a_flow_that_owes_nothing_is_refused``:
    a complete session is reopened by a call whose first read did not ask
    to:

        Failed: DID NOT RAISE <class 'fastapi.exceptions.HTTPException'>
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

import astrodeck.api.app as app_module
import astrodeck.events as events_module
from astrodeck.flows.continuation import reopen_detail, reopen_report
from astrodeck.sequence.models import ExposureStep, SequencePlan, Target
from astrodeck.sequence.session import session_store
from test_flows_continue import (LR, _bytes, _capture, _compiled, _graph,
                                 _pure_session, _reframed, _step,
                                 rig)  # noqa: F401 (fixture)

#: LR's L capture raised from 3 to 5: seven subs asked, five banked, two owed.
RAISED = _graph(_capture("c1", "L", count=5), _capture("c2", "R", count=2))
#: LR with its R capture removed and L raised to 5: R's two frames sit on a
#: step the flow no longer has, and L owes two.
L5_ONLY = _graph(_capture("c1", "L", count=5))


@pytest.fixture
def lines(monkeypatch):
    """Every ``bus.log`` line, as ``(level, message)``, passed through to the
    real bus (test_flows_recount_equal_totals.py's capture)."""
    got: list[tuple[str, str]] = []
    real = events_module.bus.log

    def log(level, message, *a, **kw):
        got.append((level, message))
        return real(level, message, *a, **kw)

    monkeypatch.setattr(events_module.bus, "log", log)
    return got


async def _finished(rig, graph=LR, steps=(0, 0, 0, 1, 1), **kw):
    """The flow saved, night one run through the route with every owed sub
    banked and the engine's own finalize ending it: ``(flow id, session)``,
    the session complete."""
    fid = await rig.save_flow(graph)
    old = await rig.night_one(fid, list(steps), reason="complete", **kw)
    assert old.status == "complete", (
        f"premise: banking every owed sub left the session {old.status}")
    return fid, old


def _sessions_of(fid: str) -> list:
    return [s for s in session_store.load_all() if s.origin_id == fid]


# =============================================================== the route

async def test_raising_the_counts_asks_then_reopens_the_same_session(rig, lines):
    """The defect and its fix, end to end: a complete session, the flow saved
    with a capture raised, Run. The first press ASKS (409 ``reopen``, the
    counts, nothing started, nothing written); ``accept_reopen`` continues
    THE SAME session: its id, its frames, status active, owing exactly the
    raise, and one session on disk for the flow.

    Before the fix this was RED, which is the defect (observed):

        AssertionError: a complete session whose flow owes more was not asked
        about: {"started":true,"flow_id":"...","frames":7,...,"session":
        {"id":"...","night":1,"continued":false,...}}
        assert 200 == 409

    RED under mutant (b) "reopen_report computes owed from the OLD plan"
    (the old plan owes nothing, so the press starts fresh) and under mutant
    (c) "the accept_reopen check removed" (the first press reopens unasked);
    both quoted in the module docstring.
    """
    fid, old = await _finished(rig)
    frame_ids = [f.id for f in old.frames]
    await rig.put_flow(fid, RAISED)
    before = _bytes(old.id)
    starts = len(rig.starts)

    r = await rig.run(fid)
    assert r.status_code == 409, (
        f"a complete session whose flow owes more was not asked about: "
        f"{r.text}")
    d = r.json()["detail"]
    assert d["code"] == "reopen", d
    assert (d["session_id"], d["recorded"], d["accepted"], d["owed"]) == (
        old.id, 5, 5, 2), d
    assert d["detail"] == (
        "This flow's session is complete: 5 subs recorded. The flow now asks "
        "for 7, so 2 more are owed. CONTINUE reopens that session and counts "
        "the 5 toward the 7. START OVER begins a new session that counts "
        "from 0."), d["detail"]
    assert len(rig.starts) == starts, "the refused press reached engine.start"
    assert _bytes(old.id) == before, "the refused press wrote the session"
    assert session_store.load(old.id).status == "complete"

    r = await rig.run(fid, accept_reopen=True)
    assert r.status_code == 200, r.text
    out = r.json()["session"]
    assert out["continued"] is True and out["reopened"] is True, out
    assert out["id"] == old.id, (
        f"a new session was started instead of reopening the finished one: "
        f"{out}")
    assert (out["kept"], out["new"], out["dropped"]) == (2, 0, 0), out
    start = rig.starts[-1]
    assert (start.won, start.session_id) == (True, old.id), start
    assert start.frame_ids == frame_ids, "the reopened ledger lost frames"
    live = session_store.load(old.id)
    assert live.status == "active"
    assert [f.id for f in live.frames] == frame_ids
    assert live.owed() == 2, "the reopened session does not owe the raise"
    assert [s.id for s in _sessions_of(fid)] == [old.id], (
        "the flow holds a second session")
    assert any(lvl == "info" and "was complete; reopened because the flow "
               "now asks for 2 more subs" in msg for lvl, msg in lines), lines


async def test_the_reopened_campaign_completes_and_then_an_unedited_flow_starts_fresh(rig):
    """After the reopened session banks the raise and the engine stamps it
    complete again, it owes nothing against the unedited flow, so the next
    press is a fresh start and no question: the rule asks only about a flow
    that owes more."""
    fid, old = await _finished(rig)
    await rig.put_flow(fid, RAISED)
    r = await rig.run(fid)
    assert r.status_code == 409, "premise: the first press asks"
    r = await rig.run(fid, accept_reopen=True)
    assert r.status_code == 200, r.text
    rig.bank([0, 0])
    await rig.end_night("complete")
    done = session_store.load(old.id)
    assert (done.status, len(done.frames), done.owed()) == ("complete", 7, 0)

    r = await rig.run(fid)
    assert r.status_code == 200, r.text
    assert r.json()["session"]["continued"] is False
    assert r.json()["session"]["id"] != old.id


async def test_a_complete_session_that_owes_nothing_starts_fresh(rig):
    """An unedited flow against its finished session: nothing is owed, so
    there is nothing to extend. A fresh session, no question, exactly as
    before (``test_flow_session_selection``'s ``complete`` row).

    RED under mutant (a) "reopen_report drops the after > 0 test": the
    finished session is asked about although the flow owes it nothing
    (quoted in the module docstring).
    """
    fid, old = await _finished(rig)
    r = await rig.run(fid)
    assert r.status_code == 200, r.text
    out = r.json()["session"]
    assert out["continued"] is False and "reopened" not in out, out
    assert out["id"] not in (None, old.id)


async def test_accept_reopen_does_not_reopen_what_owes_nothing(rig):
    """The flag answers a question; it is not a command. With the flag set
    in advance and a flow that owes nothing the press still starts fresh.

    RED under mutant (a): the unedited flow's session is asked about
    whatever the flag says in advance.
    """
    fid, old = await _finished(rig)
    r = await rig.run(fid, accept_reopen=True)
    assert r.status_code == 200, r.text
    out = r.json()["session"]
    assert out["continued"] is False and out["id"] != old.id, out


async def test_a_reframed_target_shares_no_step_and_starts_fresh(rig):
    """A single TARGET is keyed on the geometry it is at, so moving it re-keys
    every step: the compile shares no step id with the finished session and
    its frames would count toward nothing. That is a different campaign, not
    an extension, so Run starts fresh whatever the counts say, with no
    question (the dropped-steps row of spec 5.9 is the one that speaks of a
    re-frame).

    RED under mutant (f) "reopen_report drops the shared-step test": the
    re-framed flow is asked to reopen a session none of whose frames count
    (quoted in the module docstring).
    """
    fid, old = await _finished(rig)
    await rig.put_flow(fid, _reframed(RAISED, ra="06h 00m 00s"))
    r = await rig.run(fid, accept_reopen=True)
    assert r.status_code == 200, r.text
    out = r.json()["session"]
    assert out["continued"] is False and out["id"] != old.id, out


async def test_start_over_answers_the_question_with_a_fresh_session(rig):
    """START OVER is the other answer to the question: ``fresh`` skips the
    reopen entirely, begins a new session, and the finished one is left as
    it was."""
    fid, old = await _finished(rig)
    await rig.put_flow(fid, RAISED)
    r = await rig.run(fid, fresh=True)
    assert r.status_code == 200, r.text
    out = r.json()["session"]
    assert out["continued"] is False and out["id"] != old.id, out
    kept = session_store.load(old.id)
    assert (kept.status, len(kept.frames)) == ("complete", 5)


async def test_the_reopen_question_comes_before_recount_and_dropped_steps(rig):
    """Three questions can apply to one press, and the reopen question is the
    first: it decides whether the finished session is extended at all, and
    the other two are about what that does to its ledger. Each is lifted only
    by its own flag, so the press is asked three times and every answer is
    kept.

    The finished session is written back counting every sub taken (what a
    session started before S3 holds) after night one banked a rejected sub, so
    tonight's accepted-subs compile changes the total (6 recorded, 5
    accepted: the recount question is real), and the flow drops its R step
    (the dropped-steps question is real too).

    RED under mutant (e) "the reopen question asked after recount": the
    first press is asked ``recount``, and so is the second, which is when
    the order shows (quoted in the module docstring).
    """
    fid = await rig.save_flow(LR)
    old = await rig.night_one(fid, [0, 0, 0, 0, 1, 1], rejected=(0,),
                              reason="complete")
    assert old.status == "complete", "premise: five accepted subs complete it"
    old.plan.count_mode = "attempts"
    session_store.save(old)
    await rig.put_flow(fid, L5_ONLY)

    codes = []
    flags: dict = {}
    for answer in ("accept_reopen", "accept_recount", "accept_dropped", None):
        r = await rig.run(fid, **flags)
        if answer is None:
            assert r.status_code == 200, r.text
            break
        assert r.status_code == 409, r.text
        codes.append(r.json()["detail"]["code"])
        flags[answer] = True
    assert codes == ["reopen", "recount", "dropped_steps"], codes
    out = r.json()["session"]
    assert (out["continued"], out["reopened"], out["id"]) == (
        True, True, old.id), out
    assert len(session_store.load(old.id).frames) == 6, (
        "a frame was lost reopening")


async def test_the_locked_section_asks_again_and_a_flow_that_owes_nothing_is_refused(rig):
    """``_continue_flow_session`` re-reads the session inside the lock and
    decides on THAT read, as it does for a dormant one. Called as the route
    calls it, from a first read that was stale:

    * a complete session and a plan that owes it nothing now: 409
      ``session_changed`` ("the flow no longer owes more"), nothing started.
    * a complete session when the route's first read did not ask to reopen
      (``reopen`` false): ``session_changed`` too; only the route's own
      first read opens that door.
    * any other status, ``reopen`` true or not: ``session_changed``.

    RED under mutant (d) "the locked section does not ask again": the first
    call reopens a session the flow owes nothing and starts a run. RED under
    mutant (h) "the locked section reopens without the route's say-so": the
    second call starts a run. Both are ``DID NOT RAISE`` (module docstring).
    """
    fid, old = await _finished(rig)
    unchanged = _compiled(LR, fid)
    raised = _compiled(RAISED, fid)
    starts = len(rig.starts)
    yes = app_module.FlowRunBody(accept_reopen=True)

    def refused(plan, *, reopen, body=yes):
        with pytest.raises(HTTPException) as e:
            app_module._continue_flow_session(old, plan, body, reopen=reopen)
        assert e.value.status_code == 409, e.value.detail
        return e.value.detail

    d = refused(unchanged, reopen=True)
    assert d["code"] == "session_changed" and d["session_id"] == old.id, d
    assert "no longer owes more" in d["detail"], d
    d = refused(raised, reopen=False)
    assert d["code"] == "session_changed" and d["status"] == "complete", d
    assert len(rig.starts) == starts, "a refused call reached engine.start"

    gone = session_store.load(old.id)
    for status in ("active", "abandoned"):
        gone.status = status
        session_store.save(gone)
        d = refused(raised, reopen=True)
        assert d["code"] == "session_changed" and d["status"] == status, d
    assert len(rig.starts) == starts


# ============================================================= the pure half

def _complete(steps, frames_on, **kw):
    s = _pure_session(steps, frames_on, **kw)
    s.status = "complete"
    return s


def _plan_of(session, *steps):
    return SequencePlan(
        name="p", count_mode=session.plan.count_mode,
        targets=[Target(name="M42", ra_hours=5.5, dec_deg=-5.0,
                        center=False, autofocus_first=False,
                        steps=list(steps))])


class TestReopenReport:
    def test_a_raised_count_reports_what_is_owed(self):
        """Counted by TONIGHT's plan: the finished session's own plan owes
        nothing, the raised one owes the difference.

        RED under mutant (b) "reopen_report computes owed from the OLD plan":
        ``assert None is not None``.
        """
        l, r = _step("L"), _step("R")
        s = _complete([l, r], {0: 5, 1: 5})
        assert s.owed() == 0, "premise: the session is finished"
        more = l.model_copy(update={"count": 8})
        rep = reopen_report(s, _plan_of(s, more, r))
        assert rep is not None
        assert (rep.session_id, rep.recorded, rep.accepted, rep.quota,
                rep.owed) == (s.id, 10, 10, 13, 3)

    def test_accepted_and_recorded_differ_when_a_sub_was_rejected(self):
        l = _step("L")
        s = _complete([l], {0: 6}, count_mode="accepted", rejected=1)
        rep = reopen_report(s, _plan_of(s, l.model_copy(update={"count": 8})))
        assert rep is not None
        assert (rep.recorded, rep.accepted, rep.owed) == (6, 5, 3)

    def test_none_when_the_flow_owes_nothing(self):
        """RED under mutant (a): a report for a flow that owes nothing
        (``ReopenReport(... owed=0) is None`` fails).
        """
        l = _step("L")
        s = _complete([l], {0: 5})
        assert reopen_report(s, _plan_of(s, l.model_copy())) is None
        assert reopen_report(
            s, _plan_of(s, l.model_copy(update={"count": 3}))) is None

    def test_none_when_no_step_id_is_shared(self):
        """RED under mutant (f): a report for a re-framed flow
        (``ReopenReport(... owed=5) is None`` fails).
        """
        l = _step("L")
        s = _complete([l], {0: 5})
        assert reopen_report(s, _plan_of(s, _step("L"))) is None

    @pytest.mark.parametrize("status", ["dormant", "active", "abandoned"])
    def test_none_unless_the_session_is_complete(self, status):
        l = _step("L")
        s = _complete([l], {0: 5})
        s.status = status
        more = l.model_copy(update={"count": 8})
        assert reopen_report(s, _plan_of(s, more)) is None


class TestReopenDetail:
    def test_it_counts_what_counts_toward_the_new_quota(self):
        """A step whose count was LOWERED holds subs that fill nothing, so
        'the N toward the M' is quota minus owed, never the recorded total.
        L holds 5 but now asks 3; R is new and asks 4: seven asked, four
        owed, three of the five count.

        RED under mutant (g) "the sentence counts the recorded subs": it
        says "counts the 5 toward the 7" where three of them do.
        """
        l = _step("L")
        s = _complete([l], {0: 5})
        fewer = l.model_copy(update={"count": 3})
        rep = reopen_report(s, _plan_of(s, fewer, ExposureStep(
            filter="R", exposure_s=60.0, count=4)))
        assert rep is not None
        assert reopen_detail(rep) == (
            "This flow's session is complete: 5 subs recorded. The flow now "
            "asks for 7, so 4 more are owed. CONTINUE reopens that session "
            "and counts the 3 toward the 7. START OVER begins a new session "
            "that counts from 0.")

    def test_rejected_subs_are_named_and_one_is_singular(self):
        l = _step("L")
        s = _complete([l], {0: 6}, count_mode="accepted", rejected=1)
        rep = reopen_report(s, _plan_of(s, l.model_copy(update={"count": 6})))
        assert rep is not None
        assert reopen_detail(rep) == (
            "This flow's session is complete: 6 subs recorded (5 accepted). "
            "The flow now asks for 6, so 1 more is owed. CONTINUE reopens "
            "that session and counts the 5 toward the 6. START OVER begins "
            "a new session that counts from 0.")
