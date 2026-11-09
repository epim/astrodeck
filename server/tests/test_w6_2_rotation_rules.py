# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""WP 6.2 (#180 part B, owner ruling D-E, 2026-10-22): a mosaic panel that
fails is skipped for the next panel and revisited every round. It is never
dropped and never demoted for good.

THESE TESTS ARE A DELIBERATE BEHAVIOUR CHANGE. Until D-E a panel that failed
``max_failed_visits`` visits in a row was set aside for the night (or, for a
centring streak, parked once for 45 minutes). With ``GroupRun(rotate=True)``,
which the engine passes for a mosaic and never for a pool, the panel stays
live: the bound now means "skipped", the panel goes behind the others in the
next round's order, and the first visit that banks a frame ends it.

The pure decisions are pinned here; the clocked night is in
test_w6_2_failed_panel_rotates.py. Each test names the mutant it kills.
"""
from __future__ import annotations

import pytest

from astrodeck.sequence.group_rules import (
    HELD_PASS_ALERT_AT,
    HELD_PASS_SET_ASIDE_AT,
    GroupRun,
    PanelDeferred,
)
from astrodeck.sequence.panel_order import OrderSnapshot, order_panels
from astrodeck.sequence.session import Session


class _P:
    """A group member as the order reads it."""

    def __init__(self, pid: str, row: int, col: int):
        self.id, self.panel_row, self.panel_col = pid, row, col


def _run(n=3, max_failed=3, rotate=True) -> GroupRun:
    labels = ["1-1", "1-2", "1-3", "2-3", "2-2", "2-1"][:n]
    return GroupRun({f"p{i}": lab for i, lab in enumerate(labels)},
                    max_failed_visits=max_failed, rotate=rotate)


def _pier(err="read east, wanted west"):
    """A deferral a visit counts at once (a centring miss or a guide start is
    held to the pass boundary instead)."""
    return PanelDeferred("the mount chose the other pier side",
                         kind="pier_side", last_error=err)


def _fail_round(run: GroupRun, panel: str, deferred: PanelDeferred):
    """One round in which ``panel`` fails and every other live panel banks a
    frame, so the pass is not held. Returns the failing panel's action."""
    for q in run.live():
        if q != panel:
            run.visit_outcome(q, complete=False, exposures=1, accepted=1)
    act = run.visit_outcome(panel, complete=False, exposures=0, accepted=0,
                            deferred=deferred)
    end = run.close_pass()
    run.start_pass()
    return act, end


def test_a_failed_panel_is_skipped_at_the_bound_and_never_set_aside():
    """The third consecutive failure of a rotating panel is a skip: the panel
    is still live, has no set-aside entry and is named in ``skipped()``; the
    fourth and later failures skip again.

    MUTANT "drop the panel after three failures" (the set-aside restored at
    ``max_failed_visits`` in ``_count_failure``: the ``if self.rotate:`` arm
    made ``if False:``). Observed: the panel is set aside at the third round
    and the next visit of it is refused,
        ValueError: 1-2 is not live: it is complete or set aside tonight
    """
    run = _run()
    acts = [_fail_round(run, "p1", _pier())[0] for _ in range(5)]
    assert [a.action for a in acts] == ["requeue", "requeue", "skip", "skip",
                                        "skip"]
    assert run.set_aside == {} and run.set_aside_kind == {}
    assert run.is_live("p1")
    assert run.skipped() == frozenset({"p1"})
    assert run.skip_kind == {"p1": "deferred"}
    assert "1-2" in acts[2].reason and "3 consecutive" in acts[2].reason
    assert "tried again in the next" in acts[2].reason


def test_control_a_run_that_does_not_rotate_still_drops_at_the_bound():
    """Pools keep today's rule: ``rotate`` defaults to False.

    MUTANT "rotate on by default" (``rotate: bool = True``). Observed:
        assert True is False
         +  where True = <...GroupRun object>.rotate
    """
    run = GroupRun({"p0": "1-1", "p1": "1-2", "p2": "1-3"},
                   max_failed_visits=3)
    assert run.rotate is False
    acts = [_fail_round(run, "p1", _pier())[0] for _ in range(3)]
    assert acts[2].action == "set_aside"
    assert "p1" in run.set_aside and run.skipped() == frozenset()


