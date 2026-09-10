"""Satellite and comet ephemerides, from elements cached on disk.

The two kinds of object in here are not one feature with two data sources. They
sit on OPPOSITE sides of this app's site-privacy line, and the split is the most
important thing in the package:

* A SATELLITE is a location oracle. At 400 km, topocentric parallax is tens of
  degrees -- two towns twenty miles apart see the ISS in different parts of the
  sky -- so there is no useful geocentric answer to fall back on. Satellites
  follow the MOON's rule: withheld WHOLE from a caller without
  ``view.site_derived``, with a note saying so, and withheld BEFORE the
  propagator runs.
* A COMET is not. Between two sites the shift is sub-arcsecond, so comets follow
  the PLANETS' rule: served to everybody, computed through
  ``solar_system._observer(site_derived)``, geocentric for a non-holder with the
  same ``geocentric_reason`` field a planet row carries.

Modules:

``elements``   the on-disk cache and the one poller that refills it
``satellites`` SGP4 propagation, the shadow model and the satellite rows
``comets``     the MPC element parser, the conic solvers and the comet rows
``passes``     tonight's visible passes over this site's own horizon
``routes``     the three HTTP routes
"""
from __future__ import annotations

from .elements import (COMET_FILE, COMETS, ELEMENTS_DIR, SATELLITE_FILE,
                       SATELLITES, AlreadyFetching, ElementsUnavailable,
                       EphemerisStore, cache_state, ephemeris_store)

__all__ = [
    "COMETS",
    "COMET_FILE",
    "ELEMENTS_DIR",
    "SATELLITES",
    "SATELLITE_FILE",
    "AlreadyFetching",
    "ElementsUnavailable",
    "EphemerisStore",
    "cache_state",
    "ephemeris_store",
]
