"""TPPA must not walk its measurement arc across the meridian, and must not
report a fit that no mount on a tripod could produce.

Observed on the rig 2026-08-06 at LST 19.43h. The run started at hour angle
+0.69h (west of the meridian) and stepped RA UP twice, to HA -0.12h and -0.92h,
so the arc crossed the meridian between point 1 and point 2. The AM5 answered
with a pier flip. The engine reported a position-angle spread of 179.7 degrees,
fitted an RA axis 80.8 degrees BELOW the horizon, and published 7271 arcminutes
(121 degrees) of polar error to the operator with the message "adjust the
mount".

Two separate defects, tested separately here:

* the step direction was unconditional (RA always up), so whether the run stayed
  on one side of the meridian was luck; and
* nothing checked the fit against physical reality before publishing it, so a
  collapsed fit reached the operator as an instruction to turn a bolt.
"""
from __future__ import annotations

import math

import pytest

from astrodeck.devices.base import DeviceError
from astrodeck.polar.native import (MAX_PLAUSIBLE_ERROR_DEG, _RA_STEP_HOURS,
                                    _ra_step_hours, _reject_implausible_fit)
from astrodeck.sequence.schedule import hour_angle_h

_LON = -121.5          # a western-hemisphere site; the sign of HA is what matters
_LAT = 37.3


class _Hub:
    """Just the ``site`` mapping the two helpers read."""

    def __init__(self, lat=_LAT, lon=_LON):
        self.site = {"latitude": lat, "longitude": lon, "elevation_m": 0.0}


def _ra_at_hour_angle(ha_hours: float, lon_deg: float) -> float:
    """The RA a target must have RIGHT NOW to sit at hour angle ``ha_hours``.

    Derived by inverting ``hour_angle_h`` against the live clock rather than
    hard-coding an RA, so the test asserts on the geometry it means instead of
    on whatever the sidereal time happens to be when CI runs it."""
    from astrodeck.catalog.coords import lst_hours
    return (lst_hours(lon_deg) - ha_hours) % 24.0


# ------------------------------------------------------- the step direction

@pytest.mark.parametrize("ha", [0.1, 0.69, 2.0, 5.0, 11.0])
async def test_west_of_the_meridian_steps_further_west(ha):
    """HA > 0 is past transit. Stepping RA UP would walk back toward the
    meridian, so the step must be negative."""
    hub = _Hub()
    ra = _ra_at_hour_angle(ha, _LON)
    assert _ra_step_hours(hub, ra) == -_RA_STEP_HOURS


@pytest.mark.parametrize("ha", [-0.1, -0.69, -2.0, -5.0, -11.0, 0.0])
async def test_east_of_the_meridian_steps_further_east(ha):
    """HA <= 0 is before transit; the tube must keep moving east (RA up)."""
    hub = _Hub()
    ra = _ra_at_hour_angle(ha, _LON)
    assert _ra_step_hours(hub, ra) == _RA_STEP_HOURS


# ------------------------------------------------- the pier side, which wins
# inside the band where the sky and the mount genuinely disagree

async def test_just_past_the_meridian_the_pier_side_decides():
    """THE 2026-08-06 22:34 FAILURE. The run started at HA +0.057h — three and
    a half minutes past transit — and the hour-angle rule correctly said "step
    west". The mount flipped anyway: it had tracked up through the meridian and
    was still on the side a GEM uses for EASTERN targets, so asking for a target
    another hour west made it swing over. PA went 164.5 to -17.4 and the fit
    came out 2368 arcminutes.

    West of the meridian in the SKY, east-side in the MOUNT: the mount is the
    one that knows, so the step goes back east and the side is preserved."""
    hub = _Hub()
    ra = _ra_at_hour_angle(0.057, _LON)
    assert _ra_step_hours(hub, ra) == -_RA_STEP_HOURS, "precondition: sky says west"
    assert _ra_step_hours(hub, ra, "west") == _RA_STEP_HOURS


async def test_just_before_the_meridian_the_pier_side_decides_too():
    """The mirror case: a tube approaching transit that has ALREADY flipped
    early reports the western-target side while the sky still says east."""
    hub = _Hub()
    ra = _ra_at_hour_angle(-0.057, _LON)
    assert _ra_step_hours(hub, ra) == _RA_STEP_HOURS, "precondition: sky says east"
    assert _ra_step_hours(hub, ra, "east") == -_RA_STEP_HOURS


@pytest.mark.parametrize("ha,side", [(2.0, "west"), (-2.0, "east"),
                                     (5.0, "west"), (-5.0, "east")])
