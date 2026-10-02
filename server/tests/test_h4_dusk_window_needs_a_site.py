# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""With no site saved, a window boundary that needs the Sun is not resolved
(#527, its schedule half; spec 1.6 and 5.1; the #24 class).

THE DEFECT. `schedule.resolve_window` read the site through
`schedule._lat_lon`, which hands back the placeholder's 0,0 for a site nobody
has saved, and never asked whether there was one. A DUSK or DAWN boundary was
then the Sun's crossing at latitude 0, longitude 0, somewhere in the Gulf of
Guinea. Found staging the S7 CONTINUE walk on a fresh simulator: a DUSK
WINDOW flow run at 23:18 local read "waiting for start time" with ``eta_s``
42862, a dusk twelve hours away at a site that does not exist. The consumer
test allowlisted `_lat_lon` on the ground that "the guard for this family
belongs at each CALLER", and this caller had none. Two readers stand on it:
the engine freezes each target's window from it at run start, and
`resume_arm.window_open` opens auto-resume on it, so an armed DUSK session
resumed at whatever hour 0,0 was dark.

THE FIX. `resolve_window` takes the coordinates from `site_gate.site_lat_lon`,
which asks `site_is_set` and answers None rather than 0,0. With no site a
``dusk`` or ``dawn`` boundary is None, as a polar dusk already is. ``now`` and
``time`` need no site and are unchanged. The engine's gating treats a None
start as not waiting on the clock, so the run starts, and `window_open`'s
``start is not None`` keeps auto-resume off for such a session: the
conservative direction, asserted here and not changed. The log lines #527
asks for belong to the engine and resume_arm (H4-ENG-B).

THE DEFAULT SITE is the real one, `config.Site()` as the hub holds it (a dict,
``is_default`` True, 0,0), not a hand-built stand-in. The two instants are
UTC, so they mean the same thing on any test machine: local noon and local
midnight at longitude 0, when the placeholder's dusk is hours ahead and when
its night is on.

Each case names the mutant it was shown RED under, with the failure observed,
verbatim (pytest's own lines, long ones wrapped, its blank line and "Use -v"
hint cut). The mutants were applied in a private scratch copy of server/
(scratchpad/H4-SCHED-mut, under the session's scratch root), never in the
shared tree (#254):

* "guard removed: placeholder dusk returned": `resolve_window` reading the
  site through `_lat_lon` again and never asking, the code before the fix.
* "sun boundaries refused on every site": the ``dusk``/``dawn`` boundary None
  whatever the site, which the guard's controls must catch.
* "no site refuses every boundary": `resolve_window` answering
  ``(None, None)`` for a site nobody saved, ``now`` and ``time`` included.
* "time refused with no site": the guard's sun modes widened to ``time``.
* "a half-saved site read as 0,0": `resolve_window` taking 0,0 whenever
  `site_is_set` says set and `site_lat_lon` answers None (the H4-SCHED
  verifier's, in its own copy, scratchpad/H4-SCHED-verify-mut).
"""
from __future__ import annotations

import calendar

from _group_harness import LAT, LON
from astrodeck.config import Site
from astrodeck.sequence import schedule
from astrodeck.sequence.models import (ExposureStep, Schedule, SequencePlan,
                                       Target)
from astrodeck.sequence.resume_arm import window_open
from astrodeck.sequence.session import Session

TWILIGHT = -12.0
#: The site the hub holds when nobody has saved one: 0,0, ``is_default``.
DEFAULT_SITE = Site().model_dump()
#: The harness's fixture site, saved. Not the observatory's.
SAVED_SITE = {"latitude": LAT, "longitude": LON, "is_default": False}
#: A site half saved: ``is_default`` cleared and no coordinates yet, which
#: `site_is_set` calls set and `site_lat_lon` answers None for (site_gate).
HALF_SAVED_SITE = {"is_default": False, "latitude": None, "longitude": None}
#: Local noon at longitude 0: the placeholder's dusk is about six hours on.
NOON_AT_0_0 = float(calendar.timegm((2026, 9, 28, 12, 0, 0, 0, 0, 0)))
#: Local midnight at longitude 0: the placeholder's night is on.
MIDNIGHT_AT_0_0 = float(calendar.timegm((2026, 9, 28, 0, 0, 0, 0, 0, 0)))

#: The two instants, by what they are at 0,0, for the failure messages.
WHEN = {NOON_AT_0_0: "local noon at 0,0",
        MIDNIGHT_AT_0_0: "local midnight at 0,0"}

DUSK_TO_DAWN = Schedule(start_mode="dusk", start_offset_min=30,
                        stop_mode="dawn", stop_offset_min=-20)


def _target(sched: Schedule, name: str = "M31") -> Target:
    return Target(name=name, ra_hours=0.712, dec_deg=41.27, schedule=sched,
                  steps=[ExposureStep(filter="L", exposure_s=60.0, count=3)])


def _session(sched: Schedule) -> Session:
    plan = SequencePlan(name="n", targets=[_target(sched)])
    return Session(name="n", created_ts=1.0, status="dormant", plan=plan)


def test_the_default_site_is_the_placeholder():
    """The premise every case below stands on: what the hub holds with no
    site saved is 0,0 and says so. If `Site()` ever defaults elsewhere,
    these cases are asking the wrong question."""
    assert DEFAULT_SITE["is_default"] is True, DEFAULT_SITE
    assert (DEFAULT_SITE["latitude"], DEFAULT_SITE["longitude"]) == (0.0, 0.0)


def test_a_dusk_window_on_a_default_site_has_no_sun_bound_boundary():
    """On the default site a DUSK start and a DAWN stop are both None: no
    sun-bound start, no dawn stop, and no ``max_run_min`` cap, which runs
    from a start there is none of. A DAWN start is None too, and a ``now``
    start keeps ``now`` while its DAWN stop is None.

    MUTANT "guard removed: placeholder dusk returned": RED (observed; the
    placeholder's dusk and dawn, at 0,0):
        AssertionError: a DUSK start and a DAWN stop on the default site at
        local noon at 0,0
        assert (1790622514.4...56940.8950806) == (None, None)
          At index 0 diff: 1790622514.4073486 != None

    MUTANT "no site refuses every boundary": RED (observed; the ``now``
    start taken with the dusk):
        AssertionError: a NOW start and a DAWN stop on the default site at
        local noon at 0,0
        assert (None, None) == (1790596800.0, None)
          At index 0 diff: None != 1790596800.0
    """
    for now in (NOON_AT_0_0, MIDNIGHT_AT_0_0):
        placeholder_dusk = schedule._night_dusk(0.0, 0.0, TWILIGHT, now)
        assert placeholder_dusk is not None, (
            "premise: the placeholder has a dusk the old code would answer")
        said = f"on the default site at {WHEN[now]}"
        assert schedule.resolve_window(DUSK_TO_DAWN, DEFAULT_SITE, TWILIGHT,
                                       now) == (None, None), (
            f"a DUSK start and a DAWN stop {said}")
        capped = Schedule(start_mode="dusk", stop_mode="none", max_run_min=240)
        assert schedule.resolve_window(capped, DEFAULT_SITE, TWILIGHT,
                                       now) == (None, None), (
            f"a DUSK start with a 240 min cap {said}")
        dawn_start = Schedule(start_mode="dawn", start_offset_min=-90)
        assert schedule.resolve_window(dawn_start, DEFAULT_SITE, TWILIGHT,
                                       now) == (None, None), (
            f"a DAWN start {said}")
        now_to_dawn = Schedule(start_mode="now", stop_mode="dawn")
        assert schedule.resolve_window(now_to_dawn, DEFAULT_SITE, TWILIGHT,
                                       now) == (now, None), (
            f"a NOW start and a DAWN stop {said}")


def test_a_half_saved_site_has_no_sun_bound_boundary_either():
    """The other door into 0,0. A site with ``is_default`` cleared and no
    coordinates yet is set by `site_is_set` and unreadable by `site_lat_lon`,
    which answers None for it rather than the equator (site_gate's comment
    on the ``or 0.0`` idiom). `resolve_window`'s comment claims it: the
    `_lat_lon` it read before raised for this site, and a DUSK or DAWN
    boundary is now None as at the placeholder, while ``now`` keeps ``now``.
    Added by the H4-SCHED verifier; the mutants were run in a private copy
    (scratchpad/H4-SCHED-verify-mut, under the session's scratch root).

    MUTANT "guard removed: placeholder dusk returned": RED (observed; the
    raise the comment describes):
        TypeError: float() argument must be a string or a real number, not
        'NoneType'

    MUTANT "a half-saved site read as 0,0" (`resolve_window` taking 0,0
    whenever `site_is_set` says set and `site_lat_lon` answers None): RED
    (observed; the placeholder's dusk and dawn):
        AssertionError: a DUSK start and a DAWN stop on a half-saved site at
        local noon at 0,0
        assert (1790622514.4...56940.8950806) == (None, None)
          At index 0 diff: 1790622514.4073486 != None
    """
    for now in (NOON_AT_0_0, MIDNIGHT_AT_0_0):
        said = f"on a half-saved site at {WHEN[now]}"
        assert schedule.resolve_window(DUSK_TO_DAWN, HALF_SAVED_SITE,
                                       TWILIGHT, now) == (None, None), (
            f"a DUSK start and a DAWN stop {said}")
        assert schedule.resolve_window(Schedule(), HALF_SAVED_SITE, TWILIGHT,
                                       now) == (now, None), (
            f"the default NOW schedule {said}")


def test_the_issues_run_is_not_held_for_the_placeholders_dusk():
    """#527's observation, one level down: the gating of a DUSK-to-DAWN
    target on the default site at local noon at 0,0. It is not "waiting for
    start time" on a dusk six hours away at a site that does not exist; with
    no start to wait on and no altitude gate, it is ready, which is the run
    starting that #527 asks for.

    MUTANT "guard removed: placeholder dusk returned": RED (observed; the
    issue's symptom):
        AssertionError: {'eta_s': 25714.407348632812, 'reason': 'waiting
        for start time', 'start_ts': 1790622514.4073486, 'state':
        'waiting', ...}
        assert 'waiting' == 'ready'
    """
    gs = schedule.gating_status(_target(DUSK_TO_DAWN), DEFAULT_SITE, TWILIGHT,
                                NOON_AT_0_0)
    assert gs["state"] == "ready", gs
    assert (gs["start_ts"], gs["stop_ts"]) == (None, None), gs


def test_auto_resume_stays_off_for_a_dusk_session_with_no_site():
    """`resume_arm.window_open` for an armed DUSK-to-DAWN session on the
    default site, at local midnight at 0,0, when the placeholder's night is
    on: closed. `dark_enough` fails open with no site, so the answer comes
    from the window, whose ``start is not None`` finds no start. Asserted,
    not changed: it is `window_open`'s own test, and the conservative
    direction.

    The same session on the saved site, in that site's own night, is open,
    so the closed answer is the missing site's and not the session's.

    MUTANT "guard removed: placeholder dusk returned": RED (observed; the
    session resumed on the placeholder's night):
        AssertionError: an armed DUSK session opened on the placeholder's
        night
        assert True is False
    (pytest's "where" line, which prints the call's arguments, cut).

    MUTANT "sun boundaries refused on every site": RED on the saved-site
    half (observed; the "where" line cut):
        AssertionError: the same session in the saved site's own night
        assert False is True
    """
    now = MIDNIGHT_AT_0_0
    dusk = schedule._night_dusk(0.0, 0.0, TWILIGHT, now)
    dawn = schedule._night_dawn(0.0, 0.0, TWILIGHT, now)
    assert dusk + 30 * 60 < now < dawn - 20 * 60, (
        "premise: the placeholder's DUSK-to-DAWN window is open now")
    assert schedule.dark_enough(DEFAULT_SITE, TWILIGHT, now) is True, (
        "premise: the dark gate fails open with no site")
    s = _session(DUSK_TO_DAWN)
    assert window_open(s, DEFAULT_SITE, TWILIGHT, now) is False, (
        "an armed DUSK session opened on the placeholder's night")

    night = schedule.observing_night(SAVED_SITE, TWILIGHT, now)
    assert night is not None, "premise: the saved site has a night"
    middle = (night[0] + night[1]) / 2.0
    assert window_open(s, SAVED_SITE, TWILIGHT, middle) is True, (
        "the same session in the saved site's own night")


def test_control_a_saved_sites_dusk_window_is_unchanged():
    """CONTROL. On the saved site the DUSK start is that site's dusk plus
    the start offset and the DAWN stop its dawn plus the stop offset, and
    each is where the Sun crosses the twilight angle, setting at dusk and
    rising at dawn. GREEN under "guard removed: placeholder dusk returned"
    (observed), which changes nothing for a saved site.

    MUTANT "sun boundaries refused on every site": RED (observed):
        AssertionError: the saved site's DUSK-to-DAWN window asked at local
        noon at 0,0
        assert (None, None) == (1790640686.1...674314.994812)
          At index 0 diff: None != 1790640686.1785889
    """
    for now in (NOON_AT_0_0, MIDNIGHT_AT_0_0):
        start, stop = schedule.resolve_window(DUSK_TO_DAWN, SAVED_SITE,
                                              TWILIGHT, now)
        dusk = schedule._night_dusk(LAT, LON, TWILIGHT, now)
        dawn = schedule._night_dawn(LAT, LON, TWILIGHT, now)
        assert (start, stop) == (dusk + 30 * 60, dawn - 20 * 60), (
            f"the saved site's DUSK-to-DAWN window asked at {WHEN[now]}")
        for t, setting in ((dusk, True), (dawn, False)):
            assert abs(schedule.sun_altitude(LAT, LON, t) - TWILIGHT) < 0.05
            later = schedule.sun_altitude(LAT, LON, t + 60.0)
            assert (later < TWILIGHT) is setting, (t, setting, later)


def test_control_now_and_time_are_unchanged_on_a_default_site():
    """CONTROL. ``now`` and ``time`` need no site: on the default site a
    ``now`` start is ``now``, a ``time`` boundary is the clock time nearest
    ``now``, ``none`` is None, and ``max_run_min`` caps from a ``now``
    start. GREEN under "guard removed: placeholder dusk returned"
    (observed).

    MUTANT "no site refuses every boundary": RED (observed):
        AssertionError: the default NOW schedule on the default site at
        local noon at 0,0
        assert (None, None) == (1790596800.0, None)
          At index 0 diff: None != 1790596800.0

    MUTANT "time refused with no site" (the guard's sun modes widened to
    ``time``, so a TIME boundary is refused where ``now`` is kept): RED
    (observed):
        AssertionError: a TIME start and stop on the default site at local
        noon at 0,0
        assert None not in (None, None)
    The TIME check asks ``None not in`` before it compares, so a failure
    prints no clock time: a TIME boundary is the test machine's local time,
    and beside a UTC instant that names its timezone.
    """
    for now in (NOON_AT_0_0, MIDNIGHT_AT_0_0):
        said = f"on the default site at {WHEN[now]}"
        # NOW first, so a failure prints UTC instants only: a TIME boundary
        # is the test machine's local clock time, which names its timezone.
        plain = Schedule()
        assert schedule.resolve_window(plain, DEFAULT_SITE, TWILIGHT,
                                       now) == (now, None), (
            f"the default NOW schedule {said}")
        clock = Schedule(start_mode="time", start_time="21:30",
                         stop_mode="time", stop_time="04:45")
        window = schedule.resolve_window(clock, DEFAULT_SITE, TWILIGHT, now)
        assert None not in window, f"a TIME start and stop {said}"
        assert window == (schedule._clock_time_near_now("21:30", now),
                          schedule._clock_time_near_now("04:45", now)), (
            f"a TIME start and stop {said}")
        capped = Schedule(start_mode="now", stop_mode="none", max_run_min=90)
        assert schedule.resolve_window(capped, DEFAULT_SITE, TWILIGHT,
                                       now) == (now, now + 90 * 60.0), (
            f"a NOW start with a 90 min cap {said}")
