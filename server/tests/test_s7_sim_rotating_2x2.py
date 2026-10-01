"""A rotating 2x2 end to end, from a stored flow to the readers (#189 S7
item 1, the first of its four simulator scenarios; spec 5.1, 5.2, 5.4, 5.10,
6.9).

The flow is the S5 probe's (tests/_flow_night.py ``mosaic_flow``): a TARGET
2x2 "Rotate to PA" 55 at 25% overlap framed for the simulator's field, a
FILTER CYCLE of ``L 10, R 10`` for three cycles, the loop wire, a REPORT. It
is saved through ``POST /api/flows`` and run through ``POST
/api/flows/{id}/run``, which compiles it and starts the clocked engine
(tests/_group_harness.py); the centre stands 2 h east of the meridian at
the harness's ``T0``, 67 degrees up, so nothing but the group driver
decides the night.

The night is held at the first exposure of every visit, and each time the
routes are read (`FlowRig.read`): what the UI reads must be the capture in
flight, and the ledger, the reports and the progress route must agree with
the frames the night shot, then and at the end.

MUTANTS, each run in a private copy of ``server/`` (scratchpad
``S7-E2E-mut``), from a byte backup, never in the shared tree; the failure
each produced is quoted in the test that caught it.
"""
from __future__ import annotations

import time

import pytest

from _flow_night import (FlowRig, assert_the_three_agree, flow_rig,  # noqa: F401
                         mosaic_flow, panel_label, shot_counts, steps_by_panel)
from _group_harness import group_store, ra_at  # noqa: F401
from astrodeck.sequence.panel_order import order_panels

FLOW = mosaic_flow(ra_hours=ra_at(-2.0), plan="L 10, R 10", cycles=3)
#: Every pass visits the four panels in the snake order ``panel_order``
#: gives a 2x2 whose panels tie on completion (1-1 1-2 2-2 2-1), and every
#: pass starts with them tied: each panel shot one pass in the last. So the
#: night is this, three times, each visit one pass of the cycle, L then R.
PASS = ("1-1", "1-2", "2-2", "2-1")
#: The watchdog's bound in the spin case, in real seconds, as
#: test_group_harness_watchdog.py shortens it: many loop turns long.
BOUND_S = 1.0
#: The spin's own real-time limit, well past the bound, so only a missing
#: watchdog lets the spin reach it, and such a run ends instead of hanging.
SPIN_LIMIT_S = 8.0 * BOUND_S


async def test_a_rotating_2x2_visits_one_pass_at_a_time_and_every_reader_agrees(
        flow_rig):
    """Visits follow ``panel_order``, one pass of the cycle per visit; at
    the first exposure of every visit the state the UI reads names the
    rotating group, the panel in flight and its pass (as ``Night.on_capture``
    saw the engine's own state then), a viewer is served the same state (a
    group not waiting on the meridian withholds nothing, spec 6.9, 5.10),
    and the ledger holds exactly the frames shot before it, which the
    reports and the progress route repeat. At the end every step holds its
    three frames and the session is complete.

    RED under mutant "panel-first" (``group_rules.VisitBound.ends_at_round``
    answering False past its ``complete`` check, so a visit runs its panel
    to completion while the group still says rotate), observed:

        AssertionError: at +240.0 s of night 1: the visits were [('M31
        1-1', ('L', 'R', 'L', 'R', 'L', 'R')), ('M31 1-2', ('L', 'R', 'L',
        'R', 'L', 'R')), ('M31 2-2', ('L', 'R', 'L', 'R', 'L', 'R')),
        ('M31 2-1', ('L', 'R', 'L', 'R', 'L', 'R'))]
          At index 0 diff: ('M31 1-1', ('L', 'R', 'L', 'R', 'L', 'R')) !=
          ('M31 1-1', ('L', 'R'))
          Right contains 8 more items, first extra item: ('M31 1-1', ('L',
          'R'))

    RED under mutant "ledger write skipped for a cycle slot"
    (``SequenceEngine._record_session_frame`` returning None before its
    append for a frame of the cycle's R slot), at the second visit's first
    exposure, observed:

        AssertionError: at +20.0 s of night 1: the ledger is not the frames
        shot before the one in flight
        assert {('05f5e967a1...a162db6c'): 1} == {('05f5e967a1...a162db6c'):
        1}
          Right contains 1 more item:
          {('05f5e967a108594087187ed1cbbb9267',
          '451eceda24415024b4a42fb8776b63fd'): 1}

    RED under mutant "no report at the start" (#517: ``SessionReporter.
    record_policy`` without its ``_schedule_write()``, as it was), at the
    first exposure of the night, where the session already names the run's
    report and the ledger holds nothing, observed:

        AssertionError: at +0.0 s of night 1: GET
        /api/reports/M31_2x2-20260901-214809, a run the session names,
        holding 0 frame(s) in the ledger: {"detail":"report not found"}

    RED under the harness mutant "hold ignored" (``_group_harness.Night.
    _drive`` waking past a hold, its ``_hold_at`` branch made ``if False``),
    the check that the reads were taken where the test says, observed:

        AssertionError: at +240.0 s of night 1: the night was held at 0
        visits, not 12
    """
    rig: FlowRig = flow_rig
    night = await rig.night()
    fid = await rig.save_flow(FLOW)
    plan = rig.plan_for(fid, FLOW)
    ids = steps_by_panel(plan)
    first = [panel_label(t.name) for t in order_panels(
        plan.targets, cols=2, policy=plan.groups[0].order)]
    assert tuple(first) == PASS, (
        f"premise, before the night: panel_order's first pass is the "
        f"snake order: {first}")

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

    held = 0
    while await night.settle():
        rec = night.captures[-1]
        reading = await rig.read(night, fid)
        at = reading.at
        held += 1
        visit = len(seen) - 1
        group = reading.state.get("group") or {}
        assert (group.get("mode"), group.get("panel"), group.get("pass")) == (
            "rotate", panel_label(rec["target"]), visit // 4 + 1), (
            f"{at}: the state names {group} while {rec['target']} is shot "
            f"in visit {visit + 1}")
        assert (group["panel"], group["pass"]) == (
            rec["group"]["panel"], rec["group"]["pass"]), (
            f"{at}: the route and the engine's state at the capture differ")
        assert group["meridian_wait"] is False, f"{at}: no meridian wait"
        assert reading.viewer == reading.state, (
            f"{at}: a viewer is served another state outside a meridian wait")
        assert reading.state["session"]["id"] == reading.session.id, (
            f"{at}: the state names another session than the route")
        assert reading.progress["session"]["status"] == "active", at
        before = shot_counts(night.shots()[:-1])
        assert reading.ledger() == {ids[k]: n for k, n in before.items()}, (
            f"{at}: the ledger is not the frames shot before the one in "
            f"flight")
        assert_the_three_agree(reading, plan)
        night.release()
    night.on_capture = None

    end = await rig.read(night, fid)
    at = end.at
    assert night.visits() == [(p, ("L", "R")) for _ in range(3) for p in
                              [f"M31 {label}" for label in PASS]], (
        f"{at}: the visits were {night.visits()}")
    assert held == 12, f"{at}: the night was held at {held} visits, not 12"
    shots = shot_counts(night.shots())
    assert shots == {k: 3 for k in ids}, f"{at}: shots {shots}"
    assert end.ledger() == {ids[k]: n for k, n in shots.items()}, (
        f"{at}: each step's ledger count is not Night.shots for its panel "
        f"and filter")
    assert_the_three_agree(end, plan)
    assert end.progress["session"]["status"] == "complete", (
        f"{at}: the session ends {end.progress['session']}")
    (rid,) = end.session.nights
    assert end.reports[rid]["end_reason"] == "complete", at


