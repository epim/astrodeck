"""The GOES-R ABI fixed grid, stage 2.

Every test here pins one specific way the module can be plausibly wrong: the
sphere substituted for the GRS80 ellipsoid, ``perspective_point_height`` used
where the geocentric radius belongs, the far intersection root taken instead of
the near one, an absolute value quietly applied to a negative ``y_scale_rad``
and the image mirrored north-south, a longitude that walks off the antimeridian,
a nominal "2 km" used where the real ground spacing is 3.5 km.

Golden vectors are section 9 of the design, verbatim, read from two real
GOES-18 granules of 2026-08-21. They are asserted, never recomputed here: a
test that re-derives the number it is checking checks nothing.

Two GridSpecs throughout, the 2 km mask grid and the 10 km height grid, because
the projection is a property of the satellite and the sampling is not, and the
easiest way to break that is to have only ever tested one.
"""
import dataclasses
import math

import pytest

from astrodeck.cloudmap.abi_grid import (
    GridSpec,
    in_grid,
    index_to_lonlat,
    index_to_scan,
    lonlat_to_index,
    lonlat_to_scan,
    pixel_size_km,
    scan_to_index,
    scan_to_lonlat,
)
from astrodeck.cloudmap.geometry import EARTH_RADIUS_KM

# Design section 9.1, from OR_ABI-L2-ACMC-M6_G18_s20262330606176_... (2 km) and
# OR_ABI-L2-ACHAC-M6_G18_s20262330606176_... (10 km).
ACMC = GridSpec(
    sat_height_m=42164160.0,
    r_eq_m=6378137.0,
    r_pol_m=6356752.31414,
    lon_origin_deg=-137.0,
    sweep_axis="x",
    x_offset_rad=-0.06997200101613998,
    x_scale_rad=5.6000000768108293e-05,
    n_cols=2500,
    y_offset_rad=0.12821200489997864,
    y_scale_rad=-5.6000000768108293e-05,
    n_rows=1500,
)
ACHAC = GridSpec(
    sat_height_m=42164160.0,
    r_eq_m=6378137.0,
    r_pol_m=6356752.31414,
    lon_origin_deg=-137.0,
    sweep_axis="x",
    x_offset_rad=-0.06985999643802643,
    x_scale_rad=0.0002800000074785203,
    n_cols=500,
    y_offset_rad=0.12809999287128448,
    y_scale_rad=-0.0002800000074785203,
    n_rows=300,
)
# Design section 2.2's full disk, ABI-L2-ACMF: 5424 x 5424 at 2 km. Both CONUS
# sectors above sit wholly on the disk, which hides every case where a cell
# centre does not. This one's array CORNERS are off the earth entirely.
FULL_DISK = GridSpec(
    sat_height_m=42164160.0,
    r_eq_m=6378137.0,
    r_pol_m=6356752.31414,
    lon_origin_deg=-137.0,
    sweep_axis="x",
    x_offset_rad=-0.151844,
    x_scale_rad=5.6e-05,
    n_cols=5424,
    y_offset_rad=0.151844,
    y_scale_rad=-5.6e-05,
    n_rows=5424,
)

# Design section 9.2. Scan angles do not depend on the sampling, so one table
# serves both grids -- which is itself the invariant test 5 pins.
SCAN_GOLDEN = [
    (40.0, -105.0, 0.0677105022203723, 0.10686465600808957),
    (37.0, -125.0, 0.028365874074141435, 0.10232676121155931),
    (0.0, -137.0, 0.0, 0.0),
    (50.0, -160.0, -0.04146199288596409, 0.12602032966691368),
    (20.0, -100.0, 0.09608491064111407, 0.05793263512794123),
]

# Design section 9.3: (lat, lon, ACMC row/col, in grid, ACHAC row/col, in grid).
INDEX_GOLDEN = [
    (40.0, -105.0, (381, 2459), True, (76, 491), True),
    (37.0, -125.0, (462, 1756), True, (92, 351), True),
    (50.0, -160.0, (39, 509), True, (7, 101), True),
    (0.0, -137.0, (2290, 1250), False, (457, 249), False),
    (20.0, -100.0, (1255, 2965), False, (251, 593), False),
]

# Design section 9.4: (lat, lon, ACMC ns, ACMC ew, ACHAC ns, ACHAC ew), km.
PIXEL_GOLDEN = [
    (40.0, -105.0, 3.500622, 2.754963, 17.473834, 13.753146),
    (37.0, -125.0, 2.899210, 2.159355, 14.497235, 10.798411),
    (50.0, -160.0, 4.343845, 2.483640, 21.797253, 12.440205),
]

# The easternmost visible longitude on three parallels, in degrees. NOT read
# back out of the module: the line of sight grazes the ellipsoid where the
# point's own distance along the axis toward the satellite is exactly
# ``r_eq^2 / H`` = 964815.416 m, and turning that into a longitude is one
# ``acos``. Design section 7.1's approximate test puts the same five edges at
# -55.5086, -58.1208, -72.5513, -92.1824 and -105.1338 instead -- 0.19 deg out
# at the equator and 2.13 deg out at latitude 80 -- which is why the module
# deviates from it; see the DEVIATION block in ``lonlat_to_scan``.
LIMB_GOLDEN = [
    (0.0, -55.700483305),
    (40.0, -58.372953402),
    (70.0, -73.165932087),
    (78.0, -93.488042608),
    (80.0, -107.260745707),
]

# Scan angles are compared at 1e-15 rad rather than by ``==``. One ULP of 0.068
# is 1.4e-17, so this allows about 70 ULP of libm difference between platforms
# while still being 4e-8 metres of displacement at the satellite -- far tighter
# than any error this module could plausibly contain.
SCAN_TOL = 1e-15


