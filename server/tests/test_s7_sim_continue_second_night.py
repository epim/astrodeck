# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""CONTINUE on a second simulated night, end to end (#189 S7 item 1, the
third of its four simulator scenarios; spec 5.9, 6.7; S7 orchestrator
rulings 1 and 7, #473 and #430).

Night 1 is scenario 2's (tests/test_s7_sim_solve_failure.py ``night_one``):
the S5 probe's rotating 2x2, 2-2 never centring, so 2-2 is set aside at its
third pass and the run ends dormant and armed, owing 2-2's six frames.

Night 2 is a NEW Night a day later on the same session store (a fresh
simulator hub, the same captures directory), on which every panel centres.
Between the two the flow is saved again, a move of its REPORT node on the
canvas, which changes nothing the compile reads. On night 2 ``/run``
CONTINUES the dormant session (spec 5.9): the same session and the same
step ids, and no recount asked (both nights count accepted subs). 2-2 is
retried, since a set-aside is for its night only (6.7). Part-way through,
the operator stops the run and, half an hour later, presses Run again: a
second CONTINUE in the same night, which is still night 2 (ruling 7). That
run completes the flow.

The night keys are the harness zone's (tests/_flow_night.py): 2026-09-01
and 2026-09-02, and the report ids carry 21:48:09 local, whatever zone the
suite runs in.

