# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""With no site saved, a dusk/dawn-windowed run REFUSES TO START, naming the
missing setting, and a flow that will need one is warned the same way at
compile (backlog ruling D-08, owner-approved 2026-09-30; #559, #582; spec
1.6, 5.1, 5.9; the #24 class). ResumeArm's own no-site line (#527, its
engine and resume_arm lines) is unrelated and untouched.

THE DEFECT (#527, then #559). On a rig with no saved site, a DUSK WINDOW
flow waited for dusk at the 0,0 placeholder: staged on a fresh simulator at
23:18 local, the run read "waiting for start time" with ``eta_s`` 42862, and
nothing on screen said the window was computed for a site that does not
exist. H4-SCHED made `schedule.resolve_window` answer None for a dusk or
dawn boundary with no site (tests/test_h4_dusk_window_needs_a_site.py), so
the run STARTED INSTEAD, immediately, with a dawn stop that was None too --
#559 found nothing then ends such a run at daylight; a roof or dome is left
however the run left it until the reject guards or the plan's own end stop
it. The engine said so once, at the run's start (`_say_sun_windows_unresolved`),
but #582 found the said line's two clauses ungraded: no test could tell
either from an unconditional ``True``.

AS BUILT (D-08: refuse to start, fail closed).

* `sequence.engine.start` refuses -- a plain `DeviceError`, raised before
  anything moves -- a plan with a non-calibration, dusk/dawn-scheduled
  target when no site is saved, naming the target(s) and the missing
  setting (`schedule.sun_window_needs_a_site`). On both the fresh and the
  resumed path: a resume re-freezes its windows exactly as a fresh start
  does, so a site cleared between nights refuses a resume too.
* `flows.to_plan.to_sequence_plan` is pure (no site, no clock beyond
  ``when``) and so cannot say whether a site actually is saved; it shows
  the SAME sentence as a compile-time warning on any flow built with a
  dusk/dawn boundary, so the dependency is visible before anyone presses
  Run, whether or not a site happens to be saved today.
* `_say_sun_windows_unresolved`, which only ever logged after a run no
  site saved should never have started, is gone with the behaviour it
  described: `start` refuses before it would ever have been reached, for
  any plan it would have fired for.
* ResumeArm's own `_dusk_without_a_site` (coincidentally similar wording,
  "DUSK window of ...") is a DIFFERENT function, for a DIFFERENT case (an
  armed session whose window never opens), and is not touched by D-08:
  those tests, below, are unchanged.

THE NIGHT is the clocked simulator (tests/_group_harness.py) on the real
default site, ``config.Site()``, at 12:00 UTC, when the placeholder's dusk
is hours ahead: the plan's one target carries a schedule a DUSK WINDOW
card can compile to (`flows.compile`). ResumeArm's case is its real `tick`
on the same default site, with a stand-in engine that is not running.