def test_the_sub_satellite_point_maps_to_the_grid_origin():
    """(0, -137) is where the satellite is looking straight down, so both scan
    angles are exactly zero -- not nearly zero.

    Exact, and it can be: ``tan(0)`` and ``sin(0)`` are 0.0, so ``sz`` and
    ``sy`` are zero before any transcendental runs and both ``atan`` and
    ``asin`` return their arguments. If this one drifts off zero the fault is
    upstream in ``rc`` or ``H``, not in rounding.
    """
    assert lonlat_to_scan(ACMC, 0.0, -137.0) == (0.0, 0.0)
    assert lonlat_to_scan(ACHAC, 0.0, -137.0) == (0.0, 0.0)

    # And back. The inverse is not bit-exact by construction -- it runs a
    # square root and a degrees() -- so it gets a tolerance, unlike the
    # forward direction.
    lat_deg, lon_deg = scan_to_lonlat(ACMC, 0.0, 0.0)
    assert lat_deg == pytest.approx(0.0, abs=1e-12)
    assert lon_deg == pytest.approx(-137.0, abs=1e-12)


def test_scan_and_lonlat_are_exact_inverses():
    """Round trip the whole western hemisphere the sector can see, both grids.

    The point count assertion is the load-bearing half. A visibility test with
    its inequality the wrong way round returns ``None`` for every input, and
    without a count this test would then pass having asserted nothing at all --
    the exact shape design section 2.7 caught in its own author's work.
    """
    checked = 0
    worst_deg = 0.0
    for spec in (ACMC, ACHAC):
        for lat_deg in range(-20, 61, 5):
            for lon_deg in range(-175, -79, 5):
                scan = lonlat_to_scan(spec, float(lat_deg), float(lon_deg))
                assert scan is not None, (lat_deg, lon_deg)
                back = scan_to_lonlat(spec, *scan)
                assert back is not None, (lat_deg, lon_deg)
                worst_deg = max(
                    worst_deg,
                    abs(back[0] - lat_deg),
                    abs(back[1] - lon_deg),
                )
                checked += 1

    assert checked >= 200, "the sweep collapsed; a vacuous pass"
    assert checked == 680
    assert worst_deg < 1e-9


def test_a_point_behind_the_limb_is_not_visible():
    """The Atlantic at (40, -20) is on the far side of the planet from
    GOES-18, and the arithmetic alone returns a perfectly plausible scan angle
    for it.

    So the second half matters more than the first: the limb has to be in the
    right PLACE, not merely somewhere. Walking west along the 40th parallel the
    disk edge is at longitude -58.373, and one step either side of it
    disagrees. A guard that rejected the whole eastern hemisphere would pass
    the first assertion and fail here.

    THIS GOLDEN MOVED, and the design has to move with it. Design section 10
    pins the edge at -58.12, which is where section 7.1's approximate
    visibility test puts it and 21 km of Atlantic east of where the earth puts
    it -- a self-derived golden that froze the approximation it came from. See
    the DEVIATION block in ``lonlat_to_scan``. The walk below resolves to
    0.01 deg so its tolerance is one step;
    ``test_the_limb_sits_where_the_ellipsoid_puts_it`` pins the same edge four
    orders tighter.
    """
    assert lonlat_to_scan(ACMC, 40.0, -20.0) is None
    assert lonlat_to_scan(ACHAC, 40.0, -20.0) is None

    step_deg = 0.01
    steps = 0
    while steps < 7000:
        lon_deg = -20.0 - steps * step_deg
        if lonlat_to_scan(ACMC, 40.0, lon_deg) is not None:
            break
        steps += 1
    else:  # pragma: no cover - only reached if the limb vanishes entirely
        pytest.fail("no visible point found on the 40th parallel")

    first_visible_deg = -20.0 - steps * step_deg
    assert first_visible_deg == pytest.approx(LIMB_GOLDEN[1][1], abs=0.011)
    assert lonlat_to_scan(ACMC, 40.0, first_visible_deg) is not None
    assert lonlat_to_scan(ACMC, 40.0, first_visible_deg + step_deg) is None
    # Visibility is a property of the satellite, not of the sampling.
    assert lonlat_to_scan(ACHAC, 40.0, first_visible_deg) is not None

    # Deeper onto the disk stays visible; the limb is one crossing, not noise.
    assert lonlat_to_scan(ACMC, 40.0, first_visible_deg - 1.0) is not None
    assert lonlat_to_scan(ACMC, 40.0, first_visible_deg - 40.0) is not None


def test_the_limb_sits_where_the_ellipsoid_puts_it():
    """The limb is a tangency condition, and the ellipsoid is inside it.

    Five parallels, both grids, bisected and pinned to 1e-8 deg against
    numbers that come from the tangency condition rather than from this module.
    Design section 7.1's formula puts the same five edges 0.19 to 2.13 deg
    further east, so this is the test that stops that formula coming back, and
    the one that would have caught it going in.

    The flattening is load-bearing and it is INVISIBLE AT THE EQUATOR, which is
    why only one of the five latitudes is there: a sphere of the same
    equatorial radius reproduces the equatorial limb to the last bit, and then
    misses by 0.016 deg at latitude 40 and 0.084 deg at latitude 70. A limb
    test written only at the equator measures nothing about the earth's shape.
    """

    def limb_lon_deg(spec, lat_deg):
        """Bisect for the easternmost visible longitude on this parallel."""
        visible, hidden = -137.0, -20.0
        for _ in range(80):
            middle = (visible + hidden) / 2.0
            if lonlat_to_scan(spec, lat_deg, middle) is None:
                hidden = middle
            else:
                visible = middle
        return (visible + hidden) / 2.0

    for lat_deg, want_deg in LIMB_GOLDEN:
        for spec in (ACMC, ACHAC):
            assert limb_lon_deg(spec, lat_deg) == pytest.approx(
                want_deg, abs=1e-8
            ), (lat_deg, spec.n_rows)

    sphere = dataclasses.replace(ACMC, r_pol_m=ACMC.r_eq_m)
    assert limb_lon_deg(sphere, 0.0) == pytest.approx(
        LIMB_GOLDEN[0][1], abs=1e-8
    )
    assert abs(
        limb_lon_deg(sphere, 40.0) - limb_lon_deg(ACMC, 40.0)
    ) == pytest.approx(0.015972, abs=1e-5)
    assert abs(
        limb_lon_deg(sphere, 70.0) - limb_lon_deg(ACMC, 70.0)
    ) == pytest.approx(0.083604, abs=1e-5)

    # Out at the limb the scan angle itself must still be exact, and this is
    # the only place in the file that goes there. ``|sin(x_rad)|`` reaches
    # 0.151269 on the equatorial tangent -- ``r_eq / sat_height_m``, the
    # largest value this projection can produce anywhere -- while test 2's
    # sweep only ever reaches 0.137 and the ACMC sector's own edges 0.070.
    # ``_clamp_unit`` claims in its docstring never to engage; a clamp that had
    # quietly become real anywhere in the band between 0.137 and 0.1513 would
    # pass every other test here.
    near_limb = lonlat_to_scan(ACMC, 0.0, LIMB_GOLDEN[0][1] - 0.01)
    assert near_limb[0] == pytest.approx(0.151852078, abs=1e-9)
    assert math.sin(near_limb[0]) > 0.1512
    assert math.sin(near_limb[0]) < ACMC.r_eq_m / ACMC.sat_height_m


