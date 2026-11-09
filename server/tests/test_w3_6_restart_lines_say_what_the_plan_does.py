# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The restart-tonight lines say what the compiled plan will do with a
set-aside panel (#830, #730, #1112; consolidation WP 3.6).

Two promises the engine's set-aside lines made that the plan did not keep,
each pinned on the real engine on the clocked simulator (the harness of
test_w15_retry_set_aside_night.py: a 2x2 mosaic whose panel 2-2 is set aside
by the reject rule on night one):

* A RETRY ASKED AS A RESTART BEGINS. ``_skip_what_tonight_set_aside`` said
  "a restart tonight does not retry it" and marked the panel skipped in the
  report, and the first selection then drained the operator's retry and shot
  the panel. Both lines were true in order and read as a contradiction. A
  panel the operator has already asked back says that, and what the first
  selection does with it, and is not reported skipped.
* THE NEXT NIGHT. "the next night does" promised a retry on a night nothing
  starts by itself when the plan's ``resume_across_nights`` is an explicit Off
  (the flow's Automatic resume turned Off; the Tonight note says "CONTINUE it
  by hand", ``tonight._NO_LATER_RESUME``). Read off the session's frozen plan,
  as ``_finalize_report`` reads it, the lines say a later night takes the panel
  up when CONTINUE is pressed by hand. A plan that resumes keeps the words it
  always had, which test_set_aside_copy.py and a dozen others pin.

RED under mutant "the restart line ignores a queued retry" (the
``_pending_retries`` branch of ``_skip_what_tonight_set_aside`` deleted),
observed:

    test_a_retry_asked_as_a_restart_begins_is_not_called_a_skip:
        assert 'the operator asked for it back' in 'M31 2-2: set aside
        earlier tonight (...); a restart tonight does not retry it, the next
        night does'
"""
from __future__ import annotations

import pytest

from _flow_night import FlowRig, flow_rig  # noqa: F401
from _group_harness import GROUP_ID, group_store  # noqa: F401
from astrodeck.sequence.session import session_store
from test_w15_retry_set_aside_night import (LATER_TONIGHT, MISSES, SPIN_S,
                                            WALL_S, _first_night_with_2_2_set_aside,
                                            _plan)

NEXT_NIGHT = "the next night does"
BY_HAND = "the next night does only when CONTINUE is pressed by hand"


def _says(night, needle: str) -> list[str]:
    return night.said(needle)


async def _restart_with(rig: FlowRig, bad: dict, **plan_update):
    """Night one leaves 2-2 set aside; returns the restart's night and the
    stored session with ``plan_update`` applied to its frozen plan. The
    restart is started and NOT yet run, so a test can act before the
    scheduler does."""
    _night, sid, _tonight, _plan_ = await _first_night_with_2_2_set_aside(
        rig, bad)
    stored = session_store.load(sid)
    if plan_update:
        stored = stored.model_copy(update={
            "plan": stored.plan.model_copy(update=plan_update)})
    bad["on"] = False
    again = await rig.night(spin_bound_s=SPIN_S, day=LATER_TONIGHT)
    again.engine.start(stored.plan, session=stored)
    return again, sid


async def test_a_retry_asked_as_a_restart_begins_is_not_called_a_skip(flow_rig):
    """The restart reads 2-2's record back, and the operator's retry is asked
    in the same breath, before the scheduler has built its groups. The line
    the start says for 2-2 names the retry and the first selection, never
    'does not retry it', and the report carries the retry without a skip."""
    rig: FlowRig = flow_rig
    again, _sid = await _restart_with(rig, {"on": True})
    assert again.engine.retry_set_aside(GROUP_ID) == {"live": True,
                                                      "queued": ["2-2"]}
    await again.finish(wall_s=WALL_S)

    lines = _says(again, f"{MISSES}: set aside earlier tonight")
    assert len(lines) == 1, lines
    assert "the operator asked for it back" in lines[0], lines
    assert "first selection" in lines[0], lines
    assert "does not retry" not in lines[0], lines
    safety = again.engine.reporter.build().safety_events
    kinds = [e["action"] for e in safety if MISSES in e["reason"]]
    assert kinds == ["retry"], safety
    mine = [x for x in again.shots() if x[0] == MISSES]
    assert mine == [(MISSES, "L"), (MISSES, "R")] * 6, (
        f"the retry was not taken up: {mine}")


async def test_a_restart_with_no_retry_still_says_it_does_not_retry(flow_rig):
    """Control: with no retry asked the line is the one it always was, and
    the panel is reported skipped. (A plan that resumes says 'the next night
    does'.)"""
    rig: FlowRig = flow_rig
    again, _sid = await _restart_with(rig, {"on": True})
    await again.finish(wall_s=WALL_S)

    lines = _says(again, f"{MISSES}: set aside earlier tonight")
    assert len(lines) == 1, lines
    assert lines[0].endswith(f"a restart tonight does not retry it, "
                             f"{NEXT_NIGHT}"), lines
    safety = again.engine.reporter.build().safety_events
    assert [e["action"] for e in safety if MISSES in e["reason"]] == ["skip"]


async def test_a_restart_of_a_flow_that_does_not_resume_says_continue_by_hand(
        flow_rig):
    """The flow's Automatic resume is Off: nothing starts a later night by
    itself, so the restart's line for the set-aside panel says a later night
    takes it up when CONTINUE is pressed by hand."""
    rig: FlowRig = flow_rig
    again, _sid = await _restart_with(rig, {"on": True},
                                      resume_across_nights=False)
    await again.finish(wall_s=WALL_S)

    lines = _says(again, f"{MISSES}: set aside earlier tonight")
    assert len(lines) == 1, lines
    assert lines[0].endswith(f"a restart tonight does not retry it, "
                             f"{BY_HAND}"), lines


async def test_the_line_a_panel_is_set_aside_by_says_the_same(flow_rig):
    """The same clause in the line said at the moment the reject rule sets 2-2
    aside, on a night of a flow that does not resume."""
    rig: FlowRig = flow_rig
    bad = {"on": True}
    night = await rig.night(spin_bound_s=SPIN_S, stars=lambda who, _f: (
        0 if who == MISSES and bad["on"] else 50))
    night.engine.start(_plan(resume_across_nights=False))
    await night.finish(wall_s=WALL_S)

    lines = _says(night, "set aside for tonight: a restart tonight")
    assert len(lines) == 1, lines
    assert lines[0].endswith(BY_HAND), lines


# WP 6.2 (owner ruling D-E, #180 part B): a mosaic panel is no longer set
# aside for its own failures, but a pool's candidates still are, and this
# file grades what that set-aside feeds (see tests/_w6_2_three_strike.py).
# Its nights therefore run the group driver with the three-strike set-aside.
@__import__("pytest").fixture(autouse=True)
def _the_three_strike_set_aside(monkeypatch):
    from _w6_2_three_strike import three_strike
    three_strike(monkeypatch)