def test_a_skipped_panel_that_banks_a_frame_leaves_the_rotation():
    """A panel that fails then succeeds banks its frames and is no longer
    skipped, and its failure count starts again.

    MUTANT "skip_kind not cleared by a banked frame" (the ``skip_kind.pop``
    in ``visit_outcome``'s accepted branch deleted). Observed:
        AssertionError: assert (frozenset({'p1'}) == frozenset() ...
    """
    run = _run()
    for _ in range(4):
        _fail_round(run, "p1", _pier())
    assert run.skipped() == frozenset({"p1"})
    run.visit_outcome("p1", complete=False, exposures=1, accepted=1)
    assert run.skipped() == frozenset() and run.skip_kind == {}
    assert run.failed["p1"] == 0


def test_a_skipped_panel_a_group_rule_sets_aside_is_no_longer_skipped():
    """``skipped()`` is of LIVE panels: a panel the group's rules then set
    aside (D-03's held passes, a rig verdict) or that completes is out of
    it, so the next order never ranks a panel that is not in the rotation.

    MUTANT "skipped() ignores liveness" (``frozenset(self.skip_kind)``).
    Observed:
        AssertionError: assert frozenset({'p1'}) == frozenset()
    """
    run = _run()
    for _ in range(3):
        _fail_round(run, "p1", _pier())
    assert run.skipped() == frozenset({"p1"})
    run.set_aside_all("fog", kind="group")
    assert run.skipped() == frozenset()


def test_a_held_centring_streak_is_skipped_where_the_pass_closes():
    """A centring miss beside a panel that centred is the panel's, counted at
    the pass boundary: at the bound the PassEnd names it in ``skipped`` and
    sets nothing aside; the kind is "centring" (a streak of nothing else).

    MUTANT "PassEnd.skipped never filled" (the held-centring loop's ``elif
    act.action == "skip"`` arm deleted, so the skip lands in ``counted``).
    Observed:
        AssertionError: assert [] == ['p1']
         Right contains one more item: 'p1'
    """
    run = _run()
    last = None
    for _ in range(3):
        run.visit_outcome("p0", complete=False, exposures=1, accepted=1)
        run.visit_outcome("p1", complete=False, exposures=0, accepted=0,
                          deferred=PanelDeferred("did not centre",
                                                 kind="centring",
                                                 last_error="4 arcmin off"))
        last = run.close_pass()
        run.start_pass()
    assert last is not None
    assert [p for p, _r in last.skipped] == ["p1"]
    assert last.set_aside == ()
    assert run.skip_kind == {"p1": "centring"}
    assert run.set_aside == {}


def test_a_held_guide_start_streak_is_skipped_with_its_own_kind():
    """Three failed guide starts beside a start that worked: kind
    "guide_start" (the word Part A names), no set-aside.

    MUTANT "streak kind always deferred" (``kind = "deferred"``). Observed:
        AssertionError: assert ({'p1': 'deferred'} == {'p1': 'guide_start'} ...
    """
    run = _run()
    for _ in range(3):
        run.visit_outcome("p0", complete=False, exposures=1, accepted=1,
                          guide_started=True)
        run.visit_outcome("p1", complete=False, exposures=0, accepted=0,
                          deferred=PanelDeferred("guiding did not start",
                                                 kind="guide_start",
                                                 last_error="no star"))
        end = run.close_pass()
        run.start_pass()
    assert [p for p, _r in end.skipped] == ["p1"]
    assert run.skip_kind == {"p1": "guide_start"} and run.set_aside == {}


def test_a_panel_rejecting_while_others_are_accepted_is_skipped():
    """The reject rule rotates too: at the bound the panel is skipped, kind
    "rejects", and is not set aside.

    MUTANT "reject rule still sets aside for a rotating run" (``if False:``
    for the rotate arm of the reject rule). Observed:
        AssertionError: assert ['requeue', '..., 'set_aside'] == ['requeue',
        'requeue', 'skip']
    """
    run = _run()
    acts = []
    for _ in range(3):
        run.visit_outcome("p0", complete=False, exposures=1, accepted=1)
        acts.append(run.visit_outcome("p1", complete=False, exposures=2,
                                      accepted=0))
        run.close_pass()
        run.start_pass()
    assert [a.action for a in acts] == ["requeue", "requeue", "skip"]
    assert run.skip_kind == {"p1": "rejects"} and run.set_aside == {}


