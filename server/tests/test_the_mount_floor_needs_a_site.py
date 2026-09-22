"""#24: the altitude limits cannot be enforced from latitude 0, longitude 0.

`_enforce_mount_floor` is the guard in front of every slew: destination alt/az
against `max(min_alt_deg, horizon(az), nogo)` at the bottom and the zenith
keep-out at the top. It read `hub.site["latitude"]` and `["longitude"]`
directly, so at the unconfigured default it evaluated those limits for the Gulf
of Guinea.

THE FAILURE IS NOT A WRONG NUMBER, IT IS A GUARD THAT ANSWERS CONFIDENTLY IN
BOTH DIRECTIONS. A target genuinely under the floor can read as high and be
PERMITTED - the mount then drives into whatever the floor was drawn around -
and one safely high can read as low and abort the night. Both cases are below,
because a fix that only stops the false abort would leave the dangerous half.

It now fails CLOSED. That is the opposite of `schedule.dark_enough`, which
fails OPEN at a default site on purpose, and the two are not in tension:
`dark_enough` stops daylight imaging and refusing it would stop an
unconfigured rig from ever taking a frame, while this stops the mount from
hitting something. There is no reading of "we do not know where we are" that
makes a pier collision acceptable.

The refusal only reaches a rig that configured a limit. A rig with no floor, no
horizon, no no-go box and no ceiling returns earlier and never asks where it
is, which is the case below that keeps this from being a new obstacle for
someone who has not finished setting up.

MUTATIONS RUN, and what each printed:

  M1, delete the `latlon is None` refusal so the site is read directly again -
  the defect itself. 3 failed: the permitted-slew case, the ceiling-only case
  and the refusal's wording. The permitted-slew case is the one to read: with
  the site unset, a target really 44 degrees under the floor was judged at some
  other altitude entirely.

  M2, refuse whatever the site says (`if True:`). 1 failed: the configured-site
  case. A guard that refuses everything is not a guard, and nothing else
  notices - which is why that control is in the file.

  M3, move the refusal ABOVE the `has_floor or has_ceiling` early return. 2
  failed, including the no-limits case: a rig that has configured no limits at
  all would be told it cannot slew, which is a new obstacle for someone who has
  not finished setting up rather than a safety gain.
"""
from __future__ import annotations

import math

import pytest

from astrodeck.config import Site
from astrodeck.sequence.engine import SafetyAbort
from test_the_flip_stops_paying_for_itself import (  # noqa: F401 - rootdir-relative
    sim_hub)
from astrodeck.sequence import SequenceEngine, SequencePlan
from astrodeck.sequence.models import ExposureStep, Target


def _target(ra_hours: float, dec_deg: float) -> Target:
    return Target(name="T", ra_hours=ra_hours, dec_deg=dec_deg, center=False,
                  autofocus_first=False,
                  steps=[ExposureStep(filter="L", exposure_s=0.05, count=1)])


def _engine(sim_hub, *, min_alt_deg: float = 30.0):
    import astrodeck.hub as hub_mod
    cfg = hub_mod.config_store.cfg()
    cfg.safety.min_alt_deg = min_alt_deg
    e = SequenceEngine(sim_hub)
    e.plan = SequencePlan(targets=[], meridian_flip=False, guide=False)
    e._cfg = cfg
    return e, cfg


def _unset_the_site(sim_hub):
    """The shipped default: 0,0 with `is_default` True.

    Set on the live config rather than through `set_site`, which forces
    `is_default` False - "a user-saved site is, by definition, no longer the
    default" (config.py:2115). There is no supported way to SAVE an unset site,
    which is right, and it means the state this guard exists for is reached
    only by never having saved one.
    """
    import astrodeck.hub as hub_mod
    cfg = hub_mod.config_store.cfg()
    cfg.site = Site(name="", latitude=0.0, longitude=0.0, elevation_m=0.0,
                    is_default=True)
    assert hub_mod.Hub.site.fget(sim_hub)["is_default"] is True, (
        "the fixture did not actually reach an unset site")


def _ra_at_altitude_zero(sim_hub, dec_deg: float) -> float:
    """An RA that is near the horizon HERE, at the fixture's real site."""
    from astrodeck.sequence import schedule
    lon = sim_hub.site["longitude"]
    # six hours from transit is on the horizon for a dec near the equator
    return (schedule.lst_hours(lon) + 6.0) % 24.0


# ------------------------------------------------ the dangerous half, first

