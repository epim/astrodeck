"""A forced solve failure on one panel of a rotating 2x2, end to end (#189 S7
item 1, the second of its four simulator scenarios; spec 5.1's table, 5.6
step 4, 6.7, 6.8; Revision 2 ruling 5, ``max_failed_visits`` 3).

The flow is scenario 1's (tests/test_s7_sim_rotating_2x2.py): the S5
probe's 2x2 at Rotate to PA 55, ``L 10, R 10`` for three cycles, 2 h east of
the meridian. The goto script answers ``centered: False`` for 2-2 on every
attempt, as a solve that never lands does (``error_arcmin`` None, the raw
GoTo fallback), and every other panel centres.

What must follow, from the table in spec 5.1: 2-2's miss is a
``PanelDeferred``, so the panel goes behind the others and is retried on
the next pass; at its THIRD consecutive failed pass it is set aside tonight
(``max_failed_visits`` 3), recorded in the session for tonight's key and in
the report, and named in the published group with the reason; the other
three complete; the run ends owing 2-2's frames, so the session is dormant
and still armed for the next night (6.7: set aside is not done).

MUTANTS, each run in a private copy of ``server/`` (scratchpad
``S7-E2E-mut``), from a byte backup, never in the shared tree; each failure
is quoted where it was observed.
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


def misses_2_2(who: str, n: int, result: dict) -> dict:
    """Every centring of 2-2 fails as a solve that never lands does; the
    other panels centre (the harness's own answer)."""
    if who == MISSES:
        return {**result, "centered": False, "error_arcmin": None}
    return result


async def night_one(rig: FlowRig, fid: str, *, on_hold=None):
    """Night 1 of the flow saved at ``fid``, 2-2 never centring, held at the
    first exposure of every visit, where ``on_hold(night, reading, rec)`` is
    called with the routes' answers. Returns the night and its last
    reading, taken once the run has wound down. Shared with
    tests/test_s7_sim_continue_second_night.py, whose first night this is.

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

    def first_exposure_of_each_visit(rec: dict) -> None:
        if rec["visit"] not in seen:
            seen.add(rec["visit"])
            night.hold(rec["t"])

    night.on_capture = first_exposure_of_each_visit
    r = await rig.run(fid)
    assert r.status_code == 200, f"at +0.0 s of night 1: {r.text}"
    assert r.json()["session"]["continued"] is False, (
        f"at +0.0 s of night 1: a first run continued {r.json()}")
    while await night.settle():
        reading = await rig.read(night, fid)
        if on_hold is not None:
            on_hold(night, reading, night.captures[-1])
        assert reading.ledger() == {
            ids[k]: n for k, n in shot_counts(night.shots()[:-1]).items()}, (
            f"{reading.at}: the ledger is not the frames shot before the one "
            f"in flight")
        assert_the_three_agree(reading, plan)
        night.release()
    night.on_capture = None
    return night, await rig.read(night, fid)


async def test_a_panel_that_never_centres_is_set_aside_at_its_third_pass(
        flow_rig):
    """2-2 is tried once a pass and deferred, and at its third consecutive
    failed pass set aside for tonight; the other three panels complete.
    While the night runs, the state the UI reads names the group's set-aside
    panel from the moment it is set aside and never before, with its
    reason, to a viewer too (words only, 6.9); at the end the report records
    the set-aside, the progress route shows 2-2 owed and the rest banked,
    the ledger, the report and the route agree, and the session is dormant
    and armed, its set-aside record keyed to tonight.

    RED under mutant "centring miss drops the panel for the run"
    (``SequenceEngine._visit_panel``'s ``PanelDeferred`` arm, for a
    centring miss, dropping the member through ``_group_member_gone`` and
    returning False, the pre-S3 fate of a stopped panel), at the first
    exposure after 2-2's first miss, observed:

        AssertionError: at +40.0 s of night 1: 2-2 is set aside before its
        third miss (1 so far): [{'panel': '2-2', 'reason': 'centring
        failed: plate solve failed — used raw GoTo'}]

    RED under mutant "set aside at the first miss" (``GroupRun.
    _count_failure``'s ``if n < self.max_failed_visits`` made ``if n <
    1``), observed:

        AssertionError: at +40.0 s of night 1: 2-2 is set aside before its
        third miss (1 so far): [{'panel': '2-2', 'reason': 'centring failed
        on 2-2 on 1 consecutive visit: plate solve failed — used raw
        GoTo'}]

    The CONTROL is the same check at every exposure before 2-2's third
    miss: an empty set-aside list, six times.
    """
    rig: FlowRig = flow_rig
    fid = await rig.save_flow(FLOW)
    plan = rig.plan_for(fid, FLOW)
    ids = steps_by_panel(plan)
    held: list[tuple[float, list]] = []

    def on_hold(night, reading, rec) -> None:
        at = reading.at
        group = reading.state["group"]
        assert (group["mode"], group["panel"]) == (
            "rotate", panel_label(rec["target"])), f"{at}: {group}"
        misses = [t for t, who in night.gotos if who == MISSES]
        aside = group["set_aside"]
        if len(misses) < 3:
            assert aside == [], (f"{at}: 2-2 is set aside before its third "
                                 f"miss ({len(misses)} so far): {aside}")
        else:
            assert [a["panel"] for a in aside] == ["2-2"] and \
                aside[0]["reason"].startswith(ASIDE), (
                    f"{at}: after 2-2's third miss the group names {aside}")
        assert reading.viewer == reading.state, (
            f"{at}: a viewer is served another state outside a meridian wait")
        # The ledger and the three-way agreement are graded by `night_one`
        # itself, right after this, at every held exposure.
        held.append((reading.t, aside))

    night, end = await night_one(rig, fid, on_hold=on_hold)
    at = end.at
    tried = [night.rel(t) for t, who in night.gotos if who == MISSES]
    assert tried == [40.0, 60.0, 120.0], (
        f"{at}: 2-2 was tried at {tried} s, not once a pass three times")
    assert night.visits() == [
        ("M31 1-1", ("L", "R")), ("M31 1-2", ("L", "R")), (MISSES, ()),
        ("M31 2-1", ("L", "R")),
        (MISSES, ()), ("M31 1-1", ("L", "R")), ("M31 1-2", ("L", "R")),
        ("M31 2-1", ("L", "R")),
        (MISSES, ()), ("M31 1-1", ("L", "R")), ("M31 1-2", ("L", "R")),
        ("M31 2-1", ("L", "R"))], f"{at}: the visits were {night.visits()}"
    assert [len(aside) for _t, aside in held] == [0, 0, 0, 0, 0, 0, 1, 1, 1], (
            f"{at}: the group's set-aside list at each held exposure: {held}")
    deferred = night.said("centring failed on 2-2")
    assert [line.split("(")[-1] for line in deferred[:2]] == [
        "1 of 3 consecutive)", "2 of 3 consecutive)"] and \
        ASIDE in deferred[2], f"{at}: the deferral lines were {deferred}"

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
    # fix for #524 changes it to carry ASIDE.
    assert report["safety_events"] == [
        {"ts": T0 + 120.0, "reason": f"skipped {MISSES}", "action": "skip"}], (
        f"{at}: the report records {report['safety_events']}")
    (record,) = end.session.set_aside
    assert (record["target_id"], record["step_id"], record["night"]) == (
        ids[("2-2", "L")][0], None, NIGHT_ONE) and \
        record["reason"].startswith(ASIDE), (
            f"{at}: the session's set-aside record is {record}")
    assert (end.progress["session"]["status"],
            end.progress["session"]["armed"]) == ("dormant", True), (
        f"{at}: the session ends {end.progress['session']}")
    assert (end.session.status, end.session.auto_resume) == (
        "dormant", True), f"{at}: the session file ends {end.session.status}"