def test_the_round_trip_survives_grazing_incidence():
    """Design section 11's first invariant, at the only place it is in danger.

    Test 2 sweeps a box whose closest approach to a limb is 15.4 deg, where
    the round trip holds to 1.8e-13 deg and could not tell a well-conditioned
    inverse from a badly conditioned one. Against a ray that grazes the ellipsoid the
    discriminant written as design section 7.2 writes it, ``b*b - 4*a*c``,
    subtracts two numbers near 6.95e+15 that agree to sixteen digits and comes
    back with 2.0 where every significant bit has cancelled. On the 72
    points below, that form reaches 6.0e-09 deg and the rearranged one
    1.0e-10, which is what holds the rearrangement in place. The margin is not
    uniform: cancellation is spiky, it bites hardest where the visible disk is
    narrowest, and it is latitude 80 at 0.1 deg inside the limb that separates
    the two forms by 200 times. A grazing test written only at mid-latitudes
    cannot tell which discriminant is running.

    AND THE INVARIANT AS WRITTEN IS STILL FALSE, which is the second half of
    this test. Inside about 0.03 deg of the limb the error climbs past 1e-09
    however the discriminant is written, because at tangency the ground point
    is stationary in the scan angle and the inverse is ill-conditioned there;
    an independent vector ray/ellipsoid formulation is no better. Design
    section 11 has to say where its 1e-09 holds, so the last assertion pins the
    place it does not -- 1e-06 deg from the limb the round trip is off by a
    micro-degree, and closer in ``scan_to_lonlat`` reports a visible point as
    missing the earth altogether.
    """
    def lon_gap_deg(got_deg, want_deg):
        """Separation in longitude, across the antimeridian if need be.

        The western limb lands at -218 deg before normalisation, so the raw
        difference against a normalised answer is 360 deg of nothing.
        """
        gap = abs(got_deg - want_deg) % 360.0
        return min(gap, 360.0 - gap)

    # The disk is symmetric about the equator and about the sub-satellite
    # meridian, so one bisected limb longitude serves four parallels.
    parallels = []
    for lat_deg, limb_deg in LIMB_GOLDEN:
        parallels.append((lat_deg, limb_deg))
        if lat_deg != 0.0:
            parallels.append((-lat_deg, limb_deg))

    r_eq_over_h = ACMC.r_eq_m / ACMC.sat_height_m
    worst_deg = 0.0
    checked = 0
    for lat_deg, limb_deg in parallels:
        west_deg = 2.0 * ACMC.lon_origin_deg - limb_deg
        for offset_deg in (1.0, 0.5, 0.2, 0.1):
            for lon_deg in (limb_deg - offset_deg, west_deg + offset_deg):
                scan = lonlat_to_scan(ACMC, lat_deg, lon_deg)
                assert scan is not None, (lat_deg, lon_deg)
                back = scan_to_lonlat(ACMC, *scan)
                assert back is not None, (lat_deg, lon_deg)
                worst_deg = max(
                    worst_deg,
                    abs(back[0] - lat_deg),
                    lon_gap_deg(back[1], lon_deg),
                )
                assert abs(math.sin(scan[0])) <= r_eq_over_h
                checked += 1

    assert checked == 72, "the grazing sweep collapsed; a vacuous pass"
    assert worst_deg < 1e-9, worst_deg

    # The band where it does not hold, pinned so nobody restores the stronger
    # claim by accident. A micro-degree is 11 cm, which is 4e-05 of a limb
    # pixel, so this is a documentation problem and not a geometry one.
    grazing_lon_deg = LIMB_GOLDEN[0][1] - 1e-6
    scan = lonlat_to_scan(ACMC, 0.0, grazing_lon_deg)
    assert scan is not None
    back = scan_to_lonlat(ACMC, *scan)
    assert abs(back[1] - grazing_lon_deg) > 1e-8


def test_row_increases_southward_and_column_eastward():
    """Pins the negative ``y_scale_rad``.

    The cell is (381, 2459), the pixel over (40, -105) -- deliberately far from
    the sub-satellite point in both axes. Take ``abs()`` of ``y_scale_rad``
    somewhere and the image mirrors north-south, and every check made at or
    near the sub-satellite point still passes, because the origin row is its
    own reflection.
    """
    row, col = 381, 2459
    here = index_to_lonlat(ACMC, row, col)
    south = index_to_lonlat(ACMC, row + 1, col)
    east = index_to_lonlat(ACMC, row, col + 1)

    assert south[0] < here[0], "row+1 must move south"
    assert east[1] > here[1], "col+1 must move east"

    # A row step moves latitude more than a column step does, and a column
    # step moves longitude more than a row step does. Swap the two axes and
    # both of these invert. The margins are NOT large -- a row step here also
    # shifts longitude by 0.017 deg against the column step's 0.032 -- because
    # the grid axes are genuinely skewed against the graticule this far off
    # nadir. Asserting a comfortable factor of ten would fail correct code.
    assert abs(south[0] - here[0]) > abs(east[0] - here[0])
    assert abs(east[1] - here[1]) > abs(south[1] - here[1])

    # Same sense on the coarse grid, where one step is five times as far.
    row10, col10 = 76, 491
    here10 = index_to_lonlat(ACHAC, row10, col10)
    assert index_to_lonlat(ACHAC, row10 + 1, col10)[0] < here10[0]
    assert index_to_lonlat(ACHAC, row10, col10 + 1)[1] > here10[1]


