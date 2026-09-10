"""TPPA must not walk its measurement arc across the meridian, must not report
a fit that no mount on a tripod could produce, and must not report a fit whose
own axis did not turn by what the mount was told to turn.

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

A third defect, found 2026-09-09 and covered in the last section of this file:
nothing checked the fit against the ROTATION -- the one piece of ground truth
the driver owns and the fit throws away.
"""
from __future__ import annotations

import math

import pytest

from astrodeck.devices.base import DeviceError
from astrodeck.polar.native import (
    MAX_PLAUSIBLE_ERROR_DEG, MAX_ROTATION_DISAGREEMENT_DEG, _RA_STEP_HOURS,
    _cross, _dot, _ra_step_hours,
    _refuse_if_the_fit_does_not_reproduce_the_rotation,
    _reject_implausible_fit, _sky_unit_vector)
from astrodeck.sequence.schedule import hour_angle_h

_LON = -121.5          # a western-hemisphere site; the sign of HA is what matters
_LAT = 37.3


class _Hub:
    """Just the ``site`` mapping the two helpers read."""

    def __init__(self, lat=_LAT, lon=_LON):
        self.site = {"latitude": lat, "longitude": lon, "elevation_m": 0.0}


#: One fixed instant for every geometry test in this file.
#:
#: THE CLOCK IS THE TEST'S ENEMY HERE. These assertions are about a rule with a
#: DISCONTINUITY at HA == 0, and the only way to sit a target exactly on it is to
#: invert the sidereal-time formula — which needs an instant. Reading the clock
#: once to build the RA and letting the code read it again to judge the RA puts
#: the two reads microseconds apart, and sidereal time moves: HA tips from 0.0 to
#: barely-positive and ``ha > 0.0`` answers the other way.
#:
#: CI found it, as ``assert -0.8 == 0.8`` on the [0.0] case, and it had been
#: passing locally by luck on a faster machine. The fix is to state the instant
#: instead of racing it — the value is arbitrary, only its FIXEDNESS matters.
WHEN = 1_772_000_000.0


def _ra_at_hour_angle(ha_hours: float, lon_deg: float,
                      when: float = WHEN) -> float:
    """The RA a target must have AT ``when`` to sit at hour angle ``ha_hours``.

    Derived by inverting ``hour_angle_h`` rather than hard-coding an RA, so the
    test asserts on the geometry it means instead of on a magic number — but
    against a STATED instant, which is what makes the boundary case decidable.
    """
    from astrodeck.catalog.coords import lst_hours
    return (lst_hours(lon_deg, when) - ha_hours) % 24.0


# ------------------------------------------------------- the step direction

@pytest.mark.parametrize("ha", [0.1, 0.69, 2.0, 5.0, 11.0])
async def test_west_of_the_meridian_steps_further_west(ha):
    """HA > 0 is past transit. Stepping RA UP would walk back toward the
    meridian, so the step must be negative."""
    hub = _Hub()
    ra = _ra_at_hour_angle(ha, _LON)
    assert _ra_step_hours(hub, ra, now=WHEN) == -_RA_STEP_HOURS


@pytest.mark.parametrize("ha", [-0.1, -0.69, -2.0, -5.0, -11.0, 0.0])
async def test_east_of_the_meridian_steps_further_east(ha):
    """HA <= 0 is before transit; the tube must keep moving east (RA up)."""
    hub = _Hub()
    ra = _ra_at_hour_angle(ha, _LON)
    assert _ra_step_hours(hub, ra, now=WHEN) == _RA_STEP_HOURS


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
    assert _ra_step_hours(hub, ra, now=WHEN) == -_RA_STEP_HOURS, "precondition: sky says west"
    assert _ra_step_hours(hub, ra, "west", now=WHEN) == _RA_STEP_HOURS


