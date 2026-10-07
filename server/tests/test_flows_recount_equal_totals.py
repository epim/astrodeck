# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""CONTINUE asks the recount question only when a total changes (S4
orchestrator ruling 2, #348; spec 5.9's row "the compile's count_mode
differs", Revision 2 ruling 2's consequences).

Since S3 every save writes "Accepted subs" into every TARGET and POOL, so the
first CONTINUE of a flow whose dormant session predates S3 meets a mode
change: the session counted every sub taken, tonight's compile counts
accepted subs. Until the ruling ``_continue_flow_session`` answered the
``recount`` 409 whenever the modes differed, and with no rejected frame banked
it read "this session counted every sub taken (5); counting accepted subs
makes it 5": a warning-shaped question about nothing, since nothing banked is
recounted differently and only future frames are.

The ruling, as built:

* ``before == after``: the route asks nothing. The continued session runs in
  tonight's count mode, and ONE info line, after the start, names both modes
  and says nothing banked recounts differently.
* ``before != after``: the ``recount`` 409 is unchanged, with both totals,
  and ``accept_recount`` lifts it.
* the modes agree: neither the question nor the line (the control).

THE HARNESS is ``test_flows_continue.py``'s ``rig``: the real app over ASGI on
the test's loop and a real ``SequenceEngine`` whose imaging loop alone is
replaced, so the start, the ledger write and the night's end are the
engine's own. Night one runs through the route, counting accepted subs (the
saved flow does), and its session is then written back counting every sub
taken, which is what a session started before S3 holds.

Each mutation was applied to a byte-for-byte copy of ``api/app.py`` in a
private copy of ``server/`` (scratchpad ``s4-routes-mut``), never in the
shared tree, and only this file was run against it. Failures are quoted as
observed (``--tb=short``), wrapped to fit, ids elided as "...".
"""
from __future__ import annotations

import pytest

import astrodeck.events as events_module
from astrodeck.flows.continuation import recount_detail
from astrodeck.sequence.session import session_store
from test_flows_continue import (_bytes, _capture, _graph,
                                 rig)  # noqa: F401 (fixture)

#: L (4) then R (3): seven subs, so five banked leave the session owing and
#: dormant, which is what CONTINUE continues.
L4R3 = _graph(_capture("c1", "L", count=4), _capture("c2", "R", count=3))

#: The one line a quiet recount logs, less the flow's name and the count.
QUIET = ("continues counting accepted subs where its session counted every "
         "sub taken: the captured frame count is unchanged")


@pytest.fixture
def lines(monkeypatch):
    """Every ``bus.log`` line, as ``(level, message)``, passed through to the
    real bus. Wrapped rather than replaced, and with ``**kw``, because the
    engine's start logs through the same bus with ``site_derived``."""
    got: list[tuple[str, str]] = []
    real = events_module.bus.log

    def log(level, message, *a, **kw):
        got.append((level, message))
        return real(level, message, *a, **kw)

    monkeypatch.setattr(events_module.bus, "log", log)
    return got


async def _pre_s3_session(rig, steps, *, rejected=()):
    """Night one through the route on the saved flow (which counts accepted
    subs), banking ``steps``, then written back counting every sub taken,
    as a session started before S3 does. Returns the flow id and session."""
    fid = await rig.save_flow(L4R3)
    one = await rig.night_one(fid, steps, rejected=rejected)
    assert one.status == "dormant", (
        f"premise: night one left the session owing, got {one.status}")
    assert one.plan.count_mode == "accepted", (
        "premise: the saved flow's first night counts accepted subs")
    one.plan.count_mode = "attempts"
    session_store.save(one)
    return fid, one


def _quiet(lines) -> list[tuple[str, str]]:
    return [(lvl, msg) for lvl, msg in lines if QUIET in msg]