def test_a_round_in_which_every_live_panel_failed_is_a_held_pass():
    """The group-level extension: one live panel, every round a failure, no
    frame banked. Each round is held (the streak counts, no reason code), the
    alert is due at ``HELD_PASS_ALERT_AT`` and the group is set aside at
    ``HELD_PASS_SET_ASIDE_AT``, kind "deferred" for a last live panel.

    MUTANT "extension dropped" (``is_held = all_transient``). Observed:
        AssertionError: assert [0, 0, 0, 0, 0, 0] == [1, 2, 3, 4, 5, 0]
    """
    run = _run(n=1)
    streaks = []
    for _ in range(HELD_PASS_SET_ASIDE_AT):
        run.visit_outcome("p0", complete=False, exposures=0, accepted=0,
                          deferred=_pier())
        end = run.close_pass()
        streaks.append(end.held_streak)
        if end.boundary == "set_aside_all":
            break
        assert end.boundary == "defer_wait"
        run.start_pass()
    assert streaks == [1, 2, 3, 4, 5, 0]
    assert HELD_PASS_ALERT_AT in streaks
    assert end.boundary == "set_aside_all"
    assert run.set_aside_kind == {"p0": "deferred"}


def test_control_the_same_rounds_are_not_held_without_rotation():
    """A pool (rotate False) keeps the all-transient rule only.

    MUTANT "extension ignores rotate" (``every_live_failed`` without the
    ``self.rotate and``). Observed:
        AssertionError: assert 1 == 0
         +  where 1 = PassEnd(boundary='defer_wait', ..., held_streak=1,
        ...).held_streak
    """
    run = _run(n=2, max_failed=9, rotate=False)
    run.visit_outcome("p0", complete=False, exposures=0, accepted=0,
                      deferred=_pier())
    run.visit_outcome("p1", complete=False, exposures=0, accepted=0,
                      deferred=_pier())
    assert run.close_pass().held_streak == 0


def test_a_live_panel_the_pass_never_reached_keeps_the_round_unheld():
    """Not EVERY live panel failed: one was not visited this pass (held back
    by the meridian rule, say). The pass stays a plain defer_wait.

    MUTANT "any failed panel makes the round held" (``bool(self.
    _deferred_panels)`` for the subset test). Observed:
        AssertionError: assert ('defer_wait' == 'defer_wait' and 1 == 0)
    """
    run = _run(n=2)
    run.visit_outcome("p0", complete=False, exposures=0, accepted=0,
                      deferred=_pier())
    end = run.close_pass()
    assert end.boundary == "defer_wait" and end.held_streak == 0


def test_a_restart_restores_a_skip_so_the_panel_is_already_behind():
    """``restore_skip`` puts back what a same-night restart read from the
    session: the count and the kind. The next failure is a skip, not the
    first of three.

    MUTANT "restore_skip restores nothing" (only the argument check kept).
    Observed:
        AssertionError: assert frozenset() == frozenset({'p1'})
    """
    run = _run()
    run.restore_skip("p1", 5, "centring")
    assert run.skipped() == frozenset({"p1"})
    assert run.failed["p1"] == 5 and run.skip_kind == {"p1": "centring"}
    act = run.visit_outcome("p1", complete=False, exposures=0, accepted=0,
                            deferred=_pier())
    assert act.action == "skip"


def test_restore_skip_refuses_a_stranger():
    with pytest.raises(ValueError):
        _run().restore_skip("nobody", 3, "deferred")


# ----------------------------------------------------------------- the order

@pytest.mark.parametrize("policy", ["least_complete", "setting_first", "grid"])
def test_a_skipped_panel_goes_last_under_every_policy(policy):
    """The panel that has failed its bound goes behind every panel that has
    not, whatever the policy and however little of it is banked; every panel
    still comes out exactly once.

    MUTANT "skipped key dropped from order_panels" (``return
    policy_key(p)``). Observed (least_complete):
        AssertionError: assert ('p10' == 'p00' ...
    """
    panels = [_P("p00", 0, 0), _P("p01", 0, 1), _P("p11", 1, 1), _P("p10", 1, 0)]
    snap = OrderSnapshot(fraction_banked={"p00": 0.0, "p01": 0.5, "p11": 0.5,
                                          "p10": 0.5},
                         skipped=frozenset({"p00"}))
    got = [p.id for p in order_panels(panels, cols=2, policy=policy,
                                      snapshot=snap)]
    assert got[-1] == "p00" and sorted(got) == ["p00", "p01", "p10", "p11"]


def test_with_nothing_skipped_the_order_is_what_it_was():
    """The new field is empty by default: the first panel by the policy is
    still the least complete.

    MUTANT "every panel treated as skipped" would still pass this; the
    control is for the default snapshot.
    """
    panels = [_P("p00", 0, 0), _P("p01", 0, 1)]
    snap = OrderSnapshot(fraction_banked={"p00": 0.0, "p01": 0.5})
    assert [p.id for p in order_panels(panels, cols=2, snapshot=snap)] == [
        "p00", "p01"]


