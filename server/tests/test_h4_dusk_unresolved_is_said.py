"""With no site saved, a DUSK window is not applied, and the run and
auto-resume each say so, in words (#527, its engine and resume_arm lines;
spec 1.6, 5.1 and 5.9; the #24 class).

THE DEFECT. On a rig with no saved site, a DUSK WINDOW flow waited for dusk
at the 0,0 placeholder: staged on a fresh simulator at 23:18 local, the run
read "waiting for start time" with ``eta_s`` 42862, and nothing on screen
said the window was computed for a site that does not exist. H4-SCHED made
`schedule.resolve_window` answer None for a dusk or dawn boundary with no
site (tests/test_h4_dusk_window_needs_a_site.py), so the run now starts, and
`resume_arm.window_open` keeps auto-resume off. What was left is this file:
neither said anything.

AS BUILT.

* The engine says once, at the run's start, which targets' DUSK windows
  were not applied because no site is saved, and what the run does instead
  (`_say_sun_windows_unresolved`), with no number in it.
* ResumeArm says once per session that auto-resume stays off for a session
  whose every target opens on a DUSK window, with no site saved
  (`_dusk_without_a_site`), and holds on that reason instead of "it is not
  dark enough yet".

THE NIGHT is the clocked simulator (tests/_group_harness.py) on the real
default site, ``config.Site()``, at 12:00 UTC, when the placeholder's dusk
is hours ahead: the plan's one target carries the schedule a
DUSK WINDOW card compiles to (`flows.compile`: ``start_mode`` "dusk",
``stop_mode`` "dawn"). ResumeArm's case is its real `tick` on the same
default site, with a stand-in engine that is not running.

Every mutant was applied in a private copy of ``server/`` (scratchpad
``H4-ENG-B-mut``), from a byte backup restored and sha256-checked after each,
never in the shared tree (#254). The observed failure is quoted verbatim
(the first assertion line, long lines wrapped).
"""
from __future__ import annotations

import calendar
import time
from types import SimpleNamespace

import pytest

from _group_harness import Night, group_hub, group_store, single  # noqa: F401
from _site_tracking import numeric_tokens
from astrodeck.config import Site
from astrodeck.sequence.models import Schedule, SequencePlan
from astrodeck.sequence.resume_arm import ResumeArm
from astrodeck.sequence.session import Session, session_store

#: 2026-09-02 12:00 UTC: daylight at longitude 0, so the placeholder's dusk
#: is hours ahead (the Sun reaches -12 there near 18:50 UTC), and daylight at
#: the fixture site too (08:00 local at 74 W), so a saved site's window is
#: shut as well and the control below can tell the two apart.
NOON_AT_0_0 = float(calendar.timegm((2026, 9, 2, 12, 0, 0)))
#: How soon the first frame must come: the target's setup and one 30 s
#: frame, with a generous margin, against a wait of hours.
FIRST_FRAME_BY_S = 600.0
LINE = "no site is saved, so the DUSK window of"


def _dusk_plan() -> SequencePlan:
    """One target with the schedule a DUSK WINDOW compiles to."""
    t = single("M31", count=2, ha_h=-1.0)
    t.schedule = Schedule(start_mode="dusk", start_offset_min=0.0,
                          stop_mode="dawn")
    return SequencePlan(name="dusk flow", guide=False, dither_every=0,
                        autofocus_every=0, meridian_flip=False,
                        park_when_done=False, warm_cooler_when_done=False,
                        recover_guiding=False, targets=[t])


def _no_site(store) -> None:
    """The site as a fresh rig holds it: the default, never saved. Written
    past ``set_site``, which marks any site it saves as not the default."""
    store.cfg().site = Site()
    store.bump_and_save()
    assert store.cfg().site.is_default, "premise: the site is the default"


async def test_a_dusk_flow_with_no_site_starts_now_and_says_why(
        group_hub, group_store, monkeypatch):
    """No site saved, a DUSK-windowed target, 12:00 UTC. The first frame
    comes within minutes, not at the placeholder's dusk hours away,
    and the run says once, before it, that the DUSK window was not applied
    because no site is saved, with no number in the line.

    RED under mutant "line removed" (`_run_scheduled`'s
    `_say_sun_windows_unresolved` call taken out), observed:

        AssertionError: the run started a DUSK-windowed target with no site
        saved and never said the window was not applied: []

    RED under H4-SCHED's mutant "guard removed: placeholder dusk returned"
    (`schedule.resolve_window` reading the site through `_lat_lon` again,
    the code before #527), which makes it wait again, observed:

        AssertionError: the first frame came 24481.531 s in; with no site
        the DUSK window is not applied and the run starts now
    """
    _no_site(group_store)
    night = Night(group_hub, monkeypatch, t0=NOON_AT_0_0)
    try:
        night.done = await night.run(_dusk_plan())
    finally:
        await night.close()
    assert night.done, night.lines[-4:]
    assert night.captures, f"premise: the run shot: {night.lines[-4:]}"
    first = night.rel(night.captures[0]["t"])
    assert first < FIRST_FRAME_BY_S, (
        f"the first frame came {first} s in; with no site the DUSK window is "
        f"not applied and the run starts now")
    said = [(night.rel(t), m) for t, _l, m in night.lines if LINE in m]
    assert said, (
        f"the run started a DUSK-windowed target with no site saved and "
        f"never said the window was not applied: {said}")
    assert len(said) == 1 and said[0][0] <= first, said
    assert "M31" in said[0][1] and "starts now" in said[0][1] and (
        "no dawn stop" in said[0][1]), said[0][1]
    assert not numeric_tokens(said[0][1]), (
        f"the line carries numbers: {said[0][1]!r}")


async def test_control_a_saved_site_keeps_the_window_and_says_nothing(
        group_hub, group_store, monkeypatch):
    """CONTROL. The same plan and instant with the fixture site saved: the
    window is kept (the run waits for that site's dusk, so nothing is shot
    in the first minutes), and the line is not said.

    RED under mutant "said on every site" (`_say_sun_windows_unresolved`
    without its ``if site_lat_lon(site) is not None: return``), observed:

        AssertionError: a run on a saved site said its DUSK window was not
        applied: ['no site is saved, so the DUSK window of M31 was not
        applied: ...']
        assert ['no site is ... window kept'] == []
    """
    night = Night(group_hub, monkeypatch, t0=NOON_AT_0_0,
                  horizon_s=1800.0)
    try:
        await night.run(_dusk_plan())
    finally:
        await night.close()
    assert not night.captures, (
        f"premise: the saved site's dusk is ahead, so nothing is shot yet: "
        f"{night.shots()}")
    said = night.said(LINE)
    assert said == [], (
        f"a run on a saved site said its DUSK window was not applied: "
        f"{[m[:60] + ' ...' for m in said]}")


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
