"""The mosaic group driver's pure decisions (mosaic spec 5.1, 5.3, 5.6 steps 4
and 7, 5.7, 1.6, Appendix A.4; U-01, U-04, #189).

``astrodeck/sequence/group_rules.py`` holds every decision the S2 group driver
makes that needs no device, no clock and no engine: when a visit ends, what a
visit's outcome does to the panel and its counters, when a pass ends and what
follows, which panels the meridian rule lets shoot, how long the pre-flip idle
lasts, when a blocked panel clears, and what an angle verdict means for the
panel. Each is pinned here against a named mutation of that module, run from a
private scratch copy of ``server/`` (never the shared tree), with the observed
failure recorded in the test. The mutants the plan named are below; every other
test names its own as well (54 in all, none surviving):

* ``VisitBound``: "or instead of and" and "deadline checked without the frame";
* ``GroupRun.visit_outcome``: "count all zero-accept visits", "no failure
  counter", "lifetime failures", and the "since this panel's previous visit"
  window of the per-panel reject rule;
* the guide-start pass rule: "defer even then", and the pass's own ledger
  behind it: "a start that worked is not an attempt", "guide ledger never
  reset", "held guide deferral not a deferral";
* the pass boundary: "accepted frames instead of exposures";
* the meridian rule: "no hysteresis" (both halves) and "margin from the
  learned lead";
* the A.4 pre-flip idle, recomputed from ``compute_mosaic``: "span not
  converted to solar" and "linear span" across 0 h;
* ``forward_clear_ts``: "returns now when blocked";
* ``angle_decision``: "no_measurement read as ok" and "no_measurement read as
  off".

The controls are the cases where nothing should change: a visit that has met
its passes and its minimum, a frame that fits the deadline exactly, a pass
where every member rejected, a single failed guide start, a post-meridian
panel when no pre-flip panel can shoot, a panel clear at once, an angle inside
tolerance.
"""
from __future__ import annotations

import math

import pytest

from astrodeck.catalog.framing import compute_mosaic
from astrodeck.sequence import group_rules
from astrodeck.sequence.group_rules import (
    DEFER_WAIT_S,
    GUIDE_START,
    REACH_RECHECK_S,
    SOLAR_PER_SIDEREAL,
    GroupRun,
    PanelDeferred,
    PanelMeridian,
    VisitBound,
    angle_decision,
    forward_clear_ts,
    guide_start_pass_verdict,
    meridian_eligibility,
    pass_boundary,
    preflip_idle_cut_h,
    preflip_idle_whole_h,
    ra_span_sidereal_h,
    ra_span_solar_h,
    whole_visit_s,
)

#: Any instant will do; the pure rules only compare against it.
NOW = 1_790_000_000.0

MIN = 1.0 / 60.0          # one minute, in hours