async def test_far_from_the_meridian_a_contradicting_pier_side_is_ignored(ha, side):
    """Out here the sky and a CORRECT mount always agree, so a report that
    contradicts the hour angle is wrong — a fork, a bad driver, or a simulator
    returning a constant. ``SimTelescope.pier_side`` returned a fixed WEST for
    exactly this long, and obeying it would have aimed the arc at the horizon.

    The hour-angle rule keeps the tube up, so out here it wins."""
    hub = _Hub()
    ra = _ra_at_hour_angle(ha, _LON)
    expected = -_RA_STEP_HOURS if ha > 0 else _RA_STEP_HOURS
    assert _ra_step_hours(hub, ra, side) == expected


@pytest.mark.parametrize("side", [None, "", "unknown", "none"])
async def test_a_mount_that_will_not_name_a_side_keeps_the_old_rule(side):
    """Fork mounts answer ``unknown`` and cannot flip anyway, so they lose
    nothing; anything else that cannot say falls back to what shipped before."""
    hub = _Hub()
    ra = _ra_at_hour_angle(0.057, _LON)
    assert _ra_step_hours(hub, ra, side) == -_RA_STEP_HOURS


@pytest.mark.parametrize("ha,side", [(0.2, "east"), (-0.2, "west")])
async def test_inside_the_band_an_agreeing_pier_side_changes_nothing(ha, side):
    """Agreement is the common case even close in; it must not be treated as a
    disagreement and flip the arc around."""
    hub = _Hub()
    ra = _ra_at_hour_angle(ha, _LON)
    expected = -_RA_STEP_HOURS if ha > 0 else _RA_STEP_HOURS
    assert _ra_step_hours(hub, ra, side) == expected


@pytest.mark.parametrize("start_ha", [0.05, 0.69, 1.5, -0.05, -0.69, -1.5])
async def test_the_whole_three_point_arc_stays_on_one_side(start_ha):
    """The regression proper: walk the ACTUAL loop the driver walks and require
    that no point crosses the meridian.

    This is the assertion that would have failed on 2026-08-06 — the run there
    went +0.69h, -0.12h, -0.92h, and the sign change between point 1 and point 2
    is the pier flip."""
    hub = _Hub()
    ra = _ra_at_hour_angle(start_ha, _LON)
    # The driver re-reads position and re-decides before each of the two steps,
    # exactly as _drive does.
    hour_angles = [hour_angle_h(ra, _LON)]
    for _ in range(2):
        ra = (ra + _ra_step_hours(hub, ra)) % 24.0
        hour_angles.append(hour_angle_h(ra, _LON))

    signs = {math.copysign(1.0, h) for h in hour_angles}
    assert len(signs) == 1, (
        f"the arc crossed the meridian: hour angles {[round(h, 3) for h in hour_angles]}")
    # And it must actually MOVE — a step of zero would trivially satisfy the above.
    assert abs(hour_angles[-1] - hour_angles[0]) > 1.5, hour_angles


async def test_the_arc_moves_away_from_the_meridian_not_toward_it():
    """Stronger than 'same sign': |HA| must grow. A run that stepped toward the
    meridian and stopped just short would pass the sign check while still
    ending up in the worst-conditioned place available."""
    hub = _Hub()
    for start_ha in (0.69, -0.69):
        ra = _ra_at_hour_angle(start_ha, _LON)
        previous = abs(hour_angle_h(ra, _LON))
        for _ in range(2):
            ra = (ra + _ra_step_hours(hub, ra)) % 24.0
            now = abs(hour_angle_h(ra, _LON))
            assert now > previous, f"stepped toward the meridian: {previous} -> {now}"
            previous = now


# -------------------------------------------------- the plausibility gate

def _err(total_arcmin: float, alt_arcmin: float) -> dict:
    az = math.sqrt(max(0.0, total_arcmin ** 2 - alt_arcmin ** 2))
    return {"total_arcmin": total_arcmin, "alt_arcmin": alt_arcmin,
            "az_arcmin": az}


async def test_the_exact_rig_failure_is_refused():
    """The measured numbers from 2026-08-06, verbatim. alt_err -7091' against a
    37.3 degree pole puts the fitted axis 80.8 degrees underground."""
    with pytest.raises(DeviceError) as e:
        _reject_implausible_fit(_err(7271.1, -7091.0), _Hub())
    msg = str(e.value)
    assert "below the horizon" in msg.lower(), msg
    # It must not read as "you have a big polar error" — that is the exact
    # misreading that sends someone out to turn a bolt 121 degrees.
    assert "failed fit" in msg.lower(), msg
    # And it must name what to inspect, since the log now carries the evidence.
    assert "native TPPA point" in msg, msg


