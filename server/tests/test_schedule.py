"""Pure window-resolution tests (Batch 4b §1.6 / schedule.py).

Covers: the polar-latitude sun-solver guard returning None (C2-13); the
wrap-interpolated horizon floor across the 0<->360 seam (C2-3); and the four
gating_status states (ready / waiting / window_closed / never_rises)."""
from __future__ import annotations

import time

import pytest

from astrodeck.sequence import schedule as sch
from astrodeck.sequence.models import Schedule, Target

# Deterministic sites (never the default).
MID = {"name": "Mid", "latitude": 40.0, "longitude": -74.0,
       "elevation_m": 0.0, "is_default": False, "horizon_min_deg": 15.0}
# Far-north site for the polar guard. A June solstice noon there has the sun up
# all "night" (no nautical dusk), so next_sun_event(-12) must return None.
POLAR = {"name": "Svalbard", "latitude": 78.2, "longitude": 15.6,
         "elevation_m": 0.0, "is_default": False, "horizon_min_deg": 0.0}

# 2024-06-20 12:00 UTC — near June solstice (polar day in the far north).
SOLSTICE = time.mktime((2024, 6, 20, 12, 0, 0, 0, 0, 0)) - time.timezone


# ----------------------------------------------------------------- polar guard

def test_next_sun_event_polar_returns_none():
    """At a polar latitude in midsummer the sun never sinks to nautical twilight,
    so the solver must return None (guarded loop) rather than spin forever."""
    res = sch.next_sun_event(POLAR["latitude"], POLAR["longitude"], -12.0,
                             SOLSTICE, rising=False)
    assert res is None


def test_next_sun_event_midlat_returns_finite():
    """A mid-latitude site does reach nautical dusk within a day."""
    res = sch.next_sun_event(MID["latitude"], MID["longitude"], -12.0,
                             time.time(), rising=False)
    assert res is not None
    assert res > time.time()
    # the sun is genuinely near -12 at the returned moment (refined crossing)
    assert sch.sun_altitude(MID["latitude"], MID["longitude"], res) == pytest.approx(-12.0, abs=0.5)


# ----------------------------------------------------------------- horizon wrap

def test_interp_wrap_seam():
    """Linear interpolation must blend across the 0<->360 seam: a point at az=350
    (alt 30) and az=10 (alt 10) gives ~20 at az=0 (the midpoint through north)."""
    horizon = [(350.0, 30.0), (10.0, 10.0)]
    assert sch.interp_wrap(horizon, 0.0) == pytest.approx(20.0, abs=0.5)
    # exact control points return their own value
    assert sch.interp_wrap(horizon, 350.0) == pytest.approx(30.0)
    assert sch.interp_wrap(horizon, 10.0) == pytest.approx(10.0)


def test_interp_wrap_empty_is_zero():
    assert sch.interp_wrap(None, 123.0) == 0.0
    assert sch.interp_wrap([], 123.0) == 0.0


def test_interp_wrap_midsegment():
    horizon = [(0.0, 10.0), (90.0, 20.0), (180.0, 0.0)]
    assert sch.interp_wrap(horizon, 45.0) == pytest.approx(15.0)
    assert sch.interp_wrap(horizon, 135.0) == pytest.approx(10.0)


def test_effective_floor_takes_max():
    horizon = [(0.0, 25.0)]
    # min_alt_deg lower than horizon -> horizon wins
    assert sch.effective_floor(10.0, horizon, 0.0) == pytest.approx(25.0)
    # min_alt_deg higher -> floor wins
    assert sch.effective_floor(30.0, horizon, 0.0) == pytest.approx(30.0)


# ----------------------------------------------------------------- gating states

def _target(ra=1.0, dec=20.0, **sched_kw):
    return Target(name="T", ra_hours=ra, dec_deg=dec,
                  schedule=Schedule(**sched_kw))


def test_gating_ready_now():
    """start_mode=now, no altitude gate, target above the horizon -> ready."""
    now = time.time()
    # pick a target that is up: ra near the current LST at this site.
    from astrodeck.catalog.coords import lst_hours
    ra = lst_hours(MID["longitude"], now)        # transiting now -> max altitude
    t = _target(ra=ra, dec=MID["latitude"])      # dec=lat -> overhead
    st = sch.gating_status(t, MID, -12.0, now)
    assert st["state"] == "ready"
    assert st["eta_s"] == 0.0


def test_gating_waiting_on_start_time():
    """start_mode=time in the future -> waiting with a positive eta."""
    now = time.time()
    future = time.strftime("%H:%M", time.localtime(now + 3 * 3600))
    t = _target(start_mode="time", start_time=future)
    st = sch.gating_status(t, MID, -12.0, now)
    assert st["state"] == "waiting"
    assert st["eta_s"] > 0
    assert st["start_ts"] is not None and st["start_ts"] > now


def test_gating_window_closed(monkeypatch):
    """When the resolved stop boundary is already in the past, the target's window
    is closed. The engine freezes a target's window at run-start and compares a
    later live ``now`` against that frozen stop; here we pin the resolved window to
    a past stop so the gating *decision* is exercised in isolation (a self-resolving
    dawn/clock window would simply roll to tomorrow)."""
    now = time.time()
    monkeypatch.setattr(sch, "resolve_window",
                        lambda *a, **k: (now - 3600.0, now - 60.0))
    t = _target(start_mode="dawn")     # mode irrelevant; resolve_window is pinned
    st = sch.gating_status(t, MID, -12.0, now)
    assert st["state"] == "window_closed"
    assert st["stop_ts"] == pytest.approx(now - 60.0)


def test_gating_never_rises():
    """A deep-southern target from a northern site never clears a 10 deg gate."""
    now = time.time()
    t = _target(ra=1.0, dec=-80.0, min_altitude_deg=10.0)
    st = sch.gating_status(t, MID, -12.0, now)
    assert st["state"] == "never_rises"
    assert "never rises" in st["reason"]


def test_resolve_window_now_and_maxrun():
    now = 1_700_000_000.0
    t = _target(start_mode="now", max_run_min=120)
    start, stop = sch.resolve_window(t.schedule, MID, -12.0, now)
    assert start == pytest.approx(now)
    assert stop == pytest.approx(now + 120 * 60)


def test_schedule_order_sorts_by_start():
    now = time.time()
    soon = _target(start_mode="now")
    later = time.strftime("%H:%M", time.localtime(now + 4 * 3600))
    t_later = _target(start_mode="time", start_time=later)
    ordered = sch.schedule_order([t_later, soon], MID, -12.0, now)
    assert ordered[0] is soon and ordered[1] is t_later