async def test_just_before_the_meridian_the_pier_side_decides_too():
    """The mirror case: a tube approaching transit that has ALREADY flipped
    early reports the western-target side while the sky still says east."""
    hub = _Hub()
    ra = _ra_at_hour_angle(-0.057, _LON)
    assert _ra_step_hours(hub, ra, now=WHEN) == _RA_STEP_HOURS, "precondition: sky says east"
    assert _ra_step_hours(hub, ra, "east", now=WHEN) == -_RA_STEP_HOURS


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
    assert _ra_step_hours(hub, ra, side, now=WHEN) == expected


@pytest.mark.parametrize("side", [None, "", "unknown", "none"])
async def test_a_mount_that_will_not_name_a_side_keeps_the_old_rule(side):
    """Fork mounts answer ``unknown`` and cannot flip anyway, so they lose
    nothing; anything else that cannot say falls back to what shipped before."""
    hub = _Hub()
    ra = _ra_at_hour_angle(0.057, _LON)
    assert _ra_step_hours(hub, ra, side, now=WHEN) == -_RA_STEP_HOURS


@pytest.mark.parametrize("ha,side", [(0.2, "east"), (-0.2, "west")])
async def test_inside_the_band_an_agreeing_pier_side_changes_nothing(ha, side):
    """Agreement is the common case even close in; it must not be treated as a
    disagreement and flip the arc around."""
    hub = _Hub()
    ra = _ra_at_hour_angle(ha, _LON)
    expected = -_RA_STEP_HOURS if ha > 0 else _RA_STEP_HOURS
    assert _ra_step_hours(hub, ra, side, now=WHEN) == expected


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
    hour_angles = [hour_angle_h(ra, _LON, WHEN)]
    for _ in range(2):
        ra = (ra + _ra_step_hours(hub, ra, now=WHEN)) % 24.0
        hour_angles.append(hour_angle_h(ra, _LON, WHEN))

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
        previous = abs(hour_angle_h(ra, _LON, WHEN))
        for _ in range(2):
            ra = (ra + _ra_step_hours(hub, ra, now=WHEN)) % 24.0
            now = abs(hour_angle_h(ra, _LON, WHEN))
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
    import time

    from astrodeck.polar.native import _refuse_low_arc

    hub = _Hub()
    # THE ONE TEST IN THIS FILE THAT WANTS THE LIVE CLOCK, and it is worth saying
    # why. Everything above asserts pure geometry, so it pins the instant to kill
    # the boundary race. This one calls `_refuse_low_arc`, which measures
    # altitude at "now" — so its precondition ("legal as measured right now")
    # is only meaningful if the target is genuinely up right now. Building the RA
    # against a fixed instant five months away put the arc 45 degrees below the
    # horizon and the precondition raised before the test could begin.
    #
    # Safe on the live clock because HA +2.0 is nowhere near the discontinuity:
    # the race only exists where an epsilon of sidereal drift changes the sign.
    when = time.time()
    # A western start (positive HA) high enough to pass instantly, and a Dec low
    # enough that an hour of extra sky rotation matters.
    ra = _ra_at_hour_angle(2.0, _LON, when)
    result = type("R", (), {"ra_hours": ra, "dec_deg": 5.0})()
    step = _ra_step_hours(hub, ra, now=when)
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
# ------------------------------- the fit must reproduce the commanded rotation

#: The three solved points the rig logged on 2026-09-09 20:49-20:50, verbatim.
#:
#: A mount aligned days earlier, guiding 180 s narrowband subs all night and
#: drifting 16"/min unguided (about 33' of real polar error). The fit reported
#: 504.4' -- az -226.9', alt -450.5', 8.4 degrees -- and every guard in this
#: file let it through: position angle flat at 82.0/82.1/81.9 so nothing
#: flipped, RA separations 93% and 100% of the commanded step so the mount
#: arrived, a declination bend of 16.5' against a 45' threshold, 8.4 degrees
#: inside MAX_PLAUSIBLE_ERROR_DEG, and a three-point fit is exact so there is no
#: residual to inspect. Refitting these points by hand, point 2's declination
#: sits 8.3' above the line joining points 1 and 3, and that alone produces the
#: whole number: put it on the line and the same three points report 7.2'.
_20260909_POINTS = [(19.7443, 50.539), (20.4846, 50.701), (21.2837, 50.587)]

