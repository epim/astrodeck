"""A forced solve failure on one panel of a rotating 2x2, end to end (#189 S7
item 1, the second of its four simulator scenarios; spec 5.1's table, 5.6
step 4, 6.7, 6.8; Revision 2 ruling 5, ``max_failed_visits`` 3; since H4,
#534 and H4 orchestrator ruling 2).

The flow is scenario 1's (tests/test_s7_sim_rotating_2x2.py): the S5
probe's 2x2 at Rotate to PA 55, ``L 10, R 10`` for three cycles, 2 h east of
the meridian. The goto script answers ``centered: False`` for 2-2 on every
attempt, as a solve that never lands does (``error_arcmin`` None, the raw
GoTo fallback), and every other panel centres.

What must follow, from the table in spec 5.1: 2-2's miss is a
``PanelDeferred``, so the panel goes behind the others and is retried on
the next pass; at its THIRD consecutive failed pass it is set aside
(``max_failed_visits`` 3), recorded in the session for tonight's key, and
named in the published group with the reason; the other three complete;
the run ends owing 2-2's frames, so the session is dormant and still armed
for the next night (6.7: set aside is not done).

RE-PINNED IN H4 (#534, H4 orchestrator ruling 2), deliberately. Two things
changed what this night does, and both are the ruling's:

* a centring miss is COUNTED WHERE ITS PASS CLOSES, not at its visit, since
  only the whole pass says whether the panel or the sky is to blame. So
  2-2's third miss, at 120 s, sets it aside at the end of pass 3, 180 s,
  after the other panels' last exposures: at every held exposure the
  published set-aside list is empty, and it names 2-2 from 180 s on;
* a CENTRING SET-ASIDE EXPIRES, once a night, 45 minutes after it was made
  (or once the panel has climbed 10 degrees, which at the fixture site's
  40 N nothing does in 45 minutes). So 2-2 is set aside FOR NOW at 180 s,
  tried once more at 2880 s, deferred twice more 300 s apart as a lone
  panel is, and set aside for the rest of the night at 3480 s, where the
  report marks it skipped. The session holds both records, the first
  marked expired.

Before H4 the night was: tried at 40, 60 and 120 s, set aside for the night
at 120 s, skipped in the report at 120 s, one record. Everything else it
grades (the other three complete, the ledger, the reports and the progress
route agree, the session ends dormant and armed, owing 2-2's six frames) is
unchanged.

MUTANTS, each run in a private copy of ``server/`` (scratchpad
``S7-E2E-mut``, and for the H4 re-pin ``H4-ENG-A-r2-mut``), from a byte
backup, never in the shared tree; each failure is quoted where it was
observed.
"""
from __future__ import annotations

from _flow_night import (T0, FlowRig, assert_the_three_agree,  # noqa: F401
                         flow_rig, mosaic_flow, panel_label, shot_counts,
                         steps_by_panel)
from _group_harness import group_store, ra_at  # noqa: F401

FLOW = mosaic_flow(ra_hours=ra_at(-2.0), plan="L 10, R 10", cycles=3)
MISSES = "M31 2-2"
#: The reason the group driver sets 2-2 aside with (``GroupRun.
#: _count_failure``), up to the last error, which is the hub's.
ASIDE = "centring failed on 2-2 on 3 consecutive visits"
#: The night's key: 21:48 on 2026-09-01 in the harness's zone, UTC-4, on
#: every machine (tests/_flow_night.py, "NIGHT KEYS ANSWER THE SAME").
NIGHT_ONE = "2026-09-01"
#: When 2-2 is set aside for now (the end of pass 3), tried once more (its
#: expiry, 45 minutes later), and set aside for the night (its third miss
#: after the expiry, 300 s apart as a lone panel's deferrals are). Measured
#: on this night.
FOR_NOW, EXPIRY, FOR_THE_NIGHT = 180.0, 2880.0, 3480.0
#: A moment the run waits on 2-2's expiry, between the two set-asides, where
#: the routes are read with no exposure in flight.
WAITING = 1500.0


