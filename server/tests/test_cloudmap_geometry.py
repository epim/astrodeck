"""Cloud occlusion geometry, stage 1.

Every test here pins one specific way the module can be plausibly wrong: a
flat-earth slant, a half-angle dropped from the beam cone, a parallax
correction pushed away from the satellite instead of toward it, an azimuth
measured the wrong way round, a longitude that walks off the antimeridian.

Golden vectors are section 9 of the design, verbatim. The site is 40.0/-105.0
throughout -- never the real observing site.
"""
import dataclasses
import math

import pytest

from astrodeck.cloudmap.geometry import (
    EARTH_RADIUS_KM,
    GEOSTATIONARY_ALT_KM,
    GeoPoint,
    Site,
    agl_to_msl_km,
    beam_footprint_km,
    deparallax,
    look_from,
    pierce_point,
    satellite_look,
    slant_to_layer_km,
)

SITE = Site(40.0, -105.0, 0.0)


def _great_circle_km(lat1_deg, lon1_deg, lat2_deg, lon2_deg):
    """Haversine separation on the module's own sphere, kilometres."""
    p1 = math.radians(lat1_deg)
    p2 = math.radians(lat2_deg)
    dp = p2 - p1
    dl = math.radians(lon2_deg - lon1_deg)
    a = (math.sin(dp / 2.0) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dl / 2.0) ** 2)
    return 2.0 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def test_zenith_slant_is_exactly_the_layer_height():
    """Straight up, the range to the shell IS the height -- no trigonometry
    left over.

    These three are bit-exact, and that is the point of the name -- but the
    bit-exactness belongs to these particular heights, not to the function.
    The cancellation in ``sqrt(rs_sin_a**2 + top**2 - rs**2)`` loses about
    1e-12 km and whether it rounds back to h is luck: 320 of 399 heights swept
    from 0.05 to 19.95 km are NOT exact, including the module's own marine
    layer -- ``slant_to_layer_km(90.0, 0.6)`` is 0.5999999999994543. Do not
    parametrize this over a sweep of layer heights and expect ``==`` to hold.
    """
    assert slant_to_layer_km(90.0, 9.0) == 9.0
    assert slant_to_layer_km(90.0, 2.0) == 2.0
    # And with the observer lifted, it is the difference, not the height.
    assert slant_to_layer_km(90.0, 9.0, site_elev_km=2.0) == 7.0


def test_horizon_slant_matches_the_closed_form():
    """As alt -> 0 the slant tends to sqrt(2Rh + h^2), the tangent-line length
    to the shell. alt 0 itself is a domain error, so approach it."""
    h = 9.0
    closed_form = math.sqrt(2.0 * EARTH_RADIUS_KM * h + h * h)
    assert closed_form == pytest.approx(338.761, abs=1e-3)
    assert slant_to_layer_km(1e-9, h) == pytest.approx(closed_form, abs=1e-3)


def test_slant_is_not_the_flat_earth_approximation():
    """Two different wrong answers, and the test's own name only names one.

    ``h / sin(alt)`` is the flat-earth SLANT -- the substitution a careless
    implementer actually makes. ``h / tan(alt)`` is the horizontal ground
    track and not a range along the line of sight at all. At alt 15 under
    cirrus: true slant 34.438, flat-earth slant 34.773, ground track 33.588.
    Design section 10 item 3 asks only that the result differ from
    ``h / tan(alt)`` by more than 0.5 km, which the flat-earth slant clears by
    1.185 km -- so that assertion alone does not guard the failure mode it is
    named for. The golden value and the h/sin comparison are what discriminate.
    """
    alt_deg, h = 15.0, 9.0
    exact = slant_to_layer_km(alt_deg, h)
    ground_track = h / math.tan(math.radians(alt_deg))
    flat_slant = h / math.sin(math.radians(alt_deg))

    assert exact == pytest.approx(34.438, abs=1e-3)
    assert ground_track == pytest.approx(33.588, abs=1e-3)
    assert flat_slant == pytest.approx(34.773, abs=1e-3)

    # Design section 10 item 3, verbatim.
    assert exact - ground_track > 0.5
    # And the assertion it should have asked for: the two slants differ by
    # 0.335 km, so an h/sin implementation cannot hide here.
    assert abs(exact - flat_slant) > 0.2