#: What the driver COMMANDED between them. The log line reads "rotating RA to
#: 20.55h" from a mount reporting 19.7461h, which is _RA_STEP_HOURS exactly.
_20260909_STEP_H = _RA_STEP_HOURS


def _solves(points) -> list[dict]:
    """The three-point list in the shape the guard reads."""
    return [{"ra_hours": ra, "dec_deg": dec} for ra, dec in points]


def _to_radec(v) -> tuple[float, float]:
    """A unit vector back to (RA hours, Dec degrees) -- the inverse of the
    driver's ``_sky_unit_vector``."""
    dec = math.degrees(math.asin(max(-1.0, min(1.0, v[2]))))
    return (math.degrees(math.atan2(v[1], v[0])) / 15.0) % 24.0, dec


def _rotated(v, axis, angle_deg: float):
    """Rodrigues: ``v`` turned about ``axis`` by ``angle_deg``, right-handed.

    Written out here rather than reused from the driver on purpose. This is the
    FORWARD model -- "these three frames really are one rigid rotation" -- and
    it must not share code with the fit being graded, or a sign error would
    cancel itself and the arcs below would prove nothing.
    """
    a = math.radians(angle_deg)
    c, s = math.cos(a), math.sin(a)
    kv = _cross(axis, v)
    kd = _dot(axis, v)
    return tuple(v[i] * c + kv[i] * s + axis[i] * kd * (1.0 - c) for i in range(3))


def _rigid_arc(pole_distance_arcmin: float, *, step_hours: float = _RA_STEP_HOURS,
               start=(19.7443, 50.539), axis_ra_hours: float = 2.5):
    """Three solved positions that ARE one rigid rotation about an axis
    ``pole_distance_arcmin`` from the celestial pole.

    ``axis_ra_hours`` only chooses WHICH WAY the axis is tilted; the guard's
    answer must not depend on it, and the arcs below span 5 arcminutes to 5
    degrees of tilt on the same one.
    """
    axis = _sky_unit_vector(axis_ra_hours, 90.0 - pole_distance_arcmin / 60.0)
    first = _sky_unit_vector(*start)
    return [_to_radec(_rotated(first, axis, step_hours * 15.0 * k))
            for k in range(3)]


async def test_the_2026_09_09_run_is_refused():
    """The three points that made this necessary, exactly as logged."""
    with pytest.raises(DeviceError) as e:
        _refuse_if_the_fit_does_not_reproduce_the_rotation(
            _solves(_20260909_POINTS), _20260909_STEP_H)
    msg = str(e.value)
    # THE OBSERVATION: what each step actually turned, and what was asked for.
    # That is the whole evidence, and a refusal that keeps its own inputs is
    # what stops the next occurrence costing a second night.
    assert "+9.59" in msg and "+10.34" in msg, (
        f"the refusal must quote the turn it measured per step: {msg!r}")
    assert "+12.00" in msg, f"...and the turn that was commanded: {msg!r}"
    assert "2.41" in msg, f"...and how far out it was: {msg!r}"
    # NOT SOMETHING TO ACT ON, and nothing published.
    assert "not something to turn a bolt by" in msg, msg
    assert "Nothing has been reported" in msg, msg
    assert "again" in msg, msg
    # NO DIAGNOSIS. Three turn angles cannot tell a mount that was still moving
    # from a tripod leg settling from a frame that solved to the wrong place,
    # and _refuse_if_the_axis_moved already cost someone two days by naming one.
    for accusation in ("you ", "your ", "bolts alone", "flipped", "settling"):
        assert accusation not in msg, (
            f"the refusal names a cause it cannot know: {msg!r}")


