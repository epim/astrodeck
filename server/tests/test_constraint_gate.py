from __future__ import annotations
import time
from astrodeck.sequence import schedule as sch
from astrodeck.sequence.models import Target
from astrodeck.catalog import coords
from astrodeck.catalog.coords import lst_hours as _lst

SITE = {"name": "Mid", "latitude": 40.0, "longitude": -74.0,
        "elevation_m": 0.0, "is_default": False}
NOW = time.time()


def _target(ra, dec, **sched):
    return Target(name="T", ra_hours=ra, dec_deg=dec, schedule=sched)


def _ra_at_ha(ha_h: float, now: float) -> float:
    """RA (hours) that yields hour angle `ha_h` at `now` for SITE."""
    lst = _lst(SITE["longitude"], now)
    return (lst - ha_h) % 24.0


def test_hour_angle_sign_matches_meridian_flip():
    ra = 5.0
    ha = sch.hour_angle_h(ra, SITE["longitude"], NOW)
    assert abs(sch.hours_to_meridian_flip(ra, SITE["longitude"], NOW) - (-ha)) < 1e-9


def test_ha_within_window_passes():
    ra = _ra_at_ha(0.0, NOW)            # on the meridian
    assert sch.constraint_gate(_target(ra, 40.0, max_hour_angle_h=3.0), SITE, NOW) is None


def test_ha_east_waits():
    ra = _ra_at_ha(-5.0, NOW)           # 5h east of meridian, limit 3h
    g = sch.constraint_gate(_target(ra, 40.0, max_hour_angle_h=3.0), SITE, NOW)
    assert g is not None and g[0] == "waiting" and g[2] > 0


def test_ha_west_closes():
    ra = _ra_at_ha(5.0, NOW)            # 5h west, past the +3h limit
    g = sch.constraint_gate(_target(ra, 40.0, max_hour_angle_h=3.0), SITE, NOW)
    assert g is not None and g[0] == "window_closed"


def test_all_off_returns_none():
    assert sch.constraint_gate(_target(5.0, 40.0), SITE, NOW) is None


def test_moon_sep_blocks_when_close_and_moon_up():
    # place the target AT the Moon; force a moment the Moon is up.
    t = NOW
    for _ in range(48):                 # scan a day for moon-up at SITE
        if coords.moon_altaz(SITE["latitude"], SITE["longitude"], t)[0] > 10:
            break
        t += 3600.0
    mra, mdec = coords.moon_radec(t)
    g = sch.constraint_gate(_target(mra, mdec, min_moon_sep_deg=30.0), SITE, t)
    assert g is not None and g[0] == "waiting" and "Moon" in g[1]


def test_moon_sep_satisfied_when_moon_down():
    t = NOW
    for _ in range(48):                 # scan for moon BELOW horizon
        if coords.moon_altaz(SITE["latitude"], SITE["longitude"], t)[0] < -5:
            break
        t += 3600.0
    mra, mdec = coords.moon_radec(t)     # even AT the moon, it's down => OK
    assert sch.constraint_gate(_target(mra, mdec, min_moon_sep_deg=30.0), SITE, t) is None


def test_gating_status_ready_when_constraints_off():
    # a default-schedule target is still 'ready' (back-compat through step 4b).
    ra = _ra_at_ha(0.0, NOW)
    gs = sch.gating_status(_target(ra, 80.0), SITE, -12.0, NOW, target_alt=80.0)
    assert gs["state"] == "ready"