def misses_2_2(who: str, n: int, result: dict) -> dict:
    """Every centring of 2-2 fails as a solve that never lands does; the
    other panels centre (the harness's own answer)."""
    if who == MISSES:
        return {**result, "centered": False, "error_arcmin": None}
    return result


async def night_one(rig: FlowRig, fid: str, *, on_hold=None,
                    at: tuple[float, ...] = ()):
    """Night 1 of the flow saved at ``fid``, 2-2 never centring, held at the
    first exposure of every visit, where ``on_hold(night, reading, rec)`` is
    called with the routes' answers. Returns the night and its last
    reading, taken once the run has wound down. Shared with
    tests/test_s7_sim_continue_second_night.py, whose first night this is.

    ``at``: seconds into the night at which it is also held and read with
    no exposure in flight (H4: the run then waits on a set-aside's expiry,
    and shoots nothing). ``on_hold`` is called there with ``rec`` None. A
    hold there stands wherever the night last woke before that instant:
    the night's clock moves only at a wake.

    EVERY READ IS GRADED HERE (#523), whoever calls: after ``on_hold``, the
    ledger must be the frames shot before the one in flight, and the
    ledger, the reports and the progress route must agree. A read nobody
    grades is not free. Before this, scenario 3 read night 1 without
    grading it, and under the mutant "ledger write skipped for a cycle
    slot" every read then waited out `FlowRig._report`'s two seconds for a
    report that held a frame the ledger never got, visit after visit.
    Observed: no answer in over ten minutes of real time, when it was
    stopped by hand; with these two checks taken out again (the harness
    mutant "night one ungraded"), none in 90 s (timeout, exit 124). Graded
    here, the same mutant fails scenario 3 in 3 s, at night 1's second
    visit (its docstring quotes it)."""
    night = await rig.night(goto=misses_2_2)
    plan = rig.plan_for(fid, FLOW)
    ids = steps_by_panel(plan)
    seen: set[int] = set()
    in_flight: list[dict] = []
    clock_holds = sorted(night.t0 + float(a) for a in at)

    def first_exposure_of_each_visit(rec: dict) -> None:
        if rec["visit"] not in seen:
            seen.add(rec["visit"])
            in_flight[:] = [rec]
            night.hold(rec["t"])

    def hold_at_the_next_instant() -> None:
        if clock_holds:
            night.hold(clock_holds[0])

    night.on_capture = first_exposure_of_each_visit
    hold_at_the_next_instant()
    r = await rig.run(fid)
    assert r.status_code == 200, f"at +0.0 s of night 1: {r.text}"
    assert r.json()["session"]["continued"] is False, (
        f"at +0.0 s of night 1: a first run continued {r.json()}")
    while await night.settle():
        rec = in_flight.pop() if in_flight else None
        if rec is None:
            clock_holds.pop(0)
        reading = await rig.read(night, fid)
        if on_hold is not None:
            on_hold(night, reading, rec)
        shot = night.shots()[:-1] if rec is not None else night.shots()
        assert reading.ledger() == {
            ids[k]: n for k, n in shot_counts(shot).items()}, (
            f"{reading.at}: the ledger is not the frames shot before the one "
            f"in flight")
        assert_the_three_agree(reading, plan)
        night.release()
        hold_at_the_next_instant()
    night.on_capture = None
    return night, await rig.read(night, fid)


