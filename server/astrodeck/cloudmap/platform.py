"""Which GOES satellite sees a site best (stage 6b) -- longitude in, name out.

No I/O, no config, no network, no h5py, no numpy, no clock. One import, of a
private helper from stage 1, for exactly the reason :mod:`abi_grid` gives for
importing the same one: the longitude-wrapping rule is subtle enough that two
copies of it will eventually disagree.

WHY THIS MODULE EXISTS. ``cloudmap.platform`` was a hand-set string with a
default of ``G18``, and that default produces NOTHING east of about the
Rockies: GOES-West's CONUS sector does not reach there, ``lonlat_to_index``
returns an out-of-sector index, ``in_grid`` says no, and the operator sees an
empty cloud map with no explanation. ``test_cloudmap_service`` already names
the case -- "a site outside the sector, which is what an operator east of about
100 W gets by leaving platform at its G18 default". A default that is wrong for
most of the country and curable only by a knob nobody knows to turn is not a
default; it is a trap. The site's longitude already sits in config and answers
the question outright, so ask it.

THE CROSSOVER IS DERIVED, NOT GUESSED. The two sub-satellite longitudes below
are the ground truth and everything here is arithmetic on them.

Both satellites are geostationary, so both sub-satellite points sit ON THE
EQUATOR and the earth-central angle from a site to one of them is

    cos(gamma) = cos(lat) * cos(lon - sat_lon)

Zenith angle, pixel smear and parallax displacement are all monotonically
increasing in gamma -- they are three readings of the same geometry, not three
criteria that could disagree -- so "which satellite is better" is exactly
"which gamma is smaller". Setting the two equal, ``cos(lat)`` divides out of
both sides. THE CROSSOVER DOES NOT DEPEND ON LATITUDE. What is left is
``|lon - (-137.0)| = |lon - (-75.2)|`` measured the short way round, whose
solution is the meridian through the midpoint of the two sub-longitudes.
Verified numerically against :func:`geometry.satellite_look` by bisection at
latitudes 25, 32, 40, 47 and 55: -106.1 at every one of them, to six decimals.

THE CONFIG DOCSTRING SAID "ROUGHLY 100 W" AND THAT IS WRONG BY 6.1 DEGREES --
about 520 km at latitude 40, which is Denver to Salt Lake City. The honest
midpoint is 106.1 W and it is 106.1 W everywhere. The geometry design's own
worked example proves the old wording wrong on its own numbers: at 40N/105W it
tabulates GOES-19 at 55.5 deg zenith against GOES-18 at 56.7, so 105 W already
prefers GOES-EAST -- and a "roughly 100 W" rule would send it west. 106.1 W is
right; the config's "roughly 100 W" was folklore (the 100th meridian is a US
regional divide, not a satellite one) and this change corrects it.

THE BISECTOR IS A GREAT CIRCLE, SO THERE ARE TWO MERIDIANS AND NOT ONE. The
first draft of this module normalised the site longitude and then compared it
to -106.1 with a scalar ``>``, which is the obvious thing and is wrong for the
entire eastern hemisphere: at 100 E that test says GOES-East, while GOES-West
is 149.1 deg away against GOES-East's 175.2 and is the better of the two by 26
degrees. Swept against :func:`geometry.satellite_look` at half-degree steps,
the scalar form disagreed with the geometry at 214 of 721 longitudes. The
correct comparison normalises the DIFFERENCE; see
:func:`platform_for_longitude`.

WHAT THIS DOES NOT ANSWER. It picks the BETTER OF TWO satellites, which is not
the same as promising either one can see the site. A site off the CONUS sector
(Alaska, most of Canada) or off the visible disk entirely still gets a name
from here, and :func:`abi_grid.in_grid` and :func:`abi_grid.lonlat_to_scan`
still say no -- correctly, and now with the nearer satellite in the message.

HAWAII IS NOT ONE OF THEM, and this paragraph said it was until somebody ran
the numbers: Honolulu lands at row 1167 of 1500, column 220 of 2500, well
inside GOES-West's grid, because that sector is PACUS and reaches it. The
rejected-site list is Alaska and most of Canada, not Hawaii. Measured in
tests/test_cloudmap_uncovered_sites.py, which now pins it.

THE TWO REFUSALS DOWNSTREAM ARE DIFFERENT FACTS. A site on the visible disk
but off the scan raises ``granule.SiteOutsideSector`` and could be served by a
wider sector tomorrow. A site below the satellite's horizon raises
``granule.SiteBehindLimb`` and can never be served by any GOES product, because
the earth is in the way. Both carry SAFE_TO_ECHO sentences; the second one used
to be a bare ValueError, which reached the operator as the word "ValueError". Folding "not
visible" into this function would mean returning None for a question that
always has a best answer, and would put a coverage test in the one module that
has no grid to test against.
"""
from __future__ import annotations

# See the module docstring: imported rather than reimplemented so the wrapping
# rule cannot drift. Stage 2 (abi_grid) reaches for the same private name for
# the same reason, so this is the package's established practice, not a
# one-off reach into another module's internals.
from .geometry import _normalise_lon_deg

