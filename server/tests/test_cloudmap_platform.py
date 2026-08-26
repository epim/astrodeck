"""Picking the GOES satellite from the site's longitude (stage 6b).

The bug this module closes is not an arithmetic one. ``cloudmap.platform``
defaulted to ``G18``, GOES-West's CONUS sector does not reach the eastern two
thirds of the country, and so the shipped default produced an all-no_data cloud
map for most operators with no error and no hint -- ``test_cloudmap_service``
names the case in the docstring of
``test_a_download_that_cannot_be_read_still_trims_the_cache``. So the tests
that matter here are the boundary ones: where the answer flips, that it flips
in BOTH directions, and that the same place spelled two ways gets one answer.

Nothing here mocks anything. The module is stdlib arithmetic, so every
assertion is against either a published sub-satellite longitude or stage 1's
own :func:`satellite_look`, which is the independent check.

``test_the_pick_agrees_with_stage_1_everywhere_on_earth`` is the one that earns
its keep and it is here because it caught a real defect during development: the
first draft compared a normalised longitude against -106.1 with a scalar ``>``,
which is correct for every site anyone would type in and wrong for 214 of 721
longitudes on the globe, because the bisector of two points on a sphere is a
great circle with TWO meridians and not a line with one. Every hand-written
case in this file passed against that draft.
"""
import pydantic
import pytest

from astrodeck.cloudmap.geometry import Site, satellite_look
from astrodeck.cloudmap.platform import (
    CROSSOVER_LON_DEG,
    GOES_EAST_SUB_LON_DEG,
    GOES_WEST_SUB_LON_DEG,
    platform_for_longitude,
    resolve_platform,
)
from astrodeck.cloudmap.source import bucket_for
from astrodeck.config import CloudmapConfig

SUB_LON = {"G18": GOES_WEST_SUB_LON_DEG, "G19": GOES_EAST_SUB_LON_DEG}

#: A zenith-angle difference below this is the two satellites tying. 1e-9 deg
#: is 0.11 mm of ground; the ties in this geometry are exact to the last digit
#: stage 1 carries, so this only has to be above float noise.
_TIE_DEG = 1e-9


def _zenith_deg(lat_deg, lon_deg, platform):
    """Zenith angle to a platform, measured by stage 1 rather than by us.

    Deliberately routed through :func:`satellite_look` and not through a local
    ``acos(cos(lat) * cos(dlon))``: a test that re-implements the thing it is
    checking agrees with itself and with nothing else. It is also how the
    great-circle bug was found -- the re-implementation would have had the same
    flat-map mistake in it.
    """
    look = satellite_look(Site(lat_deg, lon_deg, 0.0), SUB_LON[platform])
    return 90.0 - look.alt_deg


def _better_by_geometry(lat_deg, lon_deg):
    """Which satellite stage 1 says is nearer, ties resolved to G18 to match
    the module's documented tie-break."""
    west = _zenith_deg(lat_deg, lon_deg, "G18")
    east = _zenith_deg(lat_deg, lon_deg, "G19")
    return "G19" if east < west - _TIE_DEG else "G18"


# ================================================== 1. the two ends


def test_a_site_well_west_gets_goes_west():
    """The observatory case, and the one the old default happened to serve.

    122.1 W is 46.1 degrees off GOES-18 and 65.0 off GOES-19 -- the difference
    between a 2 km pixel smearing to 2.9 km and to 4.7, per abi-fixed-grid
    design 2.1. The zenith assertion is here so that a future edit that flips
    the answer has to argue with the physics and not just with a string.
    """
    assert platform_for_longitude(-122.1) == "G18"
    assert _zenith_deg(37.4, -122.1, "G18") < _zenith_deg(37.4, -122.1, "G19")


def test_a_site_well_east_gets_goes_east():
    """84.4 W is Atlanta, and it is not merely better served by GOES-19 -- it
    is 62 degrees of longitude from GOES-18's sub-point, which is where
    GOES-West's CONUS sector ran out and the old default started returning an
    empty map."""
    assert platform_for_longitude(-84.4) == "G19"
    assert _zenith_deg(33.7, -84.4, "G19") < _zenith_deg(33.7, -84.4, "G18")


# ================================================== 2. the crossover


def test_the_crossover_flips_in_both_directions():
    """One-sided boundary tests pass against a function that has no boundary at
    all -- ``return "G18"`` satisfies every west-of case ever written. Both
    directions, one degree either side, is the cheapest thing that cannot."""
    assert platform_for_longitude(CROSSOVER_LON_DEG - 1.0) == "G18"
    assert platform_for_longitude(CROSSOVER_LON_DEG + 1.0) == "G19"