def test_both_products_agree_on_scan_angle_and_differ_only_in_index():
    """Design section 5's invariant: the projection depends on the satellite,
    the index depends on the sampling.

    The scan angles are compared to each other bit-for-bit, not merely to a
    tolerance, because nothing in ``lonlat_to_scan`` may read a sampling field
    at all -- if the two grids agree only approximately, one of them is being
    quantised somewhere it should not be.
    """
    for lat_deg, lon_deg, x_rad, y_rad in SCAN_GOLDEN:
        fine = lonlat_to_scan(ACMC, lat_deg, lon_deg)
        coarse = lonlat_to_scan(ACHAC, lat_deg, lon_deg)
        assert fine == coarse, (lat_deg, lon_deg)
        assert fine[0] == pytest.approx(x_rad, abs=SCAN_TOL)
        assert fine[1] == pytest.approx(y_rad, abs=SCAN_TOL)

    # Design section 9.2's last row: behind the limb for both, at neither.
    assert lonlat_to_scan(ACMC, 40.0, -20.0) is None
    assert lonlat_to_scan(ACHAC, 40.0, -20.0) is None

    for lat_deg, lon_deg, fine_rc, fine_in, coarse_rc, coarse_in in INDEX_GOLDEN:
        assert lonlat_to_index(ACMC, lat_deg, lon_deg) == fine_rc
        assert lonlat_to_index(ACHAC, lat_deg, lon_deg) == coarse_rc
        assert in_grid(ACMC, *fine_rc) is fine_in
        assert in_grid(ACHAC, *coarse_rc) is coarse_in
        # Five times the sampling, so the fine index is about five times the
        # coarse one -- the indices differ, and differ in the right ratio.
        assert fine_rc != coarse_rc
        assert abs(fine_rc[0] - 5 * coarse_rc[0]) <= 5
        assert abs(fine_rc[1] - 5 * coarse_rc[1]) <= 5


def test_the_sector_corners_match_the_granules_declared_bounds():
    """Design section 9.5: the four ACMC corners, against the numbers the
    granule declares about itself.

    This is the only test whose answer came from outside this codebase, which
    makes it the only one that can catch a self-consistent wrong convention --
    a projection that round trips perfectly with itself and still lands the
    sector in the wrong place.
    """
    corners = {
        "NW": ((0, 0), 53.5001, 175.6236),
        "NE": ((0, 2499), 53.5001, -89.6236),
        "SW": ((1499, 0), 14.8052, -161.5694),
        "SE": ((1499, 2499), 14.8052, -112.4306),
    }
    for name, ((row, col), lat_deg, lon_deg) in corners.items():
        got = index_to_lonlat(ACMC, row, col)
        assert got is not None, name
        assert got[0] == pytest.approx(lat_deg, abs=1e-4), name
        assert got[1] == pytest.approx(lon_deg, abs=1e-4), name

    # The granule's own global attributes, at the tolerance they actually hold
    # to. Design section 9.5 says "matching to five decimals"; measured, the
    # north and west bounds agree to 1.2e-5 deg, which rounds differently in
    # the fifth place (53.500066 against a declared 53.50006). 2e-5 is the
    # honest figure and is still 1.3 metres on the ground.
    northbound = max(
        index_to_lonlat(ACMC, 0, col)[0] for col in (0, 1250, 2499)
    )
    assert northbound == pytest.approx(53.50006, abs=2e-5)
    assert index_to_lonlat(ACMC, 0, 0)[1] == pytest.approx(175.62358, abs=2e-5)
    assert index_to_lonlat(ACMC, 0, 2499)[1] == pytest.approx(
        -89.62357, abs=2e-5
    )


def test_the_southern_edge_bows_below_its_corners():
    """The declared ``southbound`` is not at a corner, and asserting it there
    would fail correct code.

    The bottom row is a constant scan angle, not a constant latitude: it bows
    south in the middle, and the minimum sits at column 1250 directly beneath
    the satellite. This is the shape of the whole module in one row -- the grid
    is regular in ANGLE and nothing about it is regular on the ground.
    """
    bottom = ACMC.n_rows - 1
    lats = []
    for col in range(ACMC.n_cols):
        point = index_to_lonlat(ACMC, bottom, col)
        assert point is not None, col
        lats.append(point[0])

    lowest = min(lats)
    assert lats.index(lowest) == 1250
    assert lowest == pytest.approx(14.5713, abs=1e-4)
    assert index_to_lonlat(ACMC, bottom, 1250)[1] == pytest.approx(
        -136.9906, abs=1e-4
    )

    # South of BOTH corners, by a quarter of a degree -- 26 km, not rounding.
    assert lowest < lats[0] - 0.2
    assert lowest < lats[-1] - 0.2
    assert lats[0] == pytest.approx(14.8052, abs=1e-4)
    assert lats[-1] == pytest.approx(14.8052, abs=1e-4)


