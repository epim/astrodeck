"""The centring rules of the mosaic group driver, pure (#534, H4 orchestrator
ruling 2; #532, contract 1; spec 5.1, 5.6 steps 4 and 7).

The first mosaic on the rig (NGC 1499, a 3x2, 2026-09-29) started low in the
east behind an obstruction, in moonlit haze. Every panel failed centring, the
three-strike rule set all six aside in 18 minutes, and the rest of the night
was lost although the target transited three hours later. The ruling:

* a CENTRING set-aside expires once per panel per night, 45 minutes after it
  was made or once the panel centre has risen 10 degrees since, whichever
  comes first (``set_aside_expiry``);
* a pass in which every panel tried (at least two) fails centring is the
  sky's or the geometry's: no panel is struck, and the group holds
  ``CENTRING_HOLD_RETRY_S`` and tries a new pass (``centring_pass_verdict``,
  ``GroupRun.close_pass``), the guide-start pass rule's shape;
* a centring solve that could not run (``solve_transient``, #532) is a
  deferral that counts toward neither ``failed`` nor the centring pass rule.

Each rule is pinned against a named mutation of
``astrodeck/sequence/group_rules.py``, run in a private copy of ``server/``
(scratchpad ``H4-ENG-A-r2-mut``), never in the shared tree, the file restored
from a byte backup and its sha256 checked after each. The observed failure is
quoted in the test it turned red. The controls are the cases where nothing
may change: a panel not yet due, a setting panel, a pass in which one panel
centred, a lone attempted miss.
"""
from __future__ import annotations

import math

import pytest

from astrodeck.sequence import group_rules
from astrodeck.sequence.group_rules import (
    CENTRING,
    CENTRING_HOLD_RETRY_S,
    DEFER_WAIT_S,
    GUIDE_START,
    SET_ASIDE_EXPIRY_S,
    SET_ASIDE_RISE_DEG,
    SET_ASIDE_RISE_FLOOR_S,
    SOLVE_TRANSIENT,
    GroupRun,
    PanelDeferred,
    centring_pass_verdict,
    set_aside_expiry,
)

#: An instant a set-aside was made at: any clock time does.
T = 1_800_000_000.0


def _run(n: int = 6) -> GroupRun:
    labels = ["1-1", "1-2", "1-3", "2-3", "2-2", "2-1"][:n]
    return GroupRun({f"p{i}": lab for i, lab in enumerate(labels)},
                    max_failed_visits=3)


def _miss() -> PanelDeferred:
    return PanelDeferred("centring failed", kind=CENTRING,
                         last_error="plate solve failed — used raw GoTo")


def _transient() -> PanelDeferred:
    return PanelDeferred("the centring solve could not run",
                         kind=SOLVE_TRANSIENT,
                         last_error="plate solve failed — used raw GoTo")


def _missed(run: GroupRun, *panels: str) -> None:
    for p in panels:
        run.visit_outcome(p, complete=False, exposures=0, accepted=0,
                          deferred=_miss())


def _shot(run: GroupRun, *panels: str) -> None:
    for p in panels:
        run.visit_outcome(p, complete=False, exposures=2, accepted=2)


def _strike_out(run: GroupRun, panel: str, *, beside: str) -> None:
    """Three passes in which ``panel`` misses and ``beside`` centres and
    shoots: three strikes, the third setting ``panel`` aside."""
    for _ in range(3):
        _missed(run, panel)
        _shot(run, beside)
        run.close_pass()
        run.start_pass()


# ------------------------------------------------------------ the constants

def test_the_ruling_s_three_numbers_are_named_constants():
    """10 degrees, 45 minutes and 10 minutes, as H4 orchestrator ruling 2
    gives them, each a named constant the engine imports rather than a
    literal it repeats."""
    assert (SET_ASIDE_RISE_DEG, SET_ASIDE_EXPIRY_S, CENTRING_HOLD_RETRY_S) == (
        10, 2700, 600)
    assert CENTRING_HOLD_RETRY_S == 2 * DEFER_WAIT_S


# ------------------------------------------------------ the expiry verdict