@pytest.mark.parametrize("total,alt", [
    (0.5, 0.3),          # a well-aligned mount
    (30.0, 20.0),        # half a degree out
    (120.0, 90.0),       # 2 degrees out — coarse, but real and reportable
    (1500.0, 1000.0),    # 25 degrees — awful, still inside the cap and above ground
])
async def test_a_real_misalignment_is_still_reported(total, alt):
    """The gate must not become a second pole guard. A genuinely bad alignment
    is exactly when the operator needs the number, so anything physically
    possible has to pass."""
    _reject_implausible_fit(_err(total, alt), _Hub())   # must not raise


async def test_an_axis_beyond_any_adjuster_is_refused():
    """Above the horizon but 40 degrees from the pole: no altitude bolt has that
    travel, so this is a collapsed fit too."""
    with pytest.raises(DeviceError) as e:
        _reject_implausible_fit(_err(40.0 * 60.0, 20.0 * 60.0), _Hub())
    assert "failed fit" in str(e.value).lower()


async def test_the_cap_boundary_is_the_documented_constant():
    """Guards the threshold itself, both sides."""
    assert MAX_PLAUSIBLE_ERROR_DEG == 30.0
    _reject_implausible_fit(_err(MAX_PLAUSIBLE_ERROR_DEG * 60.0, 60.0), _Hub())
    with pytest.raises(DeviceError):
        _reject_implausible_fit(_err(MAX_PLAUSIBLE_ERROR_DEG * 60.0 + 1.0, 60.0),
                                _Hub())


# ------------------------------------------- the arc is projected forward in time

async def test_the_arc_is_projected_where_the_sky_WILL_be_not_where_it_is():
    """The arc is not instantaneous: two slews plus two expose/solve cycles.

    For a WESTERN arc the sidereal clock pushes hour angle the same way the step
    does, so checking all three altitudes at "now" is optimistic exactly where
    the guard matters. This proves the projection actually advances time rather
    than passing a decorative argument: with the per-leg cost inflated to an
    hour, a geometry that is comfortably legal right now must be refused.
    """
    from astrodeck.polar.native import _refuse_low_arc

    hub = _Hub()
    # A western start (positive HA) high enough to pass instantly, and a Dec low
    # enough that an hour of extra sky rotation matters.
    ra = _ra_at_hour_angle(2.0, _LON)
    result = type("R", (), {"ra_hours": ra, "dec_deg": 5.0})()
    step = _ra_step_hours(hub, ra)
    assert step < 0, "precondition: a western start must step west"

    # PRECONDITION: legal as measured right now, so the refusal below can only
    # come from the time advance.
    _refuse_low_arc(hub, result, step)

    import astrodeck.polar.native as nat
    original = nat._ARC_LEG_SECONDS
    try:
        nat._ARC_LEG_SECONDS = 3600.0
        with pytest.raises(DeviceError) as e:
            _refuse_low_arc(hub, result, step)
    finally:
        nat._ARC_LEG_SECONDS = original
    assert "too low" in str(e.value).lower(), str(e.value)


async def test_the_leg_estimate_is_the_documented_constant():
    """Pinned because it came from measured rig timings, not a guess."""
    from astrodeck.polar.native import _ARC_LEG_SECONDS
    assert _ARC_LEG_SECONDS == 45.0


async def test_the_southern_hemisphere_axis_is_inverted_correctly():
    """error_det flips the altitude sign below the equator (alt_err = pole -
    axis_alt). Reading it with the northern formula would refuse a good southern
    fit and accept an underground one — so both branches are pinned."""
    south = _Hub(lat=-33.9, lon=151.2)
    # Southern, axis 2 degrees below the pole => alt_err = +2 deg, still well up.
    _reject_implausible_fit(_err(120.0, 120.0), south)      # must not raise

    # The case that actually isolates the sign. At latitude -20, alt_err = +25
    # deg puts the axis 5 degrees UNDERGROUND, while the total (25 deg) stays
    # inside MAX_PLAUSIBLE_ERROR_DEG — so the horizon test is the only thing
    # that can fire. Read with the northern formula the axis comes out at +45
    # deg and nothing raises at all, which is the bug this pins.
    shallow = _Hub(lat=-20.0, lon=151.2)
    with pytest.raises(DeviceError) as e:
        _reject_implausible_fit(_err(25.0 * 60.0, 25.0 * 60.0), shallow)
    assert "below the horizon" in str(e.value).lower()
    assert "5.0°" in str(e.value), f"the depth should be named: {e.value}"
