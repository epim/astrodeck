# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The seasonal Sun-cone guard's pure parts (#1045, #1046).

``Hub._check_solar`` refuses a slew inside the Sun-exclusion cone, and it
reads the Sun by the calendar and no site. A test that slews to a target the
Sun can come within the cone of passes on most days of the year and fails on
the few it does not (M42 from 4 to 22 June, #1045; RA 5 h Dec +10 from early
May to early July, #1026). The failure has no code change behind it and reads
as a flake (#682). The Sun stays on the ecliptic, so the closest it ever gets
to a target is the target's ecliptic latitude, and a target is exposed to the
cone on SOME day of the year exactly when that is smaller than the cone. No
date scan is needed to know it, which is why the guard is deterministic and
the same on every day it runs.

This module is pure (no pytest, no hub), so a test can grade it directly.
``install`` wraps ``Hub._check_solar`` once and records the targets a test
asked the REAL Sun about; conftest.py fails the test at teardown unless its
file is in ``sun_cone_baseline.txt`` or it pinned the Sun
(``coords.sun_radec`` replaced) or it shot ``SUN_PROOF_TARGET``."""
from __future__ import annotations

import math
import os
from pathlib import Path

#: The obliquity the Sun's own formula uses (coords.sun_radec), in degrees.
OBLIQUITY_DEG = 23.439

BASELINE = Path(__file__).with_name("sun_cone_baseline.txt")

#: Set to a file path to APPEND one line per exposed call instead of failing
#: (``relpath<TAB>nodeid<TAB>ra<TAB>dec<TAB>latitude``): how the baseline is
#: measured and how a replay lists the files it should cover.
REPORT_ENV = "ASTRODECK_SUN_CONE_REPORT"


def ecliptic_latitude_deg(ra_hours: float, dec_deg: float) -> float:
    """Ecliptic latitude of an equatorial position, in degrees."""
    eps = math.radians(OBLIQUITY_DEG)
    ra, dec = math.radians(ra_hours * 15.0), math.radians(dec_deg)
    s = (math.sin(dec) * math.cos(eps)
         - math.cos(dec) * math.sin(eps) * math.sin(ra))
    return math.degrees(math.asin(max(-1.0, min(1.0, s))))


def closest_sun_approach_deg(ra_hours: float, dec_deg: float) -> float:
    """The smallest separation between this position and the Sun over a year."""
    return abs(ecliptic_latitude_deg(ra_hours, dec_deg))


def seasonal(ra_hours: float, dec_deg: float, cone_deg: float) -> bool:
    """Can the Sun-exclusion cone refuse this position on some day?"""
    try:
        return closest_sun_approach_deg(ra_hours, dec_deg) < float(cone_deg)
    except (TypeError, ValueError):
        return False


def load_baseline(path: Path = BASELINE) -> frozenset[str]:
    """The test files allowed to slew to a seasonal target, as posix paths
    relative to ``tests/``. Blank lines and ``#`` comments are skipped."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return frozenset()
    return frozenset(ln.strip() for ln in lines
                     if ln.strip() and not ln.lstrip().startswith("#"))


def install(hub_cls, coords_mod, config_store, sink) -> None:
    """Wrap ``hub_cls._check_solar`` once. ``sink(ra, dec, latitude)`` is
    called for each position asked of the REAL Sun (``coords_mod.sun_radec``
    is the function the module shipped with) that the cone can refuse on some
    day. The wrapper answers exactly what the wrapped method answers."""
    if getattr(hub_cls._check_solar, "_sun_cone_guard", False):
        return
    real_check = hub_cls._check_solar
    real_sun = coords_mod.sun_radec

    def check_solar(self, ra_hours, dec_deg, *args, **kwargs):
        try:
            safety = config_store.cfg().safety
            cone = float(getattr(safety, "solar_exclusion_deg", 30.0))
            armed = bool(getattr(safety, "solar_avoidance", True))
            if (armed and cone > 0 and coords_mod.sun_radec is real_sun
                    and seasonal(ra_hours, dec_deg, cone)):
                sink(float(ra_hours), float(dec_deg),
                     ecliptic_latitude_deg(ra_hours, dec_deg))
        except Exception:                    # noqa: BLE001 - a recorder only
            pass
        return real_check(self, ra_hours, dec_deg, *args, **kwargs)

    check_solar._sun_cone_guard = True
    hub_cls._check_solar = check_solar


def report_line(relpath: str, nodeid: str, hit: tuple[float, float, float]
                ) -> str:
    return f"{relpath}\t{nodeid}\t{hit[0]:g}\t{hit[1]:g}\t{hit[2]:.1f}\n"


def append_report(line: str) -> None:
    target = os.environ.get(REPORT_ENV, "").strip()
    if target:
        with open(target, "a", encoding="utf-8", newline="") as fh:
            fh.write(line)
