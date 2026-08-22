"""When will it be in view, stage 5: measuring the cloud field's motion.

Every test here pins one specific way this module can be plausibly wrong: a
correlation peak converted with the nominal 2 km cell instead of the 2.95 km
one under the site, a row shift read as northward when stage 2 fixes row 0 as
north, a measurement averaged over 600 km of unrelated weather, a wind
corroboration written as a vector difference tight enough to throw away the one
measurement this whole design rests on, a forecast offset the wrong way along
the motion, and a comparison between two fields that cannot disagree.

THE SITE IS 37.3 N, 121.9 W. Design 5 section 3.1 quotes the cell under the
site as 2.95 x 2.21 km; that is this cell -- ACMC (454, 1880) measures
2.9448 x 2.2077 km through stage 2's ``pixel_size_km``, and no other candidate
site in the sector reproduces the design's pair. Elevation is zero: nothing in
the motion measurement reads it, and the forecast tests want a ladder with no
rungs dropped out from under them.

Ground truths -- the cell size, the site's index, the downrange either side of
it -- are computed ONCE from stages 1 and 2 and pinned as literals here. A test
that re-derives its expectation through the code under test moves whenever that
code does, and stops being a test. The pixel shift a test recovers is inverted
back through those PINNED literals, so a build with a broken correlator fails
test 1 and a build with a broken cell size fails tests 4 and 5, separately.

THE SYNTHETIC FIELD IS BROADBAND ON PURPOSE, and this is the fixture detail
that took longest to get right. Phase correlation whitens every frequency to
unit magnitude, so a band-limited synthetic field -- smooth gaussian blobs, the
obvious "looks like clouds" choice -- has an empty high-frequency band that
whitening promotes to full-weight numerical noise. Measured: a gaussian-blob
field rolled by (+3, -5) came back as (0.08, -0.10) with a peak of 0.136, which
reads exactly like a broken correlator. :func:`_clouds` uses a power-law
spectrum instead, which is both what real cloud fields have and what leaves
every frequency carrying signal; the same roll comes back as (3.000, -5.000)
with a peak of 0.987.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from astrodeck.cloudmap.abi_grid import GridSpec
from astrodeck.cloudmap.geometry import Site
from astrodeck.cloudmap.granule import GranuleWindow
from astrodeck.cloudmap.motion import (
    HORIZON_S,
    MAX_SEPARATION_S,
    MIN_PEAK,
    MIN_PEAK_Z,
    MIN_SEPARATION_S,
    MIN_WINDOW_PX,
    WIND_ANGLE_DEG,
    WIND_SPEED_FACTOR,
    Motion,
    WindLevel,
    _phase_shift,
    corroborate,
    estimate_motion,
    forecast_at,
)

# --------------------------------------------------------------- the fixtures

# Stage 2 design section 9.1's 2 km CONUS packing, verbatim, as stage 4's suite
# uses it. One mask spec and not two: stage 5 correlates a product against
# ITSELF an interval later, so the two-grid confusion stage 4 guards against
# cannot arise here. What can arise is correlating two DIFFERENT grids, and
# that is refused rather than measured.
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
#: The 10 km cloud-top packing, for the forecast tests' height window.
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

SITE = Site(37.3, -121.9, 0.0)
#: The site's cell in each grid, from stage 2's ``lonlat_to_index``.
SITE_CELL = (454, 1880)
HEIGHT_CELL = (90, 376)

#: ``pixel_size_km(ACMC, 454, 1880)``. Design 5 section 3.1's 2.95 x 2.21.
NS_KM = 2.944790427532874
EW_KM = 2.2076858729929976
#: The nominal cell the product is named for, and which trap 3.1 is about.
NOMINAL_KM = 2.0

WHEN = datetime(2026, 8, 21, 6, 7, 36, tzinfo=timezone.utc)
#: One GOES CONUS refresh. Every pair below is this far apart unless it says so.
DT_S = 300.0

# The big synthetic field, and the sub-rectangle a window is cut from it. 301
# leaves 10 cells of margin around a 281-cell window, so the wrap seam that
# ``np.roll`` leaves at the field's own edge stays outside every crop the
# correlator sees.
_BIG = 301
_SPAN = 281
_BIG_ROW0 = SITE_CELL[0] - _BIG // 2
_BIG_COL0 = SITE_CELL[1] - _BIG // 2


def _clouds(seed: int, shape: tuple[int, int], beta: float = 1.8) -> np.ndarray:
    """A cloud-like probability field: power-law noise, normalised to 0..1.

    BROADBAND, for the reason the module docstring gives at length. ``beta`` is
    the spectral slope; anything from 1.0 to 3.0 recovers a known roll to
    better than 0.001 cells, so the exact value is not load-bearing and the
    breadth of the spectrum is.
    """
    rng = np.random.default_rng(seed)
    spectrum = np.fft.rfft2(rng.normal(size=shape))
    ky = np.fft.fftfreq(shape[0])[:, None]
    kx = np.fft.rfftfreq(shape[1])[None, :]
    k = np.sqrt(ky * ky + kx * kx)
    k[0, 0] = 1.0  # the DC cell, which the slope would divide by zero
    spectrum *= k ** (-beta / 2.0)
    field = np.fft.irfft2(spectrum, s=shape)
    field -= field.min()
    field /= field.max()
    return field


def _shifted(field: np.ndarray, rows: float, cols: float) -> np.ndarray:
    """``field`` moved by a possibly fractional number of cells, bilinear.

    Integer arguments reduce to a plain ``np.roll``: the zero-weight corners
    drop out. Bilinear rather than a Fourier shift on purpose -- an exact
    Fourier shift hands the correlator back its own phase ramp, and the
    interpolation is what a real resampled field would carry.
    """
    row_int, col_int = int(math.floor(rows)), int(math.floor(cols))
    row_frac, col_frac = rows - row_int, cols - col_int
    out = np.zeros_like(field)
    for d_row, w_row in ((0, 1.0 - row_frac), (1, row_frac)):
        for d_col, w_col in ((0, 1.0 - col_frac), (1, col_frac)):
            if w_row * w_col == 0.0:
                continue
            out = out + w_row * w_col * np.roll(
                field, (row_int + d_row, col_int + d_col), axis=(0, 1)
            )
    return out


def _window(
    field: np.ndarray,
    observed_at: datetime,
    row0: int,
    col0: int,
    *,
    spec: GridSpec = ACMC,
    variables: tuple[str, ...] = ("Cloud_Probabilities",),
) -> GranuleWindow:
    """A probability window at FULL-GRID origin ``(row0, col0)``."""
    data = {"Cloud_Probabilities": field}
    return GranuleWindow(
        spec=spec,
        observed_at=observed_at,
        product="ABI-L2-ACMC",
        platform="G18",
        row0=row0,
        col0=col0,
        data={k: v for k, v in data.items() if k in variables},
    )


def _pair(
    shift: tuple[float, float],
    *,
    dt_s: float = DT_S,
    seed: int = 20260821,
    later_origin: tuple[int, int] = (0, 0),
) -> tuple[GranuleWindow, GranuleWindow]:
    """Two windows whose cloud field moved by ``shift`` cells between them.

    ``later_origin`` displaces the SECOND window's full-grid origin, which is
    what stage 3 hands over whenever two fetches clip differently at a sector
    edge. Both windows still cover the site; a build that cropped them by local
    index instead of by full-grid index would fold that displacement into the
    measured shift and report the fetch's geometry as weather.
    """
    field = _clouds(seed, (_BIG, _BIG))
    moved = _shifted(field, shift[0], shift[1])
    d_row, d_col = later_origin
    earlier = _window(
        field[0:_SPAN, 0:_SPAN].copy(), WHEN, _BIG_ROW0, _BIG_COL0
    )
    later = _window(
        moved[d_row : d_row + _SPAN, d_col : d_col + _SPAN].copy(),
        WHEN + timedelta(seconds=dt_s),
        _BIG_ROW0 + d_row,
        _BIG_COL0 + d_col,
    )
    return earlier, later


def _cells_moved(motion: Motion) -> tuple[float, float]:
    """The cell shift a ``Motion`` was built from, back through PINNED sizes.

    Inverting here rather than asserting kilometres per hour keeps two failures
    apart: a correlator that finds the wrong peak fails test 1, and a build
    that converts a right peak with the wrong cell size fails tests 4 and 5.
    Asserting the speed in test 1 would have both fail the same test and say
    nothing about which.
    """
    hours = motion.dt_s / 3600.0
    return (
        -motion.north_kmh * hours / NS_KM,
        motion.east_kmh * hours / EW_KM,
    )


def _motion(speed_kmh: float, toward_deg: float, **over) -> Motion:
    """A ``Motion`` shaped as ``estimate_motion`` builds them, for section 5.2.

    The corroboration and forecast tests are about arithmetic on a speed and a
    bearing, so they take their input as a speed and a bearing rather than
    running a correlator to manufacture one.
    """
    fields = dict(
        north_kmh=speed_kmh * math.cos(math.radians(toward_deg)),
        east_kmh=speed_kmh * math.sin(math.radians(toward_deg)),
        speed_kmh=speed_kmh,
        toward_deg=toward_deg,
        peak=0.31,
        dt_s=DT_S,
        corroborated=False,
        matched_level=None,
        reason="measured",
    )
    fields.update(over)
    return Motion(**fields)


# A radiosonde column over the site, in the shape design 2 section 2.5 measured
# one: a light southerly flow at the bottom, a jet at 250 hPa. Two levels are
# here to be REJECTED, one on each gate -- 300 hPa shares the bearing and is a
# factor 1.68 out on speed, 200 hPa shares the speed and is 56 degrees off on
# bearing -- so a build that dropped either gate corroborates against the wrong
# level and test 11 catches it.
COLUMN = (
    WindLevel("925 hPa", 0.76, 17.0, 150.0),
    WindLevel("850 hPa", 1.46, 22.0, 170.0),
    WindLevel("700 hPa", 3.01, 35.0, 210.0),
    WindLevel("500 hPa", 5.57, 63.0, 250.0),
    WindLevel("300 hPa", 9.16, 66.0, 12.0),
    WindLevel("250 hPa", 10.36, 105.4, 23.0),
    WindLevel("200 hPa", 11.78, 130.0, 55.0),
)

#: Design 2 section 2.5's own measurement, the one the whole stage rests on.
MEASURED_SPEED_KMH = 111.0
MEASURED_TOWARD_DEG = 359.0


def _array(row0: int, col0: int, shape: tuple[int, int], fill) -> np.ndarray:
    """A window array, ``fill`` evaluated at FULL-GRID (row, col) per cell."""
    if not callable(fill):
        return np.full(shape, float(fill), dtype=np.float64)
    out = np.empty(shape, dtype=np.float64)
    for r in range(shape[0]):
        for c in range(shape[1]):
            out[r, c] = fill(row0 + r, col0 + c)
    return out


def mask_window(
    probability,
    *,
    dqf=0.0,
    cell: tuple[int, int] = SITE_CELL,
    half: int = 80,
) -> GranuleWindow:
    """A stage-4 shaped mask window: ``Cloud_Probabilities`` and ``DQF``."""
    row0, col0 = cell[0] - half, cell[1] - half
    shape = (2 * half + 1, 2 * half + 1)
    return GranuleWindow(
        spec=ACMC,
        observed_at=WHEN,
        product="ABI-L2-ACMC",
        platform="G18",
        row0=row0,
        col0=col0,
        data={
            "Cloud_Probabilities": _array(row0, col0, shape, probability),
            "DQF": _array(row0, col0, shape, dqf),
        },
    )


def height_window(
    top_m, *, cell: tuple[int, int] = HEIGHT_CELL, half: int = 40
) -> GranuleWindow:
    """A cloud-top window. ``HT`` is METRES, as the granule stores it."""
    row0, col0 = cell[0] - half, cell[1] - half
    shape = (2 * half + 1, 2 * half + 1)
    return GranuleWindow(
        spec=ACHAC,
        observed_at=WHEN,
        product="ABI-L2-ACHAC",
        platform="G18",
        row0=row0,
        col0=col0,
        data={"HT": _array(row0, col0, shape, top_m)},
    )


def _coordinate_tokens(*values) -> list[str]:
    """Every rendering a reason string could plausibly carry a coordinate in."""
    tokens = []
    for value in values:
        if value is None:
            continue
        tokens += [format(value, "." + repr(dp) + "f") for dp in (1, 2, 3, 4)]
    return tokens


# ------------------------------------------------------------------ the tests


def test_the_correlator_recovers_a_shift_it_was_given():
    """Design 5 section 6, test 1. Nothing below means anything without it.

    The two windows are given DIFFERENT full-grid origins, 20 cells apart on
    each axis, because that is what stage 3 hands over whenever consecutive
    fetches clip differently at the sector edge. A build that cropped each
    window by its own local index would measure (-17, -25) here: the fetch's
    geometry reported as weather, with a correlation peak just as convincing.

    The lopsided pair is the other half of the same fact. When one window
    reaches 130 cells past the site and the other only 30, the crop has to
    shrink to the shorter side on BOTH, or the cells the Hanning taper actually
    weights sit 50 cells off the site -- and design 2 measures the vector
    changing sign within 400 km of it. Both builds recover this shift, since
    the whole field moved together; the window size in the reason is what tells
    them apart, and it is the operator's only sight of which sky was measured.

    THE LAST PAIR IS TRAP 3.2, and it is the one trap of the three that no
    other test here can catch. A build that thresholds ``Cloud_Probabilities``
    into a binary mask before correlating passes every other test in this file,
    measured -- a rigid translation of a binary field is still a rigid
    translation, sub-pixel term and all. What it cannot survive is a sky whose
    probabilities never reach the threshold: broken thin cirrus reported at
    0.05 to 0.45 across the whole window is structure, it is moving, and a
    thresholded copy of it is a blank field the correlator refuses outright.
    """
    earlier, later = _pair((3, -5), later_origin=(20, 20))

    motion = estimate_motion(earlier, later, SITE)

    assert motion is not None
    di, dj = _cells_moved(motion)
    assert di == pytest.approx(3.0, abs=0.02)
    assert dj == pytest.approx(-5.0, abs=0.02)
    assert motion.dt_s == pytest.approx(DT_S)
    assert motion.peak > 0.9
    # ``estimate_motion`` measures; it does not confirm. Section 5.2 does that.
    assert motion.corroborated is False
    assert motion.matched_level is None

    # Rows: the earlier window reaches 130 cells south of the site, the later
    # only 30 north of it, so the crop is 30 either way. Columns are unclipped
    # and take the full 100. A crop that merely took the overlap would be
    # 131 x 201 and would sit 35 cells south of the site.
    field = _clouds(20260821, (_BIG, _BIG))
    lopsided = estimate_motion(
        _window(field[0:_SPAN, 0:_SPAN].copy(), WHEN, _BIG_ROW0, _BIG_COL0),
        _window(
            _shifted(field, 3, -5)[120:_BIG, :].copy(),
            WHEN + timedelta(seconds=DT_S),
            _BIG_ROW0 + 120,
            _BIG_COL0,
        ),
        SITE,
    )

    assert lopsided is not None
    assert "61 x 201 cell window" in lopsided.reason
    lop_di, lop_dj = _cells_moved(lopsided)
    assert lop_di == pytest.approx(3.0, abs=0.05)
    assert lop_dj == pytest.approx(-5.0, abs=0.05)

    faint_field = field * 0.4 + 0.05
    faint = estimate_motion(
        _window(faint_field[0:_SPAN, 0:_SPAN].copy(), WHEN, _BIG_ROW0, _BIG_COL0),
        _window(
            _shifted(faint_field, 3, -5)[0:_SPAN, 0:_SPAN].copy(),
            WHEN + timedelta(seconds=DT_S),
            _BIG_ROW0,
            _BIG_COL0,
        ),
        SITE,
    )

    assert faint_field.max() < 0.5
    assert faint is not None
    faint_di, faint_dj = _cells_moved(faint)
    assert faint_di == pytest.approx(3.0, abs=0.02)
    assert faint_dj == pytest.approx(-5.0, abs=0.02)
    assert faint.peak > 0.9


def test_sub_pixel_shifts_come_back_fractional():
    """Design 5 section 6, test 2, plus the two fractions it does not name.

    2.5 is the ONE fraction a parabolic fit gets right for free: the phase
    correlation surface of a half-cell shift is symmetric about the midpoint,
    so the three-point fit returns exactly 0.5 by construction and a test
    written only on it would pass a build whose sub-pixel term was pure
    symmetry. Measured here: 2.5 comes back within 0.003 cells while 2.25 and
    -2.75 come back within 0.17, which is the fit's real accuracy against a
    Dirichlet-shaped peak and is what the 0.2 cell tolerance is sized for.
    """
    for rows, cols in ((2.5, 0.0), (2.25, 0.0), (0.0, -2.75)):
        motion = estimate_motion(*_pair((rows, cols)), SITE)

        assert motion is not None, (rows, cols)
        di, dj = _cells_moved(motion)
        assert di == pytest.approx(rows, abs=0.2), (rows, cols)
        assert dj == pytest.approx(cols, abs=0.2), (rows, cols)
        # Not merely near the right integer: the fractional part has to be
        # there at all, or "within 0.2" is satisfied by rounding to it.
        assert abs(di - round(di)) > 0.05 or abs(dj - round(dj)) > 0.05


def test_a_southward_shift_reports_negative_north():
    """Design 5 section 5.1's minus sign, which the design calls the likely bug.

    Stage 2 fixes row 0 as NORTH, so a pattern that moved to higher row indices
    moved SOUTH and ``north_kmh`` must be negative. Both directions are
    checked: dropping the minus sign flips both answers, and a build that had
    it right one way and wrong the other would be a different bug again.
    """
    southward = estimate_motion(*_pair((2, 0)), SITE)
    northward = estimate_motion(*_pair((-2, 0)), SITE)

    assert southward is not None and northward is not None
    assert southward.north_kmh < 0.0
    assert northward.north_kmh > 0.0
    assert southward.north_kmh == pytest.approx(
        -2.0 * NS_KM / DT_S * 3600.0, rel=0.02
    )
    assert southward.toward_deg == pytest.approx(180.0, abs=2.0)
    # Bearing 0 and bearing 360 are the same direction; compare on the circle.
    assert abs((northward.toward_deg + 180.0) % 360.0 - 180.0) < 2.0


def test_pixels_become_km_through_the_real_cell_size_not_the_nominal_one():
    """Design 5 section 3.1: "2 km" is at nadir and nowhere else.

    The shift is NORTH-SOUTH and not diagonal, deliberately. North-south is
    where the error lives -- the cell under this site is 2.94 km tall against
    the nominal 2.0, a 47 percent understatement -- while east-west is only 10
    percent out. A diagonal shift averages the two and clears the design's "at
    least 30 percent" by a tenth of a percentage point, so a test written with
    one would be a single site away from failing for entirely the right reason.
    """
    motion = estimate_motion(*_pair((3, 0)), SITE)

    assert motion is not None
    nominal_kmh = 3.0 * NOMINAL_KM / DT_S * 3600.0
    assert motion.speed_kmh == pytest.approx(
        3.0 * NS_KM / DT_S * 3600.0, rel=0.02
    )
    assert motion.speed_kmh > nominal_kmh * 1.30
    assert motion.speed_kmh == pytest.approx(nominal_kmh * 1.472, rel=0.02)


def test_north_and_east_use_their_own_cell_dimensions():
    """Design 5 section 3.1's other half: the two axes differ by a third.

    Equal cell counts on the two axes are not equal distances. A build that
    took one ``pixel_size_km`` component for both -- or the mean of them --
    returns the same speed for these two, and is wrong by 33 percent on one
    axis or by 15 percent on both.
    """
    northward = estimate_motion(*_pair((-4, 0)), SITE)
    eastward = estimate_motion(*_pair((0, 4)), SITE)

    assert northward is not None and eastward is not None
    assert northward.north_kmh > 0.0
    assert eastward.east_kmh > 0.0
    ratio = northward.speed_kmh / eastward.speed_kmh
    assert ratio == pytest.approx(NS_KM / EW_KM, rel=0.01)
    assert ratio == pytest.approx(2.95 / 2.21, rel=0.01)


def test_a_flat_field_returns_none_not_a_zero_vector():
    """Design 5 section 4: a zero vector is a claim about the sky.

    Two pairs with nothing to lock onto, and they fail differently. The flat
    pair has no structure at all and its whitened correlation is identically
    zero. The independent pair has plenty of structure and none of it shared,
    which is the case that actually arrives: a sky whose pattern turned over
    between granules. Both must come back ``None``. A build returning
    ``Motion(0.0, 0.0, ...)`` would have stage 6 draw a stationary deck over a
    sky it cannot see, and nothing downstream could tell that from a calm night.
    """
    flat = np.full((_SPAN, _SPAN), 0.42)
    still = estimate_motion(
        _window(flat, WHEN, _BIG_ROW0, _BIG_COL0),
        _window(
            flat.copy(), WHEN + timedelta(seconds=DT_S), _BIG_ROW0, _BIG_COL0
        ),
        SITE,
    )

    unrelated = estimate_motion(
        _window(_clouds(1, (_SPAN, _SPAN)), WHEN, _BIG_ROW0, _BIG_COL0),
        _window(
            _clouds(2, (_SPAN, _SPAN)),
            WHEN + timedelta(seconds=DT_S),
            _BIG_ROW0,
            _BIG_COL0,
        ),
        SITE,
    )

    assert still is None
    assert unrelated is None


def test_a_pair_too_close_together_in_time_is_refused():
    """Design 5 section 4's lower separation bound, and that it is inclusive.

    A minute apart, a 100 km/h field has moved a third of a cell and the
    sub-pixel term is the whole answer. The refusal is on the SEPARATION, not
    on the correlation: this pair correlates at better than 0.98 at every
    separation, so a build that only checked the peak would report a speed
    from a shift it cannot resolve.
    """
    too_close = estimate_motion(*_pair((3, -5), dt_s=60.0), SITE)
    on_the_bound = estimate_motion(
        *_pair((3, -5), dt_s=MIN_SEPARATION_S), SITE
    )

    assert too_close is None
    assert on_the_bound is not None
    assert on_the_bound.dt_s == pytest.approx(MIN_SEPARATION_S)


def test_a_pair_too_far_apart_in_time_is_refused():
    """Design 5 section 4's upper bound against design 2's 40-minute null.

    Consecutive 5-minute pairs correlate at 0.27 to 0.34; the direct 40-minute
    pair correlates at 0.088 and returns a null shift. The pattern does not
    survive it. The synthetic pair here correlates perfectly at any separation
    -- a rolled field never decorrelates -- which is exactly why the bound has
    to be on the clock and cannot be left to the peak.
    """
    too_far = estimate_motion(*_pair((3, -5), dt_s=2400.0), SITE)
    on_the_bound = estimate_motion(
        *_pair((3, -5), dt_s=MAX_SEPARATION_S), SITE
    )

    assert too_far is None
    assert on_the_bound is not None
    assert on_the_bound.dt_s == pytest.approx(MAX_SEPARATION_S)


def test_nan_becomes_zero_and_does_not_poison_the_fft():
    """Stage 3 hands back NaN wherever the granule held its fill value.

    One NaN anywhere in an FFT input makes every output NaN, ``argmax`` then
    returns index 0, and the module reports a shift of half the window with a
    NaN peak -- which, since ``NaN >= MIN_PEAK`` is False, comes back as
    ``None`` rather than as an error. The failure is silent, so the assertion
    is that the vector is finite AND still right.
    """
    earlier, later = _pair((3, -5))
    earlier.data["Cloud_Probabilities"][50:62, 50:62] = math.nan
    later.data["Cloud_Probabilities"][120:130, 30:42] = math.nan
    # The infinities are the module's sixth stated departure and NaN alone does
    # not keep it: ``np.where(np.isnan(a), 0.0, a)`` passes a NaN-only fixture
    # and leaves an infinity to poison the whole transform exactly as one NaN
    # would. A probability cannot be infinite -- stage 3 builds it as
    # ``raw * scale + offset`` -- so this is the defensive arm being exercised
    # deliberately, not a granule anybody will meet.
    earlier.data["Cloud_Probabilities"][70:74, 70:74] = math.inf
    later.data["Cloud_Probabilities"][200:204, 90:94] = -math.inf

    motion = estimate_motion(earlier, later, SITE)

    assert motion is not None
    for value in (
        motion.north_kmh,
        motion.east_kmh,
        motion.speed_kmh,
        motion.toward_deg,
        motion.peak,
    ):
        assert math.isfinite(value)
    di, dj = _cells_moved(motion)
    assert di == pytest.approx(3.0, abs=0.05)
    assert dj == pytest.approx(-5.0, abs=0.05)


def test_the_measurement_is_local_to_the_window():
    """Design 2 section 2.5: the vector varies across the sector.

    -3.07 cells at the site, -0.04 cells 400 km south, +0.89 over the ocean. A
    global estimate is an average of unrelated weather. Here the middle 71 x 71
    cells move by (+4, -2) and everything around them by (-6, +7); the window
    centred on the site with ``half_px=25`` must report the site's motion, and
    the one reaching 140 cells out must not -- it reports the surroundings,
    which is the failure the locality exists to avoid.
    """
    field = _clouds(20260821, (_BIG, _BIG))
    rows, cols = np.ogrid[0:_BIG, 0:_BIG]
    middle = (np.abs(rows - _BIG // 2) <= 35) & (np.abs(cols - _BIG // 2) <= 35)
    later_field = np.where(
        middle, _shifted(field, 4, -2), _shifted(field, -6, 7)
    )

    earlier = _window(
        field[10 : 10 + _SPAN, 10 : 10 + _SPAN].copy(),
        WHEN,
        _BIG_ROW0 + 10,
        _BIG_COL0 + 10,
    )
    later = _window(
        later_field[10 : 10 + _SPAN, 10 : 10 + _SPAN].copy(),
        WHEN + timedelta(seconds=DT_S),
        _BIG_ROW0 + 10,
        _BIG_COL0 + 10,
    )

    local = estimate_motion(earlier, later, SITE, half_px=25)
    wide = estimate_motion(earlier, later, SITE, half_px=140)

    assert local is not None and wide is not None
    near = _cells_moved(local)
    assert near[0] == pytest.approx(4.0, abs=0.05)
    assert near[1] == pytest.approx(-2.0, abs=0.05)
    far = _cells_moved(wide)
    assert far[0] == pytest.approx(-6.0, abs=0.05)
    assert far[1] == pytest.approx(7.0, abs=0.05)


def test_a_measurement_matching_the_jet_is_corroborated():
    """Design 5 section 5.2 on design 2's own numbers.

    111.0 km/h toward 359 against the measured column: 250 hPa carries
    105.4 km/h toward 23, which agrees on speed to 5 percent and on bearing to
    24 degrees. Two neighbouring levels are in the column to be turned away,
    one on each gate, so a build that dropped either gate matches the wrong
    level here rather than merely being more generous.
    """
    motion = _motion(MEASURED_SPEED_KMH, MEASURED_TOWARD_DEG)

    confirmed = corroborate(motion, COLUMN)

    assert confirmed.corroborated is True
    assert confirmed.matched_level == "250 hPa"
    assert "250 hPa" in confirmed.reason
    # The measurement itself is untouched: corroboration is a verdict about a
    # vector, not a correction to one.
    assert confirmed.north_kmh == pytest.approx(motion.north_kmh)
    assert confirmed.east_kmh == pytest.approx(motion.east_kmh)
    assert confirmed.speed_kmh == pytest.approx(motion.speed_kmh)
    assert confirmed.peak == pytest.approx(motion.peak)


def test_a_vector_difference_test_would_have_rejected_it():
    """Design 5 section 5.2's reasoning, pinned so nobody simplifies it back.

    The obvious implementation of "does the measurement resemble the wind" is
    the distance between the two vectors, and it throws away the measurement
    this entire design rests on. 111.0 km/h toward 359 sits 45.3 km/h from
    250 hPa -- its NEAREST level, and further still from every other -- while
    agreeing on speed to 5 percent. Any vector-difference threshold tight
    enough to reject nonsense rejects this too.

    The distances are computed here from the column's own numbers, not from
    anything the module exposes, so this test still says what it says when the
    module never computes a vector difference at all -- which it must not.
    """
    motion = _motion(MEASURED_SPEED_KMH, MEASURED_TOWARD_DEG)

    distances = {}
    for level in COLUMN:
        distances[level.label] = math.hypot(
            motion.north_kmh
            - level.speed_kmh * math.cos(math.radians(level.toward_deg)),
            motion.east_kmh
            - level.speed_kmh * math.sin(math.radians(level.toward_deg)),
        )
    nearest = min(distances, key=lambda label: distances[label])

    assert nearest == "250 hPa"
    assert distances["250 hPa"] == pytest.approx(45.3, abs=0.1)
    assert min(distances.values()) > 40.0
    # And the ratio-and-angle test passes it anyway. That is the whole point.
    assert corroborate(motion, COLUMN).corroborated is True


def test_nonsense_is_not_corroborated():
    """Both gates, each shown refusing on its own.

    400 km/h toward 359 shares the jet's BEARING to a degree and is a factor
    3.1 faster than the fastest level in the column: only the speed gate turns
    it away. 111 km/h toward 185 against a column that is northerly at every
    level carries a speed every one of those levels would accept and a bearing
    none of them would: only the angle gate turns that away. A build missing
    either gate corroborates one of these.
    """
    too_fast = corroborate(_motion(400.0, MEASURED_TOWARD_DEG), COLUMN)

    northerly = (
        WindLevel("300 hPa", 9.16, 100.0, 5.0),
        WindLevel("250 hPa", 10.36, 105.4, 23.0),
        WindLevel("200 hPa", 11.78, 115.0, 350.0),
    )
    backwards = corroborate(_motion(111.0, 185.0), northerly)

    assert too_fast.corroborated is False
    assert too_fast.matched_level is None
    assert "no level" in too_fast.reason
    assert backwards.corroborated is False
    assert backwards.matched_level is None
    assert "no level" in backwards.reason
    # Every level in the northerly column would have passed on speed alone,
    # which is what makes this case about the angle gate and nothing else.
    for level in northerly:
        ratio = 111.0 / level.speed_kmh
        assert 1.0 / WIND_SPEED_FACTOR <= ratio <= WIND_SPEED_FACTOR
        assert (
            abs((185.0 - level.toward_deg + 180.0) % 360.0 - 180.0)
            > WIND_ANGLE_DEG
        )


def test_an_empty_column_is_not_a_refutation():
    """Design 5 section 5.2: no column is not a column disagreeing.

    A sonde that did not launch, a forecast fetch that failed, a site with no
    model column at all -- none of those is evidence about the sky. The
    measurement comes back intact, ``corroborated`` is False because nothing
    confirmed it, and the reason says which of the two it is so stage 6 can
    draw "unconfirmed" rather than "contradicted".
    """
    motion = _motion(MEASURED_SPEED_KMH, MEASURED_TOWARD_DEG)

    unconfirmed = corroborate(motion, ())

    assert unconfirmed.corroborated is False
    assert unconfirmed.matched_level is None
    assert "no wind column" in unconfirmed.reason
    assert "unconfirmed" in unconfirmed.reason
    assert unconfirmed.north_kmh == pytest.approx(motion.north_kmh)
    assert unconfirmed.east_kmh == pytest.approx(motion.east_kmh)
    assert unconfirmed.speed_kmh == pytest.approx(motion.speed_kmh)
    assert unconfirmed.toward_deg == pytest.approx(motion.toward_deg)
    assert unconfirmed.peak == pytest.approx(motion.peak)

    # AND THE DISTINCTION HAS TO SURVIVE INTO THE FORECAST, which is where an
    # operator actually reads it. Built from ``corroborated`` alone the clause
    # says "no wind level corroborates the motion" for both of these -- an
    # assertion that a column was consulted and disagreed, which is the one
    # thing an empty column does not mean. ``column_consulted`` is what tells
    # them apart without matching on prose.
    consulted = corroborate(_motion(400.0, MEASURED_TOWARD_DEG), COLUMN)
    assert unconfirmed.column_consulted is False
    assert consulted.column_consulted is True
    assert consulted.corroborated is False

    mask = mask_window(0.90)
    height = height_window(4000.0)
    no_column = forecast_at(SITE, 90.0, 0.0, mask, height, unconfirmed, 900.0)
    no_match = forecast_at(SITE, 90.0, 0.0, mask, height, consulted, 900.0)

    assert no_column.basis == "forecast" and no_match.basis == "forecast"
    # The empty-column forecast must not claim a level was looked at.
    assert "no wind column was available" in no_column.reason
    assert "unconfirmed rather than contradicted" in no_column.reason
    assert "no wind level corroborates" not in no_column.reason
    # The consulted one must, because there it is true.
    assert "no wind level corroborates" in no_match.reason
    assert "no wind column was available" not in no_match.reason
    # And the corroborated third state still names its level, so all three
    # branches of the clause are pinned by this test rather than one of them.
    matched = corroborate(_motion(MEASURED_SPEED_KMH, MEASURED_TOWARD_DEG), COLUMN)
    confirmed = forecast_at(SITE, 90.0, 0.0, mask, height, matched, 900.0)
    assert "matches the 250 hPa wind" in confirmed.reason


def test_the_forecast_offsets_backward_along_the_motion():
    """Design 5 section 5.3, and the sign that makes it worth having.

    Straight up, a 4.0 km deck, and a mask that is clear over the site and
    cloudy from three cells north -- about 9 km, at 2.94 km a cell. The cloud
    is moving SOUTH at 60 km/h, so in 600 s it covers exactly the 10 km between
    there and here. The forecast must read the mask 10 km UPWIND, which is
    north, and report the cloud.

    Offsetting the other way reads 10 km downwind and reports the clear sky the
    cloud has already left, so the mirror case is here too: the same geometry
    with the cloud moving north must come back clear. A build with the sign
    inverted passes neither.
    """
    mask = mask_window(
        lambda row, col: 0.90 if row <= SITE_CELL[0] - 3 else 0.04
    )
    height = height_window(4000.0)
    southward = _motion(60.0, 180.0, corroborated=True, matched_level="700 hPa")
    northward = _motion(60.0, 0.0, corroborated=True, matched_level="700 hPa")

    now = forecast_at(SITE, 90.0, 0.0, mask, height, southward, 0.0)
    ahead = forecast_at(SITE, 90.0, 0.0, mask, height, southward, 600.0)
    wrong_way = forecast_at(SITE, 90.0, 0.0, mask, height, northward, 600.0)

    assert now.probability == pytest.approx(0.04)
    assert ahead.basis == "forecast"
    assert ahead.probability == pytest.approx(0.90)
    assert wrong_way.probability == pytest.approx(0.04)
    # The sampled cell is genuinely north of the site, not merely a different
    # number: 10 km at this cell size is 3.4 rows, and row 0 is north.
    assert ahead.pierce_lat_deg > SITE.lat_deg
    assert ahead.downrange_km == pytest.approx(9.99, abs=0.05)
    assert ahead.crossing_km == pytest.approx(4.0, abs=0.2)
    assert "10 min" in ahead.reason
    assert "700 hPa" in ahead.reason
    # The reason lands in the operator journal, which keeps positions out: a
    # point within 30 km of the observatory locates the observatory.
    for token in _coordinate_tokens(
        SITE.lat_deg, SITE.lon_deg, ahead.pierce_lat_deg, ahead.pierce_lon_deg
    ):
        assert token not in ahead.reason, token


def test_beyond_the_horizon_the_forecast_is_withheld():
    """Design 5 section 4: 30 minutes, and past it there is nothing to say.

    Design 2 measures the pattern gone by 40 minutes -- 0.088 correlation, a
    null shift. A 40-minute forecast is not a worse forecast, it is a different
    sky, so the answer is ``no_data`` with a sentence rather than an
    extrapolation stage 6 has no way to tell from a measurement.
    """
    mask = mask_window(0.90)
    height = height_window(4000.0)
    motion = _motion(60.0, 180.0)

    beyond = forecast_at(SITE, 90.0, 0.0, mask, height, motion, 2400.0)
    on_the_bound = forecast_at(SITE, 90.0, 0.0, mask, height, motion, HORIZON_S)

    assert beyond.basis == "no_data"
    assert beyond.probability is None
    assert "ahead_s" in beyond.reason
    assert "1800" in beyond.reason
    assert on_the_bound.basis == "forecast"
    assert on_the_bound.probability is not None


def test_a_negative_lead_time_is_refused():
    """A lead time before now, and the three ways there is nothing to forecast.

    ``-600`` is a caller subtracting where it meant to add, and left to run it
    would offset the cloud FORWARD: a plausible number for the sky the beam has
    already seen. NaN has to be refused by the SHAPE of the comparison rather
    than by the comparison itself -- ``ahead_s < 0.0`` and
    ``ahead_s > HORIZON_S`` are both False for NaN, so the literal form of
    design 5 section 5.3 lets it through to produce a NaN sample point, and
    stage 2 then raises "cannot convert float NaN to integer" two modules away,
    naming nothing. The negated form catches it here, where the sentence can
    name the parameter.

    A missing motion is the third way, and it is not an error at all:
    ``estimate_motion`` returning ``None`` is the ordinary outcome of a sky
    whose pattern turned over between granules.
    """
    mask = mask_window(0.90)
    height = height_window(4000.0)
    motion = _motion(60.0, 180.0)

    for bad in (-1.0, -600.0, math.nan):
        refused = forecast_at(SITE, 90.0, 0.0, mask, height, motion, bad)
        assert refused.basis == "no_data", bad
        assert refused.probability is None, bad
        assert "ahead_s" in refused.reason, bad

    unmeasured = forecast_at(SITE, 90.0, 0.0, mask, height, None, 600.0)
    assert unmeasured.basis == "no_data"
    assert unmeasured.probability is None
    assert "motion" in unmeasured.reason


def test_no_test_here_correlates_a_field_with_its_own_complement():
    """Design 5 section 3.3, in executable form. A live hazard, not a theory.

    While checking whether height bands shared a signal, design 2's author
    correlated the CLEAR mask against the ANY-CLOUD mask and got "identical"
    back no matter what the sky was doing. This is why: mean-subtracting a
    field and mean-subtracting its complement give the same array with its sign
    flipped, so complementing BOTH frames of a pair leaves the phase
    correlation bit for bit where it was. Comparing the two measures nothing.

    DESIGN 5 SECTION 6 TEST 18 STATES THE HAZARD ONE STEP WRONG, and the
    correction is the useful half of this test. It asks for ``p`` against
    ``1 - p`` to return what ``p`` against ``p`` returns; it does not, because
    there the sign flip lands on the correlation itself and the whitened
    surface becomes a NEGATIVE delta -- peak 0.0 against 1.0, and the pair is
    refused outright. Pinned in both forms so the correction is not quietly
    reverted to the claim that does not hold.
    """
    earlier, later = _pair((3, -5))
    straight = estimate_motion(earlier, later, SITE)

    field = earlier.data["Cloud_Probabilities"]
    flipped = estimate_motion(
        _window(1.0 - field, earlier.observed_at, earlier.row0, earlier.col0),
        _window(
            1.0 - later.data["Cloud_Probabilities"],
            later.observed_at,
            later.row0,
            later.col0,
        ),
        SITE,
    )

    assert straight is not None and flipped is not None
    assert flipped.north_kmh == pytest.approx(straight.north_kmh, rel=1e-9)
    assert flipped.east_kmh == pytest.approx(straight.east_kmh, rel=1e-9)
    assert flipped.peak == pytest.approx(straight.peak, rel=1e-9)

    self_complement = estimate_motion(
        earlier,
        _window(1.0 - field, later.observed_at, earlier.row0, earlier.col0),
        SITE,
    )
    assert self_complement is None
    assert straight.peak > MIN_PEAK


# ------------------------------------- the invariants design 5 section 6 skips
#
# Section 7 lists six invariants, section 6 lists eighteen tests, and the two
# lists do not cover each other. Each test below pins something a single-line
# change to ``motion.py`` altered while all eighteen still passed -- measured
# one change at a time, not reasoned about.


def test_two_windows_on_different_grids_are_refused_by_name():
    """A cell of the 2 km packing is not a cell of the 10 km one.

    The same place is (454, 1880) of ACMC and (90, 376) of ACHAC, so a
    rectangle taken at matching indices out of the two is a rectangle of two
    different places, and the displacement between them is in no unit at all.
    It comes back as a number with a peak on it and nothing downstream can
    tell. Deleting the check leaves every other test here passing, because
    every other test correlates a product against itself.

    PINNED ON THE MESSAGE, NOT ON ``ValueError``. Three guards in this module
    raise that type and stage 2's ``pixel_size_km`` raises it two modules away
    for a cell off the grid, so a build with this check gone still raises
    ValueError from over there for some inputs -- the type alone cannot tell a
    working guard from a missing one.
    """
    earlier = _window(_clouds(11, (_SPAN, _SPAN)), WHEN, _BIG_ROW0, _BIG_COL0)
    later = _window(
        _clouds(11, (61, 61)),
        WHEN + timedelta(seconds=DT_S),
        HEIGHT_CELL[0] - 30,
        HEIGHT_CELL[1] - 30,
        spec=ACHAC,
    )

    with pytest.raises(ValueError, match="different grids"):
        estimate_motion(earlier, later, SITE)


def test_a_window_carrying_no_probability_field_is_none_not_a_crash():
    """Design 5 section 4's third ``None``, which section 6 lists no test for.

    Stage 3 puts in the dict only what the granule held, so a fetch that lost
    ``Cloud_Probabilities`` hands over a window without it. That is a fact
    about the fetch and not about the sky, which is why it joins the other two
    unmeasurable pairs at ``None`` rather than raising: a caller already has to
    handle ``None`` and would otherwise also have to handle a ``KeyError``
    escaping from the crop, which nothing told it about.

    BOTH WINDOWS ARE TRIED. A check that lost half of itself -- and the two
    halves are one ``or`` apart -- still passes a test that only empties one.
    """
    field = _clouds(12, (_SPAN, _SPAN))
    full_earlier = _window(field, WHEN, _BIG_ROW0, _BIG_COL0)
    full_later = _window(
        _shifted(field, 3, -5),
        WHEN + timedelta(seconds=DT_S),
        _BIG_ROW0,
        _BIG_COL0,
    )
    empty_earlier = _window(field, WHEN, _BIG_ROW0, _BIG_COL0, variables=())
    empty_later = _window(
        field,
        WHEN + timedelta(seconds=DT_S),
        _BIG_ROW0,
        _BIG_COL0,
        variables=(),
    )

    assert estimate_motion(empty_earlier, full_later, SITE) is None
    assert estimate_motion(full_earlier, empty_later, SITE) is None
    # And the same pair with the field present is measurable, so the ``None``
    # above is the missing variable and not some other refusal.
    assert estimate_motion(full_earlier, full_later, SITE) is not None


def test_a_window_too_small_to_correlate_is_refused_by_the_right_name():
    """Five refusals that are all ``ValueError`` and mean five things.

    A ``half_px`` under the floor is the CALLER asking for a window with no
    structure in it, and a negative one is the caller asking for a window that
    does not contain the site at all. An overlap under the floor is the FETCH
    falling short. Two windows that miss the site is the fetch pointing
    somewhere else. A site behind the limb is a place no granule of any product
    holds, and a site outside the sector is a place THIS product never sampled.
    The operator's next move differs for each -- fix the call, widen the fetch,
    check the site -- so each has to say which it is.

    A build that dropped the ``half_px`` check and let those two fall through
    to the crop still raises, and still raises ``ValueError``, and tells the
    caller its fetch fell short when the fetch was fine. Matching on the type
    cannot see that; matching on the message can, which is the whole reason
    these are written with ``match``.

    THE LAST TWO WERE KEPT BY NOTHING and they fail differently when deleted.
    Without the limb check the site falls into ``in_grid`` as ``None`` and dies
    on a ``TypeError`` one line later, an exception no caller was told about.
    Without the sector check the crop falls through and says the two windows do
    not both cover the site -- a sentence about the FETCH, when the truth is
    that the sector never sampled the place. Both are the "check the site" arm
    of the paragraph above, and it was the arm with no test behind it.
    """
    earlier, later = _pair((3, -5))

    with pytest.raises(ValueError, match="half_px"):
        estimate_motion(earlier, later, SITE, half_px=3)
    with pytest.raises(ValueError, match="half_px"):
        estimate_motion(earlier, later, SITE, half_px=-5)

    # A fetch that genuinely fell short: 11 cells of overlap around the site.
    small = _clouds(13, (11, 11))
    with pytest.raises(ValueError, match=repr(MIN_WINDOW_PX) + " cell minimum"):
        estimate_motion(
            _window(small, WHEN, SITE_CELL[0] - 5, SITE_CELL[1] - 5),
            _window(
                small.copy(),
                WHEN + timedelta(seconds=DT_S),
                SITE_CELL[0] - 5,
                SITE_CELL[1] - 5,
            ),
            SITE,
        )

    # A fetch of somewhere else entirely.
    elsewhere = _clouds(14, (41, 41))
    with pytest.raises(ValueError, match="do not both cover the site"):
        estimate_motion(
            _window(elsewhere, WHEN, SITE_CELL[0] + 200, SITE_CELL[1] + 200),
            _window(
                elsewhere.copy(),
                WHEN + timedelta(seconds=DT_S),
                SITE_CELL[0] + 200,
                SITE_CELL[1] + 200,
            ),
            SITE,
        )

    # A site GOES-18 cannot see at all: 40 N 20 E is over Europe, and the
    # satellite sits at 137 W. Stage 2 answers ``None`` rather than an index.
    with pytest.raises(ValueError, match="behind the satellite"):
        estimate_motion(earlier, later, Site(40.0, 20.0, 0.0))

    # And a site the satellite sees perfectly well that this CONUS sector never
    # sampled: 30 S 70 W is on the visible disk, so stage 2 hands back a real
    # index, and it is off the end of a 1500 x 2500 array.
    with pytest.raises(ValueError, match="outside this product"):
        estimate_motion(earlier, later, Site(-30.0, -70.0, 0.0))


def test_the_measurement_reason_names_no_position():
    """The same rule stage 3 and stage 4 keep, kept here on the way out.

    ``reason`` lands in the operator journal, and a point within 30 km of the
    observatory locates the observatory. Test 15 keeps coordinates out of the
    FORECAST's sentence; nothing kept them out of the measurement's, and the
    obvious thing to add to it while debugging a crop is the site cell -- which
    is a position in disguise. ``lonlat_to_index`` is a bijection on this grid,
    so (454, 1880) is 37.3 N, 121.9 W with two extra steps.

    A cell DISPLACEMENT is not a position and is expected in the sentence; the
    assertions below are on the site's own index and on its latitude and
    longitude at every rendering they could plausibly be written at.
    """
    motion = estimate_motion(*_pair((3, -5)), SITE)

    assert motion is not None
    sentences = [
        motion.reason,
        corroborate(motion, COLUMN).reason,
        corroborate(motion, ()).reason,
        corroborate(_motion(400.0, MEASURED_TOWARD_DEG), COLUMN).reason,
    ]
    for sentence in sentences:
        for token in _coordinate_tokens(SITE.lat_deg, SITE.lon_deg):
            assert token not in sentence, (token, sentence)
        for index in SITE_CELL:
            assert repr(index) not in sentence, (index, sentence)
    # The displacement itself is still there: this test must not be satisfied
    # by a build that stopped saying anything.
    assert "rows" in motion.reason and "columns" in motion.reason


def test_the_speed_of_a_diagonal_measurement_is_its_hypotenuse():
    """Every other speed assertion here is on a pure north or pure east shift.

    On those the hypotenuse, the sum of the two components and the larger of
    them are the same number, so a build that added them passes tests 4 and 5
    and reports this 170 km/h field as 238. That number is not cosmetic: it is
    the whole input to :func:`corroborate`'s speed gate, it is the displacement
    the forecast reason quotes, and it is what the operator reads as a wind
    speed.
    """
    motion = estimate_motion(*_pair((3, -5)), SITE)

    assert motion is not None
    assert motion.north_kmh < 0.0 and motion.east_kmh < 0.0
    assert motion.speed_kmh == pytest.approx(
        math.hypot(3.0 * NS_KM, 5.0 * EW_KM) / DT_S * 3600.0, rel=0.02
    )
    assert motion.speed_kmh == pytest.approx(
        math.hypot(motion.north_kmh, motion.east_kmh)
    )
    # 238.5 is what the sum of the components would give, and it is 41 percent
    # high; the tolerance above is 2 percent, so this is not a near miss.
    summed = abs(motion.north_kmh) + abs(motion.east_kmh)
    assert motion.speed_kmh < summed - 60.0
    # South and west, so the bearing is in the third quadrant and not merely
    # some number the components could also produce swapped.
    assert motion.toward_deg == pytest.approx(231.3, abs=1.0)


def test_a_measurement_too_slow_for_every_level_is_not_corroborated():
    """The speed gate has two sides and :data:`COLUMN` exercises only one.

    300 hPa sits in the column to be turned away for being 1.68 times slower
    than design 2's measurement; nothing in the column is fast enough to make
    the other bound do any work. So a one-sided gate -- ``ratio <= 1.6`` alone,
    which reads as the obvious simplification -- passes every corroboration
    test here.

    What it lets through is the failure design 2 actually recorded: the
    40-minute pair correlated at 0.088 and returned A NULL SHIFT. A near-null
    measurement is a few tens of km/h pointed anywhere, and against a 105 km/h
    jet a one-sided gate calls that a match -- a sky the operator is told is
    confirmed by a wind that is nowhere near it.
    """
    crawling = _motion(40.0, 23.0)

    verdict = corroborate(crawling, COLUMN)

    assert verdict.corroborated is False
    assert verdict.matched_level is None
    assert "no level" in verdict.reason
    # The three fastest levels agree with this bearing to well inside the angle
    # gate -- 250 hPa to the degree -- so only the LOWER half of the speed gate
    # is doing anything here.
    for level in COLUMN[-3:]:
        angle_deg = abs(
            (crawling.toward_deg - level.toward_deg + 180.0) % 360.0 - 180.0
        )
        assert angle_deg <= WIND_ANGLE_DEG, level.label
        assert crawling.speed_kmh / level.speed_kmh < 1.0 / WIND_SPEED_FACTOR


def test_the_level_reported_is_the_closest_match_and_not_the_first():
    """Which level matched changes no verdict; it changes what is shown.

    A column arrives in whatever order its source lists it -- surface upward,
    pressure downward, or a model's own -- so a rule that stops at the first
    level passing both gates makes the level the operator reads an artifact of
    that ordering. Here two levels pass: one is a factor 1.59 out on speed
    against a gate of 1.6, the other agrees to 5 percent. The near miss is
    listed first, and reversing the column must not change the answer.
    """
    motion = _motion(MEASURED_SPEED_KMH, MEASURED_TOWARD_DEG)
    barely = WindLevel("500 hPa", 5.57, 70.0, 20.0)
    squarely = WindLevel("250 hPa", 10.36, 105.4, 23.0)

    forwards = corroborate(motion, (barely, squarely))
    backwards = corroborate(motion, (squarely, barely))

    assert forwards.corroborated is True and backwards.corroborated is True
    assert forwards.matched_level == "250 hPa"
    assert backwards.matched_level == "250 hPa"
    # Both really do pass both gates, or this would be testing the gates again
    # rather than the choice between two matches.
    for level in (barely, squarely):
        ratio = motion.speed_kmh / level.speed_kmh
        assert 1.0 / WIND_SPEED_FACTOR <= ratio <= WIND_SPEED_FACTOR
        assert (
            abs((motion.toward_deg - level.toward_deg + 180.0) % 360.0 - 180.0)
            <= WIND_ANGLE_DEG
        )


def test_a_look_direction_bug_is_not_answered_with_a_forecast():
    """Stage 4's ordering rule, which the lead-time refusal must not jump.

    An altitude of 0 is a caller bug -- no mount points there -- and it stays a
    raised ValueError even when the lead time is wrong as well. A build that
    reached the ``ahead_s`` refusal first answers that bug with a serene
    "no data" for every direction in a dome, which is exactly what stage 4's
    ``_require_altitude`` docstring says it exists to prevent.

    Matched on the message rather than the type, because ``forecast_at`` raises
    ValueError for a non-finite azimuth too and stage 2 raises it from two
    modules away for a cell off the grid: the type would be satisfied by the
    wrong guard firing.
    """
    mask = mask_window(0.90)
    height = height_window(4000.0)
    motion = _motion(60.0, 180.0)

    for bad_ahead in (2400.0, -600.0, math.nan):
        with pytest.raises(ValueError, match="alt_deg"):
            forecast_at(SITE, 0.0, 0.0, mask, height, motion, bad_ahead)
    with pytest.raises(ValueError, match="az_deg"):
        forecast_at(SITE, 90.0, math.nan, mask, height, motion, 2400.0)
    # And a good look direction with the same bad lead times still answers,
    # rather than raising, so the ordering above is what is being pinned.
    assert (
        forecast_at(SITE, 90.0, 0.0, mask, height, motion, 2400.0).basis
        == "no_data"
    )


def test_unrelated_weather_in_a_small_window_is_not_a_measurement():
    """:data:`MIN_PEAK` is an absolute gate against a floor that MOVES.

    A whitened correlation surface of ``N`` cells has an extreme value near
    ``sqrt(2 ln N) / sqrt(N)``, so the height a peak must reach before it means
    anything FALLS as the window grows. Measured with the fixture below, twelve
    unrelated pairs at each size: at 201 x 201 the peak runs 0.025 to 0.030 and
    not one of the twelve clears :data:`MIN_PEAK`; at 17 x 17 it runs 0.150 to
    0.279 and all twelve do. Same correlator, same gate, opposite verdicts, and
    the only thing that changed is how much sky was cropped.

    That is why this module used to answer two granules sharing no weather at
    all with a confident velocity at every window the API allowed: the floor
    was 16 cells. The refusal existed and was set exactly where it does not
    fire.

    TWO THINGS KEEP IT SHUT AND BOTH ARE PINNED HERE. :data:`MIN_WINDOW_PX`
    refuses the sizes where no statistic separates the two populations -- at
    design 2's own correlation strength a real pair's z runs 2.9 to 4.0 at
    17 x 17 while unrelated pairs reach 4.6, so there is nothing there to
    separate. :data:`MIN_PEAK_Z`, the same peak measured in standard deviations
    of its own surface, refuses what survives at the floor itself.

    THE PAIR BELOW IS THE ONE THAT REACHES THE SECOND GATE, and it is rare on
    purpose: of 3000 unrelated 51 x 51 pairs built from this fixture, four
    clear :data:`MIN_PEAK`. This one peaks at 0.154 and would report 134 km/h
    toward 269 -- an entirely ordinary jet, out of two fields with nothing in
    common. Its peak stands 7.9 sd above its own surface where a real pair at
    this size stands at least 10.4, so the normalised gate is the only thing
    between it and a ``Motion``.
    """
    unrelated_a = _clouds(1547, (MIN_WINDOW_PX, MIN_WINDOW_PX))
    unrelated_b = _clouds(11547, (MIN_WINDOW_PX, MIN_WINDOW_PX))
    origin = (
        SITE_CELL[0] - MIN_WINDOW_PX // 2,
        SITE_CELL[1] - MIN_WINDOW_PX // 2,
    )

    _, _, peak, peak_z = _phase_shift(unrelated_a, unrelated_b)

    # The absolute gate would have let this through. That is the finding.
    assert peak >= MIN_PEAK
    assert peak_z < MIN_PEAK_Z
    refused = estimate_motion(
        _window(unrelated_a, WHEN, *origin),
        _window(unrelated_b, WHEN + timedelta(seconds=DT_S), *origin),
        SITE,
    )
    assert refused is None
    # The crop really was the floor exactly, so this is the smallest window the
    # API permits and not some smaller one refused on its size.
    assert unrelated_a.shape == (MIN_WINDOW_PX, MIN_WINDOW_PX)

    # The floor measurement that justifies MIN_WINDOW_PX, taken at the size it
    # is set for rather than at the default where the gate happens to work.
    small, default = [], []
    for seed in range(300, 312):
        small.append(
            _phase_shift(_clouds(seed, (17, 17)), _clouds(seed + 500, (17, 17)))[2]
        )
        default.append(
            _phase_shift(
                _clouds(seed, (201, 201)), _clouds(seed + 500, (201, 201))
            )[2]
        )
    assert min(small) >= MIN_PEAK, small
    assert max(default) < MIN_PEAK, default
    # Not a near miss in either direction: the two populations are an order of
    # magnitude apart and MIN_PEAK falls between them purely by window size.
    assert min(small) > 4.0 * max(default)

    # A fetch that small is refused on its SIZE and never reaches a peak at
    # all, which is the outer of the two defences.
    with pytest.raises(ValueError, match=repr(MIN_WINDOW_PX) + " cell minimum"):
        estimate_motion(
            _window(_clouds(16, (17, 17)), WHEN, SITE_CELL[0] - 8, SITE_CELL[1] - 8),
            _window(
                _clouds(17, (17, 17)),
                WHEN + timedelta(seconds=DT_S),
                SITE_CELL[0] - 8,
                SITE_CELL[1] - 8,
            ),
            SITE,
        )


def test_a_calm_level_is_skipped_rather_than_divided_by():
    """A sonde reporting 0 kt at the surface on a still night is ordinary input.

    :func:`corroborate` divides the measured speed by each level's, so a calm
    level is a ``ZeroDivisionError`` unless it is skipped -- and the skip was
    kept by nothing, because every level in :data:`COLUMN` is moving. The
    column here is design 2's jet with a dead-calm surface level in front of
    it: the verdict has to be the jet, which it cannot be if the loop dies on
    the first entry.

    An all-calm column is the other half. Calm air has no bearing to compare
    against, so it corroborates nothing -- but it is a column that WAS
    consulted, unlike an absent one, and the measurement still comes back
    whole.
    """
    motion = _motion(MEASURED_SPEED_KMH, MEASURED_TOWARD_DEG)
    with_a_calm_level = (
        WindLevel("surface", 0.0, 0.0, 0.0),
        WindLevel("250 hPa", 10.36, 105.4, 23.0),
    )

    verdict = corroborate(motion, with_a_calm_level)

    assert verdict.corroborated is True
    assert verdict.matched_level == "250 hPa"

    all_calm = corroborate(
        motion,
        (
            WindLevel("surface", 0.0, 0.0, 0.0),
            WindLevel("925 hPa", 0.76, 0.0, 90.0),
        ),
    )

    assert all_calm.corroborated is False
    assert all_calm.matched_level is None
    assert "calm air" in all_calm.reason
    # A column of calm levels IS a column, so this is not the empty-column
    # sentence and stage 6 must not draw it as one.
    assert all_calm.column_consulted is True
    assert "no wind column" not in all_calm.reason
    assert all_calm.speed_kmh == pytest.approx(motion.speed_kmh)


def test_the_correlator_centre_is_the_fftshift_origin_at_both_parities():
    """``rows // 2`` against ``(rows - 1) // 2``, which no public call separates.

    ``_crop_bounds`` returns half-extents and every crop
    :func:`estimate_motion` builds is ``2 * half + 1``, so every window that
    reaches :func:`_phase_shift` through the API is ODD -- and for odd ``n``
    the two conventions are the same number. The module docstring claims the
    zero shift sits at ``rows // 2`` FOR BOTH PARITIES; nothing kept that
    claim, and it is the correct one, because ``np.fft.fftshift`` puts index 0
    at ``n // 2`` whether ``n`` is odd or even.

    On an even window the two differ by a full cell on EACH axis -- measured,
    64 x 64 gives (+2.994, -4.997) here against (+1.994, -5.997) under the
    other reading. At 300 s and this site's cell size that is a 35 km/h error
    north-south and 26 east-west, both pointed the wrong way. Hence a direct
    test on the helper: it is the only thing that can tell them apart.
    """
    for size in (64, 128):
        field = _clouds(31, (size, size))

        di, dj, peak, peak_z = _phase_shift(
            field, np.roll(field, (3, -5), axis=(0, 1))
        )

        assert di == pytest.approx(3.0, abs=0.02), size
        assert dj == pytest.approx(-5.0, abs=0.02), size
        assert peak > 0.8 and peak_z > MIN_PEAK_Z, size
        # Say what this test would have read under the other convention, rather
        # than only what it did read: one whole cell short on each axis.
        assert size // 2 - (size - 1) // 2 == 1

    # And the odd case agrees with both, which is why the API cannot see this.
    odd = _clouds(31, (65, 65))
    odd_di, odd_dj, _, _ = _phase_shift(odd, np.roll(odd, (3, -5), axis=(0, 1)))
    assert odd_di == pytest.approx(3.0, abs=0.02)
    assert odd_dj == pytest.approx(-5.0, abs=0.02)
    assert 65 // 2 == (65 - 1) // 2


def test_a_peak_that_is_not_a_number_is_refused():
    """The peak gate is negated so a NaN lands on the refusal.

    ``Cloud_Probabilities`` cannot hold 1e307 and this test does not pretend it
    can; that magnitude is how a NaN correlation surface is reached from
    outside the module at all, since ``_finite`` scrubs the NaNs and infinities
    a granule can actually carry. What is being pinned is the SHAPE of the
    comparison, because the difference the two shapes make is neither a raise
    nor a ``None``.

    ``peak < MIN_PEAK`` is False for a NaN, so that build goes on to convert a
    surface of NaNs: ``argmax`` returns index 0, the parabola declines a
    non-finite fit and contributes nothing, and the displacement comes out as
    exactly half the window -- a FINITE 3500 km/h with a NaN peak beside it.
    Everything downstream reads a number. The negated form refuses it.
    """
    absurd = _clouds(15, (_SPAN, _SPAN)) * 1e307 + 1e307
    earlier = _window(absurd, WHEN, _BIG_ROW0, _BIG_COL0)
    later = _window(
        np.roll(absurd, (3, -5), axis=(0, 1)),
        WHEN + timedelta(seconds=DT_S),
        _BIG_ROW0,
        _BIG_COL0,
    )

    with np.errstate(over="ignore", invalid="ignore"):
        motion = estimate_motion(earlier, later, SITE)
        di, dj, peak, peak_z = _phase_shift(
            earlier.data["Cloud_Probabilities"], later.data["Cloud_Probabilities"]
        )

    assert motion is None
    # BOTH GATES REFUSE THIS, AND THAT IS WORTH STATING rather than leaving as
    # a coincidence. The peak really is NaN, so the negated comparison on it is
    # doing what this test was written for. The z is 0.0 and not NaN -- ``std``
    # is NaN whenever any cell is, and the spread guard turns that into 0.0 --
    # so :data:`MIN_PEAK_Z` independently refuses the same pair. Either line
    # alone is sufficient here, which means neither can be sabotaged into
    # failing this test on its own; what is pinned is that the pair is refused
    # and that the displacement below is what would otherwise have been sold as
    # a measurement.
    assert peak != peak
    assert peak_z == 0.0
    assert math.isfinite(di) and math.isfinite(dj)
    assert abs(di) == pytest.approx(_SPAN // 2) and abs(dj) == pytest.approx(_SPAN // 2)