def test_a_centring_set_aside_expires_45_minutes_after_it_was_made():
    """The time half: not a moment before ``SET_ASIDE_EXPIRY_S``, and from
    that instant on. The CONTROL is the tenth of a second before it.

    RED under mutant "expiry never fires" (``set_aside_expiry`` answering
    None after its argument checks), observed:

        AssertionError: assert None == 'time'
    """
    assert set_aside_expiry(now=T + SET_ASIDE_EXPIRY_S - 0.1, set_at=T) is None
    assert set_aside_expiry(now=T + SET_ASIDE_EXPIRY_S, set_at=T) == "time"
    assert set_aside_expiry(now=T + 4 * 3600.0, set_at=T) == "time"


def test_a_panel_risen_10_degrees_expires_before_45_minutes():
    """The rise half: the panel centre climbed ``SET_ASIDE_RISE_DEG`` since it
    was set aside, past ``SET_ASIDE_RISE_FLOOR_S``, and it expires then; a
    tenth of a degree short, it holds (the CONTROL).

    RED under mutant "rise ignored" (the altitude comparison deleted from
    ``set_aside_expiry``), observed:

        AssertionError: assert None == 'rise'
    """
    past_floor = T + SET_ASIDE_RISE_FLOOR_S + 100.0
    assert set_aside_expiry(now=past_floor, set_at=T, alt_at_set=20.0,
                            alt_now=30.0) == "rise"
    assert set_aside_expiry(now=past_floor, set_at=T, alt_at_set=20.0,
                            alt_now=29.9) is None


def test_the_rise_half_never_fires_before_its_geometric_floor():
    """No latitude rises ``SET_ASIDE_RISE_DEG`` this fast (#564): even a
    huge, physically impossible rise argument does not free the panel before
    ``SET_ASIDE_RISE_FLOOR_S`` have passed. At the floor the same rise DOES
    free it (the CONTROL, showing the floor gates on time, not a second
    altitude rule), so an early "rise" answer can never come from anywhere
    but a caller handing in a bad altitude pair.

    RED under mutant "floor dropped" (the
    ``t >= t0 + SET_ASIDE_RISE_FLOOR_S`` gate deleted from the rise half of
    ``set_aside_expiry``), observed:

        AssertionError: assert 'rise' is None
    """
    assert set_aside_expiry(now=T + SET_ASIDE_RISE_FLOOR_S - 0.1, set_at=T,
                            alt_at_set=0.0, alt_now=90.0) is None
    assert set_aside_expiry(now=T + SET_ASIDE_RISE_FLOOR_S, set_at=T,
                            alt_at_set=0.0, alt_now=90.0) == "rise"


def test_a_setting_panel_never_expires_by_the_rise():
    """CONTROL: a panel that has sunk since it was set aside has not left
    what hid it by climbing; only the time half can free it."""
    assert set_aside_expiry(now=T + 1800.0, set_at=T, alt_at_set=40.0,
                            alt_now=25.0) is None
    assert set_aside_expiry(now=T + SET_ASIDE_EXPIRY_S, set_at=T,
                            alt_at_set=40.0, alt_now=25.0) == "time"


def test_with_no_site_only_the_time_half_applies():
    """No site saved means no altitude: the rule takes the time half alone,
    and one altitude without the other is no altitude."""
    assert set_aside_expiry(now=T + 1800.0, set_at=T) is None
    assert set_aside_expiry(now=T + 1800.0, set_at=T, alt_at_set=20.0,
                            alt_now=None) is None
    assert set_aside_expiry(now=T + 1800.0, set_at=T, alt_at_set=None,
                            alt_now=35.0) is None
    assert set_aside_expiry(now=T + SET_ASIDE_EXPIRY_S, set_at=T) == "time"


def test_at_most_one_expiry_per_panel_per_night():
    """A panel whose set-aside expired once tonight is set aside for the rest
    of the night the second time, by the clock and by the rise alike: it
    failed again after the sky had its chance to change.

    RED under mutant "second expiry allowed" (the ``expiries >= 1`` check
    deleted), observed:

        AssertionError: assert 'time' is None
    """
    assert set_aside_expiry(now=T + 5 * 3600.0, set_at=T, expiries=1) is None
    assert set_aside_expiry(now=T + 600.0, set_at=T, alt_at_set=10.0,
                            alt_now=60.0, expiries=1) is None
    assert set_aside_expiry(now=T + SET_ASIDE_EXPIRY_S, set_at=T,
                            expiries=0) == "time"