def test_pierce_at_zenith_returns_the_site():
    p = pierce_point(SITE, 90.0, 0.0, 2.0)
    assert p.lat_deg == pytest.approx(40.0, abs=1e-5)
    assert p.lon_deg == pytest.approx(-105.0, abs=1e-5)
    assert p.height_msl_km == 2.0

    # Design section 11: no function mutates its arguments, all dataclasses
    # frozen. Stages 3 and 4 pass GeoPoints between an advection step and a
    # vectorised twin, which is exactly where a shared point being mutated in
    # place would be invisible. Unfreezing all three broke nothing else.
    with pytest.raises(dataclasses.FrozenInstanceError):
        p.lat_deg = 0.0
    with pytest.raises(dataclasses.FrozenInstanceError):
        SITE.elev_km = 1.0
    v = look_from(SITE, p)
    with pytest.raises(dataclasses.FrozenInstanceError):
        v.slant_km = 0.0


def test_azimuth_zero_goes_north_and_ninety_goes_east():
    """Azimuth is from true north increasing east. A compass convention flip
    (or a swapped atan2) shows up here before it shows up anywhere subtle."""
    north = pierce_point(SITE, 30.0, 0.0, 9.0)
    assert north.lat_deg > SITE.lat_deg
    assert north.lon_deg == pytest.approx(SITE.lon_deg, abs=1e-9)

    east = pierce_point(SITE, 30.0, 90.0, 9.0)
    assert east.lon_deg > SITE.lon_deg
    assert east.lat_deg == pytest.approx(SITE.lat_deg, abs=1e-3)

    south = pierce_point(SITE, 30.0, 180.0, 9.0)
    assert south.lat_deg < SITE.lat_deg

    west = pierce_point(SITE, 30.0, 270.0, 9.0)
    assert west.lon_deg < SITE.lon_deg


GOLDEN_PIERCE = [
    (90.0, 0.0, 2.0, 40.00000, -105.00000),
    (60.0, 0.0, 2.0, 40.01038, -105.00000),
    (30.0, 90.0, 2.0, 39.99999, -104.95936),
    (30.0, 90.0, 9.0, 39.99986, -104.81764),
    (30.0, 270.0, 9.0, 39.99986, -105.18236),
    (45.0, 45.0, 4.0, 40.02541, -104.96681),
    (20.0, 180.0, 0.6, 39.98518, -105.00000),
    (15.0, 315.0, 9.0, 40.21091, -105.27661),
]


@pytest.mark.parametrize("alt_deg,az_deg,layer_km,exp_lat,exp_lon",
                         GOLDEN_PIERCE)
def test_golden_pierce_points(alt_deg, az_deg, layer_km, exp_lat, exp_lon):
    p = pierce_point(SITE, alt_deg, az_deg, layer_km)
    assert p.lat_deg == pytest.approx(exp_lat, abs=1e-5)
    assert p.lon_deg == pytest.approx(exp_lon, abs=1e-5)
    assert p.height_msl_km == layer_km


# Design section 10 item 7 fixes only the sea-level 40N site. Widened here:
# with one site, look_from's handling of site.elev_km was entirely unguarded
# (dropping it, or doubling it, left the whole suite green while costing
# +6.6 deg of altitude at alt 30 from a 2 km site), and no test anywhere
# used a southern latitude, so a sign error on sin(lat) in any of the three
# places latitude enters the trigonometry was invisible.
ROUND_TRIP_SITES = (
    Site(40.0, -105.0, 0.0),     # section 10 item 7, verbatim
    Site(40.0, -105.0, 2.0),     # an elevated observer
    Site(-40.0, -105.0, 0.0),    # southern hemisphere
)