def test_exactly_the_crossover_is_goes_west():
    """PINNED, and pinned as a decision rather than as a measurement.

    At 106.1 W the two satellites are equally good to the last digit the
    geometry carries, so nothing about the sky picks a side. The tie-break goes
    to G18 because that keeps ``auto`` a no-op at the one longitude where it
    cannot do better than the value the field shipped with. If a later change
    wants G19 here, that is fine -- but it must come and edit this test, which
    is the whole point of writing it down.
    """
    assert platform_for_longitude(CROSSOVER_LON_DEG) == "G18"
    assert platform_for_longitude(-106.1) == "G18"
    west = _zenith_deg(40.0, CROSSOVER_LON_DEG, "G18")
    east = _zenith_deg(40.0, CROSSOVER_LON_DEG, "G19")
    assert abs(west - east) < _TIE_DEG, (
        "the crossover is supposed to be the tie; stage 1 puts these "
        + repr(abs(west - east)) + " degrees apart")


def test_the_crossover_is_the_midpoint_and_not_the_folklore_100_w():
    """The config comment used to say the answer flips "east of roughly 100 W".

    It does not. The midpoint of -137.0 and -75.2 is -106.1, and the 6.1 degree
    error is not academic: 105 W -- the geometry design's own worked example --
    falls on the GOES-EAST side and the "100 W" rule sends it west. The design
    tabulates 55.5 degrees against 56.7 there, so the folklore rule is
    contradicted by a number already in the tree.
    """
    assert CROSSOVER_LON_DEG == (
        GOES_WEST_SUB_LON_DEG + GOES_EAST_SUB_LON_DEG) / 2.0
    assert CROSSOVER_LON_DEG == pytest.approx(-106.1)
    assert platform_for_longitude(-105.0) == "G19"
    assert _zenith_deg(40.0, -105.0, "G19") < _zenith_deg(40.0, -105.0, "G18")
    assert platform_for_longitude(-101.0) == "G19", (
        "a site between 100 W and 106.1 W is where the old comment and the "
        "geometry disagree, so it is the one longitude band worth pinning")


def test_the_crossover_does_not_depend_on_latitude():
    """cos(lat) divides out of ``cos(gamma) = cos(lat) cos(dlon)``, so the
    crossover is a meridian and not a curve. Checked by bisecting stage 1's own
    :func:`satellite_look`."""
    for lat in (25.0, 32.0, 40.0, 47.0, 55.0):
        lo, hi = GOES_WEST_SUB_LON_DEG, GOES_EAST_SUB_LON_DEG
        for _ in range(80):
            mid = 0.5 * (lo + hi)
            if _zenith_deg(lat, mid, "G18") < _zenith_deg(lat, mid, "G19"):
                lo = mid
            else:
                hi = mid
        measured = 0.5 * (lo + hi)
        assert measured == pytest.approx(CROSSOVER_LON_DEG, abs=1e-6), (
            "at latitude " + repr(lat) + " the satellites actually swap at "
            + repr(measured) + ", not at " + repr(CROSSOVER_LON_DEG))


# ================================================== 3. the bisector is a
#                                                       great circle


def test_the_bisector_is_a_great_circle_not_a_meridian():
    """100 E is EAST of 106.1 W on a flat map and NEARER GOES-WEST on a globe.

    Both satellites are far behind the horizon there, so no operator is harmed
    by the answer -- but the function promises "whichever sees it at the lower
    zenith angle" and a promise that holds only in one hemisphere is the
    defect class this codebase keeps finding. GOES-18 is 149.1 degrees away and
    GOES-19 is 175.2: a 26 degree margin, not a rounding question.
    """
    assert platform_for_longitude(100.0) == "G18"
    assert _zenith_deg(0.0, 100.0, "G18") < _zenith_deg(0.0, 100.0, "G19")


def test_the_antipodal_tie_breaks_the_same_way_as_the_near_one():
    """73.9 E is the other half of the same bisector: 149.1 degrees from both.

    Two tie points and one tie rule. Left to a scalar comparison this longitude
    would not be a boundary at all, so it is worth an assertion that it is one.
    """
    antipode = CROSSOVER_LON_DEG + 180.0
    assert antipode == pytest.approx(73.9)
    west = _zenith_deg(0.0, antipode, "G18")
    east = _zenith_deg(0.0, antipode, "G19")
    assert abs(west - east) < _TIE_DEG, (
        "73.9 E is supposed to be the antipodal tie; stage 1 puts these "
        + repr(abs(west - east)) + " degrees apart")
    assert platform_for_longitude(antipode) == "G18"