def test_the_time_half_wins_when_both_hold():
    """Both halves true: the answer is the one that is not site-derived, so
    a line saying why the set-aside expired says nothing about the site."""
    assert set_aside_expiry(now=T + SET_ASIDE_EXPIRY_S, set_at=T,
                            alt_at_set=10.0, alt_now=40.0) == "time"


@pytest.mark.parametrize("kw", [
    {"now": math.nan, "set_at": T},
    {"now": T, "set_at": math.inf},
    {"now": T, "set_at": T, "alt_at_set": 10.0, "alt_now": math.nan},
    {"now": T, "set_at": T, "expiries": -1},
    {"now": T, "set_at": T, "expiries": True},
])
def test_the_expiry_refuses_what_is_not_a_number(kw):
    """A NaN compares false every way, so it would hold or free a panel
    silently: a caller bug, said as one."""
    with pytest.raises(ValueError):
        set_aside_expiry(**kw)


# -------------------------------------------------- the centring pass rule

@pytest.mark.parametrize(
    ("attempted", "failed", "expected"),
    [
        pytest.param(0, 0, "panel", id="no attempt"),
        pytest.param(1, 1, "panel", id="1 of 1 is the panel's"),
        pytest.param(2, 1, "panel", id="1 of 2"),
        pytest.param(2, 2, "sky", id="2 of 2 is the sky"),
        pytest.param(6, 6, "sky", id="6 of 6"),
        pytest.param(6, 5, "panel", id="5 of 6"),
    ],
)
def test_centring_pass_verdict_table(attempted, failed, expected):
    """At least two panels tried and every one missed is the sky's or the
    geometry's fault; anything less is a panel's. The guide-start rule's
    table (tests/test_group_rules.py), for the hop's first check."""
    assert centring_pass_verdict(attempted, failed) == expected


def test_centring_pass_verdict_refuses_more_misses_than_attempts():
    with pytest.raises(ValueError):
        centring_pass_verdict(1, 2)
    with pytest.raises(ValueError):
        centring_pass_verdict(-1, 0)


def test_a_centring_miss_is_counted_where_the_pass_closes():
    """A miss beside a panel that centred is the missing panel's, and is
    counted, but not at its visit: at the boundary, where the whole pass
    says whose fault it was. The boundary names the count it made, which
    the visit's own line said before (``counted``).

    RED under mutant "centring deferrals counted inline" (``visit_outcome``'s
    held-centring branch deleted, so a miss falls through to the count, as
    every miss did before H4), observed:

        AssertionError: held until the pass says whose fault it is
        assert 1 == 0
    """
    run = _run(3)
    _missed(run, "p0")
    assert run.failed["p0"] == 0, "held until the pass says whose fault it is"
    _shot(run, "p1", "p2")
    end = run.close_pass()
    assert end.boundary == "next_pass"
    assert run.failed == {"p0": 1, "p1": 0, "p2": 0}
    assert end.counted == (
        ("p0", "centring failed on 1-1: plate solve failed — used raw GoTo; "
               "retried on the next pass (1 of 3 consecutive)"),)
    assert end.set_aside == ()


def _guide_fail() -> PanelDeferred:
    return PanelDeferred("guiding did not start", kind=GUIDE_START,
                         last_error="no guide star found")


def test_the_guide_start_reason_names_a_centring_miss_counted_beside_it():
    """#575: p1 and p2 centre and then fail to start guiding (the rig's
    fault, both attempted and both failed), while p0 misses centring in the
    SAME pass, held and counted before the guide-start verdict is reached
    (``close_pass`` counts ``held_centring`` first). The guiding_action
    reason must not claim "no panel's failure count moved": p0's did, and
    the reason names it instead of contradicting the info line the caller
    logs for ``counted`` beside this one.

    RED under mutant "the old blanket claim restored" (``also`` replaced by
    the unconditional "No panel's failure count moved"), observed:

        AssertionError: assert "No panel's failure count moved" not in (
        "guiding did not start on any of the 2 panels tried this pass: the
        guider's fault, not a panel's. No panel's failure count moved; the
        plan's guiding_action decides")
    """
    run = _run(3)
    _missed(run, "p0")
    for p in ("p1", "p2"):
        run.visit_outcome(p, complete=False, exposures=0, accepted=0,
                          deferred=_guide_fail())
    end = run.close_pass()
    assert end.boundary == "guiding_action"
    assert end.counted == (
        ("p0", "centring failed on 1-1: plate solve failed — used raw GoTo; "
               "retried on the next pass (1 of 3 consecutive)"),)
    assert "No panel's failure count moved" not in end.reason, end.reason
    assert ("1 panel in the same pass had a centring miss counted or set "
           "aside, charged to the panel, not the guider") in end.reason, (
        end.reason)