@pytest.mark.parametrize("pole_distance_arcmin", [5.0, 33.0, 120.0, 300.0])
async def test_a_rigid_rotation_is_never_refused_however_far_off_the_axis_is(
        pole_distance_arcmin):
    """THE TRAP EVERY OTHER PLAUSIBILITY CHECK HERE HAS HAD TO BE RESCUED FROM.

    If the three frames really are one rotation about one axis, the exact
    three-point fit recovers that axis exactly and the turns come out at the
    commanded angle no matter how far from the pole the axis sits. A guard that
    fired on a large-but-real error would refuse the operator exactly the
    measurement they most need -- which is how the pole guard and the
    plausibility gate both came to be written twice."""
    worst = _refuse_if_the_fit_does_not_reproduce_the_rotation(
        _solves(_rigid_arc(pole_distance_arcmin)), _RA_STEP_HOURS)
    assert worst is not None and worst < 1e-6, (
        f"a clean rotation about an axis {pole_distance_arcmin}' from the pole "
        f"disagreed with its own command by {worst}")


async def test_five_degrees_of_real_error_passes_the_rotation_check():
    """Stated separately from the sweep above because it is the case this guard
    is most likely to be blamed for. Five degrees is 300 arcminutes -- sixty
    times a usable alignment, ten times the error this rig actually had -- and
    it still has to reach the operator as a number: ``_reject_implausible_fit``
    refuses only above MAX_PLAUSIBLE_ERROR_DEG, so a 5 degree run completes."""
    arc = _rigid_arc(5.0 * 60.0)
    assert _refuse_if_the_fit_does_not_reproduce_the_rotation(
        _solves(arc), _RA_STEP_HOURS) < 1e-6
    _reject_implausible_fit(_err(5.0 * 60.0, 4.0 * 60.0), _Hub())   # must not raise


async def test_a_westward_arc_is_graded_on_the_sign_it_was_commanded_with():
    """The step is SIGNED -- ``_ra_step_hours`` walks away from the meridian, so
    half of all runs step RA down. Measuring the turn without its sign would
    read every westward arc as 24 degrees out and refuse it."""
    arc = _rigid_arc(33.0, step_hours=-_RA_STEP_HOURS)
    assert _refuse_if_the_fit_does_not_reproduce_the_rotation(
        _solves(arc), -_RA_STEP_HOURS) < 1e-6
    # ...and the sign is really being read: grading that same arc against the
    # OPPOSITE command must refuse.
    with pytest.raises(DeviceError):
        _refuse_if_the_fit_does_not_reproduce_the_rotation(
            _solves(arc), _RA_STEP_HOURS)


@pytest.mark.parametrize("planted_arcmin", [8.3, -8.3])
async def test_the_middle_point_pushed_off_the_line_is_refused(planted_arcmin):
    """The 2026-09-09 signature reproduced on a clean arc: 8.3' of declination
    on the MIDDLE point and nothing else. That is the whole difference between
    7.2' and 504', and it is invisible to a fit that is exact through three
    points."""
    arc = _rigid_arc(33.0)
    arc[1] = (arc[1][0], arc[1][1] + planted_arcmin / 60.0)
    with pytest.raises(DeviceError) as e:
        _refuse_if_the_fit_does_not_reproduce_the_rotation(_solves(arc),
                                                           _RA_STEP_HOURS)
    assert "did not turn by what the mount was told to turn" in str(e.value)


