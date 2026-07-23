"""Low-precision Moon ephemeris (coords.py) cross-checked against astropy.

The gating path (schedule.py) is astropy-free, so coords grows a hand-rolled
low-precision Moon. These tests pin it to astropy's get_body('moon') within the
tolerance a degrees-wide separation/illumination gate needs (≲1.5°)."""
from __future__ import annotations

import time
import numpy as np
import astropy.units as u
from astropy.coordinates import EarthLocation, get_body, get_sun, SkyCoord
from astropy.time import Time

from astrodeck.catalog import coords

# Non-default deterministic site (never the real backyard).
LAT, LON = 40.0, -74.0
# A spread of instants across a lunation so we hit varied phase/position.
_TIMES = [time.mktime((2024, m, d, 3, 0, 0, 0, 0, 0)) - time.timezone
          for (m, d) in [(1, 5), (3, 21), (6, 20), (9, 10), (11, 30)]]


def _astropy_moon_radec(t: float) -> tuple[float, float]:
    # get_body('moon') already returns the GEOCENTRIC apparent position (GCRS) —
    # the correct oracle for a geocentric ephemeris. Do NOT .transform_to('icrs'):
    # that reprojects the nearby Moon to a *barycentric* direction (~100° off),
    # which is not the reference a geocentric moon_radec should be pinned to.
    body = get_body("moon", Time(t, format="unix"))
    return body.ra.to_value(u.hourangle), body.dec.to_value(u.deg)


def test_moon_radec_matches_astropy_within_1p5deg():
    for t in _TIMES:
        mra, mdec = coords.moon_radec(t)
        ara, adec = _astropy_moon_radec(t)
        sep = coords.angular_sep_deg(mra, mdec, ara, adec)
        assert sep < 1.5, f"moon off by {sep:.2f} deg at t={t}"


def test_moon_illumination_matches_astropy_within_0p03():
    for t in _TIMES:
        tm = Time(t, format="unix")
        moon = get_body("moon", tm)
        sun = get_sun(tm)
        elong = moon.separation(sun).to_value(u.rad)
        k_astro = (1 - np.cos(elong)) / 2
        k = coords.moon_illumination(t)
        assert abs(k - k_astro) < 0.03, f"illum {k:.3f} vs {k_astro:.3f} at t={t}"


def test_angular_sep_deg_basic():
    # 90° apart on the equator (6h of RA); antipodal dec.
    assert abs(coords.angular_sep_deg(0.0, 0.0, 6.0, 0.0) - 90.0) < 1e-6
    assert abs(coords.angular_sep_deg(0.0, -30.0, 0.0, 30.0) - 60.0) < 1e-6


def test_moon_altaz_agrees_with_altaz_of_radec():
    t = _TIMES[0]
    mra, mdec = coords.moon_radec(t)
    assert coords.moon_altaz(LAT, LON, t) == coords.altaz(mra, mdec, LAT, LON, t)