def test_the_guide_start_reason_names_nothing_when_nothing_moved():
    """CONTROL: no centring miss in the pass, so the reason still says
    nothing moved, worded to be true either way rather than a blanket claim
    that happens to hold here."""
    run = _run(2)
    for p in ("p0", "p1"):
        run.visit_outcome(p, complete=False, exposures=0, accepted=0,
                          deferred=_guide_fail())
    end = run.close_pass()
    assert end.boundary == "guiding_action"
    assert end.counted == () and end.set_aside == ()
    assert "no panel's centring miss was counted in the same pass" in (
        end.reason), end.reason


def test_a_pass_in_which_every_panel_misses_strikes_none_and_holds():
    """A 3x2 whose six panels all miss in a pass: the sky or the geometry,
    not the panels. No counter moves, the boundary is the centring hold, and
    the hold is ``CENTRING_HOLD_RETRY_S`` long. Three such passes set nothing
    aside, which is the point: an early hour behind the trees no longer costs
    the night.

    RED under mutant "centring deferrals counted inline": the boundary
    still reads the pass as the sky's (its ledger is kept either way), but
    the visits counted each miss already, observed:

        AssertionError: assert {'p0': 1, 'p1... 'p3': 1, ...} == {'p0': 0,
        'p1... 'p3': 0, ...}
    """
    run = _run(6)
    for n in range(3):
        _missed(run, *run.members)
        end = run.close_pass()
        assert end.boundary == "centring_hold"
        assert (end.set_aside, end.counted) == ((), ())
        assert "no panel's failure count moved" in end.reason
        assert run.failed == {p: 0 for p in run.members}
        until = run.defer_next_pass(T + n, wait_s=CENTRING_HOLD_RETRY_S,
                                    why="centring")
        assert (until, run.defer_why) == (T + n + 600.0, "centring")
        run.start_pass()
    assert run.set_aside == {}


def test_a_pass_in_which_one_panel_centres_strikes_the_others_as_before():
    """CONTROL: 1-1 centres and shoots, the five others miss. That is five
    panels' faults, each counted, the pass goes straight on (it shot), and at
    the third such pass the five are set aside, each as a CENTRING set-aside,
    the kind that may expire."""
    run = _run(6)
    for _ in range(3):
        _shot(run, "p0")
        _missed(run, "p1", "p2", "p3", "p4", "p5")
        end = run.close_pass()
        assert end.boundary == "next_pass"
        run.start_pass()
    assert [p for p, _r in end.set_aside] == ["p1", "p2", "p3", "p4", "p5"]
    assert end.set_aside[0][1] == ("centring failed on 1-2 on 3 consecutive "
                                   "visits: plate solve failed — used raw GoTo")
    assert {run.set_aside_kind[p] for p in ("p1", "p2", "p3", "p4", "p5")} == {
        CENTRING}
    assert run.live() == ["p0"]


def test_a_lone_attempted_miss_is_the_panels_and_waits():
    """CONTROL: one panel could be visited this pass (the others held,
    unvisited) and it missed. One miss out of one attempt proves nothing
    about the sky: it is counted, and the pass of no exposures waits
    ``DEFER_WAIT_S``, as today."""
    run = _run(6)
    _missed(run, "p0")
    end = run.close_pass()
    assert end.boundary == "defer_wait"
    assert run.failed["p0"] == 1


