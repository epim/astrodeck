"""A site the satellites cannot serve must say so, permanently and in words.

NEVER RUN END TO END until now, and it was flagged as mattering: a cloud map
that answers a site it cannot see is worse than no cloud map. Measured here for
nine real places rather than argued about.

The refusals fall in two groups and only one of them was speaking English.

    site          bird   sat alt   grid index          exception
    San Jose CA   G18     43.8     row  452 col 1884   -- (covered)
    Honolulu      G18     55.6     row 1167 col  220   -- (covered: PACUS)
    Anchorage     G18     19.8     row -232 col  939   SiteOutsideSector
    Sydney        G18      6.4     row 3844 col -951   SiteOutsideSector
    London        G19      0.5     None                ValueError  <-- bare
    Tokyo         G18     -3.3     None                ValueError  <-- bare

``SiteOutsideSector`` carries a constant, reviewed, SAFE_TO_ECHO sentence that
reaches the operator through ``service._safe_error``. The behind-the-limb case
raised a BARE ValueError, so the same operator got the class name "ValueError"
inside "no cloud granule has been read yet; the last attempt ended in ...".

The least useful sentence in the product, on its most permanent condition: a
site in London will never be covered by either GOES satellite, and "not yet"
invites the operator to wait for something that cannot arrive.

Two corrections to what was believed before these numbers existed:

* London is NOT invisible to both satellites. GOES-East is 0.54 degrees above
  its horizon -- grazing, but up. Sydney (6.4) and Reykjavik (6.5) likewise.
  Only Tokyo (-3.3) and Cape Town (-11.5) genuinely cannot see either bird.
  Visibility was never the operative test; sector membership is.
* Hawaii IS covered. ``platform.py``'s docstring named it among the sites
  ``lonlat_to_index`` "still says no" to; it says row 1167 of 1500, col 220 of
  2500, comfortably inside. GOES-West's CONUS sector is PACUS and reaches it.
"""
import pytest

from astrodeck.cloudmap.abi_grid import GridSpec, lonlat_to_index
from astrodeck.cloudmap.geometry import Site, satellite_look
from astrodeck.cloudmap.granule import SiteBehindLimb, SiteOutsideSector
from astrodeck.cloudmap.platform import (
    GOES_EAST_SUB_LON_DEG,
    GOES_WEST_SUB_LON_DEG,
    platform_for_longitude,
)
from astrodeck.cloudmap.service import _safe_error

SUB_LON = {"G18": GOES_WEST_SUB_LON_DEG, "G19": GOES_EAST_SUB_LON_DEG}

#: The GOES-West CONUS grid, byte-for-byte the stage 5 / service suite's.
WEST_CONUS = GridSpec(
    sat_height_m=42164160.0, r_eq_m=6378137.0, r_pol_m=6356752.31414,
    lon_origin_deg=-137.0, sweep_axis="x",
    x_offset_rad=-0.06997200101613998, x_scale_rad=5.6000000768108293e-05,
    n_cols=2500, y_offset_rad=0.12821200489997864,
    y_scale_rad=-5.6000000768108293e-05, n_rows=1500,
)


def test_the_bird_is_visible_or_not_as_measured():
    """Pins the geometry, so a regression in bird-picking shows up here.

    These are the numbers that corrected the record: London's satellite is UP,
    barely, and the sites that cannot see one at all are Tokyo and Cape Town.
    """
    def alt(lat, lon):
        bird = platform_for_longitude(lon)
        return satellite_look(Site(lat, lon, 0.0), SUB_LON[bird]).alt_deg

    assert alt(37.35, -121.80) == pytest.approx(43.8, abs=0.3), "San Jose"
    assert alt(51.5, -0.13) == pytest.approx(0.54, abs=0.3), (
        "London's satellite grazes its horizon -- it is NOT invisible")
    assert alt(-33.87, 151.21) == pytest.approx(6.4, abs=0.3), "Sydney"
    assert alt(35.68, 139.65) < 0.0, "Tokyo cannot see either bird"
    assert alt(-33.92, 18.42) < 0.0, "Cape Town cannot see either bird"


def test_hawaii_is_inside_the_west_sector():
    """The docstring said it was rejected. It is not, and the fix was the
    sentence, not the code: GOES-West's CONUS sector is PACUS."""
    idx = lonlat_to_index(WEST_CONUS, 21.31, -157.86)
    assert idx is not None, "Honolulu is on the visible disk"
    row, col = idx
    assert 0 <= row < WEST_CONUS.n_rows and 0 <= col < WEST_CONUS.n_cols, (
        f"Honolulu at row {row} col {col} is inside the grid, not outside it")


@pytest.mark.parametrize("name,lat,lon", [
    ("London", 51.5, -0.13),
    ("Tokyo", 35.68, 139.65),
])
def test_a_site_behind_the_limb_is_off_the_grid(name, lat, lon):
    """The precondition for the refusal under test: no index at all."""
    assert lonlat_to_index(WEST_CONUS, lat, lon) is None, name


def test_behind_the_limb_says_so_in_words_the_operator_can_act_on():
    """The defect: a bare ValueError reached the operator as "ValueError".

    ``_safe_error`` echoes a class only when it declares SAFE_TO_ECHO, which is
    right -- an arbitrary exception's text can carry a coordinate or a URL. So
    the fix is a NAMED exception with a constant, reviewed sentence, exactly as
    SiteOutsideSector already is for the sites that are on the disk but off the
    scan.
    """
    exc = SiteBehindLimb(sat_lon_deg=-137.0)
    assert getattr(exc, "SAFE_TO_ECHO", False) is True, (
        "without this the operator gets the class name and nothing else")

    echoed = _safe_error(exc)
    assert echoed != "SiteBehindLimb", "the class name is not a sentence"
    assert echoed != "ValueError", "the bug, verbatim"
    low = echoed.lower()
    # A SET, NOT ONE WORD. The first draft of this asserted the literal token
    # "never" and went red against "will EVER ... not now and not later",
    # which states permanence perfectly well -- a test grading the spelling of
    # a sentence rather than its meaning. What has to hold is that the
    # operator is told to stop waiting, and there is more than one way to say
    # it; what must NOT appear is the language of a transient.
    assert any(w in low for w in ("never", "ever", "not later", "permanent")), (
        "the condition is PERMANENT and the sentence has to say so -- "
        "'no granule yet' invites an operator to wait for what cannot come")
    assert "yet" not in low, (
        "'yet' is the word that makes a permanent refusal read as an outage")
    assert "horizon" in low or "limb" in low or "below" in low, (
        "it must name WHY, or the operator cannot tell it from an outage")


def test_the_two_refusals_do_not_read_the_same():
    """Off-the-scan and below-the-horizon are different problems.

    Anchorage can be served by pointing a wider sector at it. London cannot be
    served by any GOES product at all. An operator who cannot tell those apart
    files the wrong bug -- or buys the wrong fix.
    """
    limb = _safe_error(SiteBehindLimb(sat_lon_deg=-137.0))
    sector = _safe_error(SiteOutsideSector(
        centre_row=-232, centre_col=939, n_rows=1500, n_cols=2500))
    assert limb != sector
    assert "sector" in sector.lower()