async def test_a_spin_in_a_route_started_run_fails_with_the_watchdog_report(
        flow_rig, monkeypatch):
    """The watchdog a ROUTE-STARTED run has (`Night.arm`). ``POST
    /api/flows/{id}/run`` starts the engine, so `Night.run` and the watchdog
    it arms never run (#319); `FlowRig.night` arms one of its own, and
    `Night.settle` reads its report. A spin that never yields inside the
    engine's run task must fail the scenario's wait with that report, naming
    the frame that spun, within a few bounds, and never hang the suite.

    The spin is in the engine's own call, ``_record_session_frame``, as the
    night banks its third frame, and it carries a real-time limit of its own
    (``SPIN_LIMIT_S``, eight bounds; test_group_harness_watchdog.py's
    pattern), so with the watchdog missing the spin runs out and the night
    goes on, and the case fails rather than hangs. The CONTROL is the first
    case in this file: the same watchdog armed for a whole night that never
    spins, and no report. Both mutants below ran in the private copy
    scratchpad ``S7-E2E-verify-mut``, from byte backups.

    RED under harness mutant "arm never" (`Night.arm` returning before it
    starts the watchdog), where the spin runs out and the night goes on to
    its end, observed:

        AssertionError: at +240.0 s of night 1: the spin ran its full 8.0 s:
        nothing broke it

    RED under harness mutant "settle ignores the report"
    (`Night._check_watchdog` returning at once), where the watchdog breaks
    the spin and the run task ends with it, and nothing says why, observed:

        AssertionError: at +30.0 s of night 1: the night was not failed;
        settle answered False
    """
    rig: FlowRig = flow_rig
    night = await rig.night(spin_bound_s=BOUND_S)
    fid = await rig.save_flow(FLOW)
    real = night.engine._record_session_frame
    spins: list[float] = []
    spun_out: list[float] = []

    def spin_at_the_third_frame(*a, **kw):
        if not spins and len(night.engine._session.frames) == 2:
            started = time.monotonic()
            spins.append(started)
            while time.monotonic() - started < SPIN_LIMIT_S:
                pass                   # no await: the loop never comes back
            spun_out.append(time.monotonic() - started)
        return real(*a, **kw)

    monkeypatch.setattr(night.engine, "_record_session_frame",
                        spin_at_the_third_frame)
    r = await rig.run(fid)
    assert r.status_code == 200, f"at +0.0 s of night 1: {r.text}"
    report: str | None = None
    ended = None
    try:
        ended = await night.settle()
    except pytest.fail.Exception as e:
        report = str(e)
    at = rig.at(night)
    assert spins, f"premise, {at}: the night never banked a third frame"
    assert not spun_out, (
        f"{at}: the spin ran its full {spun_out[0]:.1f} s: nothing broke it")
    assert report is not None, (
        f"{at}: the night was not failed; settle answered {ended!r}")
    took = time.monotonic() - spins[0]
    assert took < BOUND_S + 3.0, (
        f"{at}: the night failed {took:.1f} s after the spin began, for a "
        f"bound of {BOUND_S:g} s")
    assert "Night (route-started run): the event loop did not come back" \
        in report and "#319" in report, f"{at}: the failure was {report}"
    spinning = next((ln for ln in report.splitlines()
                     if "spinning in " in ln), "")
    assert "spin_at_the_third_frame (" in spinning, (
        f"{at}: the report does not name the frame that spun:\n{report}")