def test_only_a_streak_of_centring_misses_expires():
    """The kind a set-aside records decides whether it may expire: three
    centring misses is ``"centring"``; a streak with a pier refusal in it is
    not, and neither is the reject rule's set-aside."""
    run = _run(3)
    _strike_out(run, "p0", beside="p1")
    assert run.set_aside_kind == {"p0": CENTRING}

    run = _run(3)
    for kind in (CENTRING, "pier_side", CENTRING):
        if kind == CENTRING:
            _missed(run, "p0")
        else:
            run.visit_outcome("p0", complete=False, exposures=0, accepted=0,
                              deferred=PanelDeferred("the mount chose the other "
                                                     "pier side",
                                                     kind="pier_side"))
        _shot(run, "p1")
        run.close_pass()
        run.start_pass()
    assert run.set_aside_kind == {"p0": "deferred"}

    run = _run(2)
    for _ in range(3):
        _shot(run, "p1")
        run.visit_outcome("p0", complete=False, exposures=2, accepted=0)
    assert run.set_aside_kind == {"p0": "rejects"}


# ------------------------------------------------------- expire_set_aside

def test_an_expired_panel_is_live_again_with_a_clean_slate():
    """After its expiry the panel is live, unvisited, and needs three more
    strikes to be set aside again, never one. Only a centring set-aside
    expires, and only a panel that is set aside."""
    run = _run(3)
    _strike_out(run, "p0", beside="p1")
    assert not run.is_live("p0")
    run.expire_set_aside("p0")
    assert run.is_live("p0") and "p0" not in run.visited
    assert (run.failed["p0"], run.expired) == (0, {"p0"})
    for n in range(3):
        _missed(run, "p0")
        _shot(run, "p1")
        end = run.close_pass()
        run.start_pass()
        assert run.is_live("p0") == (n < 2)
    assert [p for p, _r in end.set_aside] == ["p0"]

    with pytest.raises(ValueError, match="not set aside"):
        run.expire_set_aside("p1")
    run.set_aside_panel("p1", "1-2 sank below its own altitude floor",
                        kind="floor")
    with pytest.raises(ValueError, match="only a centring set-aside"):
        run.expire_set_aside("p1")


def test_an_expired_panel_starts_its_reject_count_again():
    """#580: the docstring names three resets on expiry (``failed``,
    ``reject_visits`` and the streak's kinds), but only ``failed``'s and the
    streak's were ever graded. A panel can carry rejected visits INTO a
    centring streak (a reject counts toward neither ``failed`` nor the
    streak, #580's evidence), so p0 reaches its expiry holding 2 rejects
    already, and if the reset dropped, one rejected visit after the expiry
    would strike it out at once, never the clean slate the docstring
    promises.

    RED under mutant "G2" (the ``self.reject_visits[panel] = 0`` line
    deleted from ``expire_set_aside``), observed:

        AssertionError: one rejected visit after an expiry set the panel
        aside: '1-1 rejected every frame for 3 visits while the other
        panels were accepted' ('rejects')
    """
    run = _run(2)
    for _ in range(2):
        _shot(run, "p1")
        run.visit_outcome("p0", complete=False, exposures=2, accepted=0)
        run.close_pass()
        run.start_pass()
    assert run.reject_visits["p0"] == 2, "premise: two rejected visits"
    _strike_out(run, "p0", beside="p1")
    assert run.set_aside_kind["p0"] == "centring", "premise"
    assert run.reject_visits["p0"] == 2, "premise: centring misses keep it"
    run.expire_set_aside("p0")
    _shot(run, "p1")
    run.visit_outcome("p0", complete=False, exposures=2, accepted=0)
    assert run.is_live("p0"), (
        f"one rejected visit after an expiry set the panel aside: "
        f"{run.set_aside.get('p0')!r} ({run.set_aside_kind.get('p0')!r})")
    assert run.reject_visits["p0"] == 1