def test_the_pick_agrees_with_stage_1_everywhere_on_earth():
    """The sweep, and the test that caught the great-circle bug.

    Half-degree steps over the whole globe at three latitudes, each answer
    checked against :func:`satellite_look` rather than against a rule this file
    restates. Every hand-written case above passed against a draft that got 214
    of these 721 longitudes wrong, which is the argument for having it: the
    cases a person thinks to write are the cases they already had in mind when
    they wrote the code.
    """
    wrong = []
    for lat in (-20.0, 20.0, 45.0):
        lon = -180.0
        while lon <= 180.0:
            got = platform_for_longitude(lon)
            want = _better_by_geometry(lat, lon)
            if got != want:
                wrong.append((lat, round(lon, 1), got, want))
            lon += 0.5
    assert not wrong, (
        str(len(wrong)) + " longitudes disagree with satellite_look, first "
        "ten (lat, lon, picked, nearer): " + repr(wrong[:10]))


# ================================================== 4. one place, two spellings


def test_the_same_place_spelled_0_to_360_gets_the_same_satellite():
    """285.0 and -75.0 are one place. So are 223.0 and -137.0.

    A longitude reaches this function from config, from a plan, from a URL
    query and eventually from somebody's planetarium export, and the 0..360
    convention is common enough in that last population to be a matter of when
    rather than whether. Unwrapped, 285.0 and 223.0 both sit more than 180
    degrees east of the crossover and BOTH come back G18, so one of them is
    wrong and the two that should differ do not.
    """
    assert platform_for_longitude(285.0) == platform_for_longitude(-75.0)
    assert platform_for_longitude(285.0) == "G19"
    assert platform_for_longitude(223.0) == platform_for_longitude(-137.0)
    assert platform_for_longitude(223.0) == "G18"


def test_every_spelling_of_the_crossover_lands_exactly_on_the_tie():
    """The tie has to be reachable, not merely defined.

    Wrapping the DIFFERENCE is what makes this true without a tolerance
    constant: -106.1, 253.9, -466.1 and 973.9 all give an offset of exactly
    0.0. Wrapping the longitude instead gives -106.1, -106.10000000000002 and
    -106.09999999999991 -- values spread over 1.1e-13 degrees and straddling
    the boundary, so one place would get two satellites at the one longitude
    this file documents.
    """
    for spelling in (-106.1, 253.9, -466.1, 973.9, 613.9, -826.1):
        assert platform_for_longitude(spelling) == "G18", (
            repr(spelling) + " is 106.1 W spelled differently and it did not "
            "land on the tie")


# ================================================== 5. auto, and the override


def test_auto_is_the_config_default_and_the_manual_values_still_validate():
    """The default is the fix; the Literal is the escape hatch.

    ``G16`` stays refused. It is not a spelling mistake -- GOES-16 is a real
    satellite that was in this slot until it was retired, so a config carrying
    it must fail loudly rather than resolve to something plausible.
    """
    assert CloudmapConfig().platform == "auto"
    assert CloudmapConfig(platform="G18").platform == "G18"
    assert CloudmapConfig(platform="G19").platform == "G19"
    with pytest.raises(pydantic.ValidationError):
        CloudmapConfig(platform="G16")
    with pytest.raises(pydantic.ValidationError):
        CloudmapConfig(platform="AUTO")


def test_an_explicit_platform_is_never_overruled_by_the_geometry():
    """A manual G18 in Atlanta is a strange choice and it is the operator's.

    Sector edges, an eclipse season outage, a NOAA product gap on the nearer
    bird: the reasons to override are real and none of them are visible from a
    longitude. If ``auto`` could win this argument the Literal would be
    decoration.
    """
    assert resolve_platform("G18", -84.4) == "G18"
    assert resolve_platform("G19", -122.1) == "G19"


def test_auto_resolves_to_a_name_a_bucket_answers_to():
    """Whatever comes out must be fetchable, including with no site set.

    ``bucket_for`` is the real consumer and it raises on anything it does not
    know, so routing the result through it is what stops ``auto`` from leaking
    into a URL. The unset-site case is the one that would: it is the only path
    with no longitude to reason from.
    """
    assert bucket_for(resolve_platform("auto", -122.1)) == "noaa-goes18"
    assert bucket_for(resolve_platform("auto", -84.4)) == "noaa-goes19"
    assert resolve_platform("auto", None) == "G18"
    assert bucket_for(resolve_platform("auto", None)) == "noaa-goes18"
