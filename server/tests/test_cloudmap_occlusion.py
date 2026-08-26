"""Is there cloud in the beam, stage 4.

Every test here pins one specific way this module can be plausibly wrong: a
crossing reported as an occlusion when the mask at that pierce point calls the
sky clear, the mask read overhead instead of downrange, a missing cloud-top
retrieval reported as clear sky, a false crossing manufactured at the edge of a
hole in the height field, the 2 km mask indexed through the 10 km height grid's
spec, an altitude below the floor answered with a plausible number.

Fixtures are synthetic ``GranuleWindow``s built here -- the two ``GridSpec``s of
stage 2 design section 9.1 verbatim, and small numpy arrays. No file, no
network, no h5py, so every scenario is exactly constructed, which is the only
way to know what the answer should be.

THE SITE IS 37.0 N, 125.0 W, stage 2 design section 9.2's second golden point.
That it is in the Pacific off Big Sur is deliberate three times over: it is a
pinned golden with known cells in both grids (ACMC 462, 1756 and ACHAC 92, 351),
it sits far enough inside the CONUS sector that a 5 degree ray reaching 15 km --
151 km downrange -- stays in sector at every azimuth, and it is genuinely at sea
level, so ``elev_km = 0`` is a fact and not a convenience. The one test that
needs height above the sea says so and uses Boulder.

Ground truths -- downrange kilometres, pierce cells, pixel sizes, beam widths --
are computed ONCE from stages 1 and 2 and pinned as literals. A test that
re-derives its expectation through the code under test moves whenever that code
does, and stops being a test.
"""
from __future__ import annotations

import math
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest

from astrodeck.cloudmap import occlusion as occlusion_mod
from astrodeck.cloudmap.abi_grid import GridSpec
from astrodeck.cloudmap.geometry import Site
from astrodeck.cloudmap.granule import GranuleWindow
from astrodeck.cloudmap.occlusion import (
    FALLBACK_HEIGHT_KM,
    LADDER_STEP_KM,
    MIN_USABLE_ALT_DEG,
    dome,
    occlusion_at,
)

# --------------------------------------------------------------- the fixtures

# Design section 9.1, verbatim. Two specs and not one, because the whole class
# of bug this stage can hide is reading one product through the other's
# sampling, and a suite with a single spec cannot see it.
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

