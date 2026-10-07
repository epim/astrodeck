# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""RETRY SET-ASIDE PANELS, the records and the rules under it (#600, backlog
ruling D-07, owner-approved 2026-09-30; WP-104).

A panel set aside for the night stayed out until tomorrow even once the
operator had fixed the cause (dew, a loose rotator, a solver setting): a
restart tonight reads the record back and does not retry it, and CONTINUE
reads the same record. The action the issue asks for needs three small
pieces, and this file grades each pure, with no night and no route (the
night and the route are test_w15_retry_set_aside_night.py):

* ``Session.note_set_aside_cleared``: marks every record tonight holds for the
  named targets ``cleared`` (additive on the record dict, SESSION_SCHEMA stays
  1), whole-panel and step-level alike. ``set_aside_on`` stops reading a
  cleared record, so a restart or CONTINUE tonight takes the panel up;
  ``set_aside_expiries_on`` stops counting a cleared one, so the panel's one
  expiry for the night is the operator's to restore (#534).
* ``GroupRun.retry_set_aside``: the panel is live again, of ANY kind (a
  centring set-aside expires by itself, a floor, reject, angle or group one
  never does), with a clean slate, not counted as an expiry, and D-03's held
  pass counter back at 0: the escalation to "set aside for tonight" is the
  operator's to restart, or a retry made one held pass short of it would be
  set aside again by the very next pass.
* ``SessionReporter.mark_retried``: the night report names the retry, so it
  does not show the panel only as skipped.

