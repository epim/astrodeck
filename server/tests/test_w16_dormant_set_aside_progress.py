# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A dormant session's standing set-aside panels reach the progress answer
(#727; backlog wave 16, WP-141; #600, backlog ruling D-07, owner-approved
2026-09-30).

RETRY SET-ASIDE PANELS (wave 15) draws its button off the live
``state.group``, so once a run ended owing a set-aside panel nothing on screen
said so and no button could be drawn: the dormant half of the route (POST with
``session_id``, then CONTINUE the same night) worked, and was script-only.
``flow_progress`` now takes ``now``, the clock the ROUTE hands in as it does
for ``continue_night``, and a DORMANT session's mosaic block carries
``set_aside``: the panels the route's dormant retry would clear, so a button
drawn for them never answers 409.

What is graded, and what is the control:

* the panel is listed, with its cell, name and ``for_now``;
* with no ``now`` the answer is the answer before #727 (every existing caller
  and allow-list test), and nothing but a DORMANT session lists anything;
* only TONIGHT's STANDING WHOLE-PANEL records count: another night's, an
  expired one, a cleared one and a step-level one list nothing, and a panel
  that owes nothing is left out, as the route leaves it alone;
* ``for_now`` is the engine's ``_may_expire``: a timed centring record with no
  expiry yet tonight;