def test_no_engine_import_and_the_named_constants():
    """The module is pure: it must not import the engine (which would drag the
    hub, the devices and the config store into a decision table), and the
    constants the engine owns, such as ``FLIP_FRAME_MARGIN_S``, are arguments.
    The two constants the spec names for the group are here, at the spec's
    values (5.1: ``DEFER_WAIT_S`` 300, ``REACH_RECHECK_S`` 60).

    MUTATION "import the engine" (``from .engine import FLIP_FRAME_MARGIN_S``
    at the top of the module). Observed:
        AssertionError: {'.engine', '__future__', 'collections.abc',
        'dataclasses', 'math', 'typing'}
        assert not True
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(group_rules))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(("." * node.level) + (node.module or ""))
    assert not any("engine" in name for name in imported), imported
    assert not hasattr(group_rules, "FLIP_FRAME_MARGIN_S")
    assert DEFER_WAIT_S == 300.0
    assert REACH_RECHECK_S == 60.0


# ---------------------------------------------------------------------------
# PanelDeferred
# ---------------------------------------------------------------------------

def test_panel_deferred_carries_reason_kind_and_last_error():
    """A deferral says why in words, what kind of failure it was (the guide
    rule reads the kind), and the last error the failing call reported.

    MUTATION "accept any kind" (no check against ``DEFERRAL_KINDS``). Observed:
        Failed: DID NOT RAISE <class 'ValueError'>
    """
    d = PanelDeferred("guiding did not start", kind=GUIDE_START,
                      last_error="no guide star")
    assert (d.reason, d.kind, d.last_error) == (
        "guiding did not start", "guide_start", "no guide star")
    assert str(d) == "guiding did not start: no guide star"
    assert isinstance(d, Exception)
    # a US spelling is the typo a parallel author would make; it must not pass
    # as an unknown kind the guide rule silently ignores
    with pytest.raises(ValueError, match="kind"):
        PanelDeferred("not centred", kind="centering")
    with pytest.raises(ValueError, match="words"):
        PanelDeferred("   ", kind="centring")


# ---------------------------------------------------------------------------
# VisitBound (5.3)
# ---------------------------------------------------------------------------

def test_a_visit_ends_at_a_round_only_when_both_passes_and_min_s_are_met():
    """5.3: after each round the visit ends if the panel is complete, or if
    ``rounds >= passes`` AND ``elapsed >= min_s``. ``visit_min_s`` extends a
    visit; it never shortens one below its passes.

    MUTATION "or instead of and" (``min_s`` ignored once the passes are met,
    and the passes ignored once ``min_s`` is). Observed:
        AssertionError: one pass is met but only 300 of the 600 s minimum ran
        assert not True
    """
    b = VisitBound(passes=1, min_s=600.0)
    assert not b.ends_at_round(rounds=1, elapsed_s=300.0, complete=False), (
        "one pass is met but only 300 of the 600 s minimum ran")
    assert b.ends_at_round(rounds=1, elapsed_s=600.0, complete=False)
    two = VisitBound(passes=2, min_s=0.0)
    assert not two.ends_at_round(rounds=1, elapsed_s=5000.0, complete=False), (
        "a minimum of 0 s is met at once; the second pass is still owed")
    assert two.ends_at_round(rounds=2, elapsed_s=0.0, complete=False)


def test_a_complete_panel_ends_its_visit_and_a_follower_ends_only_by_deadline():
    """Control and the follower case. A complete panel ends its visit at the
    round boundary whatever its passes. A bounded follower (1.6) carries no
    passes: only the deadline ends its visit, so the round rule never does.

    MUTATION "complete ignored" (the round rule reads only passes and time).
    Observed:
        assert False
         +  where False = ends_at_round(rounds=1, elapsed_s=10.0, complete=True)
    """
    b = VisitBound(passes=3, min_s=900.0)
    assert b.ends_at_round(rounds=1, elapsed_s=10.0, complete=True)
    follower = VisitBound(passes=None, deadline_ts=NOW + 3600.0)
    assert not follower.ends_at_round(rounds=50, elapsed_s=1e5, complete=False)
    assert follower.ends_at_round(rounds=1, elapsed_s=1.0, complete=True)


def test_the_next_frame_fits_only_when_the_whole_frame_ends_before_the_deadline():
    """5.3: before each frame the visit ends if ``now + exposure + overhead +
    margin`` would pass the deadline. The margin is the engine's
    ``FLIP_FRAME_MARGIN_S`` (30 s), passed in. A frame ending exactly on the
    deadline fits. With no deadline every frame fits.

    MUTATION "deadline checked without the frame" (``now <= deadline``).
    Observed:
        AssertionError: a 180 s frame started 100 s before the deadline would
        cross it
        assert not True
    """
    b = VisitBound(passes=1, deadline_ts=NOW + 100.0)
    assert not b.next_frame_fits(now=NOW, exposure_s=180.0, overhead_s=10.0,
                                 margin_s=30.0), (
        "a 180 s frame started 100 s before the deadline would cross it")
    exact = VisitBound(passes=1, deadline_ts=NOW + 220.0)
    assert exact.next_frame_fits(now=NOW, exposure_s=180.0, overhead_s=10.0,
                                 margin_s=30.0)
    assert not exact.next_frame_fits(now=NOW + 0.001, exposure_s=180.0,
                                     overhead_s=10.0, margin_s=30.0)
    unbounded = VisitBound(passes=1)
    assert unbounded.next_frame_fits(now=NOW, exposure_s=1e6, overhead_s=10.0,
                                     margin_s=30.0)


def test_visit_bound_refuses_nan_and_nonsense():
    """Every comparison with NaN is false, so a NaN elapsed time would never
    end a visit and a NaN deadline would end every one. Both are caller bugs
    and raise instead.

    MUTATION "no finite check" (the arguments compared as given). Observed:
        Failed: DID NOT RAISE <class 'ValueError'>
    """
    with pytest.raises(ValueError):
        VisitBound(passes=0)
    with pytest.raises(ValueError):
        VisitBound(passes=True)
    with pytest.raises(ValueError):
        VisitBound(passes=1, min_s=-1.0)
    with pytest.raises(ValueError):
        VisitBound(passes=1, deadline_ts=math.nan)
    b = VisitBound(passes=1, deadline_ts=NOW)
    with pytest.raises(ValueError):
        b.ends_at_round(rounds=1, elapsed_s=math.nan, complete=False)
    with pytest.raises(ValueError):
        b.next_frame_fits(now=math.nan, exposure_s=1.0, overhead_s=1.0,
                          margin_s=30.0)


# ---------------------------------------------------------------------------
# GroupRun.visit_outcome (5.1 table)
# ---------------------------------------------------------------------------

def _run(n=3, max_failed=3) -> GroupRun:
    labels = ["1-1", "1-2", "1-3", "2-3", "2-2", "2-1"][:n]
    return GroupRun({f"p{i}": lab for i, lab in enumerate(labels)},
                    max_failed_visits=max_failed)


def _defer(kind="centring", reason="the centring solve did not converge",
           err="3 attempts, 4.2 arcmin off"):
    return PanelDeferred(reason, kind=kind, last_error=err)


def test_a_complete_panel_is_removed_and_leaves_the_live_set():
    """Row 1: a complete panel is removed. It is no longer live, so the reject
    rule and the pass boundary stop counting it.

    MUTATION "completed not removed from live". Observed:
        AssertionError: assert ['p0', 'p1', 'p2'] == ['p1', 'p2']
    """
    run = _run()
    act = run.visit_outcome("p0", complete=True, exposures=7, accepted=7)
    assert act.action == "remove"
    assert "1-1 complete" in act.reason
    assert run.live() == ["p1", "p2"]
    with pytest.raises(ValueError, match="not live"):
        run.visit_outcome("p0", complete=False, exposures=1, accepted=1)


def test_completion_outranks_a_deferral_raised_after_the_last_frame():
    """A guiding loss can end the visit that shot the panel's last owed frame.
    The panel owes nothing, so it is removed, not requeued for a retry it does
    not need, and the deferral counts nothing.

    MUTATION "a deferral outranks completion" (the complete branch taken only
    when no deferral ended the visit). Observed:
        AssertionError: assert 'requeue' == 'remove'
    """
    run = _run()
    act = run.visit_outcome(
        "p0", complete=True, exposures=2, accepted=2,
        deferred=_defer(kind="guide_lost",
                        reason="guiding was lost and did not recover",
                        err="star lost"))
    assert act.action == "remove"
    assert run.live() == ["p1", "p2"]
    assert (run.failed["p0"], run.deferred_this_pass) == (0, 0)


def test_set_aside_all_takes_every_live_member_and_no_other():
    """``guiding_action`` skip after a rig-fault pass, or a fixed camera's
    angle beyond tolerance, sets the whole group aside: every LIVE member. A
    complete panel is not set aside, and a panel already set aside keeps the
    reason it was set aside for.

    MUTATION "set_aside_all over every member" (the members, not the live
    ones). Observed:
        AssertionError: assert ['p0', 'p1', 'p2'] == ['p2']
    """
    run = _run()
    run.visit_outcome("p0", complete=True, exposures=7, accepted=7)
    run.set_aside_panel("p1", "1-2 reached its floor")
    why = "guiding did not start on any panel, and guiding_action is skip"
    assert run.set_aside_all(why) == ["p2"]
    assert run.set_aside == {"p1": "1-2 reached its floor", "p2": why}
    assert run.live() == []


def test_an_accepted_visit_requeues_and_resets_both_counters():
    """Row 2: at least one accepted frame requeues the panel (visited, behind
    the unvisited members) and resets ``failed`` and ``reject_visits``. The
    bound is on CONSECUTIVE failures, so a panel that fails twice, banks a
    frame, and fails twice more is still live.

    MUTATION "lifetime failures" (no reset of ``failed`` on an accepted
    visit). Observed, at ``assert run.failed["p0"] == 0``:
        assert 2 == 0

    MUTATION "lifetime rejects" (no reset of ``reject_visits``). Observed, at
    ``assert run.reject_visits["p0"] == 0``:
        assert 2 == 0
    """
    run = _run()
    for _ in range(2):
        run.visit_outcome("p0", complete=False, exposures=0, accepted=0,
                          deferred=_defer())
    act = run.visit_outcome("p0", complete=False, exposures=7, accepted=2)
    assert act.action == "requeue"
    assert "p0" in run.visited
    assert run.failed["p0"] == 0
    for _ in range(2):
        act = run.visit_outcome("p0", complete=False, exposures=0, accepted=0,
                                deferred=_defer())
    assert act.action == "requeue"
    assert run.failed["p0"] == 2

    # the same for the reject counter: p1 accepts between p0's visits
    run = _run()
    for _ in range(2):
        run.visit_outcome("p1", complete=False, exposures=7, accepted=7)
        run.visit_outcome("p0", complete=False, exposures=7, accepted=0)
    assert run.reject_visits["p0"] == 2
    run.visit_outcome("p0", complete=False, exposures=7, accepted=1)
    assert run.reject_visits["p0"] == 0
    for _ in range(2):
        run.visit_outcome("p1", complete=False, exposures=7, accepted=7)
        act = run.visit_outcome("p0", complete=False, exposures=7, accepted=0)
    assert act.action == "requeue"
    assert run.reject_visits["p0"] == 2


def test_a_panel_rejecting_while_another_accepts_is_set_aside_at_the_bound():
    """Row 3: exposures taken, none accepted, while another live member
    accepted since this panel's previous visit. The requeue plus one to
    ``reject_visits``; at ``max_failed_visits`` consecutive such visits the
    panel is set aside with the spec's sentence.

    MUTATION "no reject counter" (the requeue without the count). Observed:
        AssertionError: assert 'requeue' == 'set_aside'
    """
    run = _run()
    for i in range(3):
        run.visit_outcome("p1", complete=False, exposures=7, accepted=7)
        act = run.visit_outcome("p0", complete=False, exposures=7, accepted=0)
        if i < 2:
            assert act.action == "requeue"
    assert act.action == "set_aside"
    assert act.reason == ("1-1 rejected every frame for 3 visits while the "
                          "other panels were accepted")
    assert run.set_aside == {"p0": act.reason}
    assert run.live() == ["p1", "p2"]


def test_when_every_member_rejects_nothing_moves():
    """Row 3's other half: when every member rejects, the sky is to blame and
    the counter does not move (the night guard and the cloud hold own that
    case). A cloud spell of rejects on every panel sets nothing aside.

    MUTATION "count all zero-accept visits" (the reject rule ignores whether
    another member accepted). Observed, on the third pass:
        AssertionError: assert 'set_aside' == 'requeue'
    """
    run = _run()
    for _ in range(5):
        for p in ("p0", "p1", "p2"):
            act = run.visit_outcome(p, complete=False, exposures=7, accepted=0)
            assert act.action == "requeue"
        end = run.close_pass()
        assert end.boundary == "next_pass"
        run.start_pass()
    assert run.set_aside == {}
    assert run.reject_visits == {"p0": 0, "p1": 0, "p2": 0}


def test_an_accept_before_this_panels_previous_visit_does_not_count():
    """The window is "since this panel's previous visit". p1 accepted once, at
    the start of the night; since then every panel rejects (a cloud spell).
    p0's rejects after its first visit must not be charged to p0, because
    nothing was accepted after that visit.

    MUTATION "accepts at any time count" (the window starts at the start of
    the night). Observed:
        AssertionError: assert 'set_aside' == 'requeue'
    """
    run = _run(n=2)
    run.visit_outcome("p1", complete=False, exposures=7, accepted=7)
    run.visit_outcome("p0", complete=False, exposures=7, accepted=0)
    assert run.reject_visits["p0"] == 1   # p1 accepted since p0's (no) visit
    for _ in range(3):
        run.visit_outcome("p1", complete=False, exposures=7, accepted=0)
        act = run.visit_outcome("p0", complete=False, exposures=7, accepted=0)
        assert act.action == "requeue"
    assert run.reject_visits["p0"] == 1


def test_an_accept_by_a_member_that_is_no_longer_live_does_not_count():
    """The spec's rule reads "another LIVE member". p1 accepts and is then
    set aside for its floor; p0's reject after that is not charged.

    MUTATION "any member counts" (the live check dropped). Observed:
        AssertionError: assert 1 == 0
    """
    run = _run(n=2)
    run.visit_outcome("p1", complete=False, exposures=7, accepted=7)
    run.set_aside_panel("p1", "1-2 reached its floor")
    run.visit_outcome("p0", complete=False, exposures=7, accepted=0)
    assert run.reject_visits["p0"] == 0


def test_a_deferral_counts_and_sets_the_panel_aside_at_the_bound():
    """Row 4: ``PanelDeferred`` marks the panel visited, adds one to
    ``failed`` and to ``deferred_this_pass``. At ``max_failed_visits``
    consecutive failures the panel is set aside with a reason that names the
    panel, the cause and the last error.

    MUTATION "no failure counter" (a deferral requeues without counting).
    Observed:
        AssertionError: assert 'requeue' == 'set_aside'
    """
    run = _run()
    for i in range(3):
        act = run.visit_outcome("p1", complete=False, exposures=0, accepted=0,
                                deferred=_defer())
        assert "p1" in run.visited
        assert run.deferred_this_pass == 1
        if i < 2:
            assert act.action == "requeue"
            assert "retried on the next pass" in act.reason
            run.close_pass()
            run.start_pass()
    assert act.action == "set_aside"
    assert act.reason == ("the centring solve did not converge on 1-2 on 3 "
                          "consecutive visits: 3 attempts, 4.2 arcmin off")
    assert run.set_aside["p1"] == act.reason


def test_a_mixed_streak_names_the_last_cause():
    """Three consecutive failures of different kinds still set the panel
    aside; the sentence says the count and names the last cause and error,
    rather than claiming every failure was the last one's kind.

    MUTATION "streak kinds ignored" (always the one-kind sentence). Observed:
        - 1-1 was deferred on 3 consecutive visits, the last because the mount
          chose the other pier side: read east, wanted west
        + the mount chose the other pier side on 1-1 on 3 consecutive visits:
          read east, wanted west
    """
    run = _run()
    run.visit_outcome("p0", complete=False, exposures=0, accepted=0,
                      deferred=_defer())
    run.visit_outcome("p0", complete=False, exposures=0, accepted=0,
                      deferred=_defer(kind="rotation",
                                      reason="the rotator skipped its move",
                                      err=""))
    act = run.visit_outcome("p0", complete=False, exposures=0, accepted=0,
                            deferred=_defer(kind="pier_side",
                                            reason="the mount chose the other "
                                                   "pier side",
                                            err="read east, wanted west"))
    assert act.action == "set_aside"
    assert act.reason == ("1-1 was deferred on 3 consecutive visits, the last "
                          "because the mount chose the other pier side: read "
                          "east, wanted west")


def test_a_banked_frame_clears_the_kinds_of_the_old_streak():
    """A rotator skip, then a visit that banks a frame, then three centring
    failures: the streak that sets the panel aside is three centring failures,
    and the sentence says so, not "deferred ..., the last because" as if the
    rotator skip were part of it.

    MUTATION "streak kinds kept across an accepted visit". Observed:
        - the centring solve did not converge on 1-1 on 3 consecutive visits:
          3 attempts, 4.2 arcmin off
        + 1-1 was deferred on 3 consecutive visits, the last because the
          centring solve did not converge: 3 attempts, 4.2 arcmin off
    """
    run = _run()
    run.visit_outcome("p0", complete=False, exposures=0, accepted=0,
                      deferred=_defer(kind="rotation",
                                      reason="the rotator skipped its move",
                                      err=""))
    run.visit_outcome("p0", complete=False, exposures=7, accepted=1)
    for _ in range(3):
        act = run.visit_outcome("p0", complete=False, exposures=0, accepted=0,
                                deferred=_defer())
    assert act.action == "set_aside"
    assert act.reason == ("the centring solve did not converge on 1-1 on 3 "
                          "consecutive visits: 3 attempts, 4.2 arcmin off")


def test_a_deferral_after_banked_frames_starts_a_new_streak_of_one():
    """A guiding loss that the recovery bound gives up on ends the visit with
    a ``PanelDeferred`` after frames were banked (5.6 step 7). The banked frame
    breaks the old streak and this failure starts a new one: ``failed`` is 1,
    not 0 and not the old streak plus one.

    MUTATION "reset after the count" (the accepted-frame reset runs after the
    deferral is counted). Observed:
        assert 0 == 1
    """
    run = _run()
    run.visit_outcome("p0", complete=False, exposures=0, accepted=0,
                      deferred=_defer())
    run.visit_outcome("p0", complete=False, exposures=0, accepted=0,
                      deferred=_defer())
    run.visit_outcome("p0", complete=False, exposures=3, accepted=3,
                      deferred=_defer(kind="guide_lost",
                                      reason="guiding was lost and did not "
                                             "recover", err="star lost"))
    assert run.failed["p0"] == 1
    assert "p0" in run.live()


def test_visit_outcome_refuses_impossible_reports():
    """More accepted than taken, or a guide start that both succeeded and
    failed, is a caller bug.

    MUTATION "no accepted <= exposures check". Observed:
        Failed: DID NOT RAISE <class 'ValueError'>
    """
    run = _run()
    with pytest.raises(ValueError):
        run.visit_outcome("p0", complete=False, exposures=1, accepted=2)
    with pytest.raises(ValueError):
        run.visit_outcome("p0", complete=False, exposures=0, accepted=0,
                          deferred=_defer(kind=GUIDE_START), guide_started=True)
    with pytest.raises(ValueError):
        run.visit_outcome("nope", complete=False, exposures=0, accepted=0)


# ---------------------------------------------------------------------------
# The guide-start pass rule (5.6 step 7, Revision 2 ruling 5)
# ---------------------------------------------------------------------------

def _guide_fail():
    return PanelDeferred("guiding did not start", kind=GUIDE_START,
                         last_error="no star found")


@pytest.mark.parametrize(
    ("attempted", "failed", "expected"),
    [
        pytest.param(0, 0, "panel", id="no attempt"),
        pytest.param(1, 1, "panel", id="1 of 1 is a panel deferral"),
        pytest.param(2, 1, "panel", id="1 of 2"),
        pytest.param(2, 2, "rig", id="2 of 2 is the rig"),
        pytest.param(6, 6, "rig", id="6 of 6"),
        pytest.param(6, 5, "panel", id="5 of 6"),
    ],
)
def test_guide_start_pass_verdict_table(attempted, failed, expected):
    """At least two panels attempted and every one failed is the rig's fault;
    anything less is a panel's.

    MUTATION "defer even then" (always a panel deferral). Observed, on the two
    rig rows:
        AssertionError: assert 'panel' == 'rig'
    """
    assert guide_start_pass_verdict(attempted, failed) == expected


def test_guide_start_pass_verdict_refuses_more_failures_than_attempts():
    """MUTATION "no failed <= attempted check". Observed:
        Failed: DID NOT RAISE <class 'ValueError'>
    """
    with pytest.raises(ValueError):
        guide_start_pass_verdict(1, 2)
    with pytest.raises(ValueError):
        guide_start_pass_verdict(-1, 0)


def test_a_guider_that_fails_on_every_panel_moves_no_counter():
    """Every attempted panel failed its guide start in the pass (three of
    three): the rig is to blame, the per-panel counters do not move, and the
    pass ends in ``guiding_action``'s hands. Three such passes set nothing
    aside, which is the point: a dead guider is bounded at one wasted pass by
    ``guiding_action``, not at three passes by the panel rule.

    MUTATION "defer even then". Observed, on the first pass:
        AssertionError: assert 'defer_wait' == 'guiding_action'
    """
    run = _run()
    for _ in range(3):
        for p in ("p0", "p1", "p2"):
            act = run.visit_outcome(p, complete=False, exposures=0,
                                    accepted=0, deferred=_guide_fail())
            assert act.action == "requeue"
        end = run.close_pass()
        assert end.boundary == "guiding_action"
        assert end.set_aside == ()
        assert "guiding_action" in end.reason
        assert run.failed == {"p0": 0, "p1": 0, "p2": 0}
        run.start_pass()
    assert run.set_aside == {}


def test_one_failed_start_out_of_one_attempt_is_a_panel_deferral():
    """Control. One panel attempted guiding and failed; the others deferred
    for centring before they reached the guider. That is one failure out of
    one attempt: a panel deferral, counted when the pass ends. And a failure
    beside a panel whose guider started is the failing panel's.

    MUTATION "rig on any all-failed pass" (the two-attempt floor dropped).
    Observed:
        AssertionError: assert 'guiding_action' == 'defer_wait'
    """
    run = _run()
    run.visit_outcome("p0", complete=False, exposures=0, accepted=0,
                      deferred=_guide_fail())
    assert run.failed["p0"] == 0, "held until the pass says whose fault it is"
    run.visit_outcome("p1", complete=False, exposures=0, accepted=0,
                      deferred=_defer())
    run.visit_outcome("p2", complete=False, exposures=0, accepted=0,
                      deferred=_defer())
    end = run.close_pass()
    assert end.boundary == "defer_wait"
    assert run.failed == {"p0": 1, "p1": 1, "p2": 1}
    run.start_pass()

    run.visit_outcome("p0", complete=False, exposures=0, accepted=0,
                      deferred=_guide_fail())
    run.visit_outcome("p1", complete=False, exposures=7, accepted=7,
                      guide_started=True)
    end = run.close_pass()
    assert end.boundary == "next_pass"
    assert run.failed["p0"] == 2


def test_a_held_guide_deferral_reaching_the_bound_sets_aside_at_the_pass_end():
    """The third consecutive guide-start failure of one panel, beside panels
    whose guiders start, sets it aside when the pass closes, with the spec's
    sentence.

    MUTATION "held deferrals dropped" (the pass end never counts them).
    Observed:
        AssertionError: assert () == (('p0', 'guid...star found'),)
    """
    run = _run()
    for _ in range(3):
        run.visit_outcome("p0", complete=False, exposures=0, accepted=0,
                          deferred=_guide_fail())
        run.visit_outcome("p1", complete=False, exposures=7, accepted=7,
                          guide_started=True)
        end = run.close_pass()
        run.start_pass()
    assert end.set_aside == (
        ("p0", "guiding did not start on 1-1 on 3 consecutive visits: no star "
               "found"),)
    assert run.live() == ["p1", "p2"]


def test_a_start_that_worked_makes_two_failures_the_panels_fault():
    """A guider that started on one panel is not dead: two failed starts
    beside it are the two panels' faults, each counted, and the pass goes on.
    Read as the rig's instead, ``guiding_action`` would decide over a working
    guider: abort would end the night, skip would set the mosaic aside.

    MUTATION "a start that worked is not an attempt" (``guide_started`` not
    counted). Observed:
        AssertionError: assert 'guiding_action' == 'next_pass'
    """
    run = _run()
    run.visit_outcome("p0", complete=False, exposures=0, accepted=0,
                      deferred=_guide_fail())
    run.visit_outcome("p1", complete=False, exposures=0, accepted=0,
                      deferred=_guide_fail())
    run.visit_outcome("p2", complete=False, exposures=7, accepted=7,
                      guide_started=True)
    end = run.close_pass()
    assert end.boundary == "next_pass"
    assert run.failed == {"p0": 1, "p1": 1, "p2": 0}


def test_the_guide_start_ledger_is_per_pass():
    """5.6 step 7 judges each pass on its own. Two lone failures in two
    different passes are two panel deferrals, not a rig fault; and a guider
    that dies mid-night is the rig's fault on the first pass it fails every
    panel, however well it started the pass before.

    MUTATION "guide ledger never reset" (``start_pass`` keeps
    ``guide_attempts`` and ``guide_failures``). Observed, at the second
    pass's boundary (two lone failures summed into a rig fault):
        AssertionError: assert 'guiding_action' == 'defer_wait'
    and, with the first half deleted from a scratch copy of this test, the
    guider that dies after a good pass:
        AssertionError: assert 'defer_wait' == 'guiding_action'
    """
    run = _run()
    # pass 1: p0 fails its start; p1 and p2 defer on centring first
    run.visit_outcome("p0", complete=False, exposures=0, accepted=0,
                      deferred=_guide_fail())
    for p in ("p1", "p2"):
        run.visit_outcome(p, complete=False, exposures=0, accepted=0,
                          deferred=_defer())
    assert run.close_pass().boundary == "defer_wait"
    run.start_pass()
    # pass 2: p1 fails its start alone; one failure out of one attempt again
    run.visit_outcome("p1", complete=False, exposures=0, accepted=0,
                      deferred=_guide_fail())
    for p in ("p0", "p2"):
        run.visit_outcome(p, complete=False, exposures=0, accepted=0,
                          deferred=_defer())
    assert run.close_pass().boundary == "defer_wait"
    assert run.failed == {"p0": 2, "p1": 2, "p2": 2}

    run = _run()
    for p in ("p0", "p1", "p2"):
        run.visit_outcome(p, complete=False, exposures=7, accepted=7,
                          guide_started=True)
    assert run.close_pass().boundary == "next_pass"
    run.start_pass()
    for p in ("p0", "p1", "p2"):
        run.visit_outcome(p, complete=False, exposures=0, accepted=0,
                          deferred=_guide_fail())
    end = run.close_pass()
    assert end.boundary == "guiding_action"
    assert run.failed == {"p0": 0, "p1": 0, "p2": 0}


def test_a_lone_failed_start_waits_and_never_ends_the_mosaic():
    """One panel was eligible this pass (the others wait for their meridian
    crossing, unvisited) and its guide start failed. The pass took no
    exposures but did defer, so it waits ``DEFER_WAIT_S``: a held guide
    deferral is a deferral for the pass boundary as soon as it happens. Read as
    a pass that deferred nothing, the group anti-spin would set all three
    panels aside for the night over one failed start.

    MUTATION "held guide deferral not a deferral" (``deferred_this_pass``
    skipped for a failed guide start). Observed:
        AssertionError: assert 'set_aside_all' == 'defer_wait'
    """
    run = _run()
    run.visit_outcome("p0", complete=False, exposures=0, accepted=0,
                      deferred=_guide_fail())
    end = run.close_pass()
    assert end.boundary == "defer_wait"
    assert end.set_aside == ()
    assert run.live() == ["p0", "p1", "p2"]
    assert run.failed["p0"] == 1


def test_a_held_deferral_of_a_panel_set_aside_since_is_not_counted_again():
    """p0's third failed start is held, and before the pass ends the group is
    set aside for another cause (a fixed camera beyond tolerance on p1's hop).
    The pass end must not count p0's held failure: it would overwrite the
    reason the group was set aside for and owe p0 a second alert.

    MUTATION "held deferral of a panel no longer live counted" (the
    ``is_live`` check in ``close_pass`` dropped). Observed:
        AssertionError: assert (('p0', 'guid...star found'),) == ()
          Left contains one more item: ('p0', 'guiding did not start on 1-1
          on 3 consecutive visits: no star found')
    """
    run = _run()
    for _ in range(2):
        run.visit_outcome("p0", complete=False, exposures=0, accepted=0,
                          deferred=_guide_fail())
        run.visit_outcome("p1", complete=False, exposures=7, accepted=7,
                          guide_started=True)
        run.close_pass()
        run.start_pass()
    run.visit_outcome("p0", complete=False, exposures=0, accepted=0,
                      deferred=_guide_fail())
    why = "a fixed camera cannot turn itself: turn the camera or re-frame"
    assert run.set_aside_all(why) == ["p0", "p1", "p2"]
    end = run.close_pass()
    assert end.boundary == "none_live"
    assert end.set_aside == ()
    assert run.set_aside["p0"] == why
    assert run.failed["p0"] == 2


# ---------------------------------------------------------------------------
# The pass boundary (5.1)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("exposures", "deferrals", "expected"),
    [
        pytest.param(0, 0, "set_aside_all", id="nothing at all"),
        pytest.param(0, 2, "defer_wait", id="deferrals only"),
        pytest.param(5, 0, "next_pass", id="exposures"),
        pytest.param(5, 3, "next_pass", id="both"),
    ],
)
def test_pass_boundary_table(exposures, deferrals, expected):
    """MUTATION "defer_wait on any deferral" (a pass that shot and deferred
    waits). Observed, on "both":
        AssertionError: assert 'defer_wait' == 'next_pass'
    """
    assert pass_boundary(exposures, deferrals) == expected


def test_a_clouded_pass_of_rejects_does_not_end_the_mosaic():
    """5.1 pass boundary item 1 counts EXPOSURES, accepted plus rejected, so a
    clouded pass in which every frame was rejected is a normal pass, not the
    zero-exposure anti-spin case. The reject guards handle clouds.

    MUTATION "accepted frames instead of exposures" (the pass counts accepted
    frames). Observed:
        AssertionError: assert 'set_aside_all' == 'next_pass'
    """
    run = _run()
    for p in ("p0", "p1", "p2"):
        run.visit_outcome(p, complete=False, exposures=7, accepted=0)
    end = run.close_pass()
    assert end.boundary == "next_pass"
    assert run.set_aside == {}


def test_a_pass_with_no_exposures_and_no_deferrals_sets_every_live_member_aside():
    """Item 1: nothing was shot and nothing was deferred (every visit returned
    at once). The group anti-spin sets every live member aside, with the
    spec's sentence. A panel already complete is not set aside.

    MUTATION "anti-spin sets nothing aside". Observed:
        AssertionError: assert () == (('p1', 'a fu...for tonight'))
    """
    run = _run()
    run.visit_outcome("p0", complete=True, exposures=7, accepted=7)
    run.start_pass()
    run.visit_outcome("p1", complete=False, exposures=0, accepted=0)
    run.visit_outcome("p2", complete=False, exposures=0, accepted=0)
    end = run.close_pass()
    assert end.boundary == "set_aside_all"
    reason = ("a full pass over 2 panels took no exposures; setting the mosaic "
              "aside for tonight")
    assert end.reason == reason
    assert end.set_aside == (("p1", reason), ("p2", reason))
    assert run.live() == []


def test_the_frames_of_a_visit_that_completed_its_panel_count_for_the_pass():
    """The pass shot seven frames, on the panel they completed; the other two
    visits returned with nothing and deferred nothing (a visit whose first
    frame no longer fit its deadline does that). A pass that shot is not the
    anti-spin case, so the other two get another pass instead of the night
    ending for them.

    MUTATION "a completing visit's exposures not counted". Observed:
        AssertionError: assert 'set_aside_all' == 'next_pass'
    """
    run = _run()
    run.visit_outcome("p0", complete=True, exposures=7, accepted=7)
    run.visit_outcome("p1", complete=False, exposures=0, accepted=0)
    run.visit_outcome("p2", complete=False, exposures=0, accepted=0)
    end = run.close_pass()
    assert end.boundary == "next_pass"
    assert run.live() == ["p1", "p2"]


def test_a_boundary_that_sets_aside_the_last_live_panel_says_none_is_live():
    """The last live panel reaches its failure bound as the pass closes (here
    ``max_failed_visits`` is 1). Nothing is left to shoot, so the boundary says
    so, rather than reading the all-deferred pass as a reason to wait
    ``DEFER_WAIT_S`` for nothing. The sentence counts one visit in the
    singular.

    MUTATION "no live check" (straight to ``pass_boundary``). Observed:
        AssertionError: assert 'defer_wait' == 'none_live'

    MUTATION "always plural". Observed:
        AssertionError: assert (('p0', 'guid...star found'),) == (('p0',
        'guid...star found'),)
        At index 0 diff: ('p0', 'guiding did not start on 1-1 on 1
        consecutive visits: no star found') != ('p0', 'guiding did not start
        on 1-1 on 1 consecutive visit: no star found')
    """
    run = GroupRun({"p0": "1-1"}, max_failed_visits=1)
    run.visit_outcome("p0", complete=False, exposures=0, accepted=0,
                      deferred=_guide_fail())
    end = run.close_pass()
    assert end.boundary == "none_live"
    assert end.set_aside == (
        ("p0", "guiding did not start on 1-1 on 1 consecutive visit: no star "
               "found"),)
    assert run.live() == []


def test_start_pass_clears_the_pass_and_refuses_an_unclosed_one():
    """Starting a pass with guide-start deferrals still held would drop them
    uncounted.

    MUTATION "no held check". Observed:
        Failed: DID NOT RAISE <class 'RuntimeError'>
    """
    run = _run()
    run.visit_outcome("p0", complete=False, exposures=0, accepted=0,
                      deferred=_guide_fail())
    with pytest.raises(RuntimeError, match="close_pass"):
        run.start_pass()
    run.close_pass()
    run.start_pass()
    assert run.pass_no == 2
    assert run.visited == set()
    assert (run.exposures_this_pass, run.deferred_this_pass) == (0, 0)


# ---------------------------------------------------------------------------
# Meridian eligibility (5.7), all in hours
# ---------------------------------------------------------------------------

HOP_H = 2.5 * MIN
F_H = 100.0 / 3600.0       # 60 s + 10 s overhead + the 30 s margin
LEAD_H = 10.0 * MIN


def _elig(panels, *, flipped=False, meridian_flip=True, lead_h=LEAD_H):
    return meridian_eligibility(
        {k: (v if isinstance(v, PanelMeridian) else PanelMeridian(h_p=v, f_h=F_H))
         for k, v in panels.items()},
        lead_h=lead_h, hop_h=HOP_H, flipped=flipped,
        meridian_flip=meridian_flip)


def test_a_pre_flip_panel_with_room_is_eligible_with_its_flip_point_as_deadline():
    """Not flipped: eligible when ``h_p - lead_h >= hop_h + f_h``, and the
    visit's deadline is the flip point, ``h_p - lead_h`` from now. Exactly
    enough room is enough.

    MUTATION "> instead of >=". Observed:
        AssertionError: assert (False)
         +  where False = MeridianVerdict(eligible=False, deadline_h=None,
         wake_h=0.2361111111111111, reason='too little room before its flip
         point for a hop and one frame; waiting for its meridian
         crossing').eligible
    """
    need = HOP_H + F_H
    out = _elig({"a": 1.0, "b": LEAD_H + need})
    assert out["a"].eligible and out["a"].deadline_h == pytest.approx(1.0 - LEAD_H)
    assert out["a"].wake_h is None
    assert out["b"].eligible and out["b"].deadline_h == pytest.approx(need)


def test_a_pre_flip_panel_without_room_waits_for_its_crossing():
    """``0 < h_p`` and too little room: waiting, with the wake at the
    crossing (``h_p``), not at the flip point.

    MUTATION "wake at the flip point" (``h_p - lead_h``). Observed:
        assert 0.033333333333333354 == 0.2 ± 2.0e-07
    """
    out = _elig({"a": 12.0 * MIN})
    assert not out["a"].eligible
    assert out["a"].wake_h == pytest.approx(12.0 * MIN)
    assert out["a"].deadline_h is None


def test_eight_minutes_before_transit_is_not_eligible_under_the_plans_lead():
    """5.7: the margin is the PLAN's lead, never the learned one. The learned
    lead (``_flip_lead_s``) is 0 on a mount that cannot flip early, which is
    right for when to attempt a flip and wrong as a margin, because the AM5
    stops tracking 4.7 to 7.6 min before transit. A panel 8 min before
    transit, with a 2.5 min hop and a 1.67 min first frame, must wait.

    MUTATION "margin from the learned lead" (the room is measured with the
    learned lead, 0 on this mount, instead of ``lead_h``). Observed:
        AssertionError: assert not True
         +  where True = MeridianVerdict(eligible=True,
         deadline_h=0.13333333333333333, wake_h=None, reason='before the
         meridian with room for a hop and a frame; the visit ends before its
         flip point').eligible
    The engine-side twin, calling ``_flip_lead_s`` instead of
    ``_plan_flip_lead_s``, belongs to the engine's group suite.
    """
    out = _elig({"a": 8.0 * MIN})
    assert not out["a"].eligible
    assert out["a"].wake_h == pytest.approx(8.0 * MIN)


def test_before_the_flip_post_meridian_panels_wait_while_a_pre_flip_panel_can_shoot():
    """Not flipped: a post-meridian panel (``h_p <= 0``) is eligible only
    while no pre-flip panel is. Shooting it first would flip the group and
    then need a second pier change to go back. The control: once no pre-flip
    panel has room, the post-meridian panel is eligible.

    MUTATION "no hysteresis" (each panel judged on its own geometry: eligible
    when past the meridian or with room). Observed:
        AssertionError: assert not True
         +  where True = MeridianVerdict(eligible=True, deadline_h=None,
         wake_h=None, reason='past the meridian, and no panel before the
         meridian can shoot').eligible
    """
    out = _elig({"east": 1.0, "west": -2.0 * MIN, "crossed": 0.0})
    assert out["east"].eligible
    assert not out["west"].eligible
    assert not out["crossed"].eligible
    # the held panel wakes when the last pre-flip panel runs out of room
    assert out["west"].wake_h == pytest.approx(1.0 - LEAD_H - HOP_H - F_H)

    out = _elig({"east": 5.0 * MIN, "west": -2.0 * MIN})
    assert not out["east"].eligible
    assert out["west"].eligible
    assert out["west"].deadline_h is None


def test_after_the_flip_only_post_meridian_panels_are_eligible():
    """Flipped: only post-meridian panels are eligible; every other panel
    waits for its crossing, however much room it has. Going back to it would
    be a second pier change.

    MUTATION "no hysteresis". Observed:
        AssertionError: assert not True
         +  where True = MeridianVerdict(eligible=True,
         deadline_h=0.8333333333333334, wake_h=None, reason='before the
         meridian with room for a hop and a frame; the visit ends before its
         flip point').eligible
    """
    out = _elig({"east": 1.0, "west": -2.0 * MIN}, flipped=True)
    assert not out["east"].eligible
    assert out["east"].wake_h == pytest.approx(1.0)
    assert out["west"].eligible and out["west"].deadline_h is None


def test_a_panel_on_the_meridian_is_past_it_in_both_states():
    """``h_p`` is at or below 0 after the crossing (5.7), so a panel exactly
    on the meridian is post-meridian, flipped or not. Judged as pre-flip it
    would wait with its wake at 0 h, a wake that is already due, and be asked
    again at once.

    MUTATION "the crossing instant is pre-flip, not flipped" (``h_p < 0`` in
    the not-flipped branch). Observed:
        AssertionError: assert (False)
         +  where False = MeridianVerdict(eligible=False, deadline_h=None,
         wake_h=0.0, reason='too little room before its flip point for a hop
         and one frame; waiting for its meridian crossing').eligible

    MUTATION "the crossing instant is pre-flip, flipped" (``h_p < 0`` in the
    flipped branch). Observed:
        AssertionError: assert (False)
         +  where False = MeridianVerdict(eligible=False, deadline_h=None,
         wake_h=0.0, reason='the group has flipped; this panel waits for its
         meridian crossing, since going back would be a second pier
         change').eligible
    """
    out = _elig({"on": 0.0})
    assert out["on"].eligible and out["on"].deadline_h is None
    out = _elig({"on": 0.0}, flipped=True)
    assert out["on"].eligible and out["on"].deadline_h is None


def test_flips_off_or_a_skippable_flip_disables_the_rule():
    """With ``plan.meridian_flip`` off the rule is off for every panel; with
    ``flip_can_be_skipped`` it is off for that panel. Such a panel is eligible
    with no deadline, and it does not hold the post-meridian panels back,
    since it never changes pier side.

    MUTATION "rule ignores flips off" (``meridian_flip`` not read). Observed:
        AssertionError: assert (False)
         +  where False = MeridianVerdict(eligible=False, deadline_h=None,
         wake_h=0.08333333333333333, reason='too little room before its flip
         point for a hop and one frame; waiting for its meridian
         crossing').eligible
    """
    out = _elig({"a": 5.0 * MIN, "b": 1.0}, meridian_flip=False)
    assert out["a"].eligible and out["a"].deadline_h is None
    assert out["b"].eligible and out["b"].deadline_h is None
    out = _elig({"skip": PanelMeridian(h_p=1.0, f_h=F_H,
                                       flip_can_be_skipped=True),
                 "west": -1.0 * MIN})
    assert out["skip"].eligible and out["skip"].deadline_h is None
    assert out["west"].eligible


def test_meridian_reasons_carry_no_numbers():
    """How long until a panel's crossing is a site-derived time (6.9: words
    only, never the minutes to a site-derived time). Every reason is words.

    MUTATION "hours in the reason" (the waiting sentence says "in {h_p * 60}
    min"). Observed:
        AssertionError: too little room before its flip point for a hop and
        one frame; waiting for its meridian crossing in 5 min
    """
    out = _elig({"east": 1.0, "late": 5.0 * MIN, "west": -2.0 * MIN})
    out.update(_elig({"x": 1.0, "y": -1.0 * MIN}, flipped=True))
    out.update(_elig({"z": 1.0}, meridian_flip=False))
    for v in out.values():
        assert v.reason and not any(ch.isdigit() for ch in v.reason), v.reason


def test_meridian_eligibility_refuses_nan():
    """MUTATION "no finite check" (``_finite`` without ``isfinite``).
    Observed:
        Failed: DID NOT RAISE <class 'ValueError'>
    """
    with pytest.raises(ValueError):
        _elig({"a": math.nan})
    with pytest.raises(ValueError):
        _elig({"a": 1.0}, lead_h=math.nan)


# ---------------------------------------------------------------------------
# The pre-flip idle (5.7 cost 1, Appendix A.4)
# ---------------------------------------------------------------------------

A4_LEAD_H = 10.0 * MIN
A4_HOP_S = 150.0
A4_F_H = (60.0 + 10.0 + 30.0) / 3600.0


def _panel_ras(cols, rows, fx, fy, dec):
    m = compute_mosaic({"ra_hours": 10.0, "dec_deg": dec, "fov_x_deg": fx,
                        "fov_y_deg": fy, "rows": rows, "cols": cols,
                        "overlap": 0.25, "rotation_deg": 0.0})
    return [p["ra_hours"] for p in m["panels"]]


@pytest.mark.parametrize(
    ("cols", "rows", "fx", "fy", "dec", "span_min", "cut", "whole1", "whole2"),
    [
        pytest.param(3, 2, 2.0, 1.33, 20, 12.807, 1.39, 13.89, 28.06,
                     id="3x2 at Dec 20"),
        pytest.param(3, 2, 2.0, 1.33, 41, 16.015, 0.0, 10.70, 24.86,
                     id="3x2 at Dec 41"),
        pytest.param(3, 2, 2.0, 1.33, 60, 24.344, 0.0, 2.39, 16.56,
                     id="3x2 at Dec 60"),
        pytest.param(2, 2, 0.9, 0.6, 41, 3.590, 10.59, 23.09, 37.25,
                     id="2x2 of 0.9x0.6 at Dec 41"),
    ],
)
def test_the_a4_pre_flip_idle_recomputed_from_compute_mosaic(
        cols, rows, fx, fy, dec, span_min, cut, whole1, whole2):
    """Appendix A.4, recomputed: ``compute_mosaic`` at RA 10 h, angle 0, 25%
    overlap; a 10 min lead, a 150 s hop and ``f`` = 100 s; the shipped default
    cycle (780 s of shutter and 7 frames at 10 s of overhead per pass). The
    planner's span (sidereal minutes) is asserted to 0.001 min and each idle
    to 0.05 min; the solar span to 0.001 min against the planner's span
    times 0.9972696.

    MUTATION "span not converted to solar" (the sidereal span used as solar
    time). Observed, all four rows red, on the Dec 60 row:
        AssertionError: solar span
        assert 24.344475889903734 == 24.2775311424 ± 0.001
    and, with the solar-span assertion deleted from a scratch copy of this
    test, the planner's own tell, on the Dec 60 row alone:
        AssertionError: whole visit, 1 pass
        assert 2.3221907767629313 == 2.39 ± 0.05
    """
    ras = _panel_ras(cols, rows, fx, fy, dec)
    assert ra_span_sidereal_h(ras) * 60.0 == pytest.approx(span_min, abs=1e-3)
    span_h = ra_span_solar_h(ras)
    assert span_h * 60.0 == pytest.approx(span_min * 0.9972696, abs=1e-3), (
        "solar span")
    assert preflip_idle_cut_h(lead_h=A4_LEAD_H, hop_h=A4_HOP_S / 3600.0,
                              f_h=A4_F_H, span_h=span_h) * 60.0 == (
        pytest.approx(cut, abs=0.05)), "cut visits"
    for passes, expected in ((1, whole1), (2, whole2)):
        visit_h = whole_visit_s(passes=passes, pass_shutter_s=780.0,
                                frames_per_pass=7, overhead_s=10.0,
                                hop_s=A4_HOP_S) / 3600.0
        assert preflip_idle_whole_h(visit_h=visit_h, lead_h=A4_LEAD_H,
                                    span_h=span_h) * 60.0 == (
            pytest.approx(expected, abs=0.05)), f"whole visit, {passes} pass"


def test_the_solar_factor_is_the_sidereal_day_over_the_solar_day():
    """0.9972696 solar seconds per sidereal second, the ratio of the sidereal
    day (86164.0905 s) to the solar day.

    MUTATION "factor 1.0027379" (the inverse ratio). Observed:
        assert 1.0027379 == 0.9972695659722223 ± 1.0e-07
    """
    assert SOLAR_PER_SIDEREAL == pytest.approx(86164.0905 / 86400.0, abs=1e-7)


def test_whole_visit_is_passes_of_shutter_and_overhead_plus_one_hop():
    """A.4: ``visit = passes x (780 s + 7 x 10 s) + hop``: 1000 s for one
    pass and 1850 s for two.

    MUTATION "overhead once per pass" (not once per frame). Observed:
        AssertionError: assert 940.0 == 1000.0
    """
    kw = dict(pass_shutter_s=780.0, frames_per_pass=7, overhead_s=10.0,
              hop_s=150.0)
    assert whole_visit_s(passes=1, **kw) == 1000.0
    assert whole_visit_s(passes=2, **kw) == 1850.0


def test_the_ra_span_is_the_shortest_arc_across_zero_hours():
    """A mosaic at RA 0 h has panel centres either side of 24 h. Its span is
    the shortest arc that holds every centre (12 min here), not 23.8 h.

    MUTATION "linear span" (``max - min``). Observed:
        assert 1437.0 == 12.0 ± 1.2e-05
    """
    ras = [23.9, 0.1, 23.95, 0.0]
    assert ra_span_sidereal_h(ras) * 60.0 == pytest.approx(12.0)
    assert ra_span_sidereal_h([10.0]) == 0.0
    with pytest.raises(ValueError):
        ra_span_sidereal_h([])


def test_the_idle_is_never_negative():
    """Control. A span wider than the lead, hop and frame leaves no idle, not
    a negative one.

    MUTATION "no floor at zero" (the cut-visit idle without ``max``).
    Observed:
        assert -0.82 == 0.0
    """
    assert preflip_idle_cut_h(lead_h=0.1, hop_h=0.05, f_h=0.03, span_h=1.0) == 0.0
    assert preflip_idle_whole_h(visit_h=0.3, lead_h=0.1, span_h=1.0) == 0.0


# ---------------------------------------------------------------------------
# forward_clear_ts (1.6 group_ready_ts)
# ---------------------------------------------------------------------------

def test_forward_clear_ts_finds_the_first_clear_step():
    """A panel behind the mask until 300 s from now clears at the first 60 s
    step at or after that. The predicate is asked on the step grid only.

    MUTATION "returns now when blocked" (the scan answers ``now`` when the
    panel is blocked at ``now``). Observed:
        assert 1790000000.0 == (1790000000.0 + 300.0)
    """
    asked = []

    def clear(t):
        asked.append(t - NOW)
        return t >= NOW + 280.0

    assert forward_clear_ts(clear, NOW, horizon_s=3600.0) == NOW + 300.0
    assert asked == [0.0, 60.0, 120.0, 180.0, 240.0, 300.0]


def test_forward_clear_ts_controls():
    """Control: a panel clear now clears now. A panel that never clears inside
    the horizon has no clearing time (None), so it does not bound
    ``group_ready_ts``. The last step asked is the horizon itself.

    MUTATION "last step dropped" (``range(steps)``). Observed:
        assert [0.0, 120.0, ... 360.0, 480.0] == [0.0, 120.0, ... 480.0, 600.0]
    """
    assert forward_clear_ts(lambda t: True, NOW, horizon_s=600.0) == NOW
    asked = []

    def never(t):
        asked.append(t - NOW)
        return False

    assert forward_clear_ts(never, NOW, horizon_s=600.0, step_s=120.0) is None
    assert asked == [0.0, 120.0, 240.0, 360.0, 480.0, 600.0]
    with pytest.raises(ValueError):
        forward_clear_ts(never, NOW, horizon_s=600.0, step_s=0.0)
    with pytest.raises(ValueError):
        forward_clear_ts(never, math.nan, horizon_s=600.0)


# ---------------------------------------------------------------------------
# angle_decision (5.6 step 4)
# ---------------------------------------------------------------------------

def _ad(kind, *, rotate, verified=False, evidence=False, anyway=False,
        single=False):
    return angle_decision(kind, rotate=rotate, angle_verified=verified,
                          rotator_evidence=evidence, shoot_anyway=anyway,
                          single_panel=single).action


@pytest.mark.parametrize(
    ("kind", "kw", "expected"),
    [
        pytest.param("ok", dict(rotate=False), "shoot", id="ok, fixed"),
        pytest.param("ok", dict(rotate=True), "shoot", id="ok, rotate"),
        pytest.param("off", dict(rotate=False), "set_group_aside",
                     id="off, fixed: the group is set aside"),
        pytest.param("off", dict(rotate=True), "defer", id="off, rotate"),
        pytest.param("no_measurement", dict(rotate=False, verified=True),
                     "shoot_logged", id="none, fixed, verified: shoot and log"),
        pytest.param("no_measurement", dict(rotate=False, verified=False),
                     "defer", id="none, fixed, never verified"),
        pytest.param("no_measurement", dict(rotate=True, evidence=True),
                     "warn", id="none, rotate, rotator evidence"),
        pytest.param("no_measurement", dict(rotate=True, evidence=False),
                     "defer", id="none, rotate, no evidence"),
        # a fixed camera has no rotator to read, so its evidence means nothing
        pytest.param("no_measurement", dict(rotate=False, evidence=True),
                     "defer", id="none, fixed, evidence ignored"),
        pytest.param("off", dict(rotate=False, anyway=True), "warn",
                     id="shoot anyway, off, fixed"),
        pytest.param("off", dict(rotate=True, anyway=True), "warn",
                     id="shoot anyway, off, rotate"),
        pytest.param("no_measurement", dict(rotate=False, anyway=True),
                     "warn", id="shoot anyway, none, fixed"),
        pytest.param("no_measurement", dict(rotate=True, anyway=True),
                     "warn", id="shoot anyway, none, rotate"),
        pytest.param("off", dict(rotate=False, single=True), "warn",
                     id="1x1, off, fixed"),
        pytest.param("no_measurement", dict(rotate=True, single=True), "warn",
                     id="1x1, none, rotate"),
        pytest.param("ok", dict(rotate=False, single=True), "shoot",
                     id="1x1, ok"),
        pytest.param("no_measurement", dict(rotate=False, verified=True,
                                            single=True),
                     "shoot_logged", id="1x1, none, fixed, verified"),
    ],
)
def test_angle_decision_covers_every_step_4_case(kind, kw, expected):
    """Every 5.6 step 4 case. "No measurement" is neither "ok" nor "off": a
    fixed camera verified tonight cannot have turned since, so it shoots and
    logs; one never verified defers (tiles are never laid blind); a
    calibrated rotator's own read stands in, with a warning; anything else
    defers. "Shoot anyway" turns every refusal into a warning, and a 1x1
    block only ever warns.

    MUTATION "no_measurement read as ok". Observed, on "none, fixed, never
    verified", for example:
        AssertionError: assert 'shoot' == 'defer'

    MUTATION "no_measurement read as off". Observed, on "none, fixed,
    verified", for example:
        AssertionError: assert 'set_group_aside' == 'shoot_logged'
    and on "none, rotate, rotator evidence":
        AssertionError: assert 'defer' == 'warn'
    """
    assert _ad(kind, **kw) == expected


def test_angle_decision_says_why_and_refuses_an_unknown_kind():
    """MUTATION "no kind check" (an unknown kind falls through to the
    no-measurement branch). Observed:
        Failed: DID NOT RAISE <class 'ValueError'>
    """
    d = angle_decision("off", rotate=False, angle_verified=False,
                       rotator_evidence=False, shoot_anyway=False,
                       single_panel=False)
    assert "turn the camera or re-frame at the measured angle" in d.why
    d = angle_decision("no_measurement", rotate=False, angle_verified=False,
                       rotator_evidence=False, shoot_anyway=False,
                       single_panel=False)
    assert "never laid blind" in d.why
    with pytest.raises(ValueError):
        angle_decision("maybe", rotate=False, angle_verified=False,
                       rotator_evidence=False, shoot_anyway=False,
                       single_panel=False)