def test_the_round_trip_is_exact():
    """The load-bearing test. pierce_point and look_from are inverses; if any
    convention disagrees between them -- azimuth sense, the earth-central angle
    versus the ENU projection, the sphere radius, the observer's elevation, the
    sign of the latitude -- this is where it shows."""
    for site in ROUND_TRIP_SITES:
        for alt_deg in (5.0, 15.0, 30.0, 45.0, 60.0, 89.9):
            for az_deg in (0.0, 45.0, 90.0, 180.0, 270.0, 359.0):
                for layer_km in (0.6, 2.0, 9.0):
                    if layer_km <= site.elev_km:
                        continue  # refused by contract, not a round trip
                    where = (site, alt_deg, az_deg, layer_km)
                    p = pierce_point(site, alt_deg, az_deg, layer_km)
                    back = look_from(site, p)
                    assert back.alt_deg == pytest.approx(
                        alt_deg, abs=1e-6), where
                    # Azimuth is circular: 0 and 360 are the same bearing.
                    az_err = (back.az_deg - az_deg + 180.0) % 360.0 - 180.0
                    assert az_err == pytest.approx(0.0, abs=1e-6), (
                        where, back.az_deg)
                    assert back.slant_km == pytest.approx(
                        slant_to_layer_km(alt_deg, layer_km, site.elev_km),
                        abs=1e-6), where


@pytest.mark.parametrize("sat_lon_deg,exp_zenith_deg,exp_az_deg", [
    (-137.0, 56.74, 224.19),   # GOES-West / 18
    (-75.2, 55.49, 138.30),    # GOES-East / 16
])
def test_satellite_look_matches_the_golden_vectors(
        sat_lon_deg, exp_zenith_deg, exp_az_deg):
    look = satellite_look(SITE, sat_lon_deg)
    assert 90.0 - look.alt_deg == pytest.approx(exp_zenith_deg, abs=0.01)
    assert look.az_deg == pytest.approx(exp_az_deg, abs=0.01)


def test_satellite_look_is_not_a_separate_implementation():
    """If someone ever writes bespoke satellite trigonometry it will disagree
    with look_from by some small amount and nothing else will notice."""
    for sat_lon_deg in (-137.0, -75.2, 0.0, 140.7):
        assert satellite_look(SITE, sat_lon_deg) == look_from(
            SITE, GeoPoint(0.0, sat_lon_deg, GEOSTATIONARY_ALT_KM))


def test_deparallax_moves_toward_the_subsatellite_point():
    """The sign. GOES-West sits at 0N/-137, so a cloud reported at 40N/-105 is
    north and east of it and the correction must pull it south and west. Get
    this backwards and every cloud lands twice as far off as uncorrected."""
    corrected = deparallax(40.0, -105.0, 9.0, -137.0)
    assert corrected.lat_deg < 40.0
    assert corrected.lon_deg < -105.0

    # And from the other side: a site west of GOES-East moves east toward it.
    corrected_east = deparallax(40.0, -105.0, 9.0, -75.2)
    assert corrected_east.lat_deg < 40.0
    assert corrected_east.lon_deg > -105.0

    # Southern hemisphere: "toward the sub-satellite point" is now northward,
    # so the corrected latitude must INCREASE. An abs(sin(lat)) anywhere in the
    # chain gets this backwards and nothing north of the equator notices.
    corrected_south = deparallax(-40.0, -105.0, 9.0, -137.0)
    assert corrected_south.lat_deg > -40.0
    assert corrected_south.lon_deg < -105.0


# Design section 9.4's h=9.0 golden encodes the flat-earth shift and has been
# amended: deparallax now walks the satellite ray exactly, which lands 41.8 m
# away -- outside this test's 10 m tolerance. The h=2.0 golden is unchanged
# (it moves 2.1 m). Both replacements come from an independent forward
# ray-trace, not from the module's output: place a cloud at h, cast the ray
# from the geostationary position through it, take the sphere intersection as
# the reported pixel, and require the correction to return the original cloud.
@pytest.mark.parametrize(
    "cloud_msl_km,exp_nominal_km,exp_moved_km,exp_lat,exp_lon", [
        (2.0, 3.05, 3.04778, 39.98034, -105.02493),
        (9.0, 13.72, 13.68259, 39.91172, -105.11182),
    ])