def test_pixel_size_is_not_the_nominal_resolution():
    """Design section 9.4. The "2 km" in the product name is at nadir and
    nowhere else.

    The second half of this test is the part that catches a stub: at all three
    sample points the 2 km product is over 2.5 km north-south, reaching 4.34 km
    at (50, -160). Returning the nominal value understated a measured cloud
    speed by 33 percent during the work that produced the design, which is why
    this function exists at all.
    """
    for lat_deg, lon_deg, fine_ns, fine_ew, coarse_ns, coarse_ew in PIXEL_GOLDEN:
        fine_rc = lonlat_to_index(ACMC, lat_deg, lon_deg)
        coarse_rc = lonlat_to_index(ACHAC, lat_deg, lon_deg)
        got_fine = pixel_size_km(ACMC, *fine_rc)
        got_coarse = pixel_size_km(ACHAC, *coarse_rc)

        assert got_fine[0] == pytest.approx(fine_ns, abs=1e-3)
        assert got_fine[1] == pytest.approx(fine_ew, abs=1e-3)
        assert got_coarse[0] == pytest.approx(coarse_ns, abs=1e-3)
        assert got_coarse[1] == pytest.approx(coarse_ew, abs=1e-3)

        assert got_fine[0] > 2.5, (lat_deg, lon_deg, got_fine)
        assert got_coarse[0] > 12.5, (lat_deg, lon_deg, got_coarse)
        # North-south stretches faster than east-west away from nadir, so the
        # cell is taller than it is wide at every one of these points.
        assert got_fine[0] > got_fine[1]

        # Five times the sampling is five times the ground cell, to a part in
        # a thousand -- so neither grid is being measured with the other's
        # spacing.
        assert got_coarse[0] / got_fine[0] == pytest.approx(5.0, rel=5e-3)
        assert got_coarse[1] / got_fine[1] == pytest.approx(5.0, rel=5e-3)


def test_index_rounding_is_pinned_at_a_cell_boundary():
    """Exactly half a cell past a centre is a tie, and Python's ``round``
    breaks ties to EVEN, not upward.

    Either neighbour is defensible on a boundary; what is not defensible is
    changing which one by accident. Column 100 plus half a cell stays at 100
    and column 101 plus half a cell goes to 102 -- the same displacement, two
    different directions, which is the signature of banker's rounding and
    would be 101 and 102 under ``floor(q + 0.5)``.
    """
    for base, expected in ((100, 100), (101, 102), (0, 0), (1, 2)):
        x_rad = ACMC.x_offset_rad + (base + 0.5) * ACMC.x_scale_rad
        y_rad = ACMC.y_offset_rad + (base + 0.5) * ACMC.y_scale_rad
        row, col = scan_to_index(ACMC, x_rad, y_rad)
        assert col == expected, ("col", base)
        assert row == expected, ("row", base)

    # A whole cell either side of a centre is not a tie at all, and lands where
    # arithmetic says.
    for base in (100, 101):
        x_rad, y_rad = index_to_scan(ACMC, base, base)
        assert scan_to_index(ACMC, x_rad, y_rad) == (base, base)
        assert scan_to_index(
            ACMC,
            x_rad + 0.4 * ACMC.x_scale_rad,
            y_rad + 0.4 * ACMC.y_scale_rad,
        ) == (base, base)


def test_a_visible_point_outside_the_sector_still_gets_an_index():
    """The sub-satellite point is the most visible place on the disk and it is
    900 rows below the bottom of a CONUS sector.

    ``None`` means "not on the earth". Outside the sector is a different
    answer, reached by a different call, and conflating them would tell stage 4
    that the clearest pixel on the hemisphere does not exist.
    """
    assert lonlat_to_index(ACMC, 0.0, -137.0) == (2290, 1250)
    assert in_grid(ACMC, 2290, 1250) is False
    assert lonlat_to_index(ACHAC, 0.0, -137.0) == (457, 249)
    assert in_grid(ACHAC, 457, 249) is False

    # Off the western edge too, where the index goes NEGATIVE rather than
    # clamping to zero -- clamping would silently return the edge pixel's
    # cloud state for a place the sector never saw.
    row, col = lonlat_to_index(ACMC, 53.0, 170.0)
    assert col < 0
    assert in_grid(ACMC, row, col) is False

    # And the distinction the two return types carry: behind the limb is None.
    assert lonlat_to_index(ACMC, 40.0, -20.0) is None


def test_in_grid_is_exclusive_at_the_top_and_zero_based_at_the_bottom():
    """The last usable row is ``n_rows - 1``; row ``n_rows`` and row -1 are not
    on the array at all.

    Nothing else in this file reaches the boundary. Design section 9.3's
    out-of-sector golden indices are 790 and 157 rows past the end, so an
    ``in_grid`` off by one still answers them correctly, and the negative case
    above only asserts ``col < 0``. Both directions of the off-by-one are real
    and they fail differently. One past the end raises when stage 3 subscripts
    it. One BEFORE the start does not: ``array[-1]`` is numpy's last row, so a
    negative index called in-grid returns the cloud state from the opposite
    edge of the sector, silently, with no error anywhere.
    """
    for spec in (ACMC, ACHAC, FULL_DISK):
        last_row = spec.n_rows - 1
        last_col = spec.n_cols - 1
        assert in_grid(spec, 0, 0) is True
        assert in_grid(spec, last_row, last_col) is True

        assert in_grid(spec, spec.n_rows, last_col) is False
        assert in_grid(spec, last_row, spec.n_cols) is False
        assert in_grid(spec, -1, 0) is False
        assert in_grid(spec, 0, -1) is False

    # ``pixel_size_km`` gates on ``in_grid``, so the same boundary has to hold
    # there, and it has to refuse for being OUT OF GRID rather than stumbling
    # into the degenerate-span guard one line further down.
    for row, col in ((ACMC.n_rows, 1250), (-1, 1250), (0, ACMC.n_cols), (0, -1)):
        with pytest.raises(ValueError, match="outside"):
            pixel_size_km(ACMC, row, col)