class TestEqualTotalsAskNothing:
    async def test_five_banked_all_accepted_continues_unasked(self, rig,
                                                              lines):
        """5/5: five subs, every one accepted. Counted either way they are
        five, so CONTINUE asks nothing, the continued session runs in
        tonight's mode (accepted), and one info line says why.

        RED under mutation "ask whenever the modes differ" (the ruling's
        condition reverted to the code before it, ``if s.plan.count_mode !=
        plan.count_mode and not body.accept_recount:`` raising the 409
        unconditionally), observed:

            AssertionError: {"detail":{"code":"recount","detail":"this
            session counted every sub taken (5); counting accepted subs makes
            it 5","before":5,"after":5,"session_id":"..."}}
            assert 409 == 200

        RED under mutation "no quiet line" (the info line after the start
        made unreachable, ``if quiet_recount is not None:`` -> ``if
        False:``), observed:

            AssertionError: the quiet recount said nothing: []
            assert [] == [('info', "'c... was asked.")]
              Right contains one more item: ('info', "'continue me'
              continues counting accepted subs where its session counted
              every sub taken: nothing banked recounts differently (5 subs
              either way), so nothing was asked.")
        """
        fid, one = await _pre_s3_session(rig, [0, 0, 0, 1, 1])
        assert (len(one.frames), sum(f.effective() for f in one.frames)) \
            == (5, 5), "premise: five banked, five accepted"

        r = await rig.run(fid)

        assert r.status_code == 200, r.text
        out = r.json()["session"]
        assert out["continued"] is True and out["id"] == one.id, out
        start = rig.starts[-1]
        assert start.won and start.session_id == one.id
        assert start.count_mode == "accepted", (
            "the continued session kept the old mode")
        assert session_store.load(one.id).plan.count_mode == "accepted"
        assert _quiet(lines) == [(
            "info",
            f"'continue me' {QUIET} (5 subs either way), so nothing was "
            f"asked.")], f"the quiet recount said nothing: {_quiet(lines)}"

    async def test_nothing_banked_continues_unasked(self, rig, lines):
        """0/0: a session with no frames at all. Zero either way, so the same
        as 5/5, and the line says "0 subs".

        RED under mutation "ask whenever the modes differ", observed:

            AssertionError: {"detail":{"code":"recount","detail":"this
            session counted every sub taken (0); counting accepted subs makes
            it 0","before":0,"after":0,"session_id":"..."}}
            assert 409 == 200
        """
        fid, one = await _pre_s3_session(rig, [])
        assert one.frames == [], "premise: nothing banked"

        r = await rig.run(fid)

        assert r.status_code == 200, r.text
        assert r.json()["session"]["continued"] is True, r.json()
        assert rig.starts[-1].session_id == one.id
        assert rig.starts[-1].count_mode == "accepted"
        assert _quiet(lines) == [(
            "info",
            f"'continue me' {QUIET} (0 subs either way), so nothing was "
            f"asked.")], _quiet(lines)

    async def test_one_sub_reads_in_the_singular(self, rig, lines):
        """1/1: the line counts "1 sub", not "1 subs". The line is what the
        operator reads, and it is held word for word.

        RED under mutation "ask whenever the modes differ", observed:

            AssertionError: {"detail":{"code":"recount","detail":"this
            session counted every sub taken (1); counting accepted subs makes
            it 1","before":1,"after":1,"session_id":"..."}}
            assert 409 == 200

        and under "no quiet line" (``Right contains one more item: ('info',
        "'continue me' continues counting ... (1 sub either way), so nothing
        was asked.")``)."""
        fid, one = await _pre_s3_session(rig, [0])

        r = await rig.run(fid)

        assert r.status_code == 200, r.text
        assert _quiet(lines) == [(
            "info",
            f"'continue me' {QUIET} (1 sub either way), so nothing was "
            f"asked.")], _quiet(lines)

    async def test_a_continue_refused_after_the_recount_says_nothing(
            self, rig, lines):
        """5/5 with a banked step gone from the flow: the recount asks
        nothing, but ``dropped_steps`` still refuses, and the line says
        "continues" only once the session has continued. It is logged after
        the start, so the refused request logs nothing and the accepted one
        logs it once.

        Added by the S4-ROUTES verifier: every other case here continues on
        the first request, so moving the line to where the recount decides
        (before the refusals that follow it) left the file green.

        RED under mutation "line before the start" (the same ``bus.log``
        made where ``quiet_recount`` is set, and the one after the start
        made unreachable), observed:

            AssertionError: a refused continue said it continues: [('info',
            "'continue me' continues counting accepted subs where its
            session counted every sub taken: nothing banked recounts
            differently (5 subs either way), so nothing was asked.")]
            assert [('info', "'c... was asked.")] == []
        """
        fid, one = await _pre_s3_session(rig, [0, 0, 0, 1, 1])
        await rig.put_flow(fid, _graph(_capture("c1", "L", count=4)))
        before = _bytes(one.id)

        r = await rig.run(fid)

        assert r.status_code == 409, r.text
        assert r.json()["detail"]["code"] == "dropped_steps", (
            "premise: the recount asked nothing and the dropped step refused")
        assert _bytes(one.id) == before, "a refusal wrote the session"
        assert len(rig.starts) == 1, "a refusal reached engine.start"
        assert _quiet(lines) == [], (
            f"a refused continue said it continues: {_quiet(lines)}")

        r = await rig.run(fid, accept_dropped=True)

        assert r.status_code == 200, r.text
        assert rig.starts[-1].session_id == one.id
        assert rig.starts[-1].count_mode == "accepted"
        assert _quiet(lines) == [(
            "info",
            f"'continue me' {QUIET} (5 subs either way), so nothing was "
            f"asked.")], _quiet(lines)


