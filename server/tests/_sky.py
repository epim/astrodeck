# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A sky target for a test that points the mount somewhere (#1069, #1120).

A test that slews the sim mount needs a position that no part of the calendar
can make unslewable. The literal the suite used, RA 5 h Dec +10, sits 13 deg
off the ecliptic: from early May to early July the Sun is within the 30 deg
exclusion cone of it, ``Hub._check_solar`` refuses the slew, and about 70
tests in 18 files went red for two months a year with no code change (a date
replay at 2026-06-15 reproduces it). That reads as a flake and is a fixture.

``target()`` returns a position that, at the instant asked (the replayed
clock under ``shift_clock``) and at the made-up test site, is

* at least ``MIN_SUN_SEP_DEG`` from the Sun, and
* at least ``MIN_ALT_DEG`` above the horizon.

The candidates are the two ecliptic poles. The Sun stays on the ecliptic, so
a pole is 90 deg from it on every day of the year and no date can reach it;
that is also what ``_SunConeGuard`` (conftest.py) checks, by geometry rather
than by date. The north pole is first: at the made-up site it never sets. A
site in the south hemisphere is given the south pole, and a site where
neither pole stands high enough is a fixture that cannot work, so
``target()`` raises there and does not hand back a target the mount cannot
reach.

Pure apart from the clock and ``astrodeck.catalog.coords``."""
from __future__ import annotations

import time

from _simhub import _TEST_SITE
from astrodeck.catalog import coords

#: The ecliptic poles as (RA hours, Dec degrees), north first: Dec is 90 deg
#: less the obliquity (23.44 deg), and RA 18 h and 6 h.
POLES = ((18.0, 66.56), (6.0, -66.56))

MIN_SUN_SEP_DEG = 60.0
MIN_ALT_DEG = 15.0


def target(unix_time: float | None = None, *,
           latitude: float | None = None,
           longitude: float | None = None) -> tuple[float, float]:
    """The first pole that is clear of the Sun and above the horizon at
    ``unix_time`` (default: the clock now) for the site (default: the made-up
    test site ``_simhub._TEST_SITE``). Raises ``AssertionError`` when neither
    is."""
    when = time.time() if unix_time is None else unix_time
    lat = _TEST_SITE.latitude if latitude is None else latitude
    lon = _TEST_SITE.longitude if longitude is None else longitude
    sun = coords.sun_radec(when)
    for ra, dec in POLES:
        if coords.angular_sep_deg(ra, dec, *sun) < MIN_SUN_SEP_DEG:
            continue
        if coords.altaz(ra, dec, lat, lon, when)[0] >= MIN_ALT_DEG:
            return ra, dec
    raise AssertionError(
        f"no ecliptic pole is {MIN_SUN_SEP_DEG:g} deg from the Sun and "
        f"{MIN_ALT_DEG:g} deg up at latitude {lat:g}: a test site that high "
        f"or that low needs a candidate of its own in _sky.POLES")
