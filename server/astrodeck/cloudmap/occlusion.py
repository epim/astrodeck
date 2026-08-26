"""Is there cloud in the beam (stage 4) -- the ray, the surface, and the mask.

No network, no file, no clock, no config, no h5py. Two stage-3
:class:`~astrodeck.cloudmap.granule.GranuleWindow`s in, one :class:`Occlusion`
out: stage 3 fetches, stage 4 decides. Pure ``math`` plus whatever numpy does
inside ``value_at``.

THE CROSSING LOCATES THE CLOUD; THE MASK DECIDES WHETHER CLOUD IS THERE. They
are two products because they answer two different questions, and a prototype
run against real granules found the trap that shapes this module: EVERY
direction reports a crossing, including directions the mask calls clear at
p=0.002. ACHA returns a cloud-top height wherever its retrieval converged and
that footprint does not agree with the 2 km mask, so "the ray met the surface"
on its own means nothing. The answer is the mask probability AT the crossing's
pierce point -- which at 15 degrees altitude is up to 33 km from the site, and
that displacement is the entire reason this project exists. Straight up and 25
degrees toward the west differ by 0.124 against 0.576 on the same granule pair,
which no scalar forecast can express.

A MISSING HEIGHT IS NEVER CLEAR. If the ladder finds no crossing the answer is
the mask at :data:`FALLBACK_HEIGHT_KM`, marked ``basis="mask_only"``, because
thin cirrus that ACHA cannot height is exactly the cloud that ruins a sub while
the mask still sees it.

HOW OFTEN THAT HAPPENS IS A PROPERTY OF THE SKY, NOT OF THIS MODULE. Design
section 2.1 measures 1.7 percent of 1740 rays on one granule pair and concludes
that use will never exercise the path; the measurement is of one solidly
overcast sky and the conclusion does not follow from it. Re-measured over 19350
rays -- the full 43 x 90 dome at five sites against one real GOES-18
ACMC/ACHAC pair -- the fall-through runs from 0.0 percent of the dome to 97.4
percent by site, 40.3 percent overall, and it is the MAJORITY path at two of the
five. One marine layer settles it: every cloud top below the ladder's first rung
puts every ray in the dome down this branch. Test it hard anyway, but on the
ground that survives the correction -- this is the branch that can report clear
sky through thin cirrus, and that is what costs frames, however often it runs.

Two grids, never one. The mask is 2 km and the height is 10 km; they share a
projection and not a sampling, so each lookup goes through its OWN window's
``spec``. Reusing an index between them is a five-times error that returns a
real measurement of the wrong place, and it looks entirely plausible.

FOUR DEVIATIONS FROM DESIGN, each argued at the code that makes it:

- The ladder starts above the OBSERVER, not at 0.2 km MSL. Design section 4.1's
  ladder crashes for any site above 200 m; see :func:`_ladder_km`.
- The fallback height is lifted above the observer for the same reason; see
  :func:`_fallback_height_km`.
- :func:`beam_footprint_km` is called as stage 1 declares it and with the site's
  own elevation; see :func:`_answer`.
- A mask cell holding the fill is ``no_data``, not a probability; see
  :func:`_answer`.

``reason`` NAMES NO COORDINATE. It is written for a human and it lands in the
same journal that stage 3's ``read_window`` deliberately keeps latitudes out of;
a pierce point is within 30 km of the observatory. Callers that need the
position have :attr:`Occlusion.pierce_lat_deg` and its longitude, as structured
fields they must choose to serialise.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

# ``_great_circle_km`` is private to stage 2 and imported anyway, deliberately,
# for the reason stage 2 gives when it imports stage 1's ``_normalise_lon_deg``:
# it is haversine on stage 1's sphere, which is exactly what a ground distance
# is, and a second copy of it here would eventually disagree with the one
# ``pixel_size_km`` measures cells with. Promoting it to a public name means
# editing stage 2, which stage 4 is not allowed to touch.
from functools import lru_cache

from .abi_grid import _great_circle_km, in_grid, lonlat_to_index, pixel_size_km
from .geometry import (
    apparent_point,
    GeoPoint,
    Site,
    beam_footprint_km,
    pierce_point,
    slant_to_layer_km,
)

# STAGE 3'S DATACLASS UNDER ``TYPE_CHECKING``, which is the whole of what keeps
# the first line of this docstring true. ``GranuleWindow`` appears below only in
# annotations and ``from __future__ import annotations`` leaves those as strings,
# but an ordinary import of it drags in ``granule``'s guarded ``import h5py``:
# measured in a fresh interpreter, importing stage 4 cost 0.233 s and 251
# modules against 0.011 s and 91 for stages 1 and 2 alone -- essentially all of
# it h5py and the numpy under it, for a module that never opens a file. Guarded,
# it is 0.017 s and 96 modules with neither h5py nor numpy loaded. Design section
# 8's "No h5py import" was a claim nothing kept, and a named test now keeps it.
if TYPE_CHECKING:
    from .granule import GranuleWindow

__all__ = [
    "FALLBACK_HEIGHT_KM",
    "LADDER_START_KM",
    "LADDER_STEP_KM",
    "LADDER_TOP_KM",
    "MIN_USABLE_ALT_DEG",
    "MASK_PROBABILITY",
    "MASK_QUALITY",
    "CLOUD_TOP",
    "Occlusion",
    "occlusion_at",
    "dome",
]

#: Where the mask is read when the ladder found no cloud-top surface at all.
FALLBACK_HEIGHT_KM: float = 3.0
LADDER_START_KM: float = 0.2
LADDER_STEP_KM: float = 0.2
LADDER_TOP_KM: float = 15.0
#: Below this the pierce point runs past 100 km downrange and one cloud-top
#: surface stops describing the sky between here and there.
MIN_USABLE_ALT_DEG: float = 5.0

#: The variables each window must carry. Named rather than inlined so the
#: KeyError can quote the same string the granule uses.
MASK_PROBABILITY = "Cloud_Probabilities"
MASK_QUALITY = "DQF"
CLOUD_TOP = "HT"


@dataclass(frozen=True)
class Occlusion:
    """What is in one look direction, and how much of it is actually known.

    ``basis`` is ``"crossing"`` (the ray met the cloud-top surface and the mask
    was read there), ``"mask_only"`` (THE LADDER FOUND NO CROSSING, so the mask
    was read at :data:`FALLBACK_HEIGHT_KM`) or ``"no_data"`` (nothing could be
    read at all). ``probability`` is ``None`` if and only if ``basis`` is
    ``"no_data"``.

    ``mask_only`` is not "no surface anywhere on the ladder", which is what this
    line used to say and what :attr:`reason` used to assert in every case.
    :func:`_why_no_crossing` sets out the four walks that end here; only one of
    them is a missing retrieval, and ``reason`` names which one fired.

    ``beam_m`` and ``cell_km`` exist to be shown TOGETHER. At 30 degrees the
    beam is a couple of hundred millimetres across where it meets a 4 km deck
    and the mask cell is 2.9 x 2.2 km -- over a hundred times the area. A UI
    that prints the probability without that ratio nearby is claiming a
    determination the data cannot support.
    """

    probability: float | None
    basis: str
    crossing_km: float | None
    pierce_lat_deg: float | None
    pierce_lon_deg: float | None
    downrange_km: float | None
    beam_m: float | None
    cell_km: tuple[float, float] | None
    quality: int | None
    reason: str


def _require_altitude(alt_deg: float, name: str) -> None:
    """Stage 1's domain, checked HERE so it is checked before the floor.

    An altitude of 0 is a caller bug and 4 degrees is a direction a mount can
    point; both are below :data:`MIN_USABLE_ALT_DEG`, so a build that consulted
    the floor first would answer the bug with a serene "no data". The negated
    form catches NaN, which passes every comparison written the other way.
    """
    if not alt_deg > 0.0 or alt_deg > 90.0:
        raise ValueError(
            name
            + " must satisfy 0 < alt <= 90, got "
            + repr(alt_deg)
            + " degrees"
        )


def _require_step(step_deg: float, name: str) -> None:
    """A dome step has to be a real slice of sky, not zero and not a quadrant."""
    if not step_deg > 0.0 or step_deg > 45.0:
        raise ValueError(
            name
            + " must satisfy 0 < step <= 45, got "
            + repr(step_deg)
            + " degrees"
        )


def _require_variable(window: GranuleWindow, name: str, what: str) -> None:
    """Raise at the CALL for a variable this window never read.

    ``value_at`` would raise the same KeyError, but only on the paths that
    reach a lookup: a window with no probabilities in it would answer every
    direction below the altitude floor with a plain ``no_data``, and the
    missing variable would surface hours later somewhere else.
    """
    if name not in window.data:
        raise KeyError(
            "the "
            + what
            + " window carries no "
            + repr(name)
            + "; it has "
            + ", ".join(sorted(window.data) or ["nothing"])
        )


def _ladder_km(site: Site) -> list[float]:
    """The heights the ray is sampled at, all of them ABOVE the observer.

    DEVIATION FROM DESIGN SECTION 4.1, and not a small one. The design walks
    ``[0.2, 0.4, ... 15.0]`` for every site, but stage 1 refuses a layer at or
    below the observer -- :func:`pierce_point` raises ``ValueError``, by
    design, rather than inventing a pierce point underground. So the specified
    ladder does not merely misreport a site above 200 m, it raises on the
    FIRST RUNG and the module never answers at all. Boulder, the design's own
    first golden point, is 1.624 km up; so is most of the observing in the
    western United States. Rungs at or below ``site.elev_km`` are dropped here
    instead: that is sky the beam is already above, and there is nothing in it
    to cross.

    The top is tested against the RUNG rather than against a count derived by
    dividing, so no rounding of ``span / step`` can end the ladder a step early
    and drop 200 m of sky off the top on every ray. Each rung is computed from
    its index rather than accumulated, because the equality in the crossing test
    can only fire on a rung that lands on its own decimal: accumulating 0.2
    gives 3.9999999999999996 for the 4.0 km rung and 14.999999999999979 for the
    last one, so an accumulated ladder silently loses its top rung to the test
    above. 9.0 is NOT the example to reach for -- accumulation happens to keep
    that one exact, and an earlier draft of this line cited it and proved
    nothing.

    THE CAP IS WHAT MAKES THE GUARD ABOVE IT FAIL LOUDLY. ``while True`` with
    the rung recomputed from the index does not terminate for a step that never
    climbs, so deleting that guard does not turn a test red -- it hangs the run
    while the list eats memory, and the two tests written to pin the guard are
    then the two that lock up. A job that hangs reads as infrastructure, not as
    a defect here. The cap is the exact rung count plus slack, so no ladder that
    climbs can reach it: the division rounds DOWN, 73 for the shipped constants
    against the 75 rungs actually walked, which is what the slack is for. With
    the guard deleted a zero step now dies on the division that computes the cap
    and a negative one on the cap's first pass, the suite going red in under two
    seconds where both cases used to run until the process was killed.

    THE CAP CANNOT FIRE WHILE THE GUARD HOLDS. It is an assertion, not a branch:
    no consistent set of these constants reaches it, so no test can, and a
    mutation deleting it alone leaves the suite green -- which is the right
    answer. Delete it AND the guard together and the negative-step case hangs
    again, and that measurement is what says it earns its place.
    """
    step_km = LADDER_STEP_KM
    if not step_km > 0.0:
        raise ValueError(
            "LADDER_STEP_KM must be positive, got "
            + repr(step_km)
            + " km; a zero or negative step is a ladder that never climbs"
        )
    max_rungs = int((LADDER_TOP_KM - LADDER_START_KM) / step_km) + 8
    rungs = []
    i = 0
    while True:
        if i > max_rungs:
            raise ValueError(
                "the ladder passed "
                + repr(max_rungs)
                + " rungs without reaching "
                + repr(LADDER_TOP_KM)
                + " km: LADDER_STEP_KM is "
                + repr(step_km)
                + ", which does not climb"
            )
        height_km = LADDER_START_KM + i * step_km
        if height_km > LADDER_TOP_KM:
            return rungs
        if height_km > site.elev_km:
            rungs.append(height_km)
        i += 1


def _fallback_height_km(site: Site, rungs: list[float]) -> float | None:
    """Where the mask is read when no crossing was found, or ``None``.

    DEVIATION FROM DESIGN SECTION 4.1, for the reason :func:`_ladder_km` gives:
    :data:`FALLBACK_HEIGHT_KM` is 3.0 km MSL and a 4.2 km observatory is a real
    place, so the specified fallback raises there instead of answering. The
    first rung above the observer is used in that case -- ``None`` only if the
    site is above the whole ladder, which is a balloon and not an observatory.
    """
    if FALLBACK_HEIGHT_KM > site.elev_km:
        return FALLBACK_HEIGHT_KM
    return rungs[0] if rungs else None


def _why_no_crossing(last_gap_km: float | None, lost_in_a_hole: bool) -> str:
    """Which of the four walks ended at the mask, as a clause a human reads.

    THIS EXISTS BECAUSE ONE SENTENCE FOR ALL FOUR WAS WRONG MOST OF THE TIME IT
    MATTERED. Every fall-through used to say "no cloud-top retrieval along the
    beam", and on real granules that is contradicted by the data it was computed
    from: over 19350 rays at five sites on one GOES-18 ACMC/ACHAC pair, 7798
    fell through and 1390 of those -- 17.8 percent -- had read a finite cloud
    top on the ladder. One 5 degree ray walked 19 rungs over a retrieved 4829 m
    deck and reported no retrieval. ``reason`` is the only field written for a
    human, so it was sending whoever read it to look for a hole in the satellite
    product when the beam was simply above the cloud.

    ``last_gap_km`` is the last ``top - rung`` the walk retrieved, ``None`` if it
    never retrieved one; positive means the surface was still above the beam.

    THE HOLE CASE IS NOT A CURIOSITY. ACHA fails to converge at cloud EDGES,
    which is exactly where a ray crosses the surface, so the ``prev_gap = None``
    reset preferentially eats crossings there -- 339 of those 7798, and 45 of
    3543 at one site where a further 71 were the surface standing above the
    whole ladder instead. The three want three different things from whoever
    reads them: the hole says suspect the retrieval exactly where it matters,
    the surface above the ladder says the LADDER ran out rather than the
    product, and the beam over the deck says nothing is wrong at all. The one
    sentence they used to share said the product had no heights here, which is
    the single thing none of them means.
    """
    if last_gap_km is None:
        return "no cloud-top retrieval along the beam"
    if lost_in_a_hole:
        return (
            "the cloud-top retrieval breaks off where the beam meets the "
            "surface, leaving no rung to catch the crossing"
        )
    if last_gap_km > 0.0:
        return (
            "the cloud-top surface is still above the beam at the highest rung "
            "that retrieved one"
        )
    return "the beam is above the cloud top rather than through it"


def _cells(count: int, noun: str) -> str:
    """``1 row``, ``5 rows``. The shortfall is read by a person."""
    return repr(count) + " " + noun + ("" if count == 1 else "s")


def _no_data(reason: str) -> Occlusion:
    """Nothing known but the sentence saying why -- never a plausible number."""
    return Occlusion(
        probability=None,
        basis="no_data",
        crossing_km=None,
        pierce_lat_deg=None,
        pierce_lon_deg=None,
        downrange_km=None,
        beam_m=None,
        cell_km=None,
        quality=None,
        reason=reason,
    )


#: Grid the parallax displacement is cached on, in degrees. 0.1 deg is about
#: 11 km, so a whole dome's rays share a handful of entries per rung.
_PARALLAX_CACHE_DEG = 0.1


@lru_cache(maxsize=65536)
def _parallax_shift(lat_key: float, lon_key: float, height_km: float,
                    sat_lon_deg: float) -> tuple[float, float]:
    """The parallax DISPLACEMENT, in degrees, cached on a coarse grid.

    THE DISPLACEMENT, NOT THE POINT, and that is what makes the cache work.
    The corrected position is unique to every ray; the displacement between it
    and the true position is a smooth function of where you are and varies by
    almost nothing across one dome. Measured before adopting: reusing the shift
    from a key up to 11 km away costs at most 5 m at a 2 km deck, 13 m at 5 km,
    26 m at 10 km and 38 m at 15 km -- against a mask cell 2900 m across. The
    largest error is 1.3 percent of a cell.

    WHY THIS IS NOT THE APPROXIMATION geometry.deparallax REFUSES. That one
    rejected h*tan(zenith) because the flat-earth FORM is wrong in a way that
    grows without bound with zenith angle. This computes the exact ray walk;
    it just declines to recompute it for a point 300 m away. Exactness of the
    formula and granularity of its evaluation are different questions.

    Measured cost: correcting every point exactly ran a worst-case 3870-ray
    dome to 294,120 distinct calls and 4.2 s, on a budget of 3.0 s that exists
    so stage 6 can call this per frame. Zero cache hits, because every pierce
    point in a dome is unique. Spending four seconds to refine a metre inside a
    three-kilometre cell is the wrong trade in both directions.
    """
    corrected = apparent_point(lat_key, lon_key, height_km, sat_lon_deg)
    return (corrected.lat_deg - lat_key, corrected.lon_deg - lon_key)


def _imaged(point: GeoPoint, sat_lon_deg: float) -> GeoPoint:
    """Where the satellite images ``point``. See :func:`_parallax_shift`."""
    key_lat = round(point.lat_deg / _PARALLAX_CACHE_DEG) * _PARALLAX_CACHE_DEG
    key_lon = round(point.lon_deg / _PARALLAX_CACHE_DEG) * _PARALLAX_CACHE_DEG
    dlat, dlon = _parallax_shift(key_lat, key_lon, point.height_msl_km,
                                 sat_lon_deg)
    return GeoPoint(point.lat_deg + dlat, point.lon_deg + dlon,
                    point.height_msl_km)


def _cell_for(
    window: GranuleWindow, name: str, point: GeoPoint, what: str
) -> tuple[tuple[int, int] | None, str]:
    """This window's cell for a ground point, or the clause saying why not.

    Three different shortfalls and three different sentences, because they ask
    the caller for three different things: a wider fetch, a different sector,
    or nothing at all. Folding them into one would tell stage 6 to draw the
    same grey for "you did not fetch enough" and "the satellite cannot see it".

    The bounds check duplicates ``value_at``'s deliberately. ``value_at``
    raises, and an IndexError cannot say which of the two windows fell short
    nor by how many cells, which is the part that tells a caller what to do.
    """
    # PARALLAX, APPLIED HERE SO NO LOOKUP CAN FORGET IT.
    #
    # `point` is where the cloud REALLY is -- pierce_point walked the telescope
    # ray to the layer height. The product geolocates by intersecting the
    # SATELLITE's line of sight with the ground, so that same cloud is imaged
    # displaced away from the sub-satellite point. Reading the pixel at the
    # true position reads the wrong pixel, by h*tan(zenith): measured on the
    # rig 2026-08-26, 5.4 km at a 5.2 km deck with GOES-18 43.8 deg up, which
    # is about two mask cells and always the same direction. The operator saw
    # a star in a clear gap reported as cloudy and called it "offset a bit".
    #
    # geometry.deparallax existed for this and moves the OTHER way -- it turns
    # a reported pixel into a true position, which is the correction a product
    # consumer needs and the opposite of what this consumer needs. Using it
    # here would have doubled the error. apparent_point is its exact inverse.
    #
    # The sub-satellite longitude comes off the window's own grid spec rather
    # than from config, so a G18 window and a G19 window each get their own
    # geometry with nothing to keep in step.
    imaged = point
    if point.height_msl_km > 0.0:
        try:
            imaged = _imaged(point, window.spec.lon_origin_deg)
        except ValueError:
            # apparent_point inherits pierce_point's domain guards. A point
            # the satellite cannot see has no imaged position at all, and the
            # lookup below will refuse it on its own terms rather than on a
            # correction's.
            imaged = point
    index = lonlat_to_index(window.spec, imaged.lat_deg, imaged.lon_deg)
    if index is None:
        return None, (
            "is behind the satellite's limb, so no " + what + " granule holds it"
        )
    row, col = index
    if not in_grid(window.spec, row, col):
        return None, (
            "is outside the "
            + what
            + " granule's "
            + repr(window.spec.n_rows)
            + "x"
            + repr(window.spec.n_cols)
            + " sector"
        )
    array = window.data[name]
    r = row - window.row0
    c = col - window.col0
    if 0 <= r < array.shape[0] and 0 <= c < array.shape[1]:
        return (row, col), ""

    short = []
    if r < 0:
        short.append(_cells(-r, "row"))
    elif r >= array.shape[0]:
        short.append(_cells(r - array.shape[0] + 1, "row"))
    if c < 0:
        short.append(_cells(-c, "column"))
    elif c >= array.shape[1]:
        short.append(_cells(c - array.shape[1] + 1, "column"))
    return None, (
        "is "
        + " and ".join(short)
        + " outside the fetched "
        + what
        + " window, which covers "
        + repr(array.shape[0])
        + " rows by "
        + repr(array.shape[1])
        + " columns"
    )


def _cell_size_km(
    window: GranuleWindow, row: int, col: int
) -> tuple[float, float] | None:
    """The mask cell's true ground size, or ``None`` where there is not one.

    ``pixel_size_km`` raises for a grid one cell across and for a cell whose
    neighbours are off the earth -- a full-disk array's corners. Both are real
    answers of "there is no such distance", and neither is a reason to lose the
    probability: this is the honesty field, and an honesty field that can take
    the answer down with it will get deleted the first time it does.
    """
    try:
        return pixel_size_km(window.spec, row, col)
    except ValueError:
        return None


def _answer(
    site: Site,
    alt_deg: float,
    fov_deg: float,
    point: GeoPoint,
    crossing_km: float | None,
    mask: GranuleWindow,
    *,
    no_crossing: str | None,
) -> Occlusion:
    """The mask's verdict at one pierce point, with what qualifies it.

    ``no_crossing`` is :func:`_why_no_crossing`'s clause, and it is required
    rather than defaulted even though the crossing path passes ``None``: a
    default would let the next call site inherit whichever sentence happened to
    be written first, which is precisely how one sentence came to stand for four
    different walks.

    DEVIATION FROM DESIGN SECTION 4.3, twice, in one line. The design writes
    ``beam_footprint_km(fov_deg, slant_to_layer_km(alt, h, 0))`` and stage 1
    declares ``beam_footprint_km(slant_km, fov_deg)``, so those arguments are
    the wrong way round. The call does not fail; it computes
    ``2 * fov * tan(slant / 2)`` in degrees, and BOTH forms are about
    ``slant * fov`` while the angles are small, so the swap is nearly
    invisible exactly where it is most often looked at: 235.01 m against the
    true 234.65 m at 30 degrees on a 4 km deck, 0.16 percent. It comes apart
    where the beam actually gets big -- 1042 against 1011 m at a 34 km slant,
    13.3 against 4.5 KILOMETRES at 152 km -- and past a 180 km slant it starts
    raising ValueError, because the number it is passing as a field of view is
    a distance. A tolerance loose enough to be comfortable does not catch this.

    And the third argument to the slant is the SITE's elevation, not zero: the
    slant from Boulder to a 4 km deck overhead is 2.376 km rather than 4.0, so
    a sea-level slant overstates the beam by 68 percent -- and overstates
    exactly the number the UI shows to argue the pixel dwarfs the beam.

    ADDITION: a mask cell holding the fill is ``no_data``, not a probability.
    NaN is not a number between 0 and 1, and the alternative breaks the
    invariant that ``probability is None`` iff ``basis == "no_data"``.
    """
    downrange_km = _great_circle_km(
        site.lat_deg, site.lon_deg, point.lat_deg, point.lon_deg
    )
    where = format(downrange_km, ".1f") + " km downrange"

    index, shortfall = _cell_for(mask, MASK_PROBABILITY, point, "mask")
    if index is None:
        return _no_data("the pierce point " + where + " " + shortfall)
    row, col = index

    probability = mask.value_at(MASK_PROBABILITY, row, col)
    if math.isnan(probability):
        return _no_data(
            "the mask is filled rather than retrieved at the pierce point "
            + where
            + ", so there is no probability to report"
        )
    quality = mask.value_at(MASK_QUALITY, row, col)

    slant_km = slant_to_layer_km(alt_deg, point.height_msl_km, site.elev_km)
    cell_km = _cell_size_km(mask, row, col)
    if cell_km is None:
        over = "a mask cell of no measurable size"
    else:
        over = (
            "a "
            + format(cell_km[0], ".1f")
            + " x "
            + format(cell_km[1], ".1f")
            + " km mask cell"
        )
    if crossing_km is None:
        reason = (
            (no_crossing or "no crossing was found")
            + ", so this is the mask at "
            + format(point.height_msl_km, ".1f")
            + " km and "
            + where
            + ": p="
            + format(probability, ".2f")
            + " over "
            + over
        )
    else:
        reason = (
            "the beam meets the cloud-top surface at "
            + format(crossing_km, ".1f")
            + " km, "
            + where
            + ", where the mask reads p="
            + format(probability, ".2f")
            + " over "
            + over
        )
    return Occlusion(
        probability=probability,
        basis="crossing" if crossing_km is not None else "mask_only",
        crossing_km=crossing_km,
        pierce_lat_deg=point.lat_deg,
        pierce_lon_deg=point.lon_deg,
        downrange_km=downrange_km,
        beam_m=beam_footprint_km(slant_km, fov_deg) * 1000.0,
        cell_km=cell_km,
        quality=None if math.isnan(quality) else int(quality),
        reason=reason,
    )


def occlusion_at(
    site: Site,
    alt_deg: float,
    az_deg: float,
    mask: GranuleWindow,
    height: GranuleWindow,
    *,
    fov_deg: float = 1.682,
) -> Occlusion:
    """The probability that cloud sits in this look direction, and where.

    ``mask`` is a cloud-mask window (ACMC, or ACMF for the full disk) carrying
    ``Cloud_Probabilities`` and ``DQF``; ``height`` is a cloud-top window
    carrying ``HT`` in metres. They are read through their own specs and are
    never assumed to share a sampling.

    A PIERCE POINT THE WINDOW DOES NOT COVER IS ``no_data``, for the height
    lookups as much as the mask, per design section 4.2. That has a consequence
    worth stating: the ladder walks to 151 km downrange at 5 degrees, so a
    height window narrower than the dome's reach turns its lowest rays into
    no_data rather than falling back to the mask. That is the intent -- the
    reason says how many cells short the fetch was, which is a caller telling
    itself to fetch wider, where a quiet mask_only would have hidden it.

    The walk climbs the ladder and looks for the rung where the ray passes
    from below the cloud-top surface to on or above it. Two details in that
    sentence are load-bearing:

    ``prev_gap = None`` ON A MISSING RETRIEVAL is not an optimisation. Without
    it a ray that leaves one cloudy region and enters another registers a
    crossing at the boundary between them, because the gap appears to flip sign
    across a stretch of sky that had no surface in it at all.

    ``prev_gap > 0.0 >= gap`` is STRICT above and INCLUSIVE below, so a ray
    landing exactly on the top counts as crossing it. Written ``> 0 >`` the
    grazing ray misses this rung and then misses every later one too, because
    the gap it carries forward is 0.0 rather than positive: it reports
    ``mask_only`` through a solid deck.

    A ray already above the surface at the first rung -- fog, or a top below
    the ladder's start -- never satisfies the condition and falls through to
    the mask-only branch. That is correct: the beam is over that cloud, not
    through it.
    """
    _require_altitude(alt_deg, "alt_deg")
    if not math.isfinite(az_deg):
        raise ValueError("az_deg must be finite, got " + repr(az_deg))
    _require_variable(mask, MASK_PROBABILITY, "mask")
    _require_variable(mask, MASK_QUALITY, "mask")
    _require_variable(height, CLOUD_TOP, "height")
    rungs = _ladder_km(site)

    if alt_deg < MIN_USABLE_ALT_DEG:
        return _no_data(
            "alt "
            + format(alt_deg, ".1f")
            + " deg is below the "
            + format(MIN_USABLE_ALT_DEG, ".1f")
            + " deg floor: the pierce point there runs past 100 km downrange, "
            "where a fetched window does not reach and one cloud-top surface "
            "no longer describes the sky in between"
        )

    prev_gap: float | None = None
    # What the walk saw, so a fall-through can say WHICH of the four it was
    # rather than blaming the product for all of them. ``last_gap_km`` survives
    # the reset that ``prev_gap`` does not: it is the last retrieval the ray
    # actually read, wherever on the ladder that was.
    last_gap_km: float | None = None
    under_before_a_hole = False
    lost_in_a_hole = False
    for height_km in rungs:
        point = pierce_point(site, alt_deg, az_deg, height_km)
        index, shortfall = _cell_for(height, CLOUD_TOP, point, "height")
        if index is None:
            # Not an error: the caller asked for a direction the fetched window
            # does not cover, and stage 6 has to be able to draw the edge of
            # knowledge rather than a confident blank.
            return _no_data(
                "the pierce point "
                + format(
                    _great_circle_km(
                        site.lat_deg, site.lon_deg, point.lat_deg, point.lon_deg
                    ),
                    ".1f",
                )
                + " km downrange at "
                + format(height_km, ".1f")
                + " km "
                + shortfall
            )
        top_km = height.value_at(CLOUD_TOP, index[0], index[1]) / 1000.0
        if math.isnan(top_km):
            if prev_gap is not None and prev_gap > 0.0:
                under_before_a_hole = True
            prev_gap = None
            continue
        gap = top_km - height_km
        if prev_gap is not None and prev_gap > 0.0 >= gap:
            return _answer(
                site, alt_deg, fov_deg, point, height_km, mask, no_crossing=None
            )
        if prev_gap is None and under_before_a_hole and gap <= 0.0:
            # The beam was under the surface, the retrieval went missing, and it
            # came back with the beam above: the sign change this walk exists to
            # find fell inside the hole, and the reset correctly refused to
            # invent a crossing across it.
            lost_in_a_hole = True
        prev_gap = gap
        last_gap_km = gap

    fallback_km = _fallback_height_km(site, rungs)
    if fallback_km is None:
        return _no_data(
            "the site is at "
            + format(site.elev_km, ".1f")
            + " km MSL, above the whole "
            + format(LADDER_TOP_KM, ".1f")
            + " km ladder, so there is no layer left to look through"
        )
    return _answer(
        site,
        alt_deg,
        fov_deg,
        pierce_point(site, alt_deg, az_deg, fallback_km),
        None,
        mask,
        no_crossing=_why_no_crossing(last_gap_km, lost_in_a_hole),
    )


def dome(
    site: Site,
    mask: GranuleWindow,
    height: GranuleWindow,
    *,
    alt_step_deg: float = 2.0,
    az_step_deg: float = 4.0,
    min_alt_deg: float = MIN_USABLE_ALT_DEG,
) -> list[list[float | None]]:
    """Every direction at once: ``grid[alt_index][az_index]``, probability only.

    Altitude ascends from ``min_alt_deg``, azimuth ascends from 0. It exists so
    stage 6 renders one call, and so this stage is measured on cost: the
    default 2 x 4 degrees is 43 x 90 = 3870 rays of up to 75 rungs each, and a
    test fails it above 3 seconds. If it ever needs to be faster the answer is
    to vectorise the ladder, not to coarsen the grid -- the resolution is what
    makes the picture honest.

    ADDITION TO DESIGN SECTION 6: ``min_alt_deg`` is checked against stage 1's
    altitude domain here. Left unchecked, ``min_alt_deg=0`` reaches
    :func:`occlusion_at` as a caller bug reported one row at a time.
    """
    _require_step(alt_step_deg, "alt_step_deg")
    _require_step(az_step_deg, "az_step_deg")
    _require_altitude(min_alt_deg, "min_alt_deg")

    n_alt = int(math.floor((90.0 - min_alt_deg) / alt_step_deg)) + 1
    n_az = int(math.ceil(360.0 / az_step_deg))
    grid: list[list[float | None]] = []
    for i in range(n_alt):
        # A rounding guard borrowing stage 1's ``_clamp_unit`` idiom but NOT
        # its claim: stage 1's never engages and this one is reachable. Stage
        # 1's altitude domain is exclusive above 90, and min_alt_deg=0.2 with
        # alt_step_deg=17.96 puts the last row at 90.00000000000001, so without
        # the clamp a whole rendering call dies of a ValueError at its own
        # zenith. Swept on a 0.01 degree grid, 272 (floor, step) pairs of
        # 40495500 do it. An earlier draft of this comment said the clamp does
        # not engage at any step from 0.01 to 45 degrees; that was true of the
        # five floors it was measured against and of nothing else.
        alt_deg = min(90.0, min_alt_deg + i * alt_step_deg)
        grid.append(
            [
                occlusion_at(
                    site, alt_deg, j * az_step_deg, mask, height
                ).probability
                for j in range(n_az)
            ]
        )
    return grid