# --------------------------------------------------------------- the session

def _session(nights=()) -> Session:
    return Session(id="s", nights=list(nights))


def test_a_skip_record_is_one_per_panel_per_night_and_upserted():
    """``note_panel_skipped`` keeps the newest count and kind for a (panel,
    night), and ``panel_skips_on`` reads one night's.

    MUTANT "append instead of upsert" (the match test made ``if False:``).
    Observed:
        AssertionError: assert 2 == 1
    """
    s = _session()
    s.note_panel_skipped("t1", night="2026-11-01", kind="centring", visits=3,
                         ts=100.0)
    s.note_panel_skipped("t1", night="2026-11-01", kind="deferred", visits=4,
                         ts=200.0)
    s.note_panel_skipped("t1", night="2026-11-02", kind="centring", visits=3,
                         ts=300.0)
    on = s.panel_skips_on("2026-11-01")
    assert len(on) == 1
    assert (on[0]["kind"], on[0]["visits"], on[0]["ts"]) == ("deferred", 4, 200.0)
    with pytest.raises(ValueError):
        s.note_panel_skipped("t1", night="", kind="centring", visits=3, ts=1.0)
    with pytest.raises(ValueError):
        s.note_panel_skipped("t1", night="2026-11-01", kind="centring",
                             visits=3, ts=float("nan"))


def _rid(day: int) -> str:
    return f"mosaic-202607{day:02d}-210000"


def _key(day: int) -> str:
    import time
    from astrodeck.events import night_key
    return night_key(time.mktime((2026, 7, day, 21, 0, 0, 0, 0, -1)))


def test_part_a_names_a_panel_skipped_on_three_nights_with_no_frame():
    """Part A under rotation: the panel is never set aside, so the streak is
    read from skip records. Three nights, each a skip record and no banked
    frame, is a streak of three, with the skip's kind.

    MUTANT "set_aside_streak ignores panel_skips" (``if not records or
    night in banked``). Observed:
        AssertionError: assert (0, None) == (3, 'centring')
    """
    s = _session([_rid(d) for d in (14, 15, 16)])
    for d in (14, 15, 16):
        s.note_panel_skipped("t1", night=_key(d), kind="centring", visits=3,
                             ts=1_790_000_000.0 + d)
    assert s.set_aside_streak("t1") == (3, "centring")


def test_a_night_with_a_banked_frame_ends_a_skip_streak():
    """A panel skipped on night 15 that nevertheless banked a frame that
    night (it recovered) ends the streak there.

    MUTANT "banked test dropped for skip records" (``if not (records or
    skips):``). Observed:
        AssertionError: assert (3, 'centring') == (1, 'centring')
    """
    from astrodeck.sequence.session import SessionFrame
    s = _session([_rid(d) for d in (14, 15, 16)])
    for d in (14, 15, 16):
        s.note_panel_skipped("t1", night=_key(d), kind="centring", visits=3,
                             ts=1_790_000_000.0 + d)
    s.frames.append(SessionFrame(night=_rid(15), target_id="t1", step_id="s1",
                                 auto_accepted=True))
    assert s.set_aside_streak("t1") == (1, "centring")


def test_a_skip_kind_outside_the_starving_kinds_is_not_starved():
    """A "rejects" skip names no cause Part A may state, as a rejects
    set-aside never did.

    MUTANT "any skip kind counts" (``"rejects"`` added to the kinds the
    skip list reads). Observed:
        AssertionError: assert (3, 'rejects') == (0, None)
    """
    s = _session([_rid(d) for d in (14, 15, 16)])
    for d in (14, 15, 16):
        s.note_panel_skipped("t1", night=_key(d), kind="rejects", visits=3,
                             ts=1_790_000_000.0 + d)
    assert s.set_aside_streak("t1") == (0, None)


def test_the_skip_kind_wins_over_a_group_word_on_the_same_night():
    """A lone guide-start panel is skipped (kind guide_start) and then ended
    by D-03's last-live-panel rule (kind deferred). Part A keeps naming the
    panel's own cause.

    MUTANT "newest set-aside record's kind first" (``(records or
    skips)[-1]``). Observed:
        AssertionError: assert (3, 'deferred') == (3, 'guide_start')
    """
    s = _session([_rid(d) for d in (14, 15, 16)])
    for d in (14, 15, 16):
        s.note_panel_skipped("t1", night=_key(d), kind="guide_start",
                             visits=3, ts=1_790_000_000.0 + d)
        s.note_set_aside("t1", "held", night=_key(d), kind="deferred",
                         ts=1_790_000_100.0 + d)
    assert s.set_aside_streak("t1") == (3, "guide_start")


