# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""``max_run_min`` counts from a target's own FIRST ATTEMPT, not the run's
start and not the target's own clock-resolved start (#164).

THE DEFECT (#164). ``resolve_window`` capped a target's stop at
``start + max_run_min``, where ``start`` is the schedule's own resolved
clock start -- ``now`` (the run's start) for the default "now" mode. The
engine freezes every target's window ONCE, at run start
(``sequence.engine._run_scheduled``), so in a multi-target plan a LATER
target's budget was already partly or wholly spent -- by an earlier
target's own overrun, or simply by the time the scheduler took to reach it
-- before that later target was ever touched.

THE RULING (orchestrator, 2026-10-02, owner-approved plan 2026-09-30,
backlog ruling D-nn, fix shape "(c) max_run_min is measured from the
target's own start, not the run's" from backlog WP-55c): a target's
``max_run_min`` counts from its FIRST ATTEMPT -- the first time the
scheduler selects it to visit, whether or not the visit's own setup (a
slew, a centre, a rotate) then succeeds. A target never yet attempted has
NO ``max_run_min`` limit yet: only its own dawn/clock stop (if any)
applies. Counting a refused or failed attempt as "started" keeps
``max_run_min`` working as the give-up timer for a stuck target or mosaic
panel.

THE FIX, in the two files this WP owns:

* ``schedule.apply_max_run_cap`` is the one place a stop is capped by
  ``max_run_min``, given an explicit anchor. ``schedule.resolve_window``
  gains ``max_run_from``, defaulting to a sentinel that reproduces every
  pre-#164 caller's exact old answer (the resolved clock start); only the
  live engine passes a concrete anchor.
* ``engine.SequenceEngine._first_attempt`` records each target's own first
  selection, once, in ``_schedule_loop``'s selection (whatever the visit
  that follows then does); the FIRST per-target frozen window (built in
  ``_run_scheduled``) carries NO cap at all (nothing has been attempted
  yet), and the selection point updates that SAME dict entry in place the
  moment a target's own anchor becomes known -- so every other reader of
  the frozen window (``gating_status``, ``_enforce_stop_boundary``, the
  group hold-pass sweep, the reach-wait set-aside path, the all-closed
  dawn-cutoff check) sees the same per-target value from then on.

Test 1 runs the real ``_run_scheduled`` on the group harness's clocked
simulator (``tests/_group_harness.py``), as every other engine-behaviour
test in this suite does; test 2 does the same for a mosaic panel repeatedly
refused at its own pre-slew gate; test 3 is a pure, direct test of
``schedule.apply_max_run_cap``/``resolve_window`` (Batch 4b style, as
``test_schedule.py`` tests the rest of this module), since "a target never
attempted has no cap" is that function's own contract and needs no engine
at all to show.

The fixture site (``_group_harness.LAT``/``LON``) is 40 N 74 W, not
anybody's rig, and no altitude or azimuth is printed.

Each case names the mutant it was shown RED under, with the failing
assertion recorded verbatim. Every mutant was applied to a private byte
backup inside this worktree (scratchpad w9c-mut), restored byte-identically
afterwards (sha256-compared), never left in the shared tree.
"""
from __future__ import annotations

from _group_harness import GROUP_NAME, Night, T0, group_hub, group_store, single
from astrodeck.sequence.models import Schedule
from test_group_preslew_floor_wait import (FAILING, T_CLEAR, T_GATE,
                                           _floor_plan,
                                           _hold_the_first_slew_gate)
from test_trigger_frame_banked import _plan, _run


def _name(label: str) -> str:
    return f"{GROUP_NAME} {label}"


# --------------------------------------------------------- #164's own example

async def test_a_later_now_target_gets_its_own_full_budget(group_hub, monkeypatch):
    """#164's own red test, in miniature (``ExposureStep.exposure_s`` is
    capped at 3600 s by session validation, so minutes become tens of
    seconds here; the shape is exactly #164's "two now targets share
    max_run_min 60; A runs 60 minutes; B must get its own 60"): two "now"
    targets, ``max_run_min`` 2 (minutes = 120 s) each. A's one frame runs
    150 s, past A's OWN 120 s budget (allowed to finish: the frozen-window
    check is at frame BOUNDARIES, same as every other test of it in this
    suite -- ``test_s7_consumed_jump_last_frame.py``'s re-pinned case for
    #164 is the identical shape). B's frame is only 90 s. B must get its own
    120 s budget starting from when B is FIRST picked (~150 s in), not from
    the run's start 150 s earlier, so B's 90 s frame -- which would already
    be past a run-start-anchored 120 s cap (150 s have elapsed since the
    run began) -- completes normally instead of being skipped as "window
    closed" before it was ever touched.

    MUTANT "the anchor reverted to the run start" (``_run_scheduled``'s
    frozen-window construction in engine.py changed from
    ``max_run_from=self._first_attempt.get(id(t))`` back to no
    ``max_run_from`` at all, i.e. every pre-#164 caller's default: the
    resolved clock start): RED (observed) --
        AssertionError: [('A', 'L')]
        assert [('A', 'L')] == [('A', 'L'), ('B', 'L')]
    B is found window_closed (its run-start-anchored budget, 120 s, was
    already spent by A's 150 s overrun) the moment it is first asked, and is
    skipped with the rest of the night as a dawn cutoff; it is never slewed
    to at all.
    """
    a = single("A", schedule=Schedule(max_run_min=2))
    a.steps[0].exposure_s = 150.0
    b = single("B", schedule=Schedule(max_run_min=2))
    b.steps[0].exposure_s = 90.0
    plan = _plan(a, b)
    night, _ = await _run(group_hub, monkeypatch, plan)
    assert night.done, night.lines[-6:]
    assert night.shots() == [("A", "L"), ("B", "L")], night.shots()
    closed = night.said("window closed")
    assert closed == [], f"B was found window_closed: {closed}"
    assert night.stored.status == "complete", night.stored.status


# ------------------------------------------------ a refused attempt still counts

async def test_a_refusal_during_a_held_gate_still_anchors_from_first_attempt(
        group_hub, group_store, monkeypatch):
    """Reuses ``test_group_preslew_floor_wait.py``'s own proven shape (a 2x2
    mosaic, panel 2-2 moved onto a wedge it crosses between ``T_GATE`` and
    ``T_CLEAR``, its first pre-slew gate held on the fake clock until
    ``T_GATE`` -- the time a cloud hold's monitor half can spend, per that
    file's own docstring) with ONE new ingredient: a plain target shot FIRST
    (450 s), delaying 2-2's own first attempt well past where that file's
    own tests pick it up (under 600 s there).

    2-2's own ``max_run_min`` is 22 minutes (1320 s). Anchored at ITS OWN
    first attempt (after the 450 s delay, ~570 s in), its window closes AFTER
    ``T_GATE`` (1800 s, when the held gate's real refusal lands) and BEFORE
    ``T_CLEAR`` (2400 s, when the wedge would otherwise clear on its own).
    So the SAME refusal, at the SAME ``T_GATE``, must be read as a WAIT
    (2-2's fresh budget has not run out yet), not an immediate set-aside --
    a run-start-anchored budget of the same 1320 s would already have
    closed well before ``T_GATE`` and would set 2-2 aside on that very first
    refusal instead, never read as a wait at all. 2-2's own give-up timer
    still fires later, before the wedge clears on its own.

    MUTANT 1, "the anchor reverted to the run start" (``_run_scheduled``'s
    frozen-window construction in engine.py, as test 1's): RED (observed) --
    the refusal at ``T_GATE`` is read as the window already closed (its
    run-start-anchored 1320 s cap, 0 + 1320 = 1320 s, is long past by 1800 s)
    instead of a wait, so the WAIT line is never said:
        AssertionError: the refusal at T_GATE must be read as a WAIT -- 2-2's
        own budget, anchored at its first attempt (570.0 s), has not run out
        yet: [...]
        assert []

    MUTANT 2, "first attempt never recorded" (the ``if id(ready) not in
    self._first_attempt:`` block ``_schedule_loop`` adds at selection
    changed to ``if False:``, in engine.py -- the OTHER half of the fix:
    recording the anchor at all, regardless of what the visit does next):
    RED (observed), a DIFFERENT assertion than mutant 1's -- the WAIT line
    IS said (nothing has capped 2-2's window yet, same as the real fix at
    that instant), but with no cap ever applied, the window NEVER closes:
    2-2 keeps waiting out the recheck cadence until the wedge genuinely
    clears on its own (~2460 s) and is shot normally instead of ever being
    given up on:
        AssertionError: 2-2's own give-up timer must still close its
        window: [..., (.., 'info', 'M31: panel 2-2 complete: 2 of 2
        filters')]
        assert None is not None
    """
    delay = single("Delay", ha_h=6.0)
    delay.steps[0].exposure_s = 450.0
    plan, p22 = _floor_plan(group_store, before=[delay])
    p22.schedule = Schedule(max_run_min=22)
    night = Night(group_hub, monkeypatch)
    held = _hold_the_first_slew_gate(night, monkeypatch, _name(FAILING))
    try:
        night.done = await night.run(plan, wall_s=60.0)
    finally:
        await night.close()
    assert night.done, night.lines[-6:]
    assert len(held) == 1, held
    t_held = held[0]
    gate_s = T_GATE - night.t0
    # The premise that actually makes this test distinguish the two anchors:
    # a run-start-anchored 1320 s cap is ALWAYS closed by T_GATE (0 + 1320 <
    # 1800), whatever delays 2-2's own pick; a first-attempt-anchored one
    # only stays open AT T_GATE when it was picked late enough.
    assert t_held + 22 * 60.0 > gate_s, (
        f"premise: 2-2's own first attempt ({t_held} s) must be late enough "
        f"that its own 1320 s budget is still open at T_GATE ({gate_s:.0f} "
        f"s), or this case cannot tell the two anchors apart")
    waits = night.said(f"M31: {FAILING} cannot be slewed to now")
    assert waits, (
        f"the refusal at T_GATE must be read as a WAIT -- 2-2's own budget, "
        f"anchored at its first attempt ({t_held} s), has not run out yet: "
        f"{night.lines[-6:]}")
    # 2-2 is the only panel still owed frames once the other three complete
    # their one visit each, so its own closed window reaches it through the
    # generic all-closed sweep (``M31 2-2: window closed``) rather than
    # through a second real hop attempt -- either way it is the SAME frozen
    # stop `_hop_refused_by_a_limit` would have read, so this is still the
    # per-target value this WP's fix carries from the first attempt.
    closed_at = next((t for t, _lvl, m in night.lines
                      if m == f"{_name(FAILING)}: window closed — skipping"),
                     None)
    assert closed_at is not None, (
        f"2-2's own give-up timer must still close its window: "
        f"{night.lines[-6:]}")
    rel_closed = closed_at - night.t0
    assert gate_s < rel_closed < (T_CLEAR - T0), (
        f"2-2's window closed at {rel_closed} s, expected strictly between "
        f"T_GATE ({gate_s:.0f} s) and T_CLEAR ({T_CLEAR - T0:.0f} s)")


# --------------------------------------------------------- the never-attempted control

def test_an_unattempted_target_has_no_max_run_min_limit_yet():
    """``schedule.apply_max_run_cap``'s own contract (the function
    ``resolve_window`` defers to for the whole cap): an anchor of ``None``
    -- "not attempted yet" -- leaves ``stop_ts`` untouched, however large
    ``max_run_min`` is and however far past a resolved clock start ``now``
    already sits. Only a real anchor (a timestamp) imposes a cap.

    MUTANT "an unset anchor capped anyway" (the ``anchor_ts is None`` arm of
    ``apply_max_run_cap``'s guard deleted, so it falls through to
    ``cap = anchor_ts + max_run_min * 60.0`` with ``anchor_ts=None``): RED
    (observed) --
        TypeError: unsupported operand type(s) for +: 'NoneType' and 'float'
    (a MORE PERMISSIVE mutant, ``cap = (anchor_ts or 0.0) + max_run_min *
    60.0``, was also shown RED: it caps every never-attempted target at
    ``max_run_min`` minutes past the EPOCH, which is always already in the
    past, i.e. every never-attempted target reads as closed before the run
    even starts -- the exact #164 bug one layer down --
        AssertionError: assert 60.0 == 10000000000.0
    )
    """
    from astrodeck.sequence import schedule as sch

    far_future_stop = 10_000_000_000.0   # the schedule's own clock stop, if any
    assert sch.apply_max_run_cap(far_future_stop, 1, None) == far_future_stop
    assert sch.apply_max_run_cap(None, 1, None) is None
    # A real anchor DOES cap, exactly as before (resolve_window's own prior
    # behaviour, with the anchor now explicit instead of always "start").
    assert sch.apply_max_run_cap(None, 1, 1000.0) == 1060.0
    assert sch.apply_max_run_cap(2000.0, 1, 1000.0) == 1060.0
    # max_run_min falsy (0, "no cap") is untouched by any anchor.
    assert sch.apply_max_run_cap(far_future_stop, 0, 1000.0) == far_future_stop