def test_an_index_outside_the_sector_still_has_a_ground_point():
    """``index_to_lonlat`` extrapolates off the sector; it does not refuse.

    Stage 4 walks a ray across a ladder of cloud heights and runs off the edge
    of a CONUS sector routinely. ``None`` from this function means one thing,
    that the ray misses the earth, and returning it for "outside this array"
    would tell stage 4 that a place the satellite can plainly see does not
    exist. Every other caller in this file passes in-grid indices, so nothing
    else exercises the promise the docstring makes.
    """
    for row, col in ((-10, 1250), (1600, 1250), (0, -50), (0, 2600)):
        assert in_grid(ACMC, row, col) is False
        point = index_to_lonlat(ACMC, row, col)
        assert point is not None, (row, col)
        assert -90.0 <= point[0] <= 90.0
        assert -180.0 < point[1] <= 180.0

    # It extrapolates in the right DIRECTION: ten rows above the top row is
    # north of it, and a hundred past the bottom is south of it.
    assert index_to_lonlat(ACMC, -10, 1250)[0] > index_to_lonlat(ACMC, 0, 1250)[0]
    assert (
        index_to_lonlat(ACMC, 1600, 1250)[0]
        < index_to_lonlat(ACMC, 1499, 1250)[0]
    )

    # And it still returns None for the thing None means: an extrapolated angle
    # that has walked off the disk entirely.
    assert index_to_lonlat(ACMC, -3000, 1250) is None


def test_longitude_comes_back_in_the_half_open_range():
    """The northwest corner's raw arithmetic gives -184.3764, which is not a
    longitude.

    The contract is stage 1's, unchanged: ``(-180, +180]``, with exactly -180
    normalising to +180. The second half of this test reaches that boundary
    exactly, by putting a satellite on the antimeridian and asking for its own
    sub-satellite point -- the only input for which the raw value lands on the
    excluded end.
    """
    nw_lon = index_to_lonlat(ACMC, 0, 0)[1]
    assert nw_lon == pytest.approx(-184.3764 + 360.0, abs=1e-4)
    assert -180.0 < nw_lon <= 180.0

    for spec in (ACMC, ACHAC):
        for row in range(0, spec.n_rows, 37):
            for col in range(0, spec.n_cols, 41):
                point = index_to_lonlat(spec, row, col)
                if point is None:
                    continue
                assert -180.0 < point[1] <= 180.0, (row, col, point)

    antimeridian = dataclasses.replace(ACMC, lon_origin_deg=-180.0)
    lat_deg, lon_deg = scan_to_lonlat(antimeridian, 0.0, 0.0)
    assert lat_deg == pytest.approx(0.0, abs=1e-12)
    assert lon_deg == 180.0


def test_the_far_root_is_not_chosen():
    """Both intersections of the ray with the ellipsoid are real; only one is
    the side facing the satellite.

    ``-b + sqrt(d)`` is the back of the planet and is geometrically just as
    valid, so nothing about the quadratic itself rejects it. The check is a
    distance: the point that comes back must be the near root's distance from
    the satellite, 38321 km, and not the far root's 45328 km. Taking the far
    root would also put (40, -105) at 116 degrees from the sub-satellite point,
    beyond the horizon of a satellite that is supposedly looking at it.
    """
    x_rad, y_rad = lonlat_to_scan(ACMC, 40.0, -105.0)

    # The quadratic, restated here so the two roots are visible in the test.
    ratio = ACMC.r_eq_m ** 2 / ACMC.r_pol_m ** 2
    a = math.sin(x_rad) ** 2 + math.cos(x_rad) ** 2 * (
        math.cos(y_rad) ** 2 + ratio * math.sin(y_rad) ** 2
    )
    b = -2.0 * ACMC.sat_height_m * math.cos(x_rad) * math.cos(y_rad)
    c = ACMC.sat_height_m ** 2 - ACMC.r_eq_m ** 2
    disc = b * b - 4.0 * a * c
    assert disc > 0.0, "this scan angle must have two real intersections"
    near_m = (-b - math.sqrt(disc)) / (2.0 * a)
    far_m = (-b + math.sqrt(disc)) / (2.0 * a)
    assert near_m < far_m
    assert far_m - near_m > 7.0e6

    lat_deg, lon_deg = scan_to_lonlat(ACMC, x_rad, y_rad)
    got_m = _range_to_satellite_m(ACMC, lat_deg, lon_deg)
    assert got_m == pytest.approx(near_m, rel=1e-9)
    assert got_m != pytest.approx(far_m, rel=1e-3)

    # And on the satellite-facing hemisphere, geocentrically.
    assert _central_angle_deg(lat_deg, lon_deg, 0.0, -137.0) < 90.0

    # A ray that misses entirely is the other branch of the same discriminant.
    assert scan_to_lonlat(ACMC, 0.2, 0.0) is None
    assert scan_to_lonlat(ACMC, 0.15, 0.15) is None

    # And a ray pointing AWAY from the earth is a third case the discriminant
    # does not catch: both intersections are behind the satellite, so d stays
    # positive and the near root goes negative. Anti-nadir is column 57349 of a
    # 2500-column sector and no caller can reach it, but "the ray missed" is
    # what this function promises when the ray does not reach the earth in
    # front of the instrument, and the alternative answer is the sub-satellite
    # point -- a real place, reported for a look into empty space.
    assert scan_to_lonlat(ACMC, math.pi, 0.0) is None
    assert scan_to_lonlat(ACMC, 0.0, math.pi) is None


MALFORMED = [
    ("a y-sweep instrument", {"sweep_axis": "y"}),
    ("an unknown sweep axis", {"sweep_axis": "z"}),
    ("no rows", {"n_rows": 0}),
    ("negative rows", {"n_rows": -1500}),
    ("no columns", {"n_cols": 0}),
    ("negative columns", {"n_cols": -2500}),
    ("a zero column spacing", {"x_scale_rad": 0.0}),
    ("a zero row spacing", {"y_scale_rad": 0.0}),
    ("a prolate earth", {"r_pol_m": 6378137.0 + 1.0}),
    ("a satellite at the surface", {"sat_height_m": 6378137.0}),
    ("a satellite inside the earth", {"sat_height_m": 1.0}),
]