# Design section 2.2's full disk, which stage 3 is allowed to read: ACMF at
# 2 km and its height counterpart at 10 km. They are here for one reason -- the
# CONUS sector is 5000 km from the limb, so nothing in it can reach the case
# where the pierce point is not on the earth the satellite can see.
ACMF = GridSpec(
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
ACHAF = GridSpec(
    sat_height_m=42164160.0,
    r_eq_m=6378137.0,
    r_pol_m=6356752.31414,
    lon_origin_deg=-137.0,
    sweep_axis="x",
    x_offset_rad=-0.151844,
    x_scale_rad=0.00028,
    n_cols=1085,
    y_offset_rad=0.151844,
    y_scale_rad=-0.00028,
    n_rows=1085,
)

WHEN = datetime(2026, 8, 21, 6, 7, 36, tzinfo=timezone.utc)

SITE = Site(37.0, -125.0, 0.0)
#: Design section 9.3: the site's cell in each grid.
MASK_CELL = (462, 1756)
HEIGHT_CELL = (92, 351)

#: Boulder, for the test about a site that is not at sea level. Design section
#: 9.3's first golden point; 1.624 km is the real ground there.
BOULDER = Site(40.0, -105.0, 1.624)
BOULDER_MASK_CELL = (381, 2459)
BOULDER_HEIGHT_CELL = (76, 491)

#: Stage 3's fixture cell (20, 20) of this same ACMC packing -- far enough into
#: the northwest corner that its ACHAC cell (4, 4) and its ACMC cell (20, 20)
#: are BOTH inside BOTH windows, which is what lets the confusion test read a
#: wrong number rather than merely fall off an edge.
NORTHWEST = Site(52.45650430291552, 178.31618849052433, 0.0)

#: Inside the disk GOES-18 can see, and 20 km east of it is not: stage 2 puts
#: the limb on the 40th parallel at 58.37295 W. Full-disk cells, since no CONUS
#: sector reaches here.
NEAR_THE_LIMB = Site(40.0, -58.6, 0.0)
LIMB_MASK_CELL = (949, 4771)
LIMB_HEIGHT_CELL = (190, 954)


def _array(row0: int, col0: int, shape: tuple[int, int], fill) -> np.ndarray:
    """A window array, ``fill`` evaluated at FULL-GRID (row, col) per cell."""
    if not callable(fill):
        return np.full(shape, float(fill), dtype=np.float64)
    out = np.empty(shape, dtype=np.float64)
    for r in range(shape[0]):
        for c in range(shape[1]):
            out[r, c] = fill(row0 + r, col0 + c)
    return out


def _around(
    spec: GridSpec, cell: tuple[int, int], half_rows: int, half_cols: int
) -> tuple[int, int, tuple[int, int]]:
    """Origin and shape of a window around ``cell``, CLIPPED to the sector.

    Stage 3 clips, so a fixture that did not would hand stage 4 a window
    covering cells the granule never sampled, and the out-of-sector branch
    would never be reached by any test whose window sits near an edge.
    """
    row0 = max(0, cell[0] - half_rows)
    row1 = min(spec.n_rows - 1, cell[0] + half_rows)
    col0 = max(0, cell[1] - half_cols)
    col1 = min(spec.n_cols - 1, cell[1] + half_cols)
    return row0, col0, (row1 - row0 + 1, col1 - col0 + 1)


def mask_window(
    probability,
    *,
    dqf=0.0,
    spec: GridSpec = ACMC,
    product: str = "ABI-L2-ACMC",
    cell: tuple[int, int] = MASK_CELL,
    half_rows: int = 80,
    half_cols: int = 80,
    variables: tuple[str, ...] = ("Cloud_Probabilities", "DQF"),
) -> GranuleWindow:
    """A mask window. ``probability``/``dqf`` are scalars or fill functions."""
    row0, col0, shape = _around(spec, cell, half_rows, half_cols)
    data = {
        "Cloud_Probabilities": _array(row0, col0, shape, probability),
        "DQF": _array(row0, col0, shape, dqf),
    }
    return GranuleWindow(
        spec=spec,
        observed_at=WHEN,
        product=product,
        platform="G18",
        row0=row0,
        col0=col0,
        data={k: v for k, v in data.items() if k in variables},
    )


def height_window(
    top_m,
    *,
    spec: GridSpec = ACHAC,
    product: str = "ABI-L2-ACHAC",
    cell: tuple[int, int] = HEIGHT_CELL,
    half_rows: int = 40,
    half_cols: int = 40,
    variables: tuple[str, ...] = ("HT",),
) -> GranuleWindow:
    """A height window. ``HT`` is METRES, as the granule stores it."""
    row0, col0, shape = _around(spec, cell, half_rows, half_cols)
    data = {"HT": _array(row0, col0, shape, top_m)}
    return GranuleWindow(
        spec=spec,
        observed_at=WHEN,
        product=product,
        platform="G18",
        row0=row0,
        col0=col0,
        data={k: v for k, v in data.items() if k in variables},
    )


# ------------------------------------------------------------------ the tests


def test_a_ray_through_a_flat_deck_crosses_at_the_deck_height():
    """A uniform 4.0 km deck, straight up: the ladder stops at the deck."""
    occ = occlusion_at(SITE, 90.0, 0.0, mask_window(0.5), height_window(4000.0))

    assert occ.basis == "crossing"
    assert occ.crossing_km == pytest.approx(4.0, abs=LADDER_STEP_KM)
    assert occ.probability == pytest.approx(0.5)
    assert occ.downrange_km == pytest.approx(0.0, abs=1e-6)


def test_the_crossing_moves_downrange_as_the_altitude_drops():
    """Pins that the geometry is in the loop, not a lookup at the site cell.

    The three downranges are stage 1's answer for a 4.0 km layer, computed once
    and pinned: a build that read the pixel over the observer would return 0.0
    at all three altitudes and still look entirely reasonable.
    """
    mask = mask_window(0.5)
    height = height_window(4000.0)
    found = [
        occlusion_at(SITE, alt, 0.0, mask, height) for alt in (90.0, 45.0, 20.0)
    ]

    assert [o.basis for o in found] == ["crossing", "crossing", "crossing"]
    assert found[0].downrange_km == pytest.approx(0.0, abs=1e-6)
    assert found[1].downrange_km == pytest.approx(3.996, abs=0.01)
    assert found[2].downrange_km == pytest.approx(10.957, abs=0.01)
    assert found[0].downrange_km < found[1].downrange_km < found[2].downrange_km
    assert found[2].downrange_km > 8.0


def test_the_probability_comes_from_the_mask_at_the_pierce_point_not_overhead():
    """The whole project in one test.

    The mask is clear over the site and cloudy from four cells north, which at
    2.9 km a cell is about 12 km out. Straight up reads the clear cell; 15
    degrees toward the north pierces the 4.0 km deck 14.9 km downrange in cell
    (457, 1755) and reads the cloudy one. A scalar forecast cannot tell those
    two look directions apart, and neither can any build that indexes the mask
    at the observer.
    """
    mask = mask_window(
        lambda row, col: 0.86 if row <= MASK_CELL[0] - 4 else 0.04
    )
    height = height_window(4000.0)

    overhead = occlusion_at(SITE, 90.0, 0.0, mask, height)
    north = occlusion_at(SITE, 15.0, 0.0, mask, height)

    assert overhead.probability == pytest.approx(0.04)
    assert north.probability == pytest.approx(0.86)
    assert north.downrange_km == pytest.approx(14.854, abs=0.01)
    assert north.pierce_lat_deg > SITE.lat_deg


def test_a_crossing_in_a_clear_cell_reports_the_clear_probability():
    """The trap the prototype found: every direction reports a crossing.

    ACHA retrieves a height wherever it converged and that footprint does not
    agree with the mask. A build that treated "the ladder found a surface" as
    the answer would call this sky occluded; the mask says 0.02.
    """
    occ = occlusion_at(
        SITE, 90.0, 0.0, mask_window(0.02), height_window(4000.0)
    )

    assert occ.basis == "crossing"
    assert occ.probability == pytest.approx(0.02)


def test_no_retrieval_anywhere_falls_back_to_the_mask_and_says_so():
    """A missing height is not clear sky -- thin cirrus is exactly this case.

    The downrange pins WHICH height the fallback used: 8.22 km is the ground
    track of a 20 degree ray to 3.0 km. A fallback at 4.0 km would read 10.96
    and a different mask cell with it.
    """
    occ = occlusion_at(
        SITE, 20.0, 0.0, mask_window(0.31), height_window(math.nan)
    )

    assert occ.basis == "mask_only"
    assert occ.crossing_km is None
    assert occ.probability is not None
    assert occ.probability != 0.0
    assert occ.probability == pytest.approx(0.31)
    assert occ.downrange_km == pytest.approx(8.224, abs=0.01)
    assert FALLBACK_HEIGHT_KM == 3.0
    assert "retrieval" in occ.reason

    # The other product's absent retrieval, which is NOT symmetric with it: the
    # mask is the thing that decides, so a filled mask cell leaves nothing to
    # fall back to. NaN is not a probability, and returning it would break the
    # one rule a caller can rely on -- probability is None only for no_data.
    filled = occlusion_at(
        SITE, 20.0, 0.0, mask_window(math.nan), height_window(math.nan)
    )
    assert filled.basis == "no_data"
    assert filled.probability is None
    assert "filled" in filled.reason


def test_the_fall_through_says_which_of_the_four_walks_ended_there():
    """``mask_only`` has four causes and only one of them is a missing height.

    Every fall-through used to carry the same sentence, "no cloud-top retrieval
    along the beam". ``reason`` is the only field written for a human, so a
    wrong one is the whole of what that field is for -- and on real granules it
    was wrong wherever it mattered. Over 19350 rays at five sites on one
    GOES-18 ACMC/ACHAC pair, 7798 fell through and 1390 of those had read a
    finite cloud top on the ladder: a 5 degree ray due north walked 19 rungs
    over a retrieved 4829 m deck and still reported no retrieval, sending
    anyone diagnosing it to look for a hole in the satellite product when the
    beam was simply above the cloud.

    Four skies, four walks. The load-bearing assertions are the NEGATIVE ones:
    three of these must not claim a missing retrieval. That is the assertion
    the suite did not have -- ``no_retrieval_anywhere_falls_back`` asserts
    "retrieval" is present, in the one scenario where the sentence is true.
    """
    nothing = occlusion_at(
        SITE, 20.0, 0.0, mask_window(0.31), height_window(math.nan)
    )
    fog = occlusion_at(SITE, 90.0, 0.0, mask_window(0.4), height_window(100.0))
    anvil = occlusion_at(
        SITE, 90.0, 0.0, mask_window(0.4), height_window(16000.0)
    )

    # The hole is the false-crossing fixture below with its far cirrus removed,
    # so the walk goes under the near cirrus, into the gap, and comes out over
    # the low deck with no crossing left anywhere. ACHA fails to converge at
    # cloud EDGES, which is exactly where a ray meets the surface, so this is
    # the common one on real granules rather than a contrivance: 339 of those
    # 7798, and 45 of 3543 at the least cloudy of the five sites.
    def top_m(row, col):
        if row >= 91:
            return 12000.0
        if row == 90:
            return math.nan
        return 5000.0

    hole = occlusion_at(SITE, 10.0, 0.0, mask_window(0.5), height_window(top_m))

    walks = (nothing, fog, anvil, hole)
    assert [o.basis for o in walks] == ["mask_only"] * 4

    assert "no cloud-top retrieval along the beam" in nothing.reason
    for occ in (fog, anvil, hole):
        assert "no cloud-top retrieval" not in occ.reason, occ.reason

    assert "above the cloud top" in fog.reason
    assert "still above the beam" in anvil.reason
    assert "breaks off" in hole.reason

    # Four sentences, not one sentence and three near-misses. Compared on the
    # clause alone, because the tail carries a downrange and a probability that
    # would make even four identical clauses look distinct.
    clauses = {o.reason.split(", so this is the mask")[0] for o in walks}
    assert len(clauses) == 4, clauses


def test_a_gap_in_the_height_field_does_not_fake_a_crossing():
    """Sabotage target: delete the ``prev_gap = None`` reset and this fails.

    A 10 degree ray north walks ACHAC rows 92, 91, 90, 89, 88, 87 as it climbs,
    and the field is built in bands along it: cirrus at 12 km over the first two
    rows, NO RETRIEVAL over row 90, a low deck at 5 km over rows 89 and 88, then
    cirrus at 14 km from row 87 out. The ray is under the near cirrus (gap
    +8.2 km at the last rung before the hole) and over the far low deck (gap
    -1.6 km at the first rung after it), so a walk that carries ``prev_gap``
    across the hole sees a sign flip and reports a crossing at 6.6 km through a
    stretch of sky that had no surface in it at all. The real crossing is the
    14 km cirrus, 7.4 km higher and 40 km further downrange.
    """
    def top_m(row, col):
        if row >= 91:
            return 12000.0
        if row == 90:
            return math.nan
        if row >= 88:
            return 5000.0
        return 14000.0

    occ = occlusion_at(SITE, 10.0, 0.0, mask_window(0.5), height_window(top_m))

    assert occ.basis == "crossing"
    assert occ.crossing_km == pytest.approx(14.0, abs=LADDER_STEP_KM)
    assert occ.crossing_km > 10.0


def test_a_ray_grazing_the_top_exactly_counts_as_crossing():
    """Pins ``prev_gap > 0 >= gap``: strict above, INCLUSIVE below.

    9.0 km is exactly a rung, so a 9000 m deck puts the gap at exactly 0.0
    there. Written ``prev_gap > 0 > gap`` the ray misses it, and it misses the
    next rung too because the gap it carries forward is then 0.0 rather than
    positive -- so it does not merely land one step high, it finds no crossing
    at all and reports mask_only through a solid deck.
    """
    occ = occlusion_at(SITE, 90.0, 0.0, mask_window(0.5), height_window(9000.0))

    assert occ.basis == "crossing"
    assert occ.crossing_km == 9.0


def test_a_cloud_top_below_the_first_rung_is_not_a_crossing():
    """Fog at 0.1 km: the beam starts above it, so it never crosses it.

    The gap is negative at the first rung and stays negative, which the
    condition rejects for want of a positive ``prev_gap``. A crossing here would
    put cloud in a beam that is looking over the top of it.
    """
    occ = occlusion_at(SITE, 90.0, 0.0, mask_window(0.4), height_window(100.0))

    assert occ.basis == "mask_only"
    assert occ.crossing_km is None
    assert occ.probability == pytest.approx(0.4)


def test_a_deck_between_the_first_two_rungs_is_still_crossed():
    """The bottom of the ladder, which the fog test above cannot reach.

    A 0.3 km stratus top sits ABOVE the first rung and below the second, so it
    is the only kind of deck whose crossing depends on the ladder starting at
    0.2 and not at 0.4: the gap is +0.1 at 0.2 km and -0.1 at 0.4 km, and a
    ladder that skipped its first rung would carry no positive ``prev_gap``
    into 0.4 and report mask_only straight through a deck the beam is inside.
    The 0.1 km fog above is below BOTH candidate starts and cannot tell them
    apart, so before this test the ladder's start was pinned by nothing.
    """
    occ = occlusion_at(SITE, 90.0, 0.0, mask_window(0.4), height_window(300.0))

    assert occ.basis == "crossing"
    assert occ.crossing_km == 0.4


def test_the_ladder_reaches_its_last_rung():
    """A 14.9 km anvil is crossed at 15.0 km, the rung the ladder ends on.

    ``_ladder_km`` stops on ``height_km > LADDER_TOP_KM``, and written ``>=``
    it drops the 15.0 rung: the gap at 14.8 is still +0.1, the walk runs out of
    ladder and the deepest convection in the sky comes back mask_only. That is
    the failure the ladder's docstring promises against -- 200 m of sky off the
    top of every ray -- and it is invisible to every deck lower than this one.

    14.9 km rather than 15.0 on purpose. A top at exactly 15.0 makes the gap
    there exactly 0.0, so it would also pass or fail on the inclusive ``>=`` in
    the crossing test, and a test that two separate rules can break tells you
    which one is wrong only by accident.
    """
    occ = occlusion_at(
        SITE, 90.0, 0.0, mask_window(0.7), height_window(14900.0)
    )

    assert occ.basis == "crossing"
    assert occ.crossing_km == 15.0
    assert occ.probability == pytest.approx(0.7)


def test_the_two_grids_are_never_confused():
    """The 5x sampling, read both ways round, both wrong answers available.

    Both windows contain both cells, so a lookup through the wrong spec does
    not fall off an edge and raise -- it reads a real number from a place five
    cells away and returns it. Indexing the height through the mask's spec
    crosses at the wrong altitude; indexing the mask through the height's spec
    answers the wrong probability. Neither looks like an error.

    THE CELLS MOVED WHEN PARALLAX LANDED, and the reason is worth keeping. The
    fixture used to name ACMC (20, 20) and ACHAC (4, 4) as "the same ground
    point", true of the ray's TRUE position. The lookup now reads the IMAGED
    position, and the ladder corrects each candidate rung by that rung's own
    height -- so the two products are sampled with different displacements and
    the tidy 5x correspondence between their cells no longer holds. That is
    self-consistent rather than wrong: the ladder is looking for the height
    whose imaged position holds cloud, and at the correct rung the candidate
    height IS the cloud height. The cells below are the ones the corrected ray
    actually reaches, measured, not derived from the old coincidence.
    """
    def top_m(row, col):
        if (row, col) == (3, 3):
            return 4000.0
        if (row, col) == (19, 19):
            return 9000.0
        return math.nan

    def probability(row, col):
        # (18, 19) and not (19, 19): the mask is read at the RETRIEVED height,
        # 4 km, whose displacement differs from the rung that found it. Two
        # products, two sampling heights, two cells -- the point the docstring
        # above makes, showing up one more time.
        if (row, col) == (18, 19):
            return 0.80
        if (row, col) == (3, 3):
            return 0.05
        return 0.50

    occ = occlusion_at(
        NORTHWEST,
        90.0,
        0.0,
        mask_window(probability, cell=(20, 20), half_rows=19, half_cols=19),
        height_window(top_m, cell=(4, 4), half_rows=19, half_cols=19),
    )

    assert occ.basis == "crossing"
    assert occ.crossing_km == pytest.approx(4.0, abs=LADDER_STEP_KM)
    assert occ.probability == pytest.approx(0.80)


def test_a_pierce_point_outside_the_window_says_no_data():
    """The caller asked for a direction the fetched window does not cover.

    Not an error and not a probability: stage 6 has to be able to draw the edge
    of knowledge rather than a confident blank. The reason names the window and
    how far short it fell, which is what tells the caller to fetch a wider one.
    It names no coordinate -- a reason string reaches the same journal stage 3
    keeps latitudes out of, and the pierce point is within 30 km of the site.

    Three shortfalls and three sentences, because they ask for three different
    things: fetch wider, fetch a different sector, or give up. A single grey
    "no data" for all three would tell stage 6 that the satellite cannot see a
    place the caller simply did not ask for.
    """
    height = height_window(4000.0)
    occ = occlusion_at(
        SITE, 15.0, 0.0, mask_window(0.9, half_rows=1, half_cols=1), height
    )

    assert occ.basis == "no_data"
    assert occ.probability is None
    assert "mask window" in occ.reason
    # 5, not 4: parallax moves the sampled cell about two mask cells away
    # from the satellite, so a point already outside the window falls a little
    # further outside it. The NUMBER is not the subject of this test -- that
    # the reason names the window and how far short it fell is -- but pinning
    # it is what would catch the correction being silently dropped.
    assert "5 rows" in occ.reason
    assert "37.0" not in occ.reason
    assert "-125" not in occ.reason

    # Past the other two edges. Off the far edge the shortfall counts from the
    # last cell IN the window and off the near edge from the first, which are
    # two different expressions each carrying a deliberate one.
    #
    # THEY USED TO HAVE TO MATCH, and now they must not. That assertion guarded
    # the shortfall arithmetic against a sign bug by sending the same
    # displacement southeast and northwest and requiring the same answer.
    # Parallax is DIRECTIONAL -- always away from the satellite -- so a ray
    # pointing toward it and one pointing away no longer land the same distance
    # outside the window, and demanding they do would demand the correction not
    # be applied. Both counts are pinned instead, and their INEQUALITY is now
    # the thing that goes red if the correction is dropped.
    #
    # Measured three times before being written down, because a probe beside
    # the test used height_window(nan) where the test uses height_window(4000)
    # and answered 11.2 km downrange against the fixture's 14.9 -- two
    # different questions that both looked like this one. The numbers below
    # come from the fixture itself.
    southeast = occlusion_at(
        SITE, 15.0, 135.0, mask_window(0.9, half_rows=1, half_cols=1), height
    )
    assert "2 rows and 5 columns outside" in southeast.reason
    northwest = occlusion_at(
        SITE, 15.0, 315.0, mask_window(0.9, half_rows=1, half_cols=1), height
    )
    assert "4 rows and 4 columns outside" in northwest.reason
    assert southeast.reason != northwest.reason, (
        "parallax is directional; identical shortfalls mean it was not applied")

    # The HEIGHT window is the one that runs out first in practice, because the
    # ladder walks the whole ray while the mask is read once at the crossing.
    # This one leaves a 3 x 3 height window at 4.0 km, 23 km out.
    narrow = occlusion_at(
        SITE,
        10.0,
        0.0,
        mask_window(0.9),
        height_window(4000.0, half_rows=1, half_cols=1),
    )
    assert narrow.basis == "no_data"
    assert "height window" in narrow.reason
    assert "1 row " in narrow.reason      # not "1 rows"

    # Off the SECTOR, which is a different fact from off the window: Boulder is
    # 8 cells from the east edge of the CONUS height grid and a 5 degree ray
    # east leaves it at 12 km up. No wider fetch of this granule can help.
    off_sector = occlusion_at(
        BOULDER,
        5.0,
        90.0,
        mask_window(0.9, cell=BOULDER_MASK_CELL, half_rows=30, half_cols=30),
        height_window(
            math.nan, cell=BOULDER_HEIGHT_CELL, half_rows=20, half_cols=20
        ),
    )
    assert off_sector.basis == "no_data"
    assert "sector" in off_sector.reason

    # And behind the limb, which no granule of any sector holds. A site at
    # 58.6 W is inside the disk GOES-18 can see and 20 km east of it is not.
    beyond = occlusion_at(
        NEAR_THE_LIMB,
        5.0,
        90.0,
        mask_window(
            0.9,
            spec=ACMF,
            product="ABI-L2-ACMF",
            cell=LIMB_MASK_CELL,
            half_rows=3,
            half_cols=3,
        ),
        height_window(
            math.nan,
            spec=ACHAF,
            product="ABI-L2-ACHAF",
            cell=LIMB_HEIGHT_CELL,
            half_rows=3,
            half_cols=3,
        ),
    )
    assert beyond.basis == "no_data"
    assert "limb" in beyond.reason


def _coordinate_tokens(*values) -> list[str]:
    """Every rendering a reason string could plausibly carry a coordinate in."""
    tokens = []
    for value in values:
        if value is None:
            continue
        tokens += [format(value, "." + repr(dp) + "f") for dp in (1, 2, 3, 4)]
    return tokens


def test_no_reason_names_a_coordinate():
    """The leak worth guarding is the PIERCE POINT, not the site.

    The window test above asserts only that the site's own 37.0 and -125 stay
    out, and that passes a build whose every reason opens with "37.1336 N" --
    the pierce point of a ray 15 km north, which is the same secret to three
    decimal places. A reason reaches the journal that stage 3's ``read_window``
    deliberately keeps latitudes out of, and design section 6 keeps them out of
    for a reason: a position within 30 km of the observatory locates it.

    Every basis is checked, because they are four different sentences built in
    two different functions. The two ``no_data`` ones carry no pierce fields to
    be checked against themselves, so the same direction is asked again through
    a window wide enough to answer and ITS pierce point is what must be absent.
    """
    height = height_window(4000.0)
    crossing = occlusion_at(SITE, 30.0, 0.0, mask_window(0.3), height)
    mask_only = occlusion_at(
        SITE, 20.0, 0.0, mask_window(0.31), height_window(math.nan)
    )
    # ``mask_only`` is four sentences, not one, and the other three are three
    # more strings that could carry a coordinate.
    over_the_deck = occlusion_at(
        SITE, 90.0, 0.0, mask_window(0.4), height_window(100.0)
    )
    under_the_anvil = occlusion_at(
        SITE, 90.0, 0.0, mask_window(0.4), height_window(16000.0)
    )
    filled = occlusion_at(
        SITE, 20.0, 0.0, mask_window(math.nan), height_window(math.nan)
    )
    wide = occlusion_at(SITE, 15.0, 0.0, mask_window(0.9), height)
    narrow = occlusion_at(
        SITE, 15.0, 0.0, mask_window(0.9, half_rows=1, half_cols=1), height
    )
    short = occlusion_at(
        SITE,
        10.0,
        0.0,
        mask_window(0.9),
        height_window(4000.0, half_rows=1, half_cols=1),
    )
    floored = occlusion_at(SITE, 4.9, 0.0, mask_window(0.5), height)

    assert crossing.basis == "crossing"
    assert [
        mask_only.basis,
        over_the_deck.basis,
        under_the_anvil.basis,
    ] == ["mask_only"] * 3
    assert [filled.basis, narrow.basis, short.basis, floored.basis] == [
        "no_data"
    ] * 4

    site = _coordinate_tokens(SITE.lat_deg, SITE.lon_deg)
    for occ in (
        crossing,
        mask_only,
        over_the_deck,
        under_the_anvil,
        filled,
        narrow,
        short,
        floored,
    ):
        own = _coordinate_tokens(occ.pierce_lat_deg, occ.pierce_lon_deg)
        for token in site + own:
            assert token not in occ.reason, token

    for token in _coordinate_tokens(wide.pierce_lat_deg, wide.pierce_lon_deg):
        assert token not in narrow.reason, token
    for token in _coordinate_tokens(
        mask_only.pierce_lat_deg, mask_only.pierce_lon_deg
    ):
        assert token not in filled.reason, token


def test_the_beam_and_the_cell_are_reported_together():
    """The ratio the UI must show, and the DQF that qualifies it.

    At 30 degrees the beam is 235 mm across where it meets a 4 km deck and the
    mask cell it is answered from is 2.90 x 2.16 km: 145 times the area. A
    probability printed without that ratio nearby claims a determination the
    data cannot support.

    THE BEAM TOLERANCE IS 10 MM ON PURPOSE. Stage 1's ``beam_footprint_km``
    takes the slant first and the field of view second, and called the other
    way round -- which is how the design writes it -- it returns 235.01 mm
    here against the true 234.65. Both forms are about ``slant * fov`` at small
    angles, so anything looser than a few tens of millimetres passes an
    argument swap that is a factor of three out at a 152 km slant.
    """
    occ = occlusion_at(
        SITE, 30.0, 0.0, mask_window(0.3, dqf=1.0), height_window(4000.0)
    )

    assert occ.beam_m == pytest.approx(234.648, abs=0.01)
    assert occ.cell_km is not None
    # 2.9040, not 2.9024: cell size varies across the grid, and the parallax
    # correction reads a cell about two rows away from the ray's true ground
    # position. 1.6 m of difference in a 2.9 km cell -- the number moved
    # because the LOOKUP moved, which is the fix working.
    assert occ.cell_km[0] == pytest.approx(2.9040, abs=0.001)
    assert occ.cell_km[1] == pytest.approx(2.1597, abs=0.001)
    assert occ.quality == 1

    beam_area_km2 = math.pi * (occ.beam_m / 2000.0) ** 2
    assert occ.cell_km[0] * occ.cell_km[1] / beam_area_km2 > 100.0

    # And where there IS no ground distance -- ``pixel_size_km`` raises for a
    # grid one cell across and for a cell whose neighbours are off the earth --
    # the honesty field goes missing and the answer does not. A guard that can
    # take the probability down with it gets deleted the first time it does.
    def no_such_distance(spec, row, col):
        raise ValueError("no pair of neighbouring cell centres")

    monkeypatch = pytest.MonkeyPatch()
    with monkeypatch.context() as patched:
        patched.setattr(occlusion_mod, "pixel_size_km", no_such_distance)
        sized = occlusion_at(
            SITE, 30.0, 0.0, mask_window(0.3), height_window(4000.0)
        )
    assert sized.cell_km is None
    assert sized.probability == pytest.approx(0.3)
    assert sized.basis == "crossing"


def test_altitude_below_the_floor_is_refused_not_guessed():
    """Below 5 degrees there is no answer, and the floor is inclusive.

    4.9 degrees is not an error -- it is a direction a mount can point -- so it
    returns no_data with a sentence, not an exception and not a number.
    """
    refused = occlusion_at(
        SITE, 4.9, 0.0, mask_window(0.5), height_window(4000.0)
    )

    assert refused.basis == "no_data"
    assert refused.probability is None
    assert refused.crossing_km is None
    assert "floor" in refused.reason

    at_the_floor = occlusion_at(
        SITE, MIN_USABLE_ALT_DEG, 0.0, mask_window(0.5), height_window(4000.0)
    )
    assert at_the_floor.basis != "no_data"


def _alt_at_the_horizon(mask, height, monkeypatch):
    occlusion_at(SITE, 0.0, 0.0, mask, height)


def _alt_below_the_horizon(mask, height, monkeypatch):
    occlusion_at(SITE, -1.0, 0.0, mask, height)


def _alt_past_the_zenith(mask, height, monkeypatch):
    occlusion_at(SITE, 90.5, 0.0, mask, height)


def _alt_not_a_number(mask, height, monkeypatch):
    occlusion_at(SITE, math.nan, 0.0, mask, height)


def _az_not_a_number(mask, height, monkeypatch):
    occlusion_at(SITE, 45.0, math.nan, mask, height)


def _az_infinite(mask, height, monkeypatch):
    occlusion_at(SITE, 45.0, math.inf, mask, height)


def _ladder_step_zero(mask, height, monkeypatch):
    monkeypatch.setattr(occlusion_mod, "LADDER_STEP_KM", 0.0)
    occlusion_at(SITE, 45.0, 0.0, mask, height)


def _ladder_step_negative(mask, height, monkeypatch):
    monkeypatch.setattr(occlusion_mod, "LADDER_STEP_KM", -0.2)
    occlusion_at(SITE, 45.0, 0.0, mask, height)


def _alt_step_zero(mask, height, monkeypatch):
    dome(SITE, mask, height, alt_step_deg=0.0)


def _alt_step_too_coarse(mask, height, monkeypatch):
    dome(SITE, mask, height, alt_step_deg=45.5)


def _az_step_negative(mask, height, monkeypatch):
    dome(SITE, mask, height, az_step_deg=-4.0)


def _az_step_not_a_number(mask, height, monkeypatch):
    dome(SITE, mask, height, az_step_deg=math.nan)


def _min_alt_below_the_horizon(mask, height, monkeypatch):
    dome(SITE, mask, height, min_alt_deg=0.0)


#: Each case with the parameter its ``ValueError`` has to name.
_DOMAIN_ERRORS = [
    (_alt_at_the_horizon, "alt_deg"),
    (_alt_below_the_horizon, "alt_deg"),
    (_alt_past_the_zenith, "alt_deg"),
    (_alt_not_a_number, "alt_deg"),
    (_az_not_a_number, "az_deg"),
    (_az_infinite, "az_deg"),
    (_ladder_step_zero, "LADDER_STEP_KM"),
    (_ladder_step_negative, "LADDER_STEP_KM"),
    (_alt_step_zero, "alt_step_deg"),
    (_alt_step_too_coarse, "alt_step_deg"),
    (_az_step_negative, "az_step_deg"),
    (_az_step_not_a_number, "az_step_deg"),
    (_min_alt_below_the_horizon, "min_alt_deg"),
]


@pytest.mark.parametrize(
    "call, names",
    _DOMAIN_ERRORS,
    ids=[case.__name__.lstrip("_") for case, _ in _DOMAIN_ERRORS],
)
def test_domain_errors(call, names, monkeypatch):
    """Design section 6. Nothing here answers a bad input with a number.

    An altitude of zero is the case worth naming: it is inside no_data's
    territory by value and outside the geometry's domain by definition, so a
    build that consulted the 5 degree floor first would swallow it and report a
    serene "no data" for a caller bug.

    THE ``match`` IS NOT DECORATION. ``pytest.raises(ValueError)`` alone cannot
    tell a guard from a crash, and several of these are answered by an
    accidental ValueError if their guard is deleted: a NaN azimuth STEP dies on
    ``int(math.ceil(360.0 / nan))`` a few lines further on, saying nothing about
    a step, and without the match that case passes against a dome with no step
    guard at all. The assertion has to be that THIS module refused the input.
    """
    with pytest.raises(ValueError, match=names):
        call(
            mask_window(0.5, half_rows=2, half_cols=2),
            height_window(4000.0, half_rows=2, half_cols=2),
            monkeypatch,
        )


def test_a_non_finite_azimuth_is_refused_by_name():
    """``pytest.raises(ValueError)`` above is satisfied by the wrong module.

    Delete this module's ``math.isfinite(az_deg)`` guard and both azimuth cases
    in ``test_domain_errors`` still pass, because a NaN azimuth walks all the
    way down into stage 2 and dies on ``round(nan)`` -- "cannot convert float
    NaN to integer", two modules away, naming no parameter -- while an infinite
    one dies on ``math.sin(inf)`` with "math domain error". Both are
    ``ValueError`` and neither is this module noticing anything, so the guard
    was pinned by nothing at all. Matching the parameter name is what makes the
    assertion about the guard rather than about the crash.

    The floor is where it matters. A bad azimuth below 5 degrees never reaches
    a lookup, so without the guard it comes back as a serene ``no_data`` and
    the caller's bug is answered with a picture.
    """
    mask = mask_window(0.5)
    height = height_window(4000.0)

    for bad in (math.nan, math.inf, -math.inf):
        with pytest.raises(ValueError, match="az_deg"):
            occlusion_at(SITE, 45.0, bad, mask, height)
        with pytest.raises(ValueError, match="az_deg"):
            occlusion_at(SITE, 2.0, bad, mask, height)


def test_a_dome_floor_outside_the_altitude_domain_is_refused_not_emptied():
    """``dome``'s own check on ``min_alt_deg``, which nothing else can make.

    Two cases, and the first is the one that gets past every other guard. A
    floor of 90.5 makes ``n_alt`` zero, so an unchecked ``dome`` never calls
    ``occlusion_at`` at all and hands stage 6 an EMPTY grid: no exception, no
    ``no_data``, no rows to notice the absence of.

    A floor of 0 does reach ``occlusion_at`` and does raise there, which is why
    ``test_domain_errors`` cannot tell the two builds apart -- but it raises
    once per row, naming ``alt_deg``, about a value the caller never passed
    under that name. The match is on ``min_alt_deg`` so that the error is the
    dome's own.
    """
    mask = mask_window(0.5, half_rows=2, half_cols=2)
    height = height_window(4000.0, half_rows=2, half_cols=2)

    with pytest.raises(ValueError, match="min_alt_deg"):
        dome(SITE, mask, height, min_alt_deg=90.5)
    with pytest.raises(ValueError, match="min_alt_deg"):
        dome(SITE, mask, height, min_alt_deg=0.0)


def test_a_missing_variable_raises_rather_than_defaulting():
    """A window without the variable is a caller error, not a clear sky.

    Raised at the CALL, before the altitude floor is consulted: a build that
    checked the floor first would answer a mask carrying no probabilities with
    a plain ``no_data`` for every direction below 5 degrees, and the missing
    variable would surface hours later somewhere else.

    EVERY ONE OF THE THREE IS ASKED BELOW THE FLOOR AS WELL AS ABOVE IT, and
    the below-the-floor call is the only half that pins anything. Above the
    floor the lookup itself raises the same ``KeyError`` from inside
    ``value_at``, so a suite that only asked at 45 degrees passes with all
    three ``_require_variable`` calls deleted -- which is exactly the build the
    paragraph above describes and cannot then detect.
    """
    height = height_window(4000.0)

    no_probability = mask_window(0.5, variables=("DQF",))
    with pytest.raises(KeyError) as caught:
        occlusion_at(SITE, 45.0, 0.0, no_probability, height)
    assert "Cloud_Probabilities" in str(caught.value)

    with pytest.raises(KeyError):
        occlusion_at(SITE, 2.0, 0.0, no_probability, height)

    no_quality = mask_window(0.5, variables=("Cloud_Probabilities",))
    with pytest.raises(KeyError) as caught:
        occlusion_at(SITE, 45.0, 0.0, no_quality, height)
    assert "DQF" in str(caught.value)

    with pytest.raises(KeyError):
        occlusion_at(SITE, 2.0, 0.0, no_quality, height)

    no_top = height_window(4000.0, variables=())
    with pytest.raises(KeyError) as caught:
        occlusion_at(SITE, 45.0, 0.0, mask_window(0.5), no_top)
    assert "HT" in str(caught.value)

    with pytest.raises(KeyError):
        occlusion_at(SITE, 2.0, 0.0, mask_window(0.5), no_top)


def test_the_dome_is_indexed_altitude_then_azimuth():
    """Shape, ascending altitude, and the azimuth convention end to end.

    The mask is clear except from four cells east of the site, about 9 km out.
    With no height retrieval anywhere every direction answers from the mask at
    3.0 km, whose pierce point is 33 km downrange at 5 degrees and 1.4 km at 65:
    so the eastern feature shows up in the LOW altitude row and in the az 90
    column. Index the rows the other way round and the feature lands at the
    zenith; measure azimuth westward and it lands at az 270. Both are
    plausible-looking pictures of the wrong sky.

    The steps are 30 and 45 degrees because design section 6 refuses anything
    coarser than 45, so the smallest dome that has a due-east column at all is
    eight columns wide.
    """
    mask = mask_window(
        lambda row, col: 0.90 if col >= MASK_CELL[1] + 4 else 0.05
    )
    grid = dome(
        SITE, mask, height_window(math.nan), alt_step_deg=30.0, az_step_deg=45.0
    )

    assert len(grid) == 3                        # 5, 35, 65 degrees
    assert all(len(row) == 8 for row in grid)    # 0, 45, ... 315 degrees

    assert grid[0][2] == pytest.approx(0.90)     # low and east: the feature
    assert grid[0][6] == pytest.approx(0.05)     # low and west
    assert grid[0][0] == pytest.approx(0.05)     # low and north
    assert grid[2][2] == pytest.approx(0.05)     # east, but overhead


def test_the_dome_closes_the_circle_on_a_step_that_does_not_divide_360():
    """``n_az`` is ``ceil``, and only a step like 7 degrees can say so.

    4 and 45 both divide 360, so every other dome test here passes with either
    rounding. At 7 they differ by one column: ceil gives 52 and reaches 357,
    floor gives 51 and stops at 350, leaving a 7 degree wedge of sky that stage
    6 never asks about. Nothing in the returned grid reports the gap, because
    the grid is simply one column shorter -- the picture closes, over a
    direction nobody looked at.
    """
    grid = dome(
        SITE,
        mask_window(0.31),
        height_window(math.nan),
        alt_step_deg=30.0,
        az_step_deg=7.0,
    )

    assert all(len(row) == 52 for row in grid)
    assert all((len(row) - 1) * 7.0 < 360.0 <= len(row) * 7.0 for row in grid)


def test_the_domes_zenith_row_lands_inside_the_altitude_domain():
    """The ``min(90.0, ...)`` clamp is reachable, and the module says it is not.

    A floor of 0.2 with a 17.96 degree step puts ``n_alt`` at 6 and the last
    row at ``0.2 + 5 * 17.96``, which in floating point is 90.00000000000001.
    Stage 1's altitude domain is exclusive above 90, so an unclamped dome dies
    of a ValueError at its own zenith and takes the whole rendering call with
    it. ``occlusion.dome``'s docstring claims the clamp does not engage at any
    step swept from 0.01 to 45 degrees against five floors; it engages here,
    and at 524 other (floor, step) pairs on a 0.001 degree grid, so the claim
    needs narrowing to the five floors it was measured over.
    """
    grid = dome(
        SITE,
        mask_window(0.31),
        height_window(math.nan),
        alt_step_deg=17.96,
        az_step_deg=45.0,
        min_alt_deg=0.2,
    )

    assert len(grid) == 6
    assert grid[0] == [None] * 8          # 0.2 degrees, below the floor
    assert all(value is not None for value in grid[-1])


def test_the_dome_completes_within_its_budget():
    """3870 rays at the worst case: no retrieval, so every ladder runs to 15 km.

    The budget is the reason stage 6 can call this per frame. It is also the
    test most able to pass on nothing, so it asserts the dome is full of answers
    first -- a build that returned no_data everywhere would be extremely fast.
    """
    mask = mask_window(0.31)
    height = height_window(math.nan)

    start = time.perf_counter()
    grid = dome(SITE, mask, height)
    elapsed = time.perf_counter() - start

    assert len(grid) == 43
    assert all(len(row) == 90 for row in grid)
    answered = sum(1 for row in grid for value in row if value is not None)
    assert answered == 43 * 90
    assert elapsed < 3.0


def test_a_mile_high_site_walks_the_ladder_above_itself():
    """DEVIATION from design section 4.1, and the reason for it.

    The ladder as specified starts at 0.2 km MSL for every site, and stage 1
    refuses a layer at or below the observer -- so at Boulder, 1.624 km up and
    the design's own first golden point, the first rung raises ValueError and
    the module never answers at all. Rungs at or below the site are skipped
    instead: that is sky the beam is already above.

    The beam width pins the second half of it. The slant to a 4 km deck from
    1.624 km is 2.376 km, not 4.0, so the beam there is 69.8 mm and not the
    117.4 mm a sea-level slant reports -- a 68 percent overstatement of the one
    number the UI shows to argue the pixel is far bigger than the beam.
    """
    mask = mask_window(0.42, cell=BOULDER_MASK_CELL, half_rows=30, half_cols=30)
    deck = height_window(
        4000.0, cell=BOULDER_HEIGHT_CELL, half_rows=6, half_cols=6
    )

    occ = occlusion_at(BOULDER, 90.0, 0.0, mask, deck)

    assert occ.basis == "crossing"
    assert occ.crossing_km == pytest.approx(4.0, abs=LADDER_STEP_KM)
    assert occ.probability == pytest.approx(0.42)
    assert occ.beam_m == pytest.approx(69.756, abs=0.01)

    # A site ABOVE the fallback height still answers: the fallback is lifted to
    # the first rung over the observer, where 3.0 km is a layer underfoot and
    # stage 1 refuses it.
    high = Site(40.0, -105.0, 4.2)
    nothing = height_window(
        math.nan, cell=BOULDER_HEIGHT_CELL, half_rows=6, half_cols=6
    )
    aloft = occlusion_at(high, 90.0, 0.0, mask, nothing)

    assert aloft.basis == "mask_only"
    assert aloft.probability == pytest.approx(0.42)

    # Above the whole ladder there is no rung left to fall back to, which is a
    # balloon rather than an observatory -- but it must say so rather than
    # index an empty list.
    balloon = occlusion_at(Site(40.0, -105.0, 20.0), 90.0, 0.0, mask, nothing)
    assert balloon.basis == "no_data"
    assert "ladder" in balloon.reason


_BAN_H5PY = """
import sys


class Ban:
    def find_spec(self, name, path=None, target=None):
        if name == 'h5py' or name.startswith('h5py.'):
            raise AssertionError('imported h5py')
        return None


sys.meta_path.insert(0, Ban())
import {module}
print('imported clean')
"""


def _import_under_the_ban(module):
    """Import ``module`` in a fresh interpreter with h5py forbidden."""
    root = str(Path(occlusion_mod.__file__).resolve().parents[2])
    env = dict(os.environ)
    env["PYTHONPATH"] = root + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "-c", _BAN_H5PY.format(module=module)],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )


def test_stage_four_imports_without_h5py():
    """Design section 8's "No h5py import", which was a claim nothing kept.

    ``GranuleWindow`` appears in stage 4 only in annotations, and
    ``from __future__ import annotations`` leaves those as strings -- but an
    ordinary import of it drags in stage 3's guarded ``import h5py`` all the
    same. A module whose own first line promises no h5py was paying h5py's
    import: 0.233 s and 251 modules against 0.011 s and 91 for stages 1 and 2,
    for a module that never opens a file.

    A SUBPROCESS, because this test module has already imported ``granule``
    itself. The ban has to be in place before anything under
    ``astrodeck.cloudmap`` loads, and only a fresh interpreter can promise that.

    THE CONTROL IS THE HALF THAT MAKES THIS A TEST. A meta-path finder that
    silently never fires would pass the first assertion against any build at
    all, so stage 3 is imported the same way and must be refused.
    """
    clean = _import_under_the_ban("astrodeck.cloudmap.occlusion")
    assert clean.returncode == 0, clean.stderr
    assert "imported clean" in clean.stdout

    control = _import_under_the_ban("astrodeck.cloudmap.granule")
    assert control.returncode != 0, control.stdout
    assert "imported h5py" in control.stderr