def test_deparallax_magnitude_is_h_tan_zenith(
        cloud_msl_km, exp_nominal_km, exp_moved_km, exp_lat, exp_lon):
    """How far the correction moves the point, measured from deparallax's own
    output rather than from a second copy of its formula living in the test."""
    look = satellite_look(Site(40.0, -105.0, 0.0), -137.0)
    nominal_km = cloud_msl_km * math.tan(math.radians(90.0 - look.alt_deg))
    assert nominal_km == pytest.approx(exp_nominal_km, abs=0.01)

    corrected = deparallax(40.0, -105.0, cloud_msl_km, -137.0)
    assert corrected.height_msl_km == cloud_msl_km

    moved_km = _great_circle_km(
        40.0, -105.0, corrected.lat_deg, corrected.lon_deg)
    assert moved_km == pytest.approx(exp_moved_km, abs=1e-4)
    # The magnitude this test is named for: the exact ray walk still agrees
    # with h*tan(zenith) to better than 0.3 percent, so the design's intuition
    # about the SIZE of the shift stands even though its formula is inexact.
    assert abs(moved_km - nominal_km) < 0.05

    # And it lands on the golden point, within 10 m on the ground.
    assert corrected.lat_deg == pytest.approx(exp_lat, abs=1e-4)
    assert corrected.lon_deg == pytest.approx(exp_lon, abs=1e-4)
    assert _great_circle_km(
        corrected.lat_deg, corrected.lon_deg, exp_lat, exp_lon) < 0.010


def test_deparallax_refuses_a_point_the_satellite_cannot_see():
    """Not in design section 8.7's list of domain errors, but the flat-earth
    form went NEGATIVE for a satellite below the horizon and walked the point
    backwards along the bearing with no signal at all -- exactly the sign error
    section 8.5 warns about, arrived at from the other end. Routing through
    pierce_point inherits its guards."""
    # GOES-West is 26.8 deg BELOW the horizon from 70N/60E.
    with pytest.raises(ValueError) as exc:
        deparallax(70.0, 60.0, 9.0, -137.0)
    assert "-26.8" in str(exc.value)

    # A negative cloud height is a layer below the observer.
    with pytest.raises(ValueError) as exc:
        deparallax(40.0, -105.0, -9.0, -137.0)
    assert "-9.0" in str(exc.value)


def test_deparallax_is_zero_at_the_subsatellite_point():
    """Looking straight up the satellite sees no parallax at all, so the
    correction must vanish rather than drifting on a degenerate azimuth."""
    sat_lon_deg = -137.0
    look = satellite_look(Site(0.0, sat_lon_deg, 0.0), sat_lon_deg)
    assert look.alt_deg == pytest.approx(90.0, abs=1e-4)

    corrected = deparallax(0.0, sat_lon_deg, 9.0, sat_lon_deg)
    assert corrected.lat_deg == pytest.approx(0.0, abs=1e-6)
    assert corrected.lon_deg == pytest.approx(sat_lon_deg, abs=1e-6)


def test_a_target_below_the_horizon_is_refused():
    """Not None, not a clamp: alt <= 0 has no pierce point and silently
    returning one would put a cloud verdict on a direction underground."""
    for bad_alt in (0.0, -5.0):
        with pytest.raises(ValueError) as exc:
            pierce_point(SITE, bad_alt, 0.0, 9.0)
        assert str(bad_alt) in str(exc.value)

        with pytest.raises(ValueError) as exc:
            slant_to_layer_km(bad_alt, 9.0)
        assert str(bad_alt) in str(exc.value)

    with pytest.raises(ValueError):
        pierce_point(SITE, 90.1, 0.0, 9.0)
    with pytest.raises(ValueError):
        slant_to_layer_km(90.1, 9.0)


def test_a_layer_at_or_below_the_observer_is_refused():
    """A 2 km site cannot pierce a 2 km shell; the quadratic still has a root
    and it is meaningless."""
    high = Site(40.0, -105.0, 2.0)
    for bad_layer in (2.0, 1.5):
        with pytest.raises(ValueError) as exc:
            pierce_point(high, 30.0, 0.0, bad_layer)
        assert str(bad_layer) in str(exc.value)

        with pytest.raises(ValueError) as exc:
            slant_to_layer_km(30.0, bad_layer, site_elev_km=2.0)
        assert str(bad_layer) in str(exc.value)

    # Sea-level observer, layer at sea level.
    with pytest.raises(ValueError):
        slant_to_layer_km(30.0, 0.0)