__all__ = [
    "GOES_WEST_SUB_LON_DEG",
    "GOES_EAST_SUB_LON_DEG",
    "CROSSOVER_LON_DEG",
    "platform_for_longitude",
    "resolve_platform",
]

#: GOES-18 is GOES-WEST, sub-longitude -137.0. From the granule's
#: ``goes_imager_projection:longitude_of_projection_origin`` (``granule.py``
#: reads it into ``GridSpec.lon_origin_deg``), recorded in abi-fixed-grid
#: design section 2.1 and used as the golden-vector satellite in section 9.4.
GOES_WEST_SUB_LON_DEG: float = -137.0

#: GOES-19 is GOES-EAST, sub-longitude -75.2. Same slot GOES-16 held -- GOES-16
#: is RETIRED (listing ``noaa-goes16`` returns zero keys for every ABI L2
#: product, measured 2026-08-21) and GOES-19 inherited both the station and the
#: sub-longitude, so the geometry design's "GOES-East/16, -75.2" row is still
#: numerically current under the new name.
GOES_EAST_SUB_LON_DEG: float = -75.2

#: The meridian on which the two satellites are exactly equally good, 106.1 W.
#:
#: DERIVED, never typed in: change either sub-longitude above -- NOAA does
#: drift and re-station these -- and the crossover follows without anyone
#: having to remember that it should. A hardcoded -106.1 would keep answering
#: the old question with a straight face.
#:
#: Its antimeridian, 73.9 E, is the OTHER half of the same bisector and is
#: deliberately NOT a second constant: :func:`platform_for_longitude` gets it
#: for free from wrapping the difference, which is the whole argument for
#: wrapping the difference.
CROSSOVER_LON_DEG: float = (
    GOES_WEST_SUB_LON_DEG + GOES_EAST_SUB_LON_DEG) / 2.0


def platform_for_longitude(lon_deg: float) -> str:
    """``"G18"`` or ``"G19"``, whichever sees ``lon_deg`` at the lower zenith
    angle. Total: every longitude has a better satellite.

    Accepts either longitude convention. 285.0 and -75.0 are the same place and
    must give the same answer.

    NORMALISE THE DIFFERENCE, NOT THE LONGITUDE. Wrapping ``lon - crossover``
    into ``(-180, +180]`` measures the offset from the bisector THE SHORT WAY
    ROUND, which is the only form that respects the bisector being a great
    circle and not a line on a flat map. Normalising the longitude first and
    then comparing scalars is the obvious version and is wrong for every site
    past the antimeridian of the crossover -- 214 of 721 half-degree samples,
    which is all of Asia and Australia. It also disposes of a floating-point
    question that the scalar form would need a tolerance constant for: every
    spelling of 106.1 W (-106.1, 253.9, -466.1, 973.9) gives a difference of
    exactly 0.0 here, where normalising the longitude alone gives three
    different values spread over 1.1e-13 degrees and straddling zero.

    TIES GO TO G18, BOTH OF THEM. At the crossover the difference is 0.0; at
    its antimeridian, 73.9 E, stage 1's boundary rule (exactly -180 becomes
    +180) makes it exactly +180.0. Neither tie carries any physics -- the two
    satellites are equally good there by every metric in this file -- so the
    tie-break is argued from blast radius instead: sending both to G18 means
    the longitudes where ``auto`` cannot choose are also the ones where it
    changes nothing against the shipped ``G18`` default. Excluding +180 from
    the eastern arc is what makes that ONE rule instead of two.
    """
    east_of_crossover = _normalise_lon_deg(float(lon_deg) - CROSSOVER_LON_DEG)
    return "G19" if 0.0 < east_of_crossover < 180.0 else "G18"


def resolve_platform(configured: str, lon_deg: float | None) -> str:
    """The platform to actually fetch: ``configured`` unless it says ``auto``.

    The seam between the config vocabulary and the geometry, kept here rather
    than at the call site so "what does auto mean" has ONE answer and it is
    tested in the module that owns the arithmetic.

    ``lon_deg is None`` means the site has never been set (``SiteConfig``
    reports ``is_default`` and the service already refuses to poll in that
    state). Falling back to G18 there is not a guess about where the rig is --
    it is the same answer the field shipped with, chosen so that an
    unconfigured rig's credit block and state payload keep naming a real
    satellite instead of the literal string "auto", which no bucket answers to
    (``source.bucket_for`` raises on it, by design).

    An explicit ``G18``/``G19`` ALWAYS wins. That is the whole point of keeping
    them in the Literal: an operator who has looked at both and picked one --
    for a sector edge, for a satellite in eclipse, for a NOAA outage on the
    nearer bird -- must not be overruled by arithmetic that only knows the
    great-circle distance.
    """
    if configured != "auto":
        return configured
    if lon_deg is None:
        return "G18"
    return platform_for_longitude(lon_deg)