def test_a_panel_that_expires_alone_starts_a_pass_of_its_own():
    """1-1 completes and 1-2 is struck out: the boundary answers
    ``none_live`` and starts no pass. When 1-2's set-aside expires it comes
    back into a pass of its own, so its lone miss is judged on that pass: no
    exposures, one deferral, a ``DEFER_WAIT_S`` wait before it is tried
    again, and never the old pass's frames, which would send it straight
    back for its next strike.

    RED under mutant "no fresh pass after a lone expiry" (the ``alone``
    branch of ``expire_set_aside`` deleted), observed:

        assert 3 == (3 + 1)
         +  where 3 = <astrodeck.sequence.group_rules.GroupRun object at
        0x00000153F537F830>.pass_no
    """
    run = _run(2)
    for _ in range(2):
        _missed(run, "p1")
        _shot(run, "p0")
        run.close_pass()
        run.start_pass()
    _missed(run, "p1")
    run.visit_outcome("p0", complete=True, exposures=2, accepted=2)
    end = run.close_pass()
    assert end.boundary == "none_live"
    before = run.pass_no
    run.expire_set_aside("p1")
    assert run.pass_no == before + 1
    _missed(run, "p1")
    end = run.close_pass()
    assert end.boundary == "defer_wait"
    assert run.failed["p1"] == 1


def test_a_pass_still_holding_centring_misses_cannot_be_skipped():
    """``start_pass`` before ``close_pass`` would drop the held misses
    uncounted, as it would the guide rule's."""
    run = _run(3)
    _missed(run, "p0")
    with pytest.raises(RuntimeError, match="close_pass"):
        run.start_pass()


def test_the_deferral_wait_says_which_wait_it_is():
    """The two waits are one mechanism: ``defer_next_pass`` with its default
    is the all-deferred wait, and says so, whatever the last wait was."""
    run = _run(2)
    run.defer_next_pass(T, wait_s=CENTRING_HOLD_RETRY_S, why="centring")
    assert (run.defer_until, run.defer_why) == (T + 600.0, "centring")
    run.defer_next_pass(T + 1000.0)
    assert (run.defer_until, run.defer_why) == (T + 1300.0, "deferred")
    with pytest.raises(ValueError):
        run.defer_next_pass(T, why="clouds")


# ---------------------------------------------------- a solve that never ran

def test_a_solve_that_could_not_run_counts_toward_nothing():
    """#532: 1-1's centring solve could not run (another process held its
    file), every other panel missed. The transient visit is no attempt and
    no failure: the other five are still every panel TRIED, so the pass is
    the sky's, and 1-1's count never moves however often it happens. It is
    still a deferral for the boundary: a pass of nothing else waits and
    tries again, never reads as a pass that took nothing.

    RED under mutant "transient counted" (``visit_outcome``'s transient
    branch deleted, so the deferral falls through to the count), observed:

        AssertionError: assert ('requeue' == 'requeue'
          requeue and 'not counted' in 'the centring solve could not run on
        1-1: plate solve failed — used raw GoTo; retried on the next pass (1
        of 3 consecutive)')
    """
    run = _run(6)
    for _ in range(4):
        act = run.visit_outcome("p0", complete=False, exposures=0, accepted=0,
                                deferred=_transient())
        assert act.action == "requeue" and "not counted" in act.reason
        _missed(run, "p1", "p2", "p3", "p4", "p5")
        end = run.close_pass()
        assert end.boundary == "centring_hold"
        assert run.failed == {p: 0 for p in run.members}
        run.start_pass()
    assert run.set_aside == {}

    run = _run(2)
    for p in run.members:
        run.visit_outcome(p, complete=False, exposures=0, accepted=0,
                          deferred=_transient())
    assert run.deferred_this_pass == 2
    assert run.close_pass().boundary == "defer_wait"


def test_a_transient_beside_one_miss_leaves_that_miss_the_panels():
    """CONTROL on the other side: 1-1 transient, 1-2 missed. One panel tried
    and it missed: the panel's, counted, not a hold."""
    run = _run(2)
    run.visit_outcome("p0", complete=False, exposures=0, accepted=0,
                      deferred=_transient())
    _missed(run, "p1")
    end = run.close_pass()
    assert end.boundary == "defer_wait"
    assert run.failed == {"p0": 0, "p1": 1}


def test_the_new_kind_is_a_known_deferral_kind():
    """A closed set: a misspelt kind is refused, so the rule that matches on
    a spelling cannot silently miss one."""
    assert {CENTRING, SOLVE_TRANSIENT} <= group_rules.DEFERRAL_KINDS
    with pytest.raises(ValueError):
        PanelDeferred("the solve could not run", kind="solve-transient")