Every mutant was applied in a private copy of ``server/`` (scratchpad
``w9-WP-55-mut``), from a byte backup restored and sha256-checked after
each, never in the shared tree (#254). The observed failure is quoted
verbatim (the first assertion line, long lines wrapped).
"""
from __future__ import annotations

import calendar
import time
from types import SimpleNamespace

import pytest

from _group_harness import Night, group_hub, group_store, single  # noqa: F401
from _site_tracking import numeric_tokens
from astrodeck.config import Site
from astrodeck.devices.base import DeviceError
from astrodeck.sequence.models import Schedule, SequencePlan
from astrodeck.sequence.resume_arm import ResumeArm
from astrodeck.sequence.session import Session, session_store

#: 2026-09-02 12:00 UTC: daylight at longitude 0, so the placeholder's dusk
#: is hours ahead (the Sun reaches -12 there near 18:50 UTC), and daylight at
#: the fixture site too (08:00 local at 74 W), so a saved site's window is
#: shut as well and the control below can tell the two apart.
NOON_AT_0_0 = float(calendar.timegm((2026, 9, 2, 12, 0, 0)))
#: The clause phrases `schedule.sun_window_needs_a_site` names, so a test
#: can tell one was graded from the other being merely absent by accident.
START_CLAUSE = "find dusk to start by"
STOP_CLAUSE = "find dawn to stop by"


def _plan_with_schedule(sched: Schedule) -> SequencePlan:
    """One target carrying ``sched``, otherwise a plain single-target plan."""
    t = single("M31", count=2, ha_h=-1.0)
    t.schedule = sched
    return SequencePlan(name="dusk flow", guide=False, dither_every=0,
                        autofocus_every=0, meridian_flip=False,
                        park_when_done=False, warm_cooler_when_done=False,
                        recover_guiding=False, targets=[t])


def _dusk_plan() -> SequencePlan:
    """One target with the schedule a DUSK WINDOW compiles to: a dusk start
    AND a dawn stop, so BOTH of `sun_window_needs_a_site`'s clauses apply."""
    return _plan_with_schedule(Schedule(start_mode="dusk",
                                        start_offset_min=0.0,
                                        stop_mode="dawn"))


def _no_site(store) -> None:
    """The site as a fresh rig holds it: the default, never saved. Written
    past ``set_site``, which marks any site it saves as not the default."""
    store.cfg().site = Site()
    store.bump_and_save()
    assert store.cfg().site.is_default, "premise: the site is the default"


async def test_a_dusk_flow_with_no_site_refuses_to_start(
        group_hub, group_store, monkeypatch):
    """No site saved, a target whose window is dusk-to-dawn: `start` refuses
    before the task is even created (no captures, nothing moves), naming the
    target and the missing setting, with no number in the message. Both of
    `sun_window_needs_a_site`'s clauses fire, since this schedule depends on
    the Sun at both ends.

    RED under mutant "refusal removed" (the D-08 check taken out of
    `start`), observed:

        Failed: DID NOT RAISE <class 'astrodeck.devices.base.DeviceError'>
    """
    _no_site(group_store)
    night = Night(group_hub, monkeypatch, t0=NOON_AT_0_0)
    try:
        with pytest.raises(DeviceError) as exc:
            await night.run(_dusk_plan())
    finally:
        await night.close()
    reason = str(exc.value)
    assert "M31" in reason and "Settings > Site" in reason, reason
    assert START_CLAUSE in reason and STOP_CLAUSE in reason, reason
    assert not numeric_tokens(reason), (
        f"the refusal carries numbers: {reason!r}")
    assert not night.captures, (
        f"a refused start shot a frame: {night.shots()}")


async def test_control_a_saved_site_starts_the_run(
        group_hub, group_store, monkeypatch):
    """CONTROL. The same plan and instant with the fixture site saved:
    `start` does not refuse, and the run waits for that site's real dusk
    (so nothing is shot in the first minutes) rather than running now.

    RED under mutant "refuses on every site" (the D-08 check's
    ``site_lat_lon(...) is None`` guard dropped), observed:

        AssertionError: a run on a saved site refused to start: M31 needs a
        saved site to find dusk to start by and find dawn to stop by: with
        no site saved, this run refuses to start. Set a location in
        Settings > Site.
    """
    night = Night(group_hub, monkeypatch, t0=NOON_AT_0_0,
                  horizon_s=1800.0)
    try:
        try:
            await night.run(_dusk_plan())
        except DeviceError as e:
            pytest.fail(f"a run on a saved site refused to start: {e}")
    finally:
        await night.close()
    assert not night.captures, (
        f"premise: the saved site's dusk is ahead, so nothing is shot yet: "
        f"{night.shots()}")


async def test_control_a_dusk_start_with_no_stop_names_only_the_start_clause(
        group_hub, group_store, monkeypatch):
    """CONTROL (#582 suggested fix, item 1). No site saved; the schedule a
    DUSK WINDOW card with Stop "None" compiles to (`flows.compile
    ._dusk_schedule`): a dusk start, no stop at all. The refusal names the
    START clause and not the STOP one -- there is no dawn stop to need a
    site for.

    RED under mutant E17 (#582's own name for it: the STOP clause's gate,
    ``any(t.schedule.stop_mode in _SUN_MODES for t in hit)``, replaced with
    ``True``), observed:

        AssertionError: the stop clause fired with no dawn stop in the
        schedule: 'M31 needs a saved site to find dusk to start by and find
        dawn to stop by: with no site saved, this run refuses to start. Set
        a location in Settings > Site.'
    """
    _no_site(group_store)
    plan = _plan_with_schedule(Schedule(start_mode="dusk", stop_mode="none"))
    night = Night(group_hub, monkeypatch, t0=NOON_AT_0_0)
    try:
        with pytest.raises(DeviceError) as exc:
            await night.run(plan)
    finally:
        await night.close()
    reason = str(exc.value)
    assert START_CLAUSE in reason, (
        f"the start clause did not fire for a dusk start: {reason!r}")
    assert STOP_CLAUSE not in reason, (
        f"the stop clause fired with no dawn stop in the schedule: {reason!r}")


async def test_control_a_clock_start_with_a_dawn_stop_names_only_the_stop_clause(
        group_hub, group_store, monkeypatch):
    """CONTROL (#582 suggested fix, item 2). No site saved; a Clock-time
    start (no site needed to resolve it) with a Dawn stop. The refusal names
    the STOP clause and not the START clause -- the start needs no site.

    RED under the sibling mutant (the START clause's gate,
    ``any(t.schedule.start_mode in _SUN_MODES for t in hit)``, replaced with
    ``True``), observed:

        AssertionError: the start clause fired with a clock start, not a
        dusk/dawn one: 'M31 needs a saved site to find dusk to start by and
        find dawn to stop by: with no site saved, this run refuses to
        start. Set a location in Settings > Site.'
    """
    _no_site(group_store)
    plan = _plan_with_schedule(Schedule(start_mode="time", start_time="21:30",
                                        stop_mode="dawn"))
    night = Night(group_hub, monkeypatch, t0=NOON_AT_0_0)
    try:
        with pytest.raises(DeviceError) as exc:
            await night.run(plan)
    finally:
        await night.close()
    reason = str(exc.value)
    assert STOP_CLAUSE in reason, (
        f"the stop clause did not fire for a dawn stop: {reason!r}")
    assert START_CLAUSE not in reason, (
        f"the start clause fired with a clock start, not a dusk/dawn one: "
        f"{reason!r}")


# -------------------------------------------------------------- ResumeArm

def _armed(plan: SequencePlan) -> Session:
    s = Session(name=plan.name, created_ts=NOON_AT_0_0 - 86400.0,
                updated_ts=NOON_AT_0_0 - 3600.0, status="dormant",
                plan=plan, auto_resume=True)
    session_store.save(s)
    return s


@pytest.mark.parametrize("site", ["default", "saved"])
async def test_auto_resume_says_once_that_it_stays_off_for_a_dusk_session(
        group_store, monkeypatch, bus_lines, site):
    """An armed DUSK session with no site saved: `window_open` is False on
    every tick, since no dusk can be found. The first tick says once that
    auto-resume stays off for it, and why, in words; the next ten say
    nothing more, and the hold names the reason. With the fixture site saved
    (the CONTROL), the window is closed for the ordinary reason, and the
    line is never said.

    RED under mutant "line removed" (the tick's ``if dusk:`` branch made
    ``if False:``), observed on the default site:

        AssertionError: auto-resume said 0 time(s) that it stays off for a
        DUSK session with no site: []

    RED under mutant "said every tick" (the ``if armed.id not in
    self._dusk_no_site_said`` latch taken out), observed on the default
    site:

        AssertionError: auto-resume said 11 time(s) that it stays off for a
        DUSK session with no site: ["auto-resume stays off for 'dusk flow': t
        ..."]
    """
    if site == "default":
        _no_site(group_store)
    hub = SimpleNamespace(site=group_store.cfg().site.model_dump())
    engine = SimpleNamespace(running=False)
    session = _armed(_dusk_plan())
    now = {"t": NOON_AT_0_0}
    arm = ResumeArm(engine, hub, clock=lambda: now["t"])
    for _tick in range(11):
        await arm.tick()
        now["t"] += 60.0
    said = [m for _lv, m, _src in bus_lines
            if m.startswith("auto-resume stays off for")]
    if site == "saved":
        assert said == [], said
        assert arm.hold and "DUSK" not in arm.hold["reason"], arm.hold
        return
    assert len(said) == 1, (
        f"auto-resume said {len(said)} time(s) that it stays off for a DUSK "
        f"session with no site: {[m[:40] + ' ...' for m in said[:1]]}")
    assert "'dusk flow'" in said[0] and "M31" in said[0] and (
        "no site is saved" in said[0]), said[0]
    assert not numeric_tokens(said[0]), said[0]
    assert arm.hold and arm.hold["session_id"] == session.id and (
        "no site saved" in arm.hold["reason"]), arm.hold
    assert not [m for _lv, m, _src in bus_lines
                if "auto-resume is standing by" in m], (
        "the standing-by line promises a window that never opens")


async def test_control_a_session_with_a_clock_window_is_not_said(
        group_store, monkeypatch, bus_lines):
    """CONTROL. The same armed DUSK target with no site saved, beside a
    second target whose window opens at a clock time six hours ahead: that
    window needs no site and does open, so the session is not one whose
    window never opens, and auto-resume does not say it stays off for a
    DUSK window (`_dusk_without_a_site` asks EVERY target, not any).

    Added by the H4-ENG-B verifier: no case had a session holding anything
    but DUSK targets.

    RED under mutant "any target" (`_dusk_without_a_site`'s ``any(...
    not in ("dusk", "dawn") ...)`` made ``all(...)``; applied in the
    verifier's own private copy, scratchpad ``H4-ENG-B-verify-mut``),
    observed:

        AssertionError: a session with a clock-time window was said to stay
        off for its DUSK window: ["auto-resume stays off for 'dusk flow':
        the DUSK window of M31, M33 needs a saved site to find dusk, and no
        site is saved, so the window never opens. Save the site, or start
        the run by hand"], hold 'its DUSK window cannot be placed with no
        site saved, so auto-resume stays off until a site is saved'
    """
    _no_site(group_store)
    plan = _dusk_plan()
    later = single("M33", count=2, ha_h=-1.0)
    # Local HH:MM six hours on, read the way `schedule` reads a clock time
    # (`time.localtime`), so the window is ahead on any host's time zone.
    later.schedule = Schedule(
        start_mode="time",
        start_time=time.strftime("%H:%M", time.localtime(NOON_AT_0_0
                                                         + 6 * 3600.0)))
    plan.targets.append(later)
    hub = SimpleNamespace(site=group_store.cfg().site.model_dump())
    engine = SimpleNamespace(running=False)
    _armed(plan)
    now = {"t": NOON_AT_0_0}
    arm = ResumeArm(engine, hub, clock=lambda: now["t"])
    for _tick in range(3):
        await arm.tick()
        now["t"] += 60.0
    assert arm.hold, "premise: auto-resume held, the clock window being ahead"
    said = [m for _lv, m, _src in bus_lines
            if m.startswith("auto-resume stays off for")]
    assert said == [] and "DUSK" not in arm.hold["reason"], (
        f"a session with a clock-time window was said to stay off for its "
        f"DUSK window: {said[:1]}, hold {arm.hold['reason']!r}")