def test_standing_skip_is_the_newest_record_with_no_frame_since():
    """What the Tonight note reads: the newest skip, unless the panel has
    banked an effective frame at or after it.

    MUTANT "standing_skip ignores frames" (``if False and any(...)``).
    Observed:
        AssertionError: assert {'kind': 'centring', 'night': '2026-07-14',
        'target_id': 't1', 'ts': 100.0, ...} is None
    """
    from astrodeck.sequence.session import SessionFrame
    s = _session([_rid(14)])
    s.note_panel_skipped("t1", night=_key(14), kind="centring", visits=3,
                         ts=100.0)
    got = s.standing_skip("t1")
    assert got is not None and got["visits"] == 3
    assert s.standing_skip("other") is None
    s.frames.append(SessionFrame(night=_rid(14), target_id="t1", step_id="s1",
                                 auto_accepted=True, ts=150.0))
    assert s.standing_skip("t1") is None


# ----------------------------------------------- the rig verdict and rotation

def _guide_fail():
    return PanelDeferred("guiding did not start", kind="guide_start",
                         last_error="no star")


def _rotation_of_two() -> GroupRun:
    """Four panels; p0 and p1 fail to start guiding for three rounds beside
    p2 and p3, which guide, and are skipped; p2 and p3 then complete."""
    run = _run(n=4)
    for _ in range(3):
        for q in ("p2", "p3"):
            run.visit_outcome(q, complete=False, exposures=1, accepted=1,
                              guide_started=True)
        for q in ("p0", "p1"):
            run.visit_outcome(q, complete=False, exposures=0, accepted=0,
                              deferred=_guide_fail())
        run.close_pass()
        run.start_pass()
    assert run.skipped() == frozenset({"p0", "p1"}), "premise"
    for q in ("p2", "p3"):
        run.visit_outcome(q, complete=True, exposures=1, accepted=1,
                          guide_started=True)
    run.close_pass()
    run.start_pass()
    return run


def test_a_pass_of_rotating_panels_only_is_not_the_rigs_fault():
    """Every panel ATTEMPTED in the pass is already skipped: the rig verdict
    ("every attempted panel failed to start guiding") does not apply, the
    boundary is a plain deferral wait and a held pass (D-03), and the panels
    stay skipped.

    MUTANT "drop the check" (``rotation_only`` ignored in
    ``_decide_pass_end``). Observed:
        AssertionError: assert 'guiding_action' == 'defer_wait'
    """
    run = _rotation_of_two()
    for q in ("p0", "p1"):
        run.visit_outcome(q, complete=False, exposures=0, accepted=0,
                          deferred=_guide_fail())
    end = run.close_pass()
    assert end.boundary == "defer_wait" and end.held_streak == 1, end
    assert run.skipped() == frozenset({"p0", "p1"})
    assert run.set_aside == {}


def test_a_pass_with_a_panel_not_in_the_rotation_is_judged_as_before():
    """One panel of the pass is NOT in the rotation (p2 has never failed):
    two attempted, both failed, the rig's fault as it always was.

    MUTANT "any skipped panel exempts the pass" (``rotation_only`` true when
    ANY attempted panel is skipped). Observed:
        AssertionError: assert 'defer_wait' == 'guiding_action'
    """
    run = _run(n=3)
    for _ in range(3):
        run.visit_outcome("p2", complete=False, exposures=1, accepted=1,
                          guide_started=True)
        run.visit_outcome("p0", complete=False, exposures=0, accepted=0,
                          deferred=_guide_fail())
        run.close_pass()
        run.start_pass()
    assert run.skipped() == frozenset({"p0"}), "premise"
    run.visit_outcome("p0", complete=False, exposures=0, accepted=0,
                      deferred=_guide_fail())
    run.visit_outcome("p1", complete=False, exposures=0, accepted=0,
                      deferred=_guide_fail())
    assert run.close_pass().boundary == "guiding_action"


def test_a_run_that_does_not_rotate_keeps_the_verdict():
    """Pools (a run that does not rotate) have no skipped panels, so the
    verdict is as it was. A control: nothing it guards is a mutant of its
    own."""
    run = _run(n=2, rotate=False)
    for q in ("p0", "p1"):
        run.visit_outcome(q, complete=False, exposures=0, accepted=0,
                          deferred=_guide_fail())
    assert run.close_pass().boundary == "guiding_action"
