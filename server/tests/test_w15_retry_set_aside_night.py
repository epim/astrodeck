# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""RETRY SET-ASIDE PANELS, end to end on the clocked simulator (#600, backlog
ruling D-07, owner-approved 2026-09-30; WP-104).

``POST /api/sequence/retry-set-aside`` brings tonight's set-aside panels of a
mosaic back, for one group or all, live or dormant:

* LIVE, the route only QUEUES (``SequenceEngine.retry_set_aside``), on the
  event loop, and answers the panel labels it queued. The engine's own
  scheduler drains the queue at the top of its next selection: within
  ``SCHEDULE_WAIT_STEP_S`` when it is idle, and when the visit in hand ends
  otherwise. It never mutates a run mid-visit, and it never writes the
  session file from the route: the engine's copy is the writer while live
  (``SessionStore.save_run_state`` keeps only the operator-owned fields), so
  a record cleared by the route would be written back by the next frame.
* DORMANT (the engine is not running), under the store's write lock, the
  session's records for tonight are marked cleared (``Session.
  note_set_aside_cleared``) and saved, so the same night's CONTINUE, restart
  or auto-resume takes the panel up.
* A retried panel is JUDGED, never forced: it is put back in the scheduler's
  list and the next selection asks its gating, window, horizon and meridian
  rule as it asks every panel's, so a panel that is still unfit is set aside
  again and the retry slews nowhere.