def test_longitude_normalises_across_the_antimeridian():
    """Looking east from 179.9 the pierce point is at -179.7, not 180.3."""
    site = Site(40.0, 179.9, 0.0)
    p = pierce_point(site, 15.0, 90.0, 9.0)
    assert -180.0 < p.lon_deg <= 180.0
    assert p.lon_deg < 0.0
    assert p.lon_deg == pytest.approx(-179.71003, abs=1e-5)

    # The boundary itself. The contract is half-open: exactly +180 comes back
    # as +180, never as -180. Both of these land on it exactly -- looking due
    # north from the antimeridian leaves the longitude alone -- and the one
    # line of code that implements it was invisible to the whole suite.
    assert pierce_point(
        Site(40.0, 180.0, 0.0), 30.0, 0.0, 9.0).lon_deg == 180.0
    assert pierce_point(
        Site(0.0, -180.0, 0.0), 30.0, 0.0, 9.0).lon_deg == 180.0


def test_beam_footprint_uses_the_half_angle():
    """2*s*tan(fov/2), not s*tan(fov). At 1.7 deg the two differ by 0.02 m and
    nothing catches it; at 90 deg one is 2 km and the other is 1.6e16."""
    assert beam_footprint_km(1.0, 90.0) == pytest.approx(2.0, abs=1e-12)

    # The whole-angle bug: s*tan(fov) blows up at 90 and goes NEGATIVE past it.
    assert beam_footprint_km(1.0, 90.0) < 1e3
    assert beam_footprint_km(1.0, 120.0) == pytest.approx(
        2.0 * math.tan(math.radians(60.0)), abs=1e-12)
    assert beam_footprint_km(1.0, 120.0) > 0.0

    # The three design footprints, in metres.
    fov_deg = 1.682
    for layer_km, alt_deg, exp_slant_km, exp_footprint_m in [
        (0.6, 30.0, 1.200, 35.2),
        (2.0, 45.0, 2.828, 83.0),
        (9.0, 30.0, 17.962, 527.3),
    ]:
        slant_km = slant_to_layer_km(alt_deg, layer_km)
        assert slant_km == pytest.approx(exp_slant_km, abs=1e-3)
        footprint_m = beam_footprint_km(slant_km, fov_deg) * 1000.0
        assert footprint_m == pytest.approx(exp_footprint_m, abs=0.1)

    for bad_fov in (0.0, -1.0, 180.0, 200.0):
        with pytest.raises(ValueError) as exc:
            beam_footprint_km(1.0, bad_fov)
        assert str(bad_fov) in str(exc.value)


def test_site_elevation_moves_the_pierce_point():
    """A mountain site is already partway up through the layer, so its line of
    sight reaches the shell sooner and nearer. Dropping site_elev_km puts the
    pierce point kilometres downrange."""
    sea = Site(40.0, -105.0, 0.0)
    high = Site(40.0, -105.0, 2.0)

    assert (slant_to_layer_km(30.0, 9.0, 2.0)
            < slant_to_layer_km(30.0, 9.0, 0.0))

    p_sea = pierce_point(sea, 30.0, 0.0, 9.0)
    p_high = pierce_point(high, 30.0, 0.0, 9.0)
    assert p_high.lat_deg < p_sea.lat_deg
    assert p_sea.lat_deg == pytest.approx(40.13970, abs=1e-5)
    assert p_high.lat_deg == pytest.approx(40.10870, abs=1e-5)


def test_agl_to_msl_adds_the_ground_elevation():
    """Ceilometers report AGL; nothing else in this module accepts it."""
    assert agl_to_msl_km(1.5, 1.6) == pytest.approx(3.1)
    assert agl_to_msl_km(0.6, 0.0) == pytest.approx(0.6)
    assert agl_to_msl_km(0.0, 1.6) == pytest.approx(1.6)