async def test_a_panel_that_never_centres_is_set_aside_at_its_third_pass(
        flow_rig):
    """2-2 is tried once a pass and deferred, and at the end of its third
    consecutive failed pass set aside for now; the other three panels
    complete. 45 minutes later its set-aside expires, it is tried once more,
    and three more misses set it aside for the rest of the night. While the
    night runs, the state the UI reads names the group's set-aside panel
    from the moment it is set aside and never before, with its reason, to a
    viewer too (words only, 6.9): at every held exposure the list is empty,
    and read while the run waits on the expiry it names 2-2. At the end the
    report records the set-aside for the night, the progress route shows
    2-2 owed and the rest banked, the ledger, the report and the route
    agree, and the session is dormant and armed, its two set-aside records
    keyed to tonight, the first expired.

    RED under mutant "centring miss drops the panel for the run"
    (``SequenceEngine._visit_panel``'s ``PanelDeferred`` arm, for a
    centring miss, dropping the member through ``_group_member_gone`` and
    returning False, the pre-S3 fate of a stopped panel), at the first
    exposure after 2-2's first miss, observed in H4 (S7 saw the same list
    under the check's old words, "before its third miss (1 so far)"):

        AssertionError: at +40.0 s of night 1: 2-2 is set aside before the
        end of its third pass: [{'panel': '2-2', 'reason': 'centring failed:
        plate solve failed — used raw GoTo'}]

    RED under mutant "set aside at the first miss" (``GroupRun.
    _count_failure``'s ``if n < self.max_failed_visits`` made ``if n <
    1``), observed in H4, where the first miss is counted at the end of
    pass 1:

        AssertionError: at +60.0 s of night 1: 2-2 is set aside before the
        end of its third pass: [{'panel': '2-2', 'reason': 'centring failed
        on 2-2 on 1 consecutive visit: plate solve failed — used raw
        GoTo'}]

    RED under mutant "set-aside never expires" (``_expire_or_wait`` asking
    no expiry: ``cause = None``), observed in H4:

        AssertionError: at +2880.0 s of night 1: 2-2 was tried at [40.0,
        60.0, 120.0] s, not three passes, its expiry and three more

    The CONTROL is the same check at every exposure: an empty set-aside
    list, nine times.
    """
    rig: FlowRig = flow_rig
    fid = await rig.save_flow(FLOW)
    plan = rig.plan_for(fid, FLOW)
    ids = steps_by_panel(plan)
    held: list[tuple[float, list]] = []
    waiting: list[dict] = []

    def on_hold(night, reading, rec) -> None:
        at = reading.at
        group = reading.state["group"]
        aside = group["set_aside"]
        if rec is None:
            # THE RUN WAITS ON 2-2'S EXPIRY: nothing is in flight, 2-2 is
            # named with the reason it was set aside for, and the published
            # words say it is set aside for now.
            waiting.append(reading.state)
            assert [a["panel"] for a in aside] == ["2-2"] and \
                aside[0]["reason"].startswith(ASIDE), (
                    f"{at}: while the run waits the group names {aside}")
        else:
            assert (group["mode"], group["panel"]) == (
                "rotate", panel_label(rec["target"])), f"{at}: {group}"
            assert aside == [], (f"{at}: 2-2 is set aside before the end of "
                                 f"its third pass: {aside}")
            held.append((reading.t, aside))
        assert reading.viewer == reading.state, (
            f"{at}: a viewer is served another state outside a meridian wait")
        # The ledger and the three-way agreement are graded by `night_one`
        # itself, right after this, at every held exposure.

    night, end = await night_one(rig, fid, on_hold=on_hold, at=(WAITING,))
    at = end.at
    tried = [night.rel(t) for t, who in night.gotos if who == MISSES]
    assert tried == [40.0, 60.0, 120.0, EXPIRY, EXPIRY + 300.0,
                     FOR_THE_NIGHT], (
        f"{at}: 2-2 was tried at {tried} s, not three passes, its expiry and "
        f"three more")
    assert night.visits() == [
        ("M31 1-1", ("L", "R")), ("M31 1-2", ("L", "R")), (MISSES, ()),
        ("M31 2-1", ("L", "R")),
        (MISSES, ()), ("M31 1-1", ("L", "R")), ("M31 1-2", ("L", "R")),
        ("M31 2-1", ("L", "R")),
        (MISSES, ()), ("M31 1-1", ("L", "R")), ("M31 1-2", ("L", "R")),
        ("M31 2-1", ("L", "R")),
        (MISSES, ()), (MISSES, ()), (MISSES, ())], (
        f"{at}: the visits were {night.visits()}")
    assert len(held) == 9, f"{at}: the held exposures: {held}"
    assert len(waiting) == 1 and waiting[0]["detail"] == (
        "M31: 2-2 is set aside for now; waiting to try it once more"), (
        f"{at}: the state while the run waited: {waiting}")
    # FROM THE MOMENT IT IS SET ASIDE AND NEVER BEFORE, in every state the
    # engine published, not only the ones read.
    named = [s for s in night.states
             if (s.get("group") or {}).get("set_aside")]
    first = next(e[0] for e in night.trace if e[1] == "state"
                 and ((e[2].get("group") or {}).get("set_aside")))
    assert named and first == FOR_NOW, (
        f"{at}: the group first named a set-aside panel at {first} s")
    counted = night.said("consecutive)")
    assert [m.split("(")[-1] for m in counted] == [
        "1 of 3 consecutive)", "2 of 3 consecutive)"] * 2, (
        f"{at}: the counted lines were {counted}")
    asides = [(night.rel(t), m) for t, lvl, m in night.lines
              if lvl == "warning" and ASIDE in m]
    assert [t for t, _m in asides] == [FOR_NOW, FOR_THE_NIGHT], asides
    assert "set aside for now, not for the night" in asides[0][1], asides[0]
    assert "a restart tonight does not retry it" in asides[1][1], asides[1]
    assert night.said("M31: 2-2's set-aside has expired (45 minutes have "
                      "passed)"), f"{at}: no expiry was said"

    shots = shot_counts(night.shots())
    assert shots == {k: 3 for k in ids if k[0] != "2-2"}, f"{at}: {shots}"
    assert end.ledger() == {ids[k]: n for k, n in shots.items()}, (
        f"{at}: each step's ledger count is not Night.shots for its panel "
        f"and filter")
    assert_the_three_agree(end, plan)
    assert end.owed() == {"1-1": 0, "1-2": 0, "2-1": 0, "2-2": 6}, (
        f"{at}: the progress route owes {end.owed()}")
    (rid,) = end.session.nights
    report = end.reports[rid]
    assert report["end_reason"] == "incomplete", f"{at}: {report['end_reason']}"
    # The report names the panel and not its reason, which spec 6.7 asks
    # for (#524): this pins the entry `mark_skipped` writes today, and the
    # fix for #524 changes it to carry ASIDE. Since H4 it is written once
    # the panel is set aside for the night, not while it may still expire.
    assert report["safety_events"] == [
        {"ts": T0 + FOR_THE_NIGHT, "reason": f"skipped {MISSES}",
         "action": "skip"}], (
        f"{at}: the report records {report['safety_events']}")
    first_rec, second_rec = end.session.set_aside
    for record, ts in ((first_rec, FOR_NOW), (second_rec, FOR_THE_NIGHT)):
        assert (record["target_id"], record["step_id"], record["night"],
                record["kind"], record["ts"]) == (
            ids[("2-2", "L")][0], None, NIGHT_ONE, "centring", T0 + ts) and \
            record["reason"].startswith(ASIDE), (
                f"{at}: the session's set-aside record is {record}")
    assert (first_rec.get("expired"), second_rec.get("expired")) == (
        True, None), f"{at}: {end.session.set_aside}"
    assert (end.progress["session"]["status"],
            end.progress["session"]["armed"]) == ("dormant", True), (
        f"{at}: the session ends {end.progress['session']}")
    assert (end.session.status, end.session.auto_resume) == (
        "dormant", True), f"{at}: the session file ends {end.session.status}"