@pytest.mark.parametrize(
    "what,override", MALFORMED, ids=[case[0] for case in MALFORMED]
)
def test_a_malformed_grid_is_rejected(what, override):
    """Design section 8, every condition.

    These are malformed inputs, not geometry. Unlike "behind the limb", which
    is a legitimate answer about a real place, none of these describes anything
    -- so they raise rather than returning ``None``, and they raise at
    construction so a bad GridSpec cannot be carried around and used later.

    NOTE what this list does NOT contain, because section 8 does not and
    cannot: ``sat_height_m = 35786023.0``, the granule's bare
    ``perspective_point_height``. That is the 6378 km error design section 4
    warns about most loudly, and it sails past ``sat_height_m <= r_eq_m``
    because 35786 km really is above the surface. No cheap guard catches it;
    what catches it is test 6, where the sector corners move by degrees.
    """
    with pytest.raises(ValueError):
        dataclasses.replace(ACMC, **override)


def test_a_y_sweep_instrument_is_refused_not_silently_mishandled():
    """EUMETSAT sweeps about y and its granules are otherwise the same shape.

    Section 7's algorithm is the x-sweep form. Fed a y-sweep grid it does not
    fail, it returns a plausible scan angle that is wrong by the difference
    between the two conventions -- a few pixels near nadir, growing outward. A
    wrong answer of that shape would be found by nobody.
    """
    with pytest.raises(ValueError) as excinfo:
        dataclasses.replace(ACMC, sweep_axis="y")
    assert "sweep" in str(excinfo.value).lower()
    assert "y" in str(excinfo.value)

    # Nor by casing or whitespace: the field is the granule's literal value.
    for axis in ("X", " x", "x ", ""):
        with pytest.raises(ValueError):
            dataclasses.replace(ACMC, sweep_axis=axis)


