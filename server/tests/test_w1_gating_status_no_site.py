"""``gating_status`` and ``constraint_gate`` refuse to judge a target's
altitude, hour angle or Moon distance with no site saved (#540, #24's class).

THE DEFECT. Both functions read the site through ``schedule._lat_lon``,
which hands back the placeholder's 0,0 for a site nobody has saved and never
asked whether there was one. So a target's start altitude, its peak across
the window and its hour-angle limit were all judged for the Gulf of Guinea:
a run held for hours waiting to clear a floor at latitude 0, or set aside
for the night on an hour angle at longitude 0 - neither of them an answer
about the rig's own sky.

THE FIX. Both now ask ``site_gate.site_lat_lon`` first. With no site,
``gating_status`` answers ``ready``/``"no site"`` (not a wait - the same
"unknown, so do not hold on it" reading ``_frame_altitude``'s None gets,
#121) unless the window's own clock has not opened yet, which needs no
site and still gates; ``constraint_gate`` answers ``None`` (no constraint),
exactly as it does when the schedule sets no limit at all.

SITE is a fake mid-latitude site (never the observatory's, matching
``_group_harness.LAT, LON`` and ``test_constraint_gate.py``'s own SITE).
DEFAULT_SITE is the 0,0 placeholder with ``is_default`` set, as a fresh
install holds it.

Mutants were run from a byte backup inside this worktree (never in the
shared tree), and each failure is quoted verbatim.
"""
from __future__ import annotations

import time

from astrodeck.sequence import schedule as sch
from astrodeck.sequence.models import Target

SITE = {"name": "Mid", "latitude": 40.0, "longitude": -74.0,
        "elevation_m": 0.0, "is_default": False}
DEFAULT_SITE = {"latitude": 0.0, "longitude": 0.0, "is_default": True}
#: A half-saved site: `is_default` cleared but no coordinates yet - the
#: other door into "no site" (site_gate's own comment on the `or 0.0` idiom
#: it refuses to use). `site_lat_lon` answers None for this too.
HALF_SAVED_SITE = {"is_default": False, "latitude": None, "longitude": None}
NOW = time.time()


def _target(ra=12.0, dec=40.0, **sched):
    return Target(name="T", ra_hours=ra, dec_deg=dec, schedule=sched)


# ------------------------------------------------------------- gating_status

def test_an_altitude_floor_does_not_hold_a_target_with_no_site():
    """The issue's own example: ``Schedule(min_altitude_deg=30.0)`` at the
    default site used to answer "waiting"/"below start altitude (30 deg)"
    with an eta of hours - the Gulf of Guinea's rise time, not the rig's.

    Mutant "no-site guard removed" (``gating_status`` reading `_lat_lon`
    unconditionally again, the code before this fix): RED, observed:
        assert 'waiting' == 'ready'
    """
    t = _target(min_altitude_deg=30.0)
    gs = sch.gating_status(t, DEFAULT_SITE, -12.0, NOW)
    assert gs["state"] == "ready", gs
    assert gs["reason"] == "no site", gs


def test_an_hour_angle_limit_does_not_close_the_window_with_no_site():
    """The issue's other example: ``Schedule(max_hour_angle_h=2.0)`` at the
    default site used to answer "window_closed"/"past hour-angle limit",
    setting the target aside for the night on longitude 0's hour angle.

    Mutant "no-site guard removed": RED, observed:
        assert 'window_closed' == 'ready'
    """
    t = _target(max_hour_angle_h=2.0)
    gs = sch.gating_status(t, DEFAULT_SITE, -12.0, NOW)
    assert gs["state"] == "ready", gs
    assert gs["reason"] == "no site", gs


def test_a_half_saved_site_is_no_site_too():
    t = _target(min_altitude_deg=30.0)
    gs = sch.gating_status(t, HALF_SAVED_SITE, -12.0, NOW)
    assert gs["state"] == "ready", gs
    assert gs["reason"] == "no site", gs


def test_the_window_s_own_clock_still_gates_with_no_site():
    """No site does not mean no schedule: a start time in the future still
    waits, because ``resolve_window``'s "now"/"time" boundaries need no
    site (#527) - only the altitude/hour-angle/Moon checks are skipped."""
    t = _target(min_altitude_deg=30.0, start_mode="time",
               start_time="23:59", stop_mode="none")
    gs = sch.gating_status(t, DEFAULT_SITE, -12.0, NOW,
                           window=(NOW + 3600.0, None))
    assert gs["state"] == "waiting", gs
    assert gs["reason"] == "waiting for start time", gs


def test_a_closed_window_still_closes_with_no_site():
    t = _target(min_altitude_deg=30.0)
    gs = sch.gating_status(t, DEFAULT_SITE, -12.0, NOW,
                           window=(NOW - 7200.0, NOW - 3600.0))
    assert gs["state"] == "window_closed", gs


def test_a_real_site_is_unaffected_the_altitude_gate_still_holds():
    """The control: with a real site, a target below its floor still waits,
    exactly as before this fix - the guard must not swallow a genuine gate,
    only the placeholder's fake one."""
    t = _target(ra=0.1, dec=-70.0, min_altitude_deg=80.0)
    gs = sch.gating_status(t, SITE, -12.0, NOW)
    assert gs["state"] in ("waiting", "never_rises"), gs
    assert gs["reason"] != "no site", gs


# ----------------------------------------------------------- constraint_gate

def test_constraint_gate_answers_none_with_no_site():
    """Mutant "no-site guard removed" (``constraint_gate`` reading
    `_lat_lon` unconditionally again): RED, observed:
        assert ('window_closed', 'past hour-angle limit (+2h)', 0.0) is None
    """
    t = _target(max_hour_angle_h=2.0)
    assert sch.constraint_gate(t, DEFAULT_SITE, NOW) is None


def test_constraint_gate_still_judges_a_real_site():
    """The control: a target 5 h east of the meridian against a 3 h limit
    still waits at a real site - test_constraint_gate.py's own
    ``test_ha_east_waits`` case, repeated here so this file does not depend
    on that one staying in the tree to prove the guard is narrow."""
    from astrodeck.catalog.coords import lst_hours

    ra = (lst_hours(SITE["longitude"], NOW) - (-5.0)) % 24.0  # 5h east
    t = _target(ra=ra, dec=40.0, max_hour_angle_h=3.0)
    g = sch.constraint_gate(t, SITE, NOW)
    assert g is not None and g[0] == "waiting", g