THE NIGHT is the real engine on the clocked simulator (tests/_group_harness.py)
behind the real app (tests/_flow_night.py's ``FlowRig``), with a 2x2 mosaic of
30 s frames in accepted mode, where 2-2's frames carry no stars and are
rejected by the real grader while the other panels' are accepted: after
three such visits it is set aside by the reject rule, kind ``rejects``, which
never expires, so only the operator brings it back.

Every mutant was run from a byte backup of the file named, restored and
sha256-compared after each, the mutant's marker grepped absent (#254). The
observed failure is recorded verbatim, in the test it turned red.
"""
from __future__ import annotations

import json

import pytest

import astrodeck.api.app as app_module
import astrodeck.events as events_mod
from _flow_night import DAY_S, ZONE_S, FlowRig, ZonedTime, flow_rig  # noqa: F401
from _group_harness import (GROUP_ID, GROUP_NAME, T0, grid_plan,  # noqa: F401
                            group_store)
from astrodeck.events import night_key
from astrodeck.sequence.engine import SCHEDULE_WAIT_STEP_S
from astrodeck.sequence.group_rules import SET_ASIDE_EXPIRY_S
from astrodeck.sequence.models import Schedule
from astrodeck.sequence.session import Session, session_store

MISSES = f"{GROUP_NAME} 2-2"
ROUTE = "/api/sequence/retry-set-aside"
RETRIED = f"{GROUP_NAME}: set-aside panels retried by the operator: 2-2"
#: An hour on: still the night the first run was in (``night_key``).
LATER_TONIGHT = 3600.0 / DAY_S
#: REAL seconds the harness may wait on a night, and the spin watchdog's bound
#: (#319): both are generous because these nights run beside other suites on a
#: shared box (#669, #675), where the harness's own 60 s and 10 s are tuned for
#: a quiet one. A spin that never yields still fails, in 60 s instead of 10.
WALL_S = 240.0
SPIN_S = 60.0


def _plan(count: int = 6, **kw):
    """A 2x2 in accepted mode with a star floor and a step reject guard of 5,
    ``count`` L and R each: 2-2's three rejected visits (stars 0) set it aside
    by the per-panel reject rule, kind ``rejects``, before the guard's 5."""
    return grid_plan(count_mode="accepted", min_stars=5,
                     max_consecutive_rejects=5,
                     max_consecutive_rejects_night=0,
                     panel_kw={"count": count}, **kw)


def _no_stars_while(bad: dict):
    """2-2's frames carry no stars while ``bad["on"]``."""
    return lambda who, _filt: 0 if who == MISSES and bad["on"] else 50


async def _post(rig: FlowRig, role: str | None = "operator", **body):
    rig.as_role(role)
    try:
        return await rig.client.post(ROUTE, json=body)
    finally:
        rig.as_role(None)


def _refused(r, code: int, detail: str) -> None:
    """``r`` is the refusal ``code`` with the server's own ``detail``."""
    assert r.status_code == code, (r.status_code, r.text)
    assert r.json()["detail"] == detail, r.json()


def _hold_when_a_panel_is_set_aside(night, seen: list) -> None:
    """Hold the night at the first exposure that starts while the published
    group names a set-aside panel: the pass after the one that set it aside."""
    def on_capture(rec: dict) -> None:
        if (rec["group"] or {}).get("set_aside") and not seen:
            seen.append(rec)
            night.hold(rec["t"])
    night.on_capture = on_capture


def _written_to_the_night_log(needle: str) -> bool:
    """Is ``needle`` in a line of ANY file of the durable night log
    (captures/logs/<night>.jsonl, the file that outlives the 200-entry ring)?"""
    for path in events_mod.NightLogWriter.dir().glob("*.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            if needle in json.loads(line).get("data", {}).get("message", ""):
                return True
    return False


def _after(night, t: float) -> list[tuple[str, str]]:
    """The exposures that started at or after the fake instant ``t``."""
    return [(c["target"], c["filter"]) for c in night.captures if c["t"] >= t]


# ------------------------------------------------------------- the live route

async def test_a_live_retry_brings_a_final_set_aside_panel_back_on_the_next_pass(
        flow_rig):
    """2-2 is set aside after its third rejected visit, for the rest of the
    night (kind ``rejects``): it has left the scheduler's list, and nothing
    in the engine would bring it back before tomorrow. The operator fixes
    the cause and presses RETRY: the route answers the label it QUEUED, the
    scheduler picks it up at its next selection, 2-2 is visited in that very
    pass and owes, and shoots, all twelve of its frames, and the mosaic
    completes. The night log carries one line naming the group and the panel,
    the report carries a ``retry`` entry beside the earlier skip, the session
    record is marked ``cleared``, and a restart would not read it.

    RED under mutant "route accepts the request but clears nothing"
    (``SequenceEngine.retry_set_aside`` returning the labels without
    appending them to ``_pending_retries``), observed:

        AssertionError: 2-2 shot nothing after the retry: []
        assert [] == [('M31 2-2', ...2', 'R'), ...]
          Right contains 12 more items, first extra item: ('M31 2-2', 'L')

    RED under mutant "drain loop body deleted" (the call to
    ``_drain_set_aside_retries`` replaced by ``pass`` in ``_schedule_loop``),
    the same lines (observed).

    RED under mutant "panel not re-added to remaining" (the insert in
    ``_drain_set_aside_retries`` replaced by ``pass``, so the panel is cleared
    in every record but never put back in the list, which a FINAL set-aside
    needs since it was dropped from it), the same lines (observed), where
    the for-now case below stays GREEN, since its panel never left the list.
    """
    rig: FlowRig = flow_rig
    bad = {"on": True}
    night = await rig.night(spin_bound_s=SPIN_S, stars=_no_stars_while(bad))
    seen: list[dict] = []
    _hold_when_a_panel_is_set_aside(night, seen)
    night.engine.start(_plan())
    sid = night.engine._session.id
    assert await night.settle(wall_s=WALL_S), "premise: the night reached the hold"
    assert [a["panel"] for a in night.engine.state["group"]["set_aside"]] == [
        "2-2"], "premise: 2-2 is set aside"
    assert night.engine.state["group"]["set_aside"][0]["for_now"] is False, (
        "premise: for the rest of the night, not for now")
    hold_t = seen[0]["t"]

    r = await _post(rig, group=GROUP_ID)
    assert r.status_code == 200, r.text
    assert r.json() == {"queued": ["2-2"], "live": True}

    bad["on"] = False
    night.on_capture = None
    await night.finish(wall_s=WALL_S)
    after = _after(night, hold_t)
    mine = [x for x in after if x[0] == MISSES]
    assert mine == [(MISSES, "L"), (MISSES, "R")] * 6, (
        f"2-2 shot nothing after the retry: {mine}")
    # In the pass the hold stood in: the in-flight visit is visit
    # ``seen[0]["visit"]``, and the three that follow it are the rest of that
    # pass, which the retried panel joins.
    next_three = night.visits()[seen[0]["visit"]:seen[0]["visit"] + 3]
    assert (MISSES, ("L", "R")) in next_three, (
        f"2-2 was not visited in the pass it was retried in: {next_three}")
    stored = session_store.load(sid)
    assert stored.status == "complete" and stored.owed() == 0, (
        stored.status, stored.owed())

    said = night.said("set-aside panels retried by the operator")
    assert said == [RETRIED], said
    assert _written_to_the_night_log(RETRIED), (
        "the line is not in the durable night log")
    safety = night.engine.reporter.build().safety_events
    kinds = [(e["action"], MISSES in e["reason"]) for e in safety
             if MISSES in e["reason"]]
    assert kinds == [("skip", True), ("retry", True)], safety

    records = [x for x in stored.set_aside if x["target_id"] == "p11"]
    assert records and all(x.get("cleared") for x in records), records
    assert stored.set_aside_on(night_key(hold_t)) == []


async def test_a_retry_asked_during_the_visit_that_ends_the_list_is_not_lost(
        flow_rig):
    """The other three panels are done but the last, whose visit is in hand,
    and 2-2 is set aside for the night. The operator presses RETRY during that
    visit and is told it is queued. When the visit ends the scheduler's list
    is EMPTY: the loop used to end there, with the retry still in its queue,
    and the run finished owing 2-2 while the operator had been told it was
    coming back. The loop now runs on while a retry is queued, drains it into
    the empty list, and 2-2 is shot to completion on this run.

    RED under mutant "loop ends when the list empties" (``_schedule_loop``'s
    ``while remaining or self._pending_retries`` made ``while remaining``),
    observed:

        AssertionError: 2-2 shot nothing after the retry: []
        assert [] == [('M31 2-2', ...2', 'R'), ...]
    """
    rig: FlowRig = flow_rig
    bad = {"on": True}
    night = await rig.night(spin_bound_s=SPIN_S, stars=_no_stars_while(bad))
    seen: list[dict] = []

    def at_the_last_visit(rec: dict) -> None:
        g = rec["group"] or {}
        if g.get("set_aside") and g.get("panels_done") == 2 and not seen:
            seen.append(rec)
            night.hold(rec["t"])

    night.on_capture = at_the_last_visit
    night.engine.start(_plan())
    sid = night.engine._session.id
    assert await night.settle(wall_s=WALL_S), "premise: the night reached the last visit"
    assert [(p["panel"]) for p in night.engine.state["group"]["set_aside"]] == [
        "2-2"], "premise: 2-2 is still set aside"
    hold_t = seen[0]["t"]
    r = await _post(rig, group=GROUP_ID)
    assert r.status_code == 200 and r.json()["queued"] == ["2-2"], r.text
    bad["on"] = False
    night.on_capture = None
    await night.finish(wall_s=WALL_S)
    mine = [x for x in _after(night, hold_t) if x[0] == MISSES]
    assert mine == [(MISSES, "L"), (MISSES, "R")] * 6, (
        f"2-2 shot nothing after the retry: {mine}")
    stored = session_store.load(sid)
    assert stored.status == "complete" and stored.owed() == 0, (
        stored.status, stored.owed())


async def test_a_live_retry_takes_a_set_aside_for_now_at_once_not_at_its_expiry(
        flow_rig):
    """A centring set-aside is set aside FOR NOW: the panel waits in the
    list for its 45 minutes (H4, #534). The operator's retry does not wait
    for them: the idle scheduler picks it up within ``SCHEDULE_WAIT_STEP_S``
    seconds twice over, 2-2 is tried again at once, centres, and the mosaic
    completes long before the expiry would have freed it. Nothing is said to
    have expired, and the panel is in the list once (not added a second time
    beside the waiter it already was).

    RED under mutant "wait not ended by a retry" (the ``and not
    self._pending_retries`` of ``_wait_until_or_retry`` removed, so the idle
    wait runs to the waiter's wake, as ``_wait_until`` always did; since WP-140
    (#726) that function is ``_wait_until_or_queued`` and the check it ends on
    is ``_queued_for_the_scheduler``, where the same mutant, re-run, still goes
    red on this case), observed:

        AssertionError: 2-2 was tried at [120.0, 180.0, 360.0, 3240.0, 3300.0,
        3360.0], the retry at 1200.0 s was not taken up: it waited for its
        expiry
        assert 3240.0 <= (1200.0 + (2 * 5.0))

    Also RED under "route accepts the request but clears nothing" (the first
    case's first mutant), with the same lines (observed).
    """
    rig: FlowRig = flow_rig

    def misses_first_three(who, n, result):
        if who == MISSES and n <= 3:
            return {**result, "centered": False, "error_arcmin": None}
        return result

    night = await rig.night(spin_bound_s=SPIN_S, goto=misses_first_three)
    night.engine.start(grid_plan())
    hold = night.t0 + 1200.0
    assert await night.until(hold, wall_s=WALL_S), "premise: the night is waiting at the hold"
    assert [a["for_now"] for a in night.engine.state["group"]["set_aside"]] == [
        True], "premise: 2-2 is set aside for now, waiting on its expiry"
    r = await _post(rig, group=GROUP_ID)
    assert r.status_code == 200, r.text
    assert r.json() == {"queued": ["2-2"], "live": True}
    night.release()
    await night.finish(wall_s=WALL_S)
    tried = [night.rel(t) for t, who in night.gotos if who == MISSES]
    taken_up = tried[3]
    assert 1200.0 <= taken_up <= 1200.0 + 2 * SCHEDULE_WAIT_STEP_S, (
        f"2-2 was tried at {tried}, the retry at 1200.0 s was not taken up: "
        f"it waited for its expiry")
    assert taken_up < 540.0 + SET_ASIDE_EXPIRY_S - 1000.0
    assert night.said("set-aside has expired") == []
    assert night.said("set-aside panels retried by the operator") == [RETRIED]


# ----------------------------------------------------------- the dormant route

async def _first_night_with_2_2_set_aside(rig: FlowRig, bad: dict):
    """Night one runs to its end with 2-2 set aside for the night (the other
    three complete): the session is dormant, owes 2-2's twelve frames, and
    holds the record that a restart tonight reads back. Returns the night, the
    session's id, its night key and the plan."""
    night = await rig.night(spin_bound_s=SPIN_S, stars=_no_stars_while(bad))
    plan = _plan()
    night.engine.start(plan)
    sid = night.engine._session.id
    await night.finish(wall_s=WALL_S)
    first = session_store.load(sid)
    assert first.status == "dormant" and first.owed() == 12, (
        first.status, first.owed())
    tonight = night_key(night.clock.t)
    assert [x["target_id"] for x in first.set_aside_on(tonight)] == ["p11"], (
        "premise: 2-2's record stands, so a CONTINUE tonight skips it")
    return night, sid, tonight, plan


async def test_a_dormant_retry_lets_the_same_nights_continue_shoot_the_panel(
        flow_rig):
    """The run ends with 2-2 set aside for the night (the other three
    complete), and the session is dormant and owes its frames. A CONTINUE
    the same night would skip it: its record stands, and ``start`` reads it
    back. The operator presses RETRY with the engine idle: the route answers
    the label it CLEARED, the record is marked ``cleared`` in the stored
    session, and the CONTINUE an hour later hops to 2-2, shoots all twelve of
    its frames and completes the session. The night log names it.

    RED under mutant "records not marked cleared" (``note_set_aside_cleared``
    with its ``rec["cleared"] = True`` replaced by ``pass``), observed, at the
    stored session's records, before the continue is reached:

        assert [None] == [True]
          At index 0 diff: None != True
    """
    rig: FlowRig = flow_rig
    bad = {"on": True}
    _night, sid, tonight, _plan_ = await _first_night_with_2_2_set_aside(rig, bad)

    r = await _post(rig, group=GROUP_ID, session_id=sid)
    assert r.status_code == 200, r.text
    assert r.json() == {"cleared": ["2-2"], "live": False}
    stored = session_store.load(sid)
    assert [x.get("cleared") for x in stored.set_aside] == [True]
    assert stored.set_aside_on(tonight) == []
    assert stored.status == "dormant", "the route must not start the run"
    assert _written_to_the_night_log(f"{GROUP_NAME}: set-aside panels retried "
                                     f"by the operator: 2-2")

    bad["on"] = False
    again = await rig.night(spin_bound_s=SPIN_S, day=LATER_TONIGHT)
    again.engine.start(stored.plan, session=stored)
    await again.finish(wall_s=WALL_S)
    mine = [x for x in again.shots() if x[0] == MISSES]
    assert mine == [(MISSES, "L"), (MISSES, "R")] * 6, (
        f"2-2 shot nothing on the continue: {mine}")
    done = session_store.load(sid)
    assert done.status == "complete" and done.owed() == 0, (
        done.status, done.owed())


async def test_a_retry_asked_as_a_restart_begins_is_taken_up_when_the_scheduler_is(
        flow_rig):
    """A restart tonight reads 2-2's record back, and the run it starts spends
    its first minutes before the scheduler: the cooling wait, the camera-lane
    wait. The engine has no group run state yet, and the retry asked THEN
    must still find the panel, in the records the start seeded, and queue
    it: the scheduler builds its groups, drops the panel as set aside
    earlier tonight, and drains the queue at its first selection, so 2-2 is
    shot on this very run. Asked of the engine in the same breath as the
    start, before any await, so the run is certainly before its scheduler.

    RED under mutant "retry before the scheduler finds nothing" (the
    ``else`` branch of ``retry_set_aside`` answering no panels), observed:

        AssertionError: assert {'live': True, 'queued': []} == {'live': True...ued': ['2-2']}
          Differing items:
          {'queued': []} != {'queued': ['2-2']}
    """
    rig: FlowRig = flow_rig
    bad = {"on": True}
    _night, sid, _tonight, _plan_ = await _first_night_with_2_2_set_aside(rig, bad)
    stored = session_store.load(sid)
    bad["on"] = False
    again = await rig.night(spin_bound_s=SPIN_S, day=LATER_TONIGHT)
    again.engine.start(stored.plan, session=stored)
    assert again.engine._group_runs == {}, "premise: the scheduler has not run"
    assert again.engine.retry_set_aside(GROUP_ID) == {"live": True,
                                                      "queued": ["2-2"]}
    await again.finish(wall_s=WALL_S)
    mine = [x for x in again.shots() if x[0] == MISSES]
    assert mine == [(MISSES, "L"), (MISSES, "R")] * 6, (
        f"2-2 shot nothing on this run: {mine}")
    assert again.said("set-aside panels retried by the operator") == [RETRIED]
    done = session_store.load(sid)
    assert done.status == "complete" and done.owed() == 0, (
        done.status, done.owed())


# ------------------------------------------------------------------ the rules

def _dormant_with_a_panel_set_aside(night: str, *, status: str = "dormant",
                                    set_aside: bool = True) -> Session:
    """A stored session with 2-2's record standing for ``night``, built
    directly: the route's checks do not need a night to have run."""
    s = Session(name="stored", plan=_plan(), status=status)
    if set_aside:
        s.note_set_aside("p11", "rejected every frame for 3 visits",
                         night=night, kind="rejects", ts=1.0)
    session_store.save(s)
    return s


def _pin_the_clock(rig: FlowRig) -> str:
    """The app reads the night at ``T0``; returns that night's key."""
    rig.mp.setattr(app_module, "time", ZonedTime(lambda: T0, ZONE_S))
    return night_key(T0)


async def test_a_viewer_is_refused_and_an_operator_is_served(flow_rig):
    """The route moves nothing itself, but a retried panel is slewed to by
    the run, so it is ``control.mount`` like pause, resume and abort: a
    viewer gets 403 and the record is untouched; an operator gets 200.

    RED under mutant "route open to a viewer" (``Depends(require(
    CAP_CONTROL_MOUNT))`` replaced by ``CAP_VIEW_STATUS`` on the route),
    observed:

        AssertionError: {"cleared":["2-2"],"live":false}
        assert 200 == 403
         +  where 200 = <Response [200 OK]>.status_code
    """
    rig: FlowRig = flow_rig
    night = _pin_the_clock(rig)
    s = _dormant_with_a_panel_set_aside(night)
    denied = await _post(rig, "viewer", group=GROUP_ID, session_id=s.id)
    assert denied.status_code == 403, denied.text
    assert session_store.load(s.id).set_aside[0].get("cleared") is None, (
        "a refused request changed the record")
    served = await _post(rig, "operator", group=GROUP_ID, session_id=s.id)
    assert served.status_code == 200, served.text
    assert served.json() == {"cleared": ["2-2"], "live": False}


async def test_a_session_named_by_id_is_retried_on_its_own_while_another_run_is_live(
        flow_rig):
    """Last night's stored session still has 2-2's record standing for the
    night key the app reads, and tonight's run is live with every panel in
    play. A request that NAMES the stored session is about that one: its
    record is cleared (the dormant answer, ``live`` false) and the live run's
    queue stays empty. Without the name the same request is the live run's,
    which has nothing set aside.

    RED under mutant "a named session is the live run's" (the route's
    ``other`` check removed, so any request goes to the engine while a run is
    live), observed:

        AssertionError: (409, '{"detail":"nothing is set aside"}')
        assert 409 == 200
    """
    rig: FlowRig = flow_rig
    tonight = _pin_the_clock(rig)
    stored = _dormant_with_a_panel_set_aside(tonight)
    live = await rig.night(spin_bound_s=SPIN_S)
    live.engine.start(_plan())
    assert await live.until(live.t0 + 100.0, wall_s=WALL_S)
    r = await _post(rig, group=GROUP_ID, session_id=stored.id)
    assert r.status_code == 200, (r.status_code, r.text)
    assert r.json() == {"cleared": ["2-2"], "live": False}
    assert session_store.load(stored.id).set_aside[0].get("cleared") is True
    assert live.engine._pending_retries == [], (
        "the live run was asked to retry for a session it is not running")
    _refused(await _post(rig, group=GROUP_ID), 409, "nothing is set aside")


async def test_nothing_set_aside_is_a_409_and_the_other_refusals_say_why(
        flow_rig):
    """A request that would clear nothing is answered, not swallowed: 409
    "nothing is set aside", live (a run with every panel in play) or dormant
    (a session with no record tonight, or one whose record is another
    night's). An active or complete session is 409 not dormant, an unknown
    session 404, an unknown group 404, and with no session named and none
    armed there is nothing to retry on, 404.

    RED under mutant "an empty answer is a 200" (the dormant branch's ``if
    not standing:`` made ``if False:``), observed:

        AssertionError: (200, '{"cleared":[],"live":false}')
        assert 200 == 409
    """
    rig: FlowRig = flow_rig
    tonight = _pin_the_clock(rig)

    quiet = _dormant_with_a_panel_set_aside(tonight, set_aside=False)
    r = await _post(rig, group=GROUP_ID, session_id=quiet.id)
    _refused(r, 409, "nothing is set aside")
    old = _dormant_with_a_panel_set_aside("2026-08-30")
    r = await _post(rig, group=GROUP_ID, session_id=old.id)
    _refused(r, 409, "nothing is set aside")     # another night's: history
    assert session_store.load(old.id).set_aside[0].get("cleared") is None

    for status in ("active", "complete"):
        s = _dormant_with_a_panel_set_aside(tonight, status=status)
        r = await _post(rig, group=GROUP_ID, session_id=s.id)
        _refused(r, 409, f"session is {status}, not dormant")
        assert session_store.load(s.id).set_aside[0].get("cleared") is None

    r = await _post(rig, group=GROUP_ID, session_id="no-such-session")
    _refused(r, 404, "session not found")
    r = await _post(rig, group="no-such-group", session_id=quiet.id)
    _refused(r, 404, "no such group")
    r = await _post(rig, group=GROUP_ID)
    assert r.status_code == 404, r.text
    assert "session_id" in r.json()["detail"]

    # LIVE: every panel is in play, so there is nothing to bring back.
    live = await rig.night(spin_bound_s=SPIN_S)
    live.engine.start(_plan())
    assert await live.until(live.t0 + 100.0, wall_s=WALL_S)
    r = await _post(rig, group=GROUP_ID)
    _refused(r, 409, "nothing is set aside")
    r = await _post(rig, group="no-such-group")
    _refused(r, 404, "no such group")


# ----------------------------------------------------------------- never forced

async def test_a_retried_panel_past_its_window_is_judged_again_and_never_slewed_to(
        flow_rig):
    """2-2's window is capped at 15 minutes from its first attempt
    (``max_run_min``), and it is set aside by rejects before that. The
    operator retries it at 1100 s, after the cap has closed its window. The
    retry puts it back in the list and the next selection asks its gating:
    ``window_closed``, so it is never a candidate, no goto is made to it, and
    when the others are done it leaves the list as the closed window it is.
    The report reads skipped, retried, skipped (window closed): the retry
    is the operator's act and the sky's answer is the sky's.

    RED under mutant "retry reopens the panel's window" (``_drain_set_aside_
    retries`` writing ``self._frozen[id(t)] = (None, None)`` for the panel it
    brings back, so gating finds the window open), observed:

        AssertionError: 2-2 was slewed to after the retry: [1140.0, 1320.0]
        assert [1140.0, 1320.0] == []
    """
    rig: FlowRig = flow_rig
    bad = {"on": True}
    night = await rig.night(spin_bound_s=SPIN_S, stars=_no_stars_while(bad))
    plan = _plan(count=10)
    next(t for t in plan.targets if t.id == "p11").schedule = Schedule(
        max_run_min=15)
    night.engine.start(plan)
    hold = night.t0 + 1100.0
    assert await night.until(hold, wall_s=WALL_S)
    assert [a["panel"] for a in night.engine.state["group"]["set_aside"]] == [
        "2-2"], "premise: 2-2 is set aside"
    r = await _post(rig, group=GROUP_ID)
    assert r.status_code == 200 and r.json()["queued"] == ["2-2"], r.text
    night.release()
    await night.finish(wall_s=WALL_S)
    slewed = [night.rel(t) for t, who in night.gotos
              if who == MISSES and t > hold]
    assert slewed == [], f"2-2 was slewed to after the retry: {slewed}"
    assert night.said("set-aside panels retried by the operator") == [RETRIED]
    mine = [(e["action"], e["reason"]) for e in
            night.engine.reporter.build().safety_events if MISSES in e["reason"]]
    assert [a for a, _r in mine] == ["skip", "retry", "skip"], mine
    assert mine[2][1].endswith("window closed"), mine