MUTANTS, each run in a private copy of ``server/`` (scratchpad
``S7-E2E-mut``), from a byte backup, never in the shared tree; each failure
is quoted where it was observed.
"""
from __future__ import annotations

import asyncio
import time

from _flow_night import (FlowRig, assert_the_three_agree, flow_rig,  # noqa: F401
                         moved, shot_counts, steps_by_panel)
from _group_harness import group_store  # noqa: F401
from test_s7_sim_solve_failure import FLOW, MISSES, night_one

#: The runs' report ids: the plan's name slugged, then the local start in
#: the harness's zone (UTC-4) on every machine, so a literal can name them.
NIGHT_ONE_RUN = "M31_2x2-20260901-214809"
NIGHT_TWO_RUN = "M31_2x2-20260902-214809"
#: How long after the operator's stop Run is pressed again: half an hour of
#: the same night.
PAUSE_S = 1800.0


async def _progress(rig: FlowRig, fid: str, at: str) -> dict:
    return await rig.get(f"/api/flows/{fid}/progress", at=at)


async def test_continue_on_night_two_retries_the_set_aside_panel_and_counts_nights(
        flow_rig):
    """After night 1 the session is armed and holds the flow's saved time; a
    save before night 2 leaves it older than the flow's; night 2's Run
    continues the same session with the same step ids and no recount,
    says night 2 (so does the progress route), and moves the saved time to
    the flow's (ruling 1). 2-2 is shot again. Stopped and continued half
    an hour later, the run still says night 2, and the progress route
    still counts 2 nights over 3 runs (ruling 7). At the end 2-2 is
    complete, the ledger holds each night's frames and no others, every
    run's report is that run's share of it, and the session is complete.

    RED under mutant "night is the run count" (``Session.observing_nights``
    keying each run by its own id, ``key = rid``), at the second Run of
    night 2 (the first CONTINUE of night 2 reads 2 either way, one run so
    far plus tonight), observed:

        AssertionError: at +1820.0 s of night 2: the second Run of night 2
        answered {'id': 'b5b3378461954a9d927c59c599417bf9', 'night': 3,
        'continued': True, 'kept': 8, 'new': 0, 'dropped': 0}
          At index 2 diff: 3 != 2

    RED under mutant "set-aside carried to the next night"
    (``Session.set_aside_on`` answering every record, whatever its night):
    night 2's run finds 2-2 set aside "earlier tonight", has nothing live
    to shoot and ends at once, dormant and armed again, before the first
    read of night 2, observed:

        AssertionError: at +0.0 s of night 2: the progress route reads
        {'id': '8e34429000d34568930666f7c6f0612e', 'status': 'dormant',
        'nights': 2, 'count_mode': 'accepted', 'armed': True,
        'plan_saved_ts': 1790655889.6635725}
          At index 2 diff: True != False

    RED under the harness mutant "the zone is UTC+10" (``_flow_night.
    ZONE_S`` made +10 h, the zone an unpinned suite would read in Sydney),
    observed first at night 1's report id:

        AssertionError: at +180.0 s of night 1: night 1's report ids are
        ['M31_2x2-20260902-114809']
          At index 0 diff: 'M31_2x2-20260902-114809' !=
          'M31_2x2-20260901-214809'

    and, with the two report-id literals taken out of a probe copy of this
    test (scratch only), what it does to the night count: night 2 starts
    at 11:48 local and the second Run, at 12:18, is past the noon rollover:

        AssertionError: at +1820.0 s of night 2: the second Run of night 2
        answered {'id': '69fdff9eca654127950a11fc7a59eef3', 'night': 3,
        'continued': True, 'kept': 8, 'new': 0, 'dropped': 0}

    RED under scenario 1's mutant "ledger write skipped for a cycle slot"
    too, in 3 s, at night 1's second visit, where `night_one` grades every
    read it takes, observed:

        AssertionError: at +20.0 s of night 1: the ledger is not the frames
        shot before the one in flight
          Right contains 1 more item:
          {('57a70e818f0655de964d752d978d6e37',
          'd43578252de55d368f77fa7aa340d8b4'): 1}

    With `night_one`'s own grading taken out as well (harness mutant
    "night one ungraded", the reads taken and not graded, as they were),
    the same mutant gave no answer in 90 s of real time (timeout, exit
    124), and before the grading was added it ran for over ten minutes
    until it was stopped by hand: each ungraded read waited out
    `FlowRig._report`'s two seconds for a report holding a frame the
    ledger never got (#523). The harness's wall budget now ends that run
    at 180 s (tests/_flow_night.py ``WALL_BUDGET_S`` quotes it).

    TWO REPORT IDS, ONE FOR EACH NIGHT'S FIRST RUN. The acceptance asks for
    two report ids and for a restart later in night 2. Every start appends
    a report id (``engine.start``, a resume's included), so the restart is
    a third, and it has to be: with one run a night, the run count and the
    night count are both 2, and "night is the run count" could not go red.
    So the session holds three ids, and the first two are each night's
    first run, named by literal.
    """
    rig: FlowRig = flow_rig
    fid = await rig.save_flow(FLOW, at="before night 1")
    saved = (await rig.flow(fid, at="before night 1"))["updated_ts"]
    plan = rig.plan_for(fid, FLOW)
    ids = steps_by_panel(plan)
    step_ids = [s.id for t in plan.targets for s in t.steps]

    one_night, one = await night_one(rig, fid)
    at = one.at
    sid = one.session.id
    assert one.session.nights == [NIGHT_ONE_RUN], (
        f"{at}: night 1's report ids are {one.session.nights}")
    assert (one.progress["session"]["status"], one.progress["session"]["armed"],
            one.progress["session"]["plan_saved_ts"]) == (
        "dormant", True, saved), (
        f"{at}: after night 1 the session reads {one.progress['session']}, "
        f"the flow was saved at {saved}")
    assert one.owed()["2-2"] == 6, f"premise, {at}: 2-2 is owed after night 1"
    night_one_shots = shot_counts(one_night.shots())

    # BETWEEN THE NIGHTS: night 1 has ended where ``one`` was read, and no
    # night 2 exists yet, so the moment is named by night 1's end.
    at = f"between the nights, after night 1 ended {one.at}"
    await rig.put_flow(fid, moved(FLOW), at=at)
    resaved = (await rig.flow(fid, at=at))["updated_ts"]
    between = await _progress(rig, fid, at)
    assert between["session"]["plan_saved_ts"] == saved < resaved, (
        f"{at}: after the save, the session holds "
        f"{between['session']['plan_saved_ts']}, the flow {resaved}")
    assert rig.plan_for(fid, moved(FLOW)).targets[0].steps[0].id == \
        step_ids[0], f"premise, {at}: moving the REPORT node moves no id"

    night = await rig.night(day=1)
    night.hold(night.clock.t)
    visit_two: list[dict] = []

    def second_visit(rec: dict) -> None:
        if rec["visit"] == 2 and not visit_two:
            visit_two.append(rec)
            night.hold(rec["t"])

    night.on_capture = second_visit
    r = await rig.run(fid)
    at = "at +0.0 s of night 2"
    assert r.status_code == 200, f"{at}: {r.text}"
    out = r.json()["session"]
    assert (out["id"], out["continued"], out["night"], out["kept"],
            out["new"], out["dropped"]) == (sid, True, 2, 8, 0, 0), (
        f"{at}: /run answered {out}")
    assert [s.id for t in night.engine.plan.targets for s in t.steps] == \
        step_ids, f"{at}: night 2 runs other step ids"
    assert night.said("continues counting") == [], (
        f"{at}: a recount was spoken of: {night.said('continues counting')}")
    two = await _progress(rig, fid, at)
    assert (two["session"]["id"], two["session"]["nights"],
            two["session"]["armed"], two["session"]["plan_saved_ts"]) == (
        sid, 2, False, resaved), (
        f"{at}: the progress route reads {two['session']}")

    night.release()
    assert await night.settle(), (
        f"{rig.at(night)}: night 2 never reached 2-2's second visit: 2-2 "
        f"was not retried")
    mid = await rig.read(night, fid)
    at = mid.at
    assert visit_two[0]["target"] == MISSES, (
        f"{at}: night 2's second visit is to {visit_two[0]['target']}")
    assert mid.session.nights == [NIGHT_ONE_RUN, NIGHT_TWO_RUN], (
        f"{at}: the session holds the report ids {mid.session.nights}")
    tonight = shot_counts(night.shots()[:-1])
    assert mid.ledger() == {
        ids[k]: night_one_shots.get(k, 0) + tonight.get(k, 0)
        for k in set(night_one_shots) | set(tonight)}, (
        f"{at}: the ledger is not night 1's frames and night 2's so far")
    assert_the_three_agree(mid, plan)
    assert mid.progress["session"]["nights"] == 2, at

    # THE OPERATOR'S STOP, with the night still held: the abort cancels the
    # run before its first await, and only then is the clock let go, for
    # the wind-down alone.
    # "aborting" OR "aborted": either means the stop reached the engine. On
    # the Linux CI runner the abort and its wind-down both finish before the
    # first poll sees the transient "aborting" (diagnosed in run 36817980463:
    # engine state 'aborted', running=False, the stop task done with no
    # exception), so the wind-down on this path does not wait on the held clock.
    REACHED = ("aborting", "aborted")
    stop = asyncio.create_task(rig.abort(at=at))
    # A wall-clock deadline, not an iteration count: 2000 sleeps of 1 ms are
    # about 31 s on Windows (each sleep rounds up to the 15.6 ms timer) but
    # about 2 s on Linux, where a loaded CI runner did not reach "aborting"
    # in time (run 36814198383, 2026-10-01).
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        if night.engine.state.get("state") in REACHED:
            break
        await asyncio.sleep(0.001)
    if night.engine.state.get("state") not in REACHED:
        # Make the failure explain itself (#610 follow-up, CI runs 36814198383
        # and 36816188010): what the engine says, and where the stop task is.
        where = "running"
        if stop.done():
            where = "done: %r" % (stop.exception(),)
        else:
            where = " <- ".join("%s:%d %s" % (fr.f_code.co_filename.rsplit("/", 1)[-1].rsplit(chr(92), 1)[-1], fr.f_lineno, fr.f_code.co_name) for fr in stop.get_stack(limit=12))
        raise AssertionError(
            f"{at}: the stop never reached the engine; engine state "
            f"{night.engine.state.get('state')!r}, running="
            f"{night.engine.running}; the stop task is {where}")
    night.release()
    await stop
    await night.finish()

    night.advance(PAUSE_S)
    night.record_again()
    night.hold(night.clock.t)
    r = await rig.run(fid)
    at = f"at +{night.rel(night.clock.t):.1f} s of night 2"
    assert r.status_code == 200, f"{at}: {r.text}"
    out = r.json()["session"]
    assert (out["id"], out["continued"], out["night"]) == (sid, True, 2), (
        f"{at}: the second Run of night 2 answered {out}")
    again = await _progress(rig, fid, at)
    assert (again["session"]["nights"], again["session"]["plan_saved_ts"]) == (
        2, resaved), f"{at}: the progress route reads {again['session']}"

    await night.finish()
    end = await rig.read(night, fid)
    at = end.at
    assert len(end.session.nights) == 3 and \
        end.session.nights[:2] == [NIGHT_ONE_RUN, NIGHT_TWO_RUN], (
            f"{at}: the session holds the report ids {end.session.nights}")
    assert end.progress["session"]["nights"] == 2, (
        f"{at}: three runs on two nights read as "
        f"{end.progress['session']['nights']} nights")
    # The exposure in flight when the operator stopped the run was cut off,
    # and a frame never exposed to its end is never banked: every other
    # capture of night 2 is a frame.
    cut = visit_two[0]
    night_two_shots = shot_counts([(c["target"], c["filter"])
                                   for c in night.captures if c is not cut])
    assert set(k for k in night_two_shots) == {("2-2", "L"), ("2-2", "R")}, (
        f"{at}: night 2 shot {night_two_shots}")
    assert end.ledger() == {
        ids[k]: night_one_shots.get(k, 0) + night_two_shots.get(k, 0)
        for k in ids}, (f"{at}: the ledger does not add up across the "
                        f"nights")
    assert_the_three_agree(end, plan)
    assert end.owed() == {"1-1": 0, "1-2": 0, "2-1": 0, "2-2": 0}, (
        f"{at}: the progress route owes {end.owed()}")
    assert end.progress["session"]["status"] == "complete", (
        f"{at}: the session ends {end.progress['session']}")