Every mutant was run from a byte backup of the file named, restored and
sha256-compared after each, the mutant's marker grepped absent (#254). The
observed failure is recorded verbatim, in the test it turned red.
"""
from __future__ import annotations

import pytest

from astrodeck.sequence.group_rules import (CENTRING, HELD_PASS_SET_ASIDE_AT,
                                            GroupRun, PanelDeferred)
from astrodeck.sequence.models import ExposureStep, SequencePlan, Target
from astrodeck.sequence.report import SessionReporter
from astrodeck.sequence.session import Session

TONIGHT, ANOTHER_NIGHT = "2026-09-01", "2026-09-02"


def _session() -> Session:
    plan = SequencePlan(name="records", targets=[
        Target(id=f"p{i}", name=f"M31 {i}", ra_hours=1.0, dec_deg=41.0,
               steps=[ExposureStep(id=f"p{i}-L", filter="L", exposure_s=30.0,
                                   count=3)])
        for i in (1, 2, 3)])
    return Session(name="records", plan=plan)


# ------------------------------------------------------------- the session

def test_clearing_marks_every_record_tonight_for_the_named_targets():
    """p1 has a whole-panel record and a step record tonight and one more
    last night, p2 has a record tonight, and p3 has one only another night.
    Clearing p1 and p3 for tonight marks p1's two records tonight, and nothing
    else: p3 has nothing tonight, and p2, p3's other night and p1's last night
    are untouched. The answer is the records that were STANDING, in the order
    they were made.

    RED under mutant "records not marked cleared" (``note_set_aside_cleared``
    a no-op: its loop's ``rec["cleared"] = True`` replaced by ``pass``),
    observed:

        AssertionError: assert [None, None] == [True, True]
          At index 0 diff: None != True
    """
    s = _session()
    s.note_set_aside("p1", "rejected 3 visits", night=TONIGHT, kind="rejects")
    s.note_set_aside("p1", "L set aside", night=TONIGHT, step_id="p1-L")
    s.note_set_aside("p2", "centring", night=TONIGHT, kind="centring", ts=5.0)
    s.note_set_aside("p3", "floor", night=ANOTHER_NIGHT, kind="floor")
    s.note_set_aside("p1", "last night's", night="2026-08-31", kind="floor")
    out = s.note_set_aside_cleared(["p1", "p3"], night=TONIGHT)
    assert [r["reason"] for r in out] == ["rejected 3 visits", "L set aside"]
    assert [r.get("cleared") for r in s.set_aside[:2]] == [True, True]
    assert [r.get("cleared") for r in s.set_aside[2:]] == [None, None, None], (
        "a record of another target, or another night, was marked")


def test_a_cleared_record_is_not_read_back_by_a_restart_tonight():
    """``set_aside_on`` is what ``engine.start`` and ResumeArm read: a cleared
    record is history, as an expired one is, and every other record tonight
    still stands.

    RED under mutant "cleared still read" (``set_aside_on`` not asking
    ``cleared``), observed:

        AssertionError: assert ['p1', 'p2'] == ['p2']
          At index 0 diff: 'p1' != 'p2'
    """
    s = _session()
    s.note_set_aside("p1", "x", night=TONIGHT, kind="rejects")
    s.note_set_aside("p2", "y", night=TONIGHT, kind="floor")
    s.note_set_aside_cleared(["p1"], night=TONIGHT)
    assert [r["target_id"] for r in s.set_aside_on(TONIGHT)] == ["p2"]


def test_a_retry_restores_the_panels_one_expiry():
    """A panel's centring set-aside expired once tonight (record 1, expired)
    and it was set aside again for good (record 2). ``set_aside_expiries_on``
    counts the first, so the second would stand for the night however often
    the run restarted. The operator's retry clears both, and the count goes
    back to nothing: the panel may expire once more, as on its first
    set-aside. Another panel's expiry is still counted.

    RED under mutant "cleared expiry still counted" (``set_aside_expiries_on``
    not asking ``cleared``), observed:

        AssertionError: assert {'p1': 1, 'p2': 1} == {'p2': 1}
          Left contains 1 more item: {'p1': 1}
    """
    s = _session()
    for pid in ("p1", "p2"):
        s.note_set_aside(pid, "centring", night=TONIGHT, kind="centring", ts=1.0)
        assert s.note_set_aside_expired(pid, night=TONIGHT) is not None
    s.note_set_aside("p1", "struck out again", night=TONIGHT, kind="group")
    assert s.set_aside_expiries_on(TONIGHT) == {"p1": 1, "p2": 1}, "premise"
    standing = s.note_set_aside_cleared(["p1"], night=TONIGHT)
    assert [r["reason"] for r in standing] == ["struck out again"], (
        "the expired record is history, so it is not a record that stood")
    assert s.set_aside_expiries_on(TONIGHT) == {"p2": 1}
    assert s.set_aside_on(TONIGHT) == []


def test_clearing_a_target_with_nothing_set_aside_clears_nothing():
    """CONTROL: nothing to clear answers an empty list and marks nothing,
    which the route reads as "nothing is set aside"."""
    s = _session()
    s.note_set_aside("p2", "y", night=TONIGHT, kind="floor")
    assert s.note_set_aside_cleared(["p1"], night=TONIGHT) == []
    assert "cleared" not in s.set_aside[0]


# ------------------------------------------------------------------ the rules

def _run(n: int = 3) -> GroupRun:
    return GroupRun({f"p{i}": f"1-{i}" for i in range(1, n + 1)},
                    max_failed_visits=3)


def _miss() -> PanelDeferred:
    return PanelDeferred("centring failed", kind=CENTRING,
                         last_error="plate solve failed — used raw GoTo")


@pytest.mark.parametrize("kind", [CENTRING, "floor", "rejects", "group",
                                  "angle", "deferred", "pier", "panel"])
def test_a_panel_set_aside_for_any_kind_is_live_again_and_not_an_expiry(kind):
    """Only a centring set-aside expires by itself (``expire_set_aside``
    refuses every other kind); the operator's retry takes ANY of them back,
    with a clean slate (failed, reject_visits and the streak's kinds start
    again), unvisited in the pass, and NOT counted as an expiry.

    RED under mutant "retry asks for a centring kind" (``retry_set_aside``
    raising unless ``set_aside_kind`` is CENTRING, ``expire_set_aside``'s
    check copied in), observed for every kind but centring:

        ValueError: 1-2's set-aside is not a centring one ('floor'), and only
        a centring set-aside expires
    """
    run = _run(3)
    run.failed["p2"] = 2
    run.reject_visits["p2"] = 2
    run.visited.add("p2")
    run.set_aside_panel("p2", "gave up", kind=kind)
    assert not run.is_live("p2"), "premise"
    run.retry_set_aside("p2")
    assert run.is_live("p2")
    assert (run.failed["p2"], run.reject_visits["p2"]) == (0, 0)
    assert "p2" not in run.visited and "p2" not in run.set_aside_kind
    assert run.expired == set(), "a retry is not an expiry"


def test_a_panel_that_is_not_set_aside_cannot_be_retried():
    """A caller bug says so, as ``expire_set_aside`` says it."""
    run = _run(3)
    with pytest.raises(ValueError, match="is not set aside"):
        run.retry_set_aside("p2")
    with pytest.raises(ValueError, match="not a member"):
        run.retry_set_aside("nope")


def test_a_retry_restarts_the_held_pass_streak():
    """D-03 (#563, #576): five held passes in a row, one short of the
    ``HELD_PASS_SET_ASIDE_AT`` that sets the group aside for tonight. The
    operator brings the other panel back; the streak is theirs to restart,
    so the next held pass is the FIRST, and the group is not set aside by it.
    Kept, the very next pass would be the sixth and set both panels aside
    again, the operator's retry undone before it was tried.

    RED under mutant "held streak kept" (the two resets deleted from
    ``retry_set_aside``), observed:

        AssertionError: assert 5 == 0
         +  where 5 = <astrodeck.sequence.group_rules.GroupRun object at 0x...>.held_streak
    """
    run = _run(2)
    run.set_aside_panel("p1", "floor", kind="floor")
    for n in range(1, HELD_PASS_SET_ASIDE_AT):
        run.visit_outcome("p2", complete=False, exposures=0, accepted=0,
                          deferred=_miss())
        end = run.close_pass()
        assert (end.boundary, end.held_streak) == ("centring_hold", n)
        run.start_pass()
    assert run.held_streak == HELD_PASS_SET_ASIDE_AT - 1, "premise"
    run.retry_set_aside("p1")
    assert run.held_streak == 0
    for p in ("p1", "p2"):
        run.visit_outcome(p, complete=False, exposures=0, accepted=0,
                          deferred=_miss())
    end = run.close_pass()
    assert (end.boundary, end.held_streak) == ("centring_hold", 1), (
        "the retried group's next held pass is its first, not the sixth")
    assert run.live() == ["p1", "p2"]


def test_a_panel_retried_alone_begins_a_pass_of_its_own():
    """1-1 is complete and 1-2 is set aside, the last live panel gone: the
    boundary that set it aside answered ``none_live`` and began no pass, so
    the counts standing are the closed pass's. The retried panel comes back
    into a pass of its own, as ``expire_set_aside``'s does, and a transient
    deferral the closed pass still held cannot make that refuse.

    RED under mutant "no fresh pass after a lone retry" (the ``alone`` branch
    of ``retry_set_aside`` deleted), observed:

        assert 1 == (1 + 1)
         +  where 1 = <astrodeck.sequence.group_rules.GroupRun object at 0x...>.pass_no
    """
    run = _run(2)
    run.visit_outcome("p1", complete=True, exposures=2, accepted=2)
    run.set_aside_panel("p2", "gave up", kind="rejects")
    run.visited.add("p2")
    run._held_transient.append(("p2", _miss()))
    before = run.pass_no
    run.retry_set_aside("p2")
    assert run.pass_no == before + 1
    assert run.visited == set()


# ----------------------------------------------------------------- the report

class _Named:
    def __init__(self, name: str) -> None:
        self.name = name


def test_the_report_names_the_retry_beside_the_skip():
    """The report already says the panel was skipped, with why; the retry is
    a second line, action ``retry``, that names the panel, so the morning
    record reads "skipped, then brought back by the operator" and not a
    skip that was never undone.

    RED under mutant "retry not recorded" (``mark_retried`` returning
    before it appends), observed:

        AssertionError: assert ['skip'] == ['skip', 'retry']
    """
    rep = SessionReporter(SequencePlan(name="r"))
    rep.mark_skipped(_Named("M31 2-2"), "rejected every frame")
    rep.mark_retried(_Named("M31 2-2"))
    events = rep.build().safety_events
    assert [e["action"] for e in events] == ["skip", "retry"]
    assert "M31 2-2" in events[1]["reason"] and "operator" in events[1]["reason"]