class TestADifferenceStillAsks:
    async def test_five_banked_one_rejected_asks_with_both_totals(self, rig,
                                                                  lines):
        """5/4: five subs, one rejected. Every sub taken is five, accepted
        subs are four: continuing would recount a banked frame, so the 409
        is asked exactly as before the ruling, nothing is started or
        written, and no quiet line is logged. ``accept_recount`` lifts it,
        and the accepted recount logs no quiet line either: it was asked.

        RED under mutation "never ask when the modes differ" (the 409 branch
        made unreachable, ``if before != after and not body.accept_recount:``
        -> ``if False and before != after and not body.accept_recount:``),
        observed:

            AssertionError: {"started":true,"flow_id":"...","frames":7,
            "unmapped":[],"session":{"id":"...","night":2,"continued":true,
            "kept":2,"new":0,"dropped":0}}
            assert 200 == 409
        """
        fid, one = await _pre_s3_session(rig, [0, 0, 0, 1, 1],
                                         rejected=(4,))
        before = _bytes(one.id)

        r = await rig.run(fid)

        assert r.status_code == 409, r.text
        assert r.json()["detail"] == {
            "code": "recount",
            "detail": recount_detail("attempts", "accepted", 5, 4),
            "before": 5, "after": 4, "session_id": one.id}
        assert _bytes(one.id) == before, "a refusal wrote the session"
        assert len(rig.starts) == 1, "a refusal reached engine.start"
        assert _quiet(lines) == []

        r = await rig.run(fid, accept_recount=True)

        assert r.status_code == 200, r.text
        assert rig.starts[-1].session_id == one.id
        assert rig.starts[-1].count_mode == "accepted"
        assert _quiet(lines) == [], (
            "an asked and accepted recount also said it was not asked")


class TestControl:
    async def test_the_same_mode_asks_nothing_and_says_nothing(self, rig,
                                                               lines):
        """The session counts accepted subs, as tonight's compile does: no
        mode changes, so there is no question and no line, whatever was
        rejected (five taken, four accepted). This is the case the ruling
        must leave exactly as it was."""
        fid = await rig.save_flow(L4R3)
        one = await rig.night_one(fid, [0, 0, 0, 1, 1], rejected=(4,))
        assert one.plan.count_mode == "accepted", "premise: no mode change"

        r = await rig.run(fid)

        assert r.status_code == 200, r.text
        assert rig.starts[-1].session_id == one.id
        assert rig.starts[-1].count_mode == "accepted"
        assert _quiet(lines) == []