* nothing the record says in free text, and no key outside the entry's
  allow-list, reaches a viewer (the route is ``CAP_VIEW_STATUS``, #19).

THE CLOCK IS PINNED TWICE (#682): the answer's ``now`` is a constant, and the
tonight the records are stamped with is ``night_key`` of that same constant,
so no hour of the day the suite runs at moves a record across the noon
rollover.

Each case was run against the mutants named in it from a byte backup of
``astrodeck/flows/progress.py`` inside this worktree, restored and
sha256-compared after each, the mutant's marker grepped absent (#254). The
observed failure is recorded verbatim.
"""
from __future__ import annotations

import json

import pytest

from astrodeck.events import night_key
from astrodeck.flows.compile import compile_plan
from astrodeck.flows.models import FlowEdge, FlowGraph, FlowNode
from astrodeck.flows.progress import flow_progress
from astrodeck.flows.to_plan import to_sequence_plan
from astrodeck.sequence.session import Session, SessionFrame

FLOW = "flow-w16-dormant-set-aside"
FOV = (2.0, 1.33)
#: An instant mid-afternoon of a made-up night, then the night key it falls
#: in and the one before: whatever zone the suite runs in, the key is the
#: function's own and the day before it is a different night.
NOW = 1_790_000_000.0
TONIGHT = night_key(NOW)
LAST_NIGHT = night_key(NOW - 24 * 3600.0)
#: A made-up reason, free text the answer must never carry.
REASON = "RSN-7731 mount reported a fault near the third marker"

assert TONIGHT != LAST_NIGHT


def _n(nid, ntype, x=0.0, y=0.0, **params):
    return FlowNode(id=nid, type=ntype, x=x, y=y, params=params)


def _e(a, ap, b, bp):
    return FlowEdge(**{"from": a, "fromPort": ap, "to": b, "toPort": bp})


def _graph() -> FlowGraph:
    """A single TARGET, then a 2x2 mosaic with one capture each, so the
    answer has a block that must NOT list a set-aside (a single target is
    in no mosaic group) beside the one that may."""
    return FlowGraph(
        nodes=[_n("s", "target", name="M31", ra="00h 42m 44s",
                  dec="+41 16 09", rotation=-1),
               _n("sc", "capture", x=50, filter="L", exposure=60, gain=100,
                  bin="1", count=5, goal=0),
               _n("m", "target", x=100, name="M16", ra="18h 18m 48s",
                  dec="-13 49 00", rotation=30, angle="Rotate to PA",
                  rows=2, cols=2, overlap=25, fovX=FOV[0], fovY=FOV[1],
                  counts="Accepted subs"),
               _n("mc", "capture", x=150, filter="Ha", exposure=300,
                  gain=100, bin="1", count=4, goal=0)],
        edges=[_e("s", "target", "sc", "run"),
               _e("sc", "complete", "m", "arm"),
               _e("m", "target", "mc", "run")])


def _world(*, status="dormant", frames=()):
    compiled = compile_plan(_graph(), "n")
    plan, _ = to_sequence_plan(compiled, _graph(), flow_id=FLOW)
    plan = plan.model_copy(update={"count_mode": "accepted"})
    session = Session(id="session-w16", status=status, nights=[TONIGHT],
                      plan=plan, frames=list(frames), origin="flow",
                      origin_id=FLOW)
    return compiled, plan, session


def _panel(plan, row, col):
    return next(t for t in plan.targets if (t.panel_row, t.panel_col)
                == (row, col) and t.mosaic_group)


def _fill(target, n):
    return [SessionFrame(target_id=target.id, step_id=target.steps[0].id,
                         auto_accepted=True) for _ in range(n)]


def _answer(compiled, plan, session, *, now=NOW):
    return flow_progress(compiled, plan, session, flow_id=FLOW, now=now)


def _mosaic_block(answer):
    return next(b for b in answer["blocks"] if "grid" in b)


def _aside(answer):
    return _mosaic_block(answer).get("set_aside")


def test_a_dormant_session_lists_the_panels_it_holds_set_aside_tonight():
    """1-2 was struck out for rejects tonight. The mosaic block lists it:
    its id, its name as the plan names it, its 0-based cell, and
    ``for_now`` false (a rejects set-aside never expires). The single
    TARGET's block lists nothing, and nothing else is added to any block.

    RED under mutant "set_aside never listed" (the ``_set_aside_tonight``
    call removed from ``flow_progress``, the answer before #727), observed:

        AssertionError: assert None == [{'col': 1, 'for_now': False, 'name':
        'M16 1-2', 'row': 0, ...}]

    (six cases of this file go red under it: the order case, the other-night
    case's second half, the complete-panel case, the for_now case and the
    allow-list case also read the key.)
    """
    compiled, plan, session = _world()
    p = _panel(plan, 0, 1)
    session.note_set_aside(p.id, REASON, night=TONIGHT, kind="rejects",
                           ts=NOW - 600.0)
    answer = _answer(compiled, plan, session)
    assert _aside(answer) == [{"target_id": p.id, "name": p.name,
                               "row": 0, "col": 1, "for_now": False}]
    singles = [b for b in answer["blocks"] if "grid" not in b]
    assert singles and all("set_aside" not in b for b in singles)


def test_the_panels_are_listed_in_the_plans_order():
    """Two panels set aside: the entries follow the block's panels, the
    plan's own order, never the order the records were written in."""
    compiled, plan, session = _world()
    first, second = _panel(plan, 0, 0), _panel(plan, 1, 1)
    for p in (second, first):
        session.note_set_aside(p.id, REASON, night=TONIGHT, kind="rejects")
    block = _mosaic_block(_answer(compiled, plan, session))
    order = [p["target_id"] for p in block["panels"]]
    assert [e["target_id"] for e in block["set_aside"]] == [
        t for t in order if t in {first.id, second.id}]
    assert len(block["set_aside"]) == 2


def test_with_no_clock_the_answer_is_the_answer_before_the_change():
    """CONTROL: ``flow_progress`` reads no clock of its own, so asked with no
    ``now`` it lists nothing, however many records the session holds. This is
    what keeps every existing caller and allow-list test byte-identical until
    the route hands the clock in."""
    compiled, plan, session = _world()
    session.note_set_aside(_panel(plan, 0, 1).id, REASON, night=TONIGHT,
                           kind="rejects")
    answer = flow_progress(compiled, plan, session, flow_id=FLOW)
    assert all("set_aside" not in b for b in answer["blocks"])


def test_a_target_that_is_no_mosaic_panel_is_not_listed():
    """CONTROL: the retry takes the panels of a plan MOSAIC GROUP
    (``t.mosaic_group in groups``) and nothing else, so a whole-target record
    held for a single TARGET (a floor set-aside, say) is not one it clears,
    and a button drawn for it would answer 409. The single block has a panels
    list of its own, so only the mosaic test keeps it out: the target is
    listed by neither block.

    RED under mutant "every block lists" (the ``"grid" not in block`` skip of
    ``_set_aside_tonight`` dropped), observed:

        AssertionError: a single target is not a panel the retry clears
    """
    compiled, plan, session = _world()
    single = next(t for t in plan.targets if not t.mosaic_group)
    session.note_set_aside(single.id, REASON, night=TONIGHT, kind="floor")
    answer = _answer(compiled, plan, session)
    assert any(single.id == p["target_id"]
               for b in answer["blocks"] for p in b["panels"]
               if "grid" not in b), "premise: the single block names it"
    assert all("set_aside" not in b for b in answer["blocks"]), (
        "a single target is not a panel the retry clears")


def test_a_flow_with_no_session_lists_nothing_and_does_not_raise():
    """CONTROL: the commonest progress answer is a flow that has never run, a
    ``session`` of None, asked WITH the clock once the route hands it in. The
    status test reads ``session.status``, so the None guard is what keeps that
    answer from raising ``AttributeError`` on every card of a fresh flow.

    RED under mutant "no None guard" (``session is None or`` dropped from the
    first test of ``_set_aside_tonight``), observed:

        AttributeError: 'NoneType' object has no attribute 'status'
    """
    compiled, plan, _session = _world()
    answer = flow_progress(compiled, plan, None, flow_id=FLOW, now=NOW)
    assert answer["session"] is None
    assert all("set_aside" not in b for b in answer["blocks"])


@pytest.mark.parametrize("status", ["active", "complete"])
def test_only_a_dormant_session_lists_anything(status):
    """CONTROL: the route's retry runs on a DORMANT session only (409
    otherwise), and a live run's panels are the live ``state.group``'s to
    word. An active or complete session lists nothing, records or not.

    RED under mutant "any status lists" (the dormant test dropped), observed
    for both rows:

        AssertionError: assert [{'col': 1, 'for_now': False, 'name':
        'M16 1-2', 'row': 0, ...}] is None
    """
    compiled, plan, session = _world(status=status)
    session.note_set_aside(_panel(plan, 0, 1).id, REASON, night=TONIGHT,
                           kind="rejects")
    assert _aside(_answer(compiled, plan, session)) is None


def test_a_record_of_another_night_lists_nothing():
    """CONTROL: every panel is retried the next night, so last night's record
    is history, and a button drawn for it would answer 409. The same
    session, the same panel, the night before.

    RED under mutant "any night" (``set_aside_on(night)`` replaced by every
    record not expired and not cleared, whatever its night), observed:

        AssertionError: assert [{'col': 1, 'for_now': False, 'name':
        'M16 1-2', 'row': 0, ...}] is None
    """
    compiled, plan, session = _world()
    session.note_set_aside(_panel(plan, 0, 1).id, REASON, night=LAST_NIGHT,
                           kind="rejects")
    assert _aside(_answer(compiled, plan, session)) is None
    # And the same record IS tonight's once the clock is on the other side of
    # the noon rollover: the key moves with the clock, not with the suite.
    assert _aside(_answer(compiled, plan, session,
                          now=NOW - 24 * 3600.0)) is not None


def test_an_expired_or_cleared_record_is_not_standing():
    """CONTROL: an expired centring record (the panel is live again) and one
    the operator cleared (#600) are not set aside any more."""
    compiled, plan, session = _world()
    a, b = _panel(plan, 0, 0), _panel(plan, 0, 1)
    session.note_set_aside(a.id, REASON, night=TONIGHT, kind="centring",
                           ts=NOW - 4000.0)
    session.note_set_aside_expired(a.id, night=TONIGHT)
    session.note_set_aside(b.id, REASON, night=TONIGHT, kind="rejects")
    session.note_set_aside_cleared([b.id], night=TONIGHT)
    assert _aside(_answer(compiled, plan, session)) is None


def test_a_step_level_record_does_not_make_a_panel_set_aside():
    """CONTROL: the reject guard set ONE step of the panel aside; its other
    steps are still shot, so a line that said the panel was set aside would
    be false (the live ``state.group`` lists whole panels only).

    RED under mutant "step records count" (the ``step_id is None`` test
    dropped), observed:

        AssertionError: assert [{'col': 1, 'for_now': False, 'name':
        'M16 1-2', 'row': 0, ...}] is None
    """
    compiled, plan, session = _world()
    p = _panel(plan, 0, 1)
    session.note_set_aside(p.id, REASON, night=TONIGHT, step_id=p.steps[0].id)
    assert _aside(_answer(compiled, plan, session)) is None


def test_a_panel_that_owes_nothing_is_left_out_as_the_route_leaves_it():
    """CONTROL: ``POST /api/sequence/retry-set-aside`` leaves a complete
    panel's record alone (the panel owes nothing), so a button drawn for it
    would answer 409. 1-1 banked its whole quota, 1-2 did not; both have a
    record, and only 1-2 is listed.

    RED under mutant "complete panels listed" (the owed test dropped),
    observed:

        AssertionError: assert ['486c02c6c5a...f9ffbe58669b'] ==
        ['a335a0d1680...f9ffbe58669b']
    """
    compiled, plan, session = _world()
    done, owing = _panel(plan, 0, 0), _panel(plan, 0, 1)
    session.frames.extend(_fill(done, done.steps[0].count))
    for p in (done, owing):
        session.note_set_aside(p.id, REASON, night=TONIGHT, kind="rejects")
    got = _aside(_answer(compiled, plan, session))
    assert [e["target_id"] for e in got] == [owing.id]


def test_a_calibration_target_is_left_alone_as_the_route_leaves_it():
    """CONTROL: ``POST /api/sequence/retry-set-aside`` takes the panels of a
    group and leaves a calibration target (darks, flats) alone, so a record
    held for one is never listed, whatever block names it. A plan built by
    the compile never puts a calibration target in a mosaic group, so the
    case flags a panel as one directly (the guard mirrors the route's own
    rule and is not otherwise reachable). The premise half is the same
    record on the same panel unflagged, which IS listed.

    RED under mutant "calibration listed" (``or target.calibration`` dropped
    from the skip test of ``_set_aside_tonight``), observed:

        AssertionError: a calibration target is not a panel to retry
    """
    compiled, plan, session = _world()
    p = _panel(plan, 0, 1)
    session.note_set_aside(p.id, REASON, night=TONIGHT, kind="rejects")
    assert [e["target_id"] for e in _aside(_answer(compiled, plan, session))
            ] == [p.id], "premise: the unflagged panel is listed"
    flagged = plan.model_copy(update={"targets": [
        t.model_copy(update={"calibration": True}) if t.id == p.id else t
        for t in plan.targets]})
    assert _aside(_answer(compiled, flagged, session)) is None, (
        "a calibration target is not a panel to retry")


def test_a_record_of_a_panel_the_plan_no_longer_has_lists_nothing():
    """CONTROL: a re-frame re-keys the ids (spec 3.3), and the record under
    the old id is nobody's in this compile: nothing to retry here."""
    compiled, plan, session = _world()
    session.note_set_aside("a-target-id-no-plan-has", REASON, night=TONIGHT,
                           kind="rejects")
    assert _aside(_answer(compiled, plan, session)) is None


def test_for_now_is_a_centring_record_that_may_still_expire_tonight():
    """A timed centring record with no expiry of that panel yet tonight is
    set aside FOR NOW (a restart tonight tries it again when its 45 minutes
    are up); every other kind, a centring one with no time, and a centring
    one that already expired once tonight and was set aside again, is for
    the night.

    Four mutants, each red in this case (the last also reddens the listing
    case, whose rejects record must read ``for_now`` false), each observed:

    "for_now never" (the flag always false):
        assert [False, False, False, False] == [True, False, False, False]
    "expiry count ignored" (``expired.get(tid, 0) == 0`` dropped):
        assert [True, False, False, True] == [True, False, False, False]
    "untimed centring counts" (the ``timed`` test dropped):
        assert [True, False, True, False] == [True, False, False, False]
    "any kind counts" (``kind == CENTRING`` dropped):
        assert [True, True, False, False] == [True, False, False, False]
    """
    compiled, plan, session = _world()
    c00, c01, c10, c11 = (_panel(plan, 0, 0), _panel(plan, 0, 1),
                          _panel(plan, 1, 0), _panel(plan, 1, 1))
    session.note_set_aside(c00.id, REASON, night=TONIGHT, kind="centring",
                           ts=NOW - 100.0)
    session.note_set_aside(c01.id, REASON, night=TONIGHT, kind="floor",
                           ts=NOW - 100.0)
    session.note_set_aside(c10.id, REASON, night=TONIGHT, kind="centring")
    # 1-2's twin: expired once, set aside again, the second one standing.
    session.note_set_aside(c11.id, REASON, night=TONIGHT, kind="centring",
                           ts=NOW - 5000.0)
    session.note_set_aside_expired(c11.id, night=TONIGHT)
    session.note_set_aside(c11.id, REASON, night=TONIGHT, kind="centring",
                           ts=NOW - 100.0)
    got = {e["target_id"]: e["for_now"]
           for e in _aside(_answer(compiled, plan, session))}
    assert [got[c.id] for c in (c00, c01, c10, c11)] == [
        True, False, False, False]


def test_the_entry_carries_words_and_flags_only():
    """The route is ``CAP_VIEW_STATUS`` and a viewer reads it: the entry's
    keys are an allow-list, and the record's free-text reason, kind, night
    and time are nowhere in the JSON answer.

    RED under mutant "reason published" (``"reason": rec.get("reason")``
    added to the entry), observed:

        AssertionError: assert {'col', 'for_..., 'target_id'} <=
        {'col', 'for_..., 'target_id'}
    """
    compiled, plan, session = _world()
    p = _panel(plan, 0, 1)
    session.note_set_aside(p.id, REASON, night=TONIGHT, kind="centring",
                           ts=NOW - 100.0)
    answer = _answer(compiled, plan, session)
    entry = _aside(answer)[0]
    assert set(entry) <= {"target_id", "name", "row", "col", "for_now"}
    assert set(entry) == {"target_id", "name", "row", "col", "for_now"}, (
        "premise: every allowed key is carried")
    blob = json.dumps(answer, allow_nan=False)
    assert "RSN-7731" not in blob and TONIGHT not in blob
    assert json.loads(blob) == answer, "JSON-ready"