async def test_a_slew_under_the_floor_is_not_permitted_by_an_unsited_rig(
        sim_hub):
    """THE ONE THAT MATTERS. A target genuinely below the configured floor must
    not be waved through because the altitude was computed for the wrong
    hemisphere. This is the guard permitting exactly what it exists to refuse.
    """
    from astrodeck.catalog import altaz
    import time as _time

    e, _cfg = _engine(sim_hub, min_alt_deg=30.0)
    lat = sim_hub.site["latitude"]
    lon = sim_hub.site["longitude"]
    t = _target(_ra_at_altitude_zero(sim_hub, 0.0), 0.0)
    here, _az = altaz(t.ra_hours, t.dec_deg, lat, lon, _time.time())
    assert here < 30.0, (
        f"premise: this target must really be under the floor here, and it is "
        f"at {here:.1f} degrees")

    _unset_the_site(sim_hub)
    with pytest.raises(SafetyAbort) as caught:
        await e._enforce_mount_floor(projected=False, target=t)
    assert "no observing site" in str(caught.value), str(caught.value)


async def test_the_refusal_names_what_to_do_about_it(sim_hub):
    """A safety abort in the middle of a night has to say which of the two
    things the operator can change - save the site, or drop the limits."""
    e, _cfg = _engine(sim_hub, min_alt_deg=30.0)
    _unset_the_site(sim_hub)
    with pytest.raises(SafetyAbort) as caught:
        await e._enforce_mount_floor(projected=True, target=_target(12.0, 45.0))
    message = str(caught.value)
    assert "Settings" in message and "latitude 0" in message, message


# ------------------------------------------------------------- the controls

async def test_a_configured_site_is_judged_normally(sim_hub):
    """The fixture's site is real, so a target well above the floor passes and
    one below it aborts for the ordinary reason. Without this the refusal above
    could be a guard that simply never lets anything through."""
    from astrodeck.sequence import schedule
    e, _cfg = _engine(sim_hub, min_alt_deg=30.0)
    lat = sim_hub.site["latitude"]
    overhead = _target((schedule.lst_hours(sim_hub.site["longitude"])) % 24.0,
                       lat)
    await e._enforce_mount_floor(projected=False, target=overhead)  # no raise

    low = _target(_ra_at_altitude_zero(sim_hub, 0.0), 0.0)
    with pytest.raises(SafetyAbort) as caught:
        await e._enforce_mount_floor(projected=False, target=low)
    assert "below safety floor" in str(caught.value), str(caught.value)


async def test_a_rig_with_no_limits_at_all_is_not_asked_where_it_is(sim_hub):
    """THE HALF THAT KEEPS THIS FROM BLOCKING A NEW RIG. No floor, no horizon,
    no no-go box, no ceiling: there is nothing to enforce, so the site is never
    needed and the slew goes ahead. Someone who has not finished setting up
    must not be stopped by a limit they never set."""
    e, cfg = _engine(sim_hub, min_alt_deg=0.0)
    cfg.safety.horizon = None
    cfg.safety.nogo_box = None
    cfg.safety.max_alt_deg = None
    _unset_the_site(sim_hub)
    await e._enforce_mount_floor(projected=True, target=_target(12.0, 45.0))


async def test_a_ceiling_alone_still_needs_the_site(sim_hub):
    """The zenith keep-out is a limit too, and it is checked after the floor.
    A rig with only a ceiling configured must not slip through the gap."""
    e, cfg = _engine(sim_hub, min_alt_deg=0.0)
    cfg.safety.horizon = None
    cfg.safety.nogo_box = None
    cfg.safety.max_alt_deg = 85.0
    _unset_the_site(sim_hub)
    with pytest.raises(SafetyAbort) as caught:
        await e._enforce_mount_floor(projected=True, target=_target(12.0, 45.0))
    assert "no observing site" in str(caught.value)


def test_a_site_with_no_coordinates_cannot_EXIST_to_be_judged():
    """The door this guard would otherwise be walked through, and it turns out
    to be bricked up one layer below.

    `site_lat_lon` is careful about a half-saved site - `is_default` cleared but
    no coordinates yet - because `float(get("latitude", 0.0) or 0.0)`, which is
    what four call sites wrote, turns a null latitude into the equator. Tried to
    reach that state here and could not: `Site` types both coordinates as
    `float`, so pydantic refuses it outright and `cfg.site` can never hold one.

    Recorded rather than deleted, because it says where the protection actually
    lives. The null-coordinate case is closed by the MODEL; `site_lat_lon`'s
    care is the backstop for the other shape `hub.site` arrives in - a plain
    dict, which nothing validates.
    """
    with pytest.raises(Exception) as caught:
        Site(name="half", latitude=None, longitude=None, elevation_m=0.0,
             is_default=False)
    assert "latitude" in str(caught.value)

    # ...and the dict shape, which is the one that is NOT validated.
    from astrodeck.site_gate import site_lat_lon
    assert site_lat_lon({"latitude": None, "longitude": None,
                         "is_default": False}) is None