async def test_plate_solve_noise_at_the_documented_floor_never_refuses():
    """The guard's cost, measured rather than argued.

    0.42' is the documented plate-solve floor for this rig. Every arc below IS
    one rigid rotation -- only the three solved positions are jittered -- so
    every refusal here would be a night thrown away for nothing. Seeded, so a
    failure is reproducible rather than "it went red once".

    Measured over 20,000 trials while the threshold was being chosen: 99.9th
    percentile 0.35 degrees, worst 0.48, against a limit of 1.0. At 1.00' of
    noise -- a bad night -- the worst was 1.15, which is the honest edge of this
    guard."""
    import random

    rng = random.Random(20260909)
    trials, refused = 250, 0
    worst_seen = 0.0
    for _ in range(trials):
        arc = _rigid_arc(33.0)
        jittered = []
        for ra_hours, dec_deg in arc:
            dec = dec_deg + rng.gauss(0.0, 0.42) / 60.0
            ra = ra_hours + (rng.gauss(0.0, 0.42) / 60.0 / 15.0
                             / math.cos(math.radians(dec_deg)))
            jittered.append((ra, dec))
        try:
            worst_seen = max(worst_seen,
                             _refuse_if_the_fit_does_not_reproduce_the_rotation(
                                 _solves(jittered), _RA_STEP_HOURS))
        except DeviceError:
            refused += 1
    assert refused == 0, (
        f"{refused} of {trials} clean arcs at the 0.42' solve floor were "
        f"refused; the worst disagreement seen was {worst_seen:.3f} deg against "
        f"a {MAX_ROTATION_DISAGREEMENT_DEG} deg limit")
    assert worst_seen < MAX_ROTATION_DISAGREEMENT_DEG / 2.0, (
        f"solve noise alone reached {worst_seen:.3f} deg, more than half the "
        f"budget -- the threshold no longer has the margin it was chosen with")


async def test_the_rotation_threshold_is_the_documented_constant():
    """Pinned like MAX_PLAUSIBLE_ERROR_DEG above: it came from measured
    distributions (99.9th percentile 0.35 at the 0.42' solve floor) and from a
    measured failure (2.412 on 2026-09-09), not from a guess."""
    assert MAX_ROTATION_DISAGREEMENT_DEG == 1.0


async def test_nothing_commanded_means_nothing_to_grade():
    """``step_hours`` is None until the first point is solved, and a caller with
    no commanded rotation has no evidence -- which is not a refusal."""
    assert _refuse_if_the_fit_does_not_reproduce_the_rotation(
        _solves(_20260909_POINTS), None) is None
    assert _refuse_if_the_fit_does_not_reproduce_the_rotation(
        _solves(_20260909_POINTS), 0.0) is None


async def test_three_identical_points_are_left_to_the_engine():
    """A collapsed arc defines no plane, so there is no axis and no turn. The
    engine refuses that input itself ("mount did not move between points") and
    ``_refuse_if_it_did_not_arrive`` catches it a frame earlier; inventing a
    refusal out of a degenerate cross product here would only mislabel it."""
    same = [(19.7443, 50.539)] * 3
    assert _refuse_if_the_fit_does_not_reproduce_the_rotation(
        _solves(same), _RA_STEP_HOURS) is None


async def test_a_southern_arc_is_graded_the_same_way():
    """The axis is signed toward the NORTH pole in both hemispheres, because it
    is compared against a commanded RA step and RA increases in the same
    right-handed sense from either. Signing it by the VISIBLE pole -- which is
    what the engine does, for its own different purpose -- would make every
    southern turn come out negative against a positive command and refuse every
    southern run."""
    # The axis LINE, named by its north end -- which is also 33' from the
    # south celestial pole, 40 degrees from the tube, and rotating the sky
    # eastward in RA. Naming it by its south end instead turns +12 degrees
    # into a westward arc, which is a fault in the forward model and not in
    # the guard (the first draft of this test did exactly that).
    axis = _sky_unit_vector(2.5, 90.0 - 33.0 / 60.0)
    first = _sky_unit_vector(19.7443, -50.539)
    arc = [_to_radec(_rotated(first, axis, _RA_STEP_HOURS * 15.0 * k))
           for k in range(3)]
    worst = _refuse_if_the_fit_does_not_reproduce_the_rotation(_solves(arc),
                                                               _RA_STEP_HOURS)
    assert worst is not None and worst < 1e-6, worst
