# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The mosaic hold escalation (backlog ruling D-03, owner-approved
2026-09-30; #563 (a), #576 (b); WP-21 of the 2026-09-30 open-issue backlog).

THE DEFECT. Before this, neither way a pass can be held ever escalated.
#563: a pass in which every attempted panel fails centring (the "sky" verdict,
`centring_pass_verdict`) holds the mosaic `CENTRING_HOLD_RETRY_S` and tries
again -- forever, if the cause is on the rig and not in the sky, since no
panel is ever struck and nothing bounds the hold but the target's window.
#576: a pass whose every deferral was a solve that could not run
(SOLVE_TRANSIENT) closes as a plain ``defer_wait``, indistinguishable from an
ordinary mixed pass of deferrals -- so a stuck solve-frame lock re-slews
every panel every `DEFER_WAIT_S` all night with nothing to say anything is
wrong either.

D-03's RULING, verbatim: "Keep one counter per group of consecutive held
passes, with all-fail and all-transient passes both counting. Alert the
operator at 3 held passes (about 30 min). Set the group aside for the night
at 6, or immediately when every failure in two passes in a row carries the
same rig-side reason code." (Dropping the "at least two attempted" condition
is #591, WP-33 in wave 4 -- not built here, and not tested here.)

WHAT IT DOES NOW. `GroupRun.close_pass` feeds every pass through
`GroupRun._apply_held_pass_rule` (`HELD_PASS_ALERT_AT` = 3,
`HELD_PASS_SET_ASIDE_AT` = 6): a pass that is not held (it shot a frame, its
deferrals were not all one of the two held kinds, or a different hold -- the
guiding rig's -- is escalating its own way) resets the streak; a held pass
reports its running count on ``PassEnd.held_streak`` for the caller to warn
on, and the method itself escalates to a ``set_aside_all`` boundary at the
count or on a repeated reason. ``GENERIC_SOLVE_FAILURE`` -- the one message
the engine gives for ANY outright solve failure today, with no more specific
cause available -- is read as no reason code at all (CONTROL tests below),
since treating it as one would make every real-world all-fail centring hold
blame "the same reason" by the SECOND pass, collapsing the counted path
(3/6) the ruling also asks for.

PURE TESTS pin `GroupRun` directly (the style of
tests/test_h4_group_rules_centring.py); ONE engine-level test
(tests/_group_harness.py's `Night`) closes the loop end to end for the
all-transient path (#576), the one `test_h4_centring_group_hold.py` does not
cover.

Every mutant was applied to a byte-for-byte backup of
server/astrodeck/sequence/group_rules.py inside this worktree, restored and
sha256-checked after each (never left in the tree). The exact failing
assertion is quoted in the test it turns red, and in this work package's own
report.
"""
from __future__ import annotations

from _group_harness import (GROUP_NAME, Night, grid_plan,  # noqa: F401
                            group_hub, group_store)
from astrodeck.sequence.group_rules import (
    CENTRING,
    CENTRING_HOLD_RETRY_S,
    DEFER_WAIT_S,
    GENERIC_SOLVE_FAILURE,
    HELD_PASS_ALERT_AT,
    HELD_PASS_SET_ASIDE_AT,
    SOLVE_TRANSIENT,
    GroupRun,
    PanelDeferred,
)
from astrodeck.sequence.session import session_store


def _run(n: int = 2) -> GroupRun:
    labels = ["1-1", "1-2", "1-3", "2-3"][:n]
    return GroupRun({f"p{i}": lab for i, lab in enumerate(labels)},
                    max_failed_visits=3)


def _miss(reason: str = GENERIC_SOLVE_FAILURE) -> PanelDeferred:
    return PanelDeferred("centring failed", kind=CENTRING, last_error=reason)


def _transient(reason: str = GENERIC_SOLVE_FAILURE) -> PanelDeferred:
    return PanelDeferred("the centring solve could not run",
                         kind=SOLVE_TRANSIENT, last_error=reason)


def _all_missed(run: GroupRun, reason: str = GENERIC_SOLVE_FAILURE) -> None:
    """Every member of ``run`` fails centring this visit, all with ``reason``
    (the all-fail hold, #563)."""
    for p in run.members:
        run.visit_outcome(p, complete=False, exposures=0, accepted=0,
                          deferred=_miss(reason))


def _all_transient(run: GroupRun, reason: str = GENERIC_SOLVE_FAILURE) -> None:
    """Every member's solve could not run this visit, all with ``reason``
    (the all-transient pass, #576)."""
    for p in run.members:
        run.visit_outcome(p, complete=False, exposures=0, accepted=0,
                          deferred=_transient(reason))


def _shot(run: GroupRun, *panels: str) -> None:
    for p in panels:
        run.visit_outcome(p, complete=False, exposures=2, accepted=2)


# ---------------------------------------------------- the streak itself

def test_the_streak_counts_both_held_kinds_toward_one_counter():
    """D-03: "one counter ... with all-fail and all-transient passes both
    counting". Pass 1 is all-fail centring (#563's own hold); pass 2 is
    all-transient (#576's); pass 3 is all-fail again. The streak climbs
    1, 2, 3 across the two DIFFERENT boundaries -- it is one counter, not
    one per kind -- and 3 is exactly ``HELD_PASS_ALERT_AT``.

    RED under mutant "all-transient never held" (the ``is_held`` computed
    for the ``defer_wait`` branch of ``GroupRun._decide_pass_end`` hard-coded
    to ``False``), observed:

        AssertionError: assert ('defer_wait', 0) == ('defer_wait', 2)
          At index 1 diff: 0 != 2
    """
    run = _run(2)
    _all_missed(run)
    end = run.close_pass()
    assert (end.boundary, end.held_streak) == ("centring_hold", 1)
    run.start_pass()

    _all_transient(run)
    end = run.close_pass()
    assert (end.boundary, end.held_streak) == ("defer_wait", 2)
    run.start_pass()

    _all_missed(run)
    end = run.close_pass()
    assert (end.boundary, end.held_streak) == ("centring_hold",
                                               HELD_PASS_ALERT_AT)


def test_a_shot_pass_resets_the_streak():
    """Progress (a panel banks a frame) is not a held pass: the streak drops
    to 0, and the NEXT held pass starts over at 1, never climbing from where
    it left off.

    RED under mutant "the streak never resets" (the ``self.held_streak = 0``
    line in the ``not is_held`` branch of ``_apply_held_pass_rule``
    deleted), observed:

        AssertionError: assert 3 == 1
    """
    run = _run(2)
    _all_missed(run)
    assert run.close_pass().held_streak == 1
    run.start_pass()
    _all_missed(run)
    assert run.close_pass().held_streak == 2
    run.start_pass()

    _shot(run, "p0")
    _shot(run, "p1")
    end = run.close_pass()
    assert (end.boundary, end.held_streak) == ("next_pass", 0)
    run.start_pass()

    _all_missed(run)
    assert run.close_pass().held_streak == 1


def test_a_mixed_defer_wait_pass_is_not_held():
    """CONTROL: one panel's solve could not run, the other genuinely missed
    (counted, at once -- only one panel was TRIED, so `centring_pass_verdict`
    answers "panel", not "sky"). Neither all-fail nor all-transient: the
    streak the prior held pass built is dropped, not carried, and the
    missed panel's own failure count still moves -- a mixed pass is an
    ordinary deferral wait, exactly as it was before D-03.

    RED under mutant "any deferral is held" (``is_held`` in
    ``_decide_pass_end``'s ``defer_wait`` branch set to
    ``bool(self.deferred_this_pass)``, dropping the all-transient check),
    observed:

        AssertionError: assert ('defer_wait', 2) == ('defer_wait', 0)
          At index 1 diff: 2 != 0
    """
    run = _run(2)
    _all_missed(run)
    assert run.close_pass().held_streak == 1
    run.start_pass()

    run.visit_outcome("p0", complete=False, exposures=0, accepted=0,
                      deferred=_transient())
    run.visit_outcome("p1", complete=False, exposures=0, accepted=0,
                      deferred=_miss())
    end = run.close_pass()
    assert (end.boundary, end.held_streak) == ("defer_wait", 0)
    assert run.failed["p1"] == 1, "the one real miss still counts"


# --------------------------------------------------------- the 6-pass bound

def test_set_aside_for_the_night_at_six_held_passes():
    """D-03: "set the group aside for the night at 6". Six all-fail passes,
    the SAME generic reason every time (today's only real-world shape, see
    the CONTROL below) -- never the immediate same-reason path, only the
    count. The 6th pass's boundary becomes ``set_aside_all``, every member
    is set aside with the escalation's own words, and the streak resets (the
    group is done, not merely paused).

    RED under mutant "no 6-pass bound" (the
    ``streak >= HELD_PASS_SET_ASIDE_AT`` half of the escalation condition in
    ``_apply_held_pass_rule`` deleted, leaving only the same-reason half),
    observed:

        AssertionError: assert 'centring_hold' == 'set_aside_all'
    """
    run = _run(2)
    end = None
    for n in range(1, HELD_PASS_SET_ASIDE_AT + 1):
        _all_missed(run)
        end = run.close_pass()
        if n < HELD_PASS_SET_ASIDE_AT:
            assert (end.boundary, end.held_streak) == ("centring_hold", n), n
            run.start_pass()
    assert end.boundary == "set_aside_all"
    assert "6 passes in a row" in end.reason, end.reason
    assert run.live() == []
    assert run.held_streak == 0


# ------------------------------------------- the same-reason short-circuit

def test_two_passes_with_the_same_rig_side_reason_set_aside_at_once():
    """D-03: "...or immediately when every failure in two passes in a row
    carries the same rig-side reason code." Two held passes, short of 6,
    both every panel blaming the identical (non-generic) text: the group is
    set aside at once, naming the shared reason.

    RED under mutant "same-reason path removed" (the ``same_as_last``
    branch deleted from ``_apply_held_pass_rule``, leaving only the
    6-count), observed:

        AssertionError: assert 'centring_hold' == 'set_aside_all'
    """
    run = _run(2)
    _all_missed(run, "solver not found")
    assert run.close_pass().held_streak == 1
    run.start_pass()

    _all_missed(run, "solver not found")
    end = run.close_pass()
    assert end.boundary == "set_aside_all"
    assert "'solver not found'" in end.reason, end.reason
    assert run.live() == []
    assert run.held_streak == 0


def test_two_passes_with_different_reasons_do_not_set_aside_early():
    """CONTROL: two held passes in a row, but NOT the same reason -- the
    sky changed between them, or two different rig faults took turns. The
    streak climbs normally; nothing is set aside before 6."""
    run = _run(2)
    _all_missed(run, "solver not found")
    assert run.close_pass().held_streak == 1
    run.start_pass()

    _all_missed(run, "camera disconnected")
    end = run.close_pass()
    assert (end.boundary, end.held_streak) == ("centring_hold", 2)


def test_two_passes_with_the_generic_reason_do_not_set_aside_early():
    """CONTROL -- and the reason ``GENERIC_SOLVE_FAILURE`` is excluded
    (module docstring): today's engine gives this SAME text for any
    outright solve failure, rig or sky, with no more specific cause. Two,
    or many, held passes all blaming it are read as D-03's counted path
    (3/6), never the immediate one -- else the immediate path would fire on
    the SECOND pass of nearly every real all-fail hold, which is exactly
    what tests/test_h4_group_rules_centring.py and
    tests/test_h4_centring_group_hold.py's own multi-pass holds already do.

    RED under mutant "the generic text counts as a reason code" (the
    ``errors[0] != GENERIC_SOLVE_FAILURE`` guard deleted from
    ``_held_pass_reason_code``), observed here:

        AssertionError: 2
        assert ('set_aside_all', 0) == ('centring_hold', 2)
          At index 0 diff: 'set_aside_all' != 'centring_hold'

    and, confirming this guard is exactly what the module docstring says it
    protects, the SAME mutant also turns four PRE-EXISTING tests red
    (tests/test_h4_group_rules_centring.py's
    ``test_a_pass_in_which_every_panel_misses_strikes_none_and_holds`` and
    ``test_a_solve_that_could_not_run_counts_toward_nothing``,
    tests/test_h4_centring_group_hold.py's
    ``test_a_pass_of_misses_on_every_panel_strikes_none_and_holds`` and
    ``test_the_hold_ends_with_the_window_and_sets_nothing_aside``), e.g.:

        AssertionError: assert [0.0, 600.0] == [0.0, 600.0, 1200.0]
          Right contains one more item: 1200.0
    """
    run = _run(2)
    for n in (1, 2, 3):
        _all_missed(run)  # GENERIC_SOLVE_FAILURE, the default
        end = run.close_pass()
        assert (end.boundary, end.held_streak) == ("centring_hold", n), n
        run.start_pass()


def test_a_pass_whose_panels_disagree_carries_no_reason_code():
    """CONTROL: within ONE pass, the panels blame different things --
    ``_held_pass_reason_code`` reads that as no code at all (the module
    docstring: "a pass whose panels do not all blame the same last_error is
    not evidence of one persistent cause"). It cannot match a later pass's
    single reason, however that later pass turns out."""
    run = _run(2)
    run.visit_outcome("p0", complete=False, exposures=0, accepted=0,
                      deferred=_miss("solver not found"))
    run.visit_outcome("p1", complete=False, exposures=0, accepted=0,
                      deferred=_miss("file in use"))
    end = run.close_pass()
    assert (end.boundary, end.held_streak) == ("centring_hold", 1)
    run.start_pass()

    _all_missed(run, "solver not found")
    end = run.close_pass()
    assert (end.boundary, end.held_streak) == ("centring_hold", 2), (
        "a disagreeing pass must not seed a match for the next one")


# ---------------------------------------------- the engine, end to end (#576)

async def test_an_all_transient_run_alerts_then_sets_the_mosaic_aside(
        group_hub, monkeypatch):
    """ENGINE-LEVEL, #576's own scenario: every hop's centring solve could
    not run (the hub's ``centring_solve_transient``, as WP-22 will set it),
    every pass, forever -- the "stuck solve-frame lock" night. Before D-03
    this re-slews every panel every ``DEFER_WAIT_S`` all night with no
    alert and no stop (the defect `test_h4_transient_solve_is_no_strike.py`
    never had to catch, since it only ever drives ONE panel transient
    beside others that centre).

    Now: a WARNING names the hold at the 3rd held pass (`bus.log`'s own
    route into alerting.py's dispatcher, WP-04), and the mosaic is set
    aside for the night at the 6th, with no further hop after it.

    RED under mutant "no alert logged" (the ``_alert_held_streak`` call
    deleted from both branches of ``SequenceEngine._close_group_pass``),
    observed:

        AssertionError: no warning named the held streak: []
    """
    def goto(who, n, result):
        return {**result, "centered": False, "error_arcmin": None,
                "centring_solve_transient": True}

    # `night.run` arms its own spin watchdog; `arm()` is only for a night
    # driven by `engine.start`/`until`/`finish` instead (test_h4_centring_
    # group_hold.py's own `_night` helper), and calling both together races
    # two independent watchdogs against the same event loop.
    night = Night(group_hub, monkeypatch, goto=goto)
    try:
        night.done = await night.run(grid_plan(rows=2, cols=2))
    finally:
        await night.close()
    night.stored = session_store.load(night.session_id)

    assert night.done, night.lines[-5:]
    # The alert's own prefix ("{mosaic}: held for N passes...") is distinct
    # from the set-aside's ("{mosaic}: the mosaic has been held for N
    # passes..."), so this never also catches that later line.
    warned = [(t, m) for t, lvl, m in night.lines
             if lvl == "warning" and m.startswith(f"{GROUP_NAME}: held for")]
    assert len(warned) == 1, f"no warning named the held streak: {warned}"
    assert f"{GROUP_NAME}: held for {HELD_PASS_ALERT_AT} passes in a row" in (
        warned[0][1]), warned[0]
    # Hops take no fake time, so the Nth held pass closes at (N-1) waits of
    # DEFER_WAIT_S past the first (instant) pass -- the shorter of the two
    # waits D-03 can count (the other, CENTRING_HOLD_RETRY_S, is #563's own
    # "about 30 min").
    assert night.rel(warned[0][0]) == (HELD_PASS_ALERT_AT - 1) * DEFER_WAIT_S, (
        warned[0])

    aside = night.stored.set_aside
    assert aside and aside[0]["target_id"].startswith("p"), aside
    assert len(aside) == 4, "every panel of the 2x2 is set aside"
    # The set-aside's own prefix ("the mosaic has been held for...") is
    # distinct from the alert's ("held for..." with no "the mosaic has
    # been"), so this never also catches the earlier line.
    stop_line = night.said("the mosaic has been held for")
    assert len(stop_line) == 1, stop_line
    assert f"{HELD_PASS_SET_ASIDE_AT} passes in a row" in stop_line[0]
    # NOTHING RE-SLEWS AFTER THE SET-ASIDE (#576's whole complaint): the
    # last hop is no later than the pass that set the mosaic aside (the
    # 6th, closing at 5 waits past the first instant pass).
    last_hop = max(night.rel(t) for t, _who in night.gotos)
    assert last_hop == (HELD_PASS_SET_ASIDE_AT - 1) * DEFER_WAIT_S, (
        f"a hop happened after the set-aside: last hop at +{last_hop}s")


async def test_an_all_fail_centring_run_alerts_then_sets_the_mosaic_aside(
        group_hub, monkeypatch):
    """ENGINE-LEVEL, #563's OWN scenario (the issue's own suggested test:
    "a harness night where the solver always fails ... assert that an
    alert fires within N passes and that the group does not slew all
    night"). Added by the INDEPENDENT VERIFIER for WP-21: the test above
    drives the ``defer_wait`` branch of `SequenceEngine._close_group_pass`
    (the all-transient path, #576); this one drives the SEPARATE
    ``centring_hold`` branch (#563's own all-fail path), a different call
    site for `_alert_held_streak`. Without this test, nothing in the suite
    reaches that call site: a mutant that deletes ONLY the ``centring_hold``
    branch's ``self._alert_held_streak(mosaic, end)`` call (the
    ``defer_wait`` branch's call left in place) passed all 491 tests of
    this work package's own referencing batch, confirmed by hand from a
    byte backup before this test existed.

    Every panel's centring fails, every pass, forever (``error_arcmin:
    None``, no transient key anywhere): the real #563 night, a persistent
    rig-side cause with no distinguishing ``last_error`` beyond
    ``GENERIC_SOLVE_FAILURE`` (today's engine gives no other text for an
    outright solve failure) -- so, like
    ``test_two_passes_with_the_generic_reason_do_not_set_aside_early``
    above, this also exercises the counted path (3/6) alone, never the
    immediate same-reason one.

    RED under mutant "no alert on the centring_hold branch" (the
    ``self._alert_held_streak(mosaic, end)`` call in the ``elif
    end.boundary == "centring_hold":`` branch of `SequenceEngine.
    _close_group_pass` deleted, the ``defer_wait`` branch's call left
    alone), observed:

        AssertionError: no warning named the held streak: []
    """
    def goto(who, n, result):
        return {**result, "centered": False, "error_arcmin": None}

    night = Night(group_hub, monkeypatch, goto=goto)
    try:
        night.done = await night.run(grid_plan(rows=2, cols=2))
    finally:
        await night.close()
    night.stored = session_store.load(night.session_id)

    assert night.done, night.lines[-5:]
    warned = [(t, m) for t, lvl, m in night.lines
             if lvl == "warning" and m.startswith(f"{GROUP_NAME}: held for")]
    assert len(warned) == 1, f"no warning named the held streak: {warned}"
    assert f"{GROUP_NAME}: held for {HELD_PASS_ALERT_AT} passes in a row" in (
        warned[0][1]), warned[0]
    # Hops take no fake time, so the Nth held pass closes at (N-1) holds of
    # CENTRING_HOLD_RETRY_S past the first (instant) pass -- #563's own
    # wait, the longer of the two D-03 can count.
    assert night.rel(warned[0][0]) == (
        HELD_PASS_ALERT_AT - 1) * CENTRING_HOLD_RETRY_S, warned[0]

    aside = night.stored.set_aside
    assert aside and aside[0]["target_id"].startswith("p"), aside
    assert len(aside) == 4, "every panel of the 2x2 is set aside"
    stop_line = night.said("the mosaic has been held for")
    assert len(stop_line) == 1, stop_line
    assert f"{HELD_PASS_SET_ASIDE_AT} passes in a row" in stop_line[0]
    # NOTHING RE-SLEWS AFTER THE SET-ASIDE (#563's own complaint): the last
    # hop is no later than the pass that set the mosaic aside (the 6th,
    # closing at 5 holds past the first instant pass).
    last_hop = max(night.rel(t) for t, _who in night.gotos)
    assert last_hop == (
        HELD_PASS_SET_ASIDE_AT - 1) * CENTRING_HOLD_RETRY_S, (
        f"a hop happened after the set-aside: last hop at +{last_hop}s")