def test_pixel_size_is_total_at_the_grid_edge():
    """Row 0 has no row -1 and the last row has no row +1, so the neighbour
    clamps and the divisor halves.

    Get the divisor wrong and the edge rows come back at half or double the
    true spacing, which is exactly the band of the image a motion estimate runs
    off the end of.
    """
    for spec in (ACMC, ACHAC):
        last_row = spec.n_rows - 1
        last_col = spec.n_cols - 1
        for row, col in (
            (0, 0),
            (0, spec.n_cols // 2),
            (0, last_col),
            (last_row, 0),
            (last_row, spec.n_cols // 2),
            (last_row, last_col),
        ):
            ns_km, ew_km = pixel_size_km(spec, row, col)
            assert math.isfinite(ns_km) and ns_km > 0.0, (row, col)
            assert math.isfinite(ew_km) and ew_km > 0.0, (row, col)

    # The clamped edge value is the one-sided spacing, so it agrees with the
    # centred value one row in to within the curvature between them -- not off
    # by the factor of two a wrong divisor would give.
    edge_ns, edge_ew = pixel_size_km(ACMC, 0, 1250)
    inside_ns, inside_ew = pixel_size_km(ACMC, 1, 1250)
    assert edge_ns == pytest.approx(inside_ns, rel=2e-3)
    assert edge_ew == pytest.approx(inside_ew, rel=2e-3)

    # And it is specifically the CLAMPED form, not a centred difference that
    # extrapolates a row off the top of the array. On a CONUS sector both are
    # finite and they differ by only 6e-5 to 3e-3, so nothing above would
    # notice; the difference matters on a full disk, where row -1 at the middle
    # column is off the disk entirely and the unclamped form has no answer at
    # all. Pinned exactly, in both axes and on both grids.
    for spec in (ACMC, ACHAC):
        for row, col in ((0, spec.n_cols // 2), (spec.n_rows - 1, 0)):
            row_lo = max(0, row - 1)
            row_hi = min(spec.n_rows - 1, row + 1)
            col_lo = max(0, col - 1)
            col_hi = min(spec.n_cols - 1, col + 1)
            want_ns = _ground_km(
                index_to_lonlat(spec, row_lo, col),
                index_to_lonlat(spec, row_hi, col),
            ) / (row_hi - row_lo)
            want_ew = _ground_km(
                index_to_lonlat(spec, row, col_lo),
                index_to_lonlat(spec, row, col_hi),
            ) / (col_hi - col_lo)
            got_ns, got_ew = pixel_size_km(spec, row, col)
            assert got_ns == pytest.approx(want_ns, rel=1e-12), (row, col)
            assert got_ew == pytest.approx(want_ew, rel=1e-12), (row, col)

    # Outside the grid there is no cell to size, and clamping the neighbours
    # of a row -5 would give a negative divisor and a negative "size".
    with pytest.raises(ValueError):
        pixel_size_km(ACMC, -5, 1250)
    with pytest.raises(ValueError):
        pixel_size_km(ACMC, 2290, 1250)


def test_pixel_size_says_so_when_a_neighbour_is_off_the_disk():
    """A full-disk grid's array corners are not on the earth, and there is no
    ground distance to report for them.

    Every other GridSpec here is a CONUS sector where every cell centre is on
    the disk, so this branch never runs. Design section 2.2 names
    ``ABI-L2-ACMF`` as a product stage 3 may read, and on that grid
    ``index_to_lonlat`` returns ``None`` for the corners while ``in_grid``
    says yes -- without the guard the subscript on the next line raises
    ``TypeError`` from inside a coordinate routine instead of the documented
    ``ValueError``.
    """
    assert in_grid(FULL_DISK, 0, 0) is True
    assert index_to_lonlat(FULL_DISK, 0, 0) is None

    for row, col in (
        (0, 0),
        (0, FULL_DISK.n_cols - 1),
        (FULL_DISK.n_rows - 1, 0),
        (FULL_DISK.n_rows - 1, FULL_DISK.n_cols - 1),
        (0, FULL_DISK.n_cols // 2),
    ):
        with pytest.raises(ValueError, match="does not see the earth"):
            pixel_size_km(FULL_DISK, row, col)

    # The middle of the same grid is fine, so it is the corner being refused
    # and not the whole spec -- and it is 2 km there, which is the one place
    # the product name is honest.
    middle_ns, middle_ew = pixel_size_km(
        FULL_DISK, FULL_DISK.n_rows // 2, FULL_DISK.n_cols // 2
    )
    assert middle_ns == pytest.approx(2.015, abs=1e-3)
    assert middle_ew == pytest.approx(2.002, abs=1e-3)


def test_pixel_size_refuses_a_grid_one_cell_across():
    """A ground spacing is a distance between two neighbouring cell centres,
    and a one-cell axis has no such pair.

    Design section 8 rejects only ``n_rows`` or ``n_cols`` below 1, so this
    grid constructs, and section 7.4 says the edge clamp leaves the function
    total for every in-grid cell, so it promises an answer here. It cannot have
    one. Before the guard the promise came out as ``ZeroDivisionError: float
    division by zero`` escaping from arithmetic -- both clamped neighbours
    collapse onto the same cell and the divisor is 0. Stage 3 reading a
    subsetted granule is how a grid this shape arrives.
    """
    one_cell = dataclasses.replace(ACMC, n_rows=1, n_cols=1)
    strip = dataclasses.replace(ACMC, n_rows=1)
    column = dataclasses.replace(ACMC, n_cols=1)

    for spec, cell in (
        (one_cell, (0, 0)),
        (strip, (0, 1250)),
        (column, (750, 0)),
    ):
        assert in_grid(spec, *cell) is True
        with pytest.raises(ValueError, match="one cell across"):
            pixel_size_km(spec, *cell)

    # Two cells across is enough, and the answer is the one-sided spacing.
    two_rows = dataclasses.replace(ACMC, n_rows=2)
    ns_km, ew_km = pixel_size_km(two_rows, 0, 1250)
    assert math.isfinite(ns_km) and ns_km > 0.0
    assert math.isfinite(ew_km) and ew_km > 0.0


def test_nothing_here_uses_the_sphere_for_the_projection():
    """Substitute a sphere for GRS80 and the answer must MOVE.

    Design section 4: stage 1 is a sphere, stage 2 is an ellipsoid, and mixing
    them is the single most likely bug in this module. A spherical earth of the
    same equatorial radius puts (40, -105) 5.6e-4 rad away, about 21 km on the
    ground -- ten pixels. The x component alone clears the design's 1e-4 rad
    threshold by only 8 percent, so the assertion is on the magnitude of the
    displacement, with the components checked separately below it.
    """
    sphere = dataclasses.replace(ACMC, r_pol_m=ACMC.r_eq_m)
    ellipsoid_scan = lonlat_to_scan(ACMC, 40.0, -105.0)
    sphere_scan = lonlat_to_scan(sphere, 40.0, -105.0)

    dx_rad = abs(sphere_scan[0] - ellipsoid_scan[0])
    dy_rad = abs(sphere_scan[1] - ellipsoid_scan[1])
    assert math.hypot(dx_rad, dy_rad) > 1e-4
    assert dx_rad == pytest.approx(1.0760e-4, abs=1e-8)
    assert dy_rad == pytest.approx(5.5085e-4, abs=1e-8)

    # It is really the flattening doing it: at the equator the two agree, and
    # the gap grows with latitude.
    assert lonlat_to_scan(sphere, 0.0, -105.0)[1] == pytest.approx(
        lonlat_to_scan(ACMC, 0.0, -105.0)[1], abs=1e-12
    )
    gaps = [
        abs(
            lonlat_to_scan(sphere, lat_deg, -137.0)[1]
            - lonlat_to_scan(ACMC, lat_deg, -137.0)[1]
        )
        for lat_deg in (10.0, 20.0, 30.0, 40.0)
    ]
    assert gaps == sorted(gaps)

    # The inverse has to be on the same ellipsoid, or the round trip would
    # quietly launder the sphere back into agreement.
    assert scan_to_lonlat(sphere, *sphere_scan)[0] == pytest.approx(
        40.0, abs=1e-9
    )
    assert scan_to_lonlat(ACMC, *sphere_scan)[0] != pytest.approx(
        40.0, abs=1e-6
    )


def _range_to_satellite_m(spec, lat_deg, lon_deg):
    """Straight-line distance from the satellite to a point on the ellipsoid.

    Deliberately built from geodetic first principles rather than from the
    module under test, so that a wrong root cannot validate itself.
    """
    phi = math.radians(lat_deg)
    lam = math.radians(lon_deg)
    e2 = 1.0 - spec.r_pol_m ** 2 / spec.r_eq_m ** 2
    n = spec.r_eq_m / math.sqrt(1.0 - e2 * math.sin(phi) ** 2)
    point = (
        n * math.cos(phi) * math.cos(lam),
        n * math.cos(phi) * math.sin(lam),
        n * (1.0 - e2) * math.sin(phi),
    )
    lam0 = math.radians(spec.lon_origin_deg)
    sat = (
        spec.sat_height_m * math.cos(lam0),
        spec.sat_height_m * math.sin(lam0),
        0.0,
    )
    return math.dist(point, sat)


def _ground_km(point_a, point_b):
    """Haversine separation on stage 1's sphere, kilometres.

    Haversine and not the law of cosines: these separations are a few
    kilometres on a 6371 km sphere, an angle of 3e-4 rad, where ``acos`` of a
    number that close to 1 throws away most of the mantissa. The comparison
    this feeds is at 1e-12.

    The same earth ``pixel_size_km`` measures on, so this checks how the two
    neighbour distances are COMBINED, not the projection underneath them --
    that is what tests 6 and 7 are for.
    """
    p1 = math.radians(point_a[0])
    p2 = math.radians(point_b[0])
    dp = p2 - p1
    dl = math.radians(point_b[1] - point_a[1])
    a = (
        math.sin(dp / 2.0) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(dl / 2.0) ** 2
    )
    return 2.0 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def _central_angle_deg(lat1_deg, lon1_deg, lat2_deg, lon2_deg):
    """Earth-central angle between two points, degrees."""
    p1 = math.radians(lat1_deg)
    p2 = math.radians(lat2_deg)
    dl = math.radians(lon2_deg - lon1_deg)
    cosine = math.sin(p1) * math.sin(p2) + math.cos(p1) * math.cos(p2) * math.cos(dl)
    return math.degrees(math.acos(max(-1.0, min(1.0, cosine))))
