"""When will it be in view (stage 5) -- how the cloud field is moving.

No network, no file, no clock, no config, no h5py. Two stage-3
:class:`~astrodeck.cloudmap.granule.GranuleWindow`s of the same product an
interval apart, a site, and a wind column handed in as data; a velocity out,
and stage 4's answer displaced along it. Pure ``math`` plus numpy for the FFT.

WHAT THIS MODULE MAY AND MAY NOT CLAIM, settled against live data in design 2
section 2.5 and repeated here because each one forbids a plausible build:

- Motion is real and measurable. 111.0 km/h from the 2 km probability field
  against 112.9 km/h from the 10 km height field: two algorithms, two grids,
  2.3 km/h apart over nine consecutive pairs.
- ONE vector, measured LOCALLY. Per-height-band tracking gives 126 km/h for
  cloud below 2 km against a 17 km/h wind there, so :class:`WindLevel` carries
  a height for the operator to read and nothing here selects a level by it.
  And the vector varies across the sector -- -3.07 cells at the site, -0.04
  cells 400 km south, +0.89 over the ocean -- so a sector-wide estimate is an
  average of unrelated weather.
- The horizon is thirty minutes. Consecutive 5-minute pairs correlate at 0.27
  to 0.34; the direct 40-minute pair correlates at 0.088 and returns a null
  shift. Past :data:`HORIZON_S` there is no forecast to give.

A ZERO VECTOR IS A CLAIM ABOUT THE SKY AND ``None`` IS THE ABSENCE OF ONE.
:func:`estimate_motion` returns ``None`` for a pair it cannot measure, never
``Motion(0.0, 0.0, ...)``, because stage 6 draws the two differently: a still
deck against a sky nobody could read. That is also why an uncorroborated
measurement is returned rather than dropped -- :func:`corroborate` says whether
the wind agrees, and "nothing confirmed it" is not "the wind contradicts it".

THREE TRAPS, each paid for once already during the work that produced design 2:

- THE NOMINAL CELL IS WRONG BY A THIRD. "2 km" is at nadir; under the site the
  cell is 2.95 x 2.21 km, and converting a correlation peak with 2.0
  understated a measured speed by 33 percent. Every displacement here becomes
  kilometres through stage 2's :func:`~astrodeck.cloudmap.abi_grid.pixel_size_km`
  at the window's own centre cell, north-south and east-west SEPARATELY,
  because those two differ from each other by 33 percent as well.
- CORRELATE THE CONTINUOUS FIELD, NOT A THRESHOLDED MASK. Sub-pixel
  interpolation on a binary field is biased toward integer shifts, and a binary
  field has less structure for the correlator to lock onto. Only
  ``Cloud_Probabilities`` is read.
- A COMPARISON BETWEEN COMPLEMENTS CANNOT FAIL. Mean-subtracting a field and
  mean-subtracting its complement give the same array with its sign flipped, so
  a pair and its complemented pair have the same phase correlation to the last
  bit. Design 2's author compared the clear mask against the any-cloud mask and
  got "identical" back whatever the sky was doing. A named test in the suite
  keeps that hazard executable.

``reason`` NAMES NO COORDINATE, for the reason stage 4 gives: it is written for
a human and it lands in the same journal stage 3's ``read_window`` deliberately
keeps latitudes out of, and a point within 30 km of the observatory locates the
observatory. A cell DISPLACEMENT is not a position and appears freely; a cell
INDEX is a position in disguise and does not.

NINE DEPARTURES FROM AND ADDITIONS TO DESIGN 5, each argued at the code that
makes it:

- The crop is symmetric about the site cell even when a window clips; see
  :func:`_crop_bounds`.
- ``ahead_s`` is bounded by a negated comparison so NaN cannot pass; see
  :func:`forecast_at`.
- A matched level is the CLOSEST match rather than the first in the column; see
  :func:`corroborate`.
- Two mismatched grids raise rather than returning ``None``; see
  :func:`estimate_motion`.
- A crop under :data:`MIN_WINDOW_PX` cells raises rather than answering with
  noise; see the constant.
- The infinities are scrubbed alongside NaN; see :func:`_finite`.
- :func:`forecast_at` checks the look direction BEFORE the lead time, so a
  caller bug is not answered with a picture; see its docstring.
- The peak is gated a SECOND time against its own surface, because design 5
  section 4's :data:`MIN_PEAK` is an absolute number against a noise floor that
  moves with the crop size; see :data:`MIN_PEAK_Z`.
- :class:`Motion` carries a third corroboration state, because
  ``corroborated=False`` alone cannot say whether a wind column was ever
  consulted; see the dataclass.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Iterable

import numpy as np

from .abi_grid import in_grid, lonlat_to_index, pixel_size_km
from .geometry import EARTH_RADIUS_KM, GeoPoint, Site

# STAGE 4'S PRIVATES, IMPORTED DELIBERATELY, for the reason stage 4 gives when
# it imports stage 2's ``_great_circle_km``: a second copy of any of these would
# eventually disagree with the one the present-time answer is built from, and
# then "now" and "in 15 minutes" would be two different pictures of the same
# sky for reasons no reader could see. ``_answer`` is the whole mask lookup --
# the fill check, the cell size, the shortfall sentences, the beam width --
# ``_ladder_km`` and ``_fallback_height_km`` are how stage 4 decides which layer
# a mask-only answer was read at, and ``_no_data`` is its one shape for knowing
# nothing. Promoting any of them to a public name means editing stage 4, which
# stage 5 is not allowed to touch.
from .occlusion import (
    MASK_PROBABILITY,
    Occlusion,
    _answer,
    _fallback_height_km,
    _ladder_km,
    _no_data,
    _require_altitude,
    occlusion_at,
)

# Stage 3's dataclass under TYPE_CHECKING, which is what keeps the first line of
# this docstring true. Stage 4 measured the cost: an ordinary import of
# ``granule`` drags in its guarded ``import h5py`` and the numpy under it. Stage
# 5 needs numpy anyway, but it does not need h5py, and a module that never opens
# a file should not require the optional extra to import.
if TYPE_CHECKING:
    from .granule import GranuleWindow

__all__ = [
    "HORIZON_S",
    "MIN_PEAK",
    "MIN_SEPARATION_S",
    "MAX_SEPARATION_S",
    "WIND_SPEED_FACTOR",
    "WIND_ANGLE_DEG",
    "MIN_PEAK_Z",
    "MIN_WINDOW_PX",
    "Motion",
    "WindLevel",
    "estimate_motion",
    "corroborate",
    "forecast_at",
]

#: Thirty minutes. Beyond it the pattern design 2 measured is gone, so no
#: forecast is offered rather than one being extrapolated.
#:
#: WHAT THIS BOUND IS AND IS NOT. Design 2's 40-minute pair correlating at
#: 0.088 says the CORRELATOR stops finding a shift; it does not say that
#: advecting the field predicts the sky, and those are two different
#: quantities. Decorrelation bounds where a MEASUREMENT is possible, which is
#: what this constant is derived from and all it is entitled to claim. Whether
#: a forecast at a given lead beats simply saying "the same as now" is a SKILL
#: question, it is scene-dependent -- a stationary marine layer advected inland
#: by the single vector this module measures never arrives, which is the
#: one-vector limitation design 2 accepted, surfacing at long leads -- and
#: nothing here measures it. Design 5 section 8 already gives stage 6 the
#: calibration duty; scoring each lead against the granule that actually
#: arrived is that duty, and until it is done the honest reading of 1800 is
#: "the longest lead the measurement supports", not "the longest lead worth
#: giving".
HORIZON_S: float = 1800.0
#: Below this the correlation is noise AT THE DEFAULT WINDOW, and only there.
#: A whitened correlation surface of ``N`` cells has an extreme value near
#: ``sqrt(2 ln N) / sqrt(N)``, so the floor this number is set against FALLS as
#: the crop grows and an absolute gate stops being a gate at small ones.
#: Measured over 120 unrelated broadband pairs per size: median peak 0.028 at
#: 201 x 201, 0.090 at 51 x 51, 0.213 at 17 x 17. A real consecutive pair peaks
#: at 0.27 to 0.34 whatever the size. :data:`MIN_PEAK_Z` is the half of the
#: gate that does not move, and :data:`MIN_WINDOW_PX` keeps the crop where both
#: halves work.
#:
#: THE JUSTIFICATION THAT USED TO BE HERE WAS FALSE and design 5 section 4
#: inherits it. It read "two unrelated fields of a few hundred cells peak
#: around 0.03". A 17 x 17 crop IS a few hundred cells -- 289 of them -- and
#: they peak at a median of 0.213, eight times that. The 0.03 was measured at
#: 40401 cells and written down as though it were a property of the
#: correlator rather than of the window it was measured in.
MIN_PEAK: float = 0.15
#: Two granules closer together than this cannot resolve motion: at 100 km/h a
#: minute is a third of a cell and the sub-pixel term would be the whole answer.
MIN_SEPARATION_S: float = 120.0
#: Beyond twenty minutes the pattern has moved on. Both bounds are INCLUSIVE.
MAX_SEPARATION_S: float = 1200.0
#: The measured speed must sit within this factor of some level's speed...
WIND_SPEED_FACTOR: float = 1.6
#: ...and within this angle of the same level's bearing. Separately -- see
#: :func:`corroborate` for why not a vector difference.
WIND_ANGLE_DEG: float = 50.0

#: How far the peak must stand above its OWN correlation surface, in standard
#: deviations of that surface. ADDITION TO DESIGN 5 SECTION 4, and the half of
#: the gate :data:`MIN_PEAK` cannot be: it is measured against the surface the
#: peak came from, so it does not move when the crop does.
#:
#: WHAT IT PREVENTS, measured rather than argued. With :data:`MIN_PEAK` alone
#: and the 16 cell floor this module shipped with, two granules sharing no
#: weather at all came back with a confident velocity at every window size the
#: API allowed below about 41 cells: of 120 unrelated pairs, 99 percent were
#: accepted as a measurement at 17 x 17, 85 percent at 21 x 21 and 71 percent
#: at 25 x 25. The refusal existed, and it was set exactly where it does not
#: fire.
#:
#: 8.0 SITS BETWEEN THE TWO POPULATIONS at every size this module now permits,
#: with margin at both ends. At 51 x 51, 120 pairs each: unrelated peaks reach
#: z 6.5 at worst, while a pair correlated no better than design 2's real
#: 5-minute pairs -- peak 0.29, the bottom of its measured 0.27 to 0.34 band --
#: never falls below 10.4. At the default 201 x 201 a real pair runs above 50
#: and this gate never engages, which is the point: it is there for the crops
#: :data:`MIN_PEAK` has stopped covering.
MIN_PEAK_Z: float = 8.0

#: The smallest crop the correlator will work on, cells, on each axis. ADDITION
#: TO DESIGN 5 SECTION 4. A handful of cells has no structure to lock onto and
#: its Hanning taper is nearly all taper, so the answer would be noise with a
#: plausible peak on it rather than a refusal. This is a fetch that fell short,
#: which is why it raises rather than returning ``None``.
#:
#: 51 AND NOT 16, and that difference is most of what makes the gate a gate.
#: Below about 41 cells NO statistic separates a real pair from an unrelated
#: one, because there is not enough of either to tell apart: at design 2's own
#: correlation strength a real pair's z runs 2.9 to 4.0 at 17 x 17 and 25 x 25
#: while unrelated pairs reach 4.6 to 6.5, and the two populations lie on top
#: of one another. Raising :data:`MIN_PEAK` instead cannot work, because it is
#: the unrelated pairs that peak HIGHER at those sizes. The only honest answer
#: at 25 cells is to refuse the size.
#:
#: IT CANNOT GO HIGHER EITHER. Design 5 section 6 test 10 measures locality
#: with ``half_px=25``, which is a 51 cell crop exactly, so 51 is at once the
#: smallest window whose answer can be trusted and the largest floor the design
#: permits. That the two coincide is luck, and worth knowing about before
#: anyone widens this.
MIN_WINDOW_PX: int = 51


@dataclass(frozen=True)
class Motion:
    """How the cloud field is moving over the site, and whether to believe it.

    ``north_kmh`` and ``east_kmh`` are the components; ``speed_kmh`` and
    ``toward_deg`` the same vector as a magnitude and a bearing, 0 = north,
    90 = east, in the direction the cloud is GOING (not the meteorological
    convention, which names where wind comes FROM -- these are 180 degrees
    apart and both are plausible readings of a number in a UI).

    ``peak`` is the phase correlation strength at the shift that was chosen,
    0 to 1. It is the honesty field: 0.98 is a synthetic test, 0.30 is a real
    consecutive pair, and anything under :data:`MIN_PEAK` never became a
    ``Motion`` at all.

    ``corroborated`` and ``matched_level`` are :func:`corroborate`'s verdict and
    are False and ``None`` on a fresh measurement -- absence of confirmation,
    not a refutation.

    ``column_consulted`` IS THE THIRD STATE, and it is an ADDITION TO DESIGN 5
    SECTION 4's field list, appended so the specified fields keep their
    specified order. Without it ``corroborated=False`` means two different
    things -- no wind column was available, or one was and no level in it
    matched -- and :func:`corroborate` writes two different sentences for them
    precisely because stage 6 draws "unconfirmed" and "contradicted"
    differently. That distinction survived only in prose, so
    :func:`forecast_at` rebuilt its own clause from the boolean and told the
    operator "no wind level corroborates the motion" for a sonde that never
    launched. A caller should not have to match on a string to tell the two
    apart, and now does not.
    """

    north_kmh: float
    east_kmh: float
    speed_kmh: float
    toward_deg: float
    peak: float
    dt_s: float
    corroborated: bool
    matched_level: str | None
    reason: str
    column_consulted: bool = False


@dataclass(frozen=True)
class WindLevel:
    """One level of a wind column: where it is, how fast, and which way.

    ``toward_deg`` is the direction the air is GOING, matching
    :attr:`Motion.toward_deg`. A column transcribed from a sounding, which
    reports the direction wind comes FROM, must be turned around before it gets
    here -- a column entered backwards corroborates nothing and looks exactly
    like a sky the wind disagrees with.

    ``height_km`` IS FOR THE OPERATOR TO READ AND NOT FOR THE MATCH. Design 2
    section 2.5 tracked cloud by height band and got 126 km/h for cloud below
    2 km against a 17 km/h wind there; choosing a level by the cloud's height
    is precisely the thing that does not work. :func:`corroborate` asks whether
    the measurement resembles the wind at SOME level and never at a chosen one.
    """

    label: str
    height_km: float
    speed_kmh: float
    toward_deg: float


def _bearing_deg(north: float, east: float) -> float:
    """Compass bearing of a vector given north and east components, 0..360."""
    return math.degrees(math.atan2(east, north)) % 360.0


def _angle_between_deg(a_deg: float, b_deg: float) -> float:
    """Smaller angle between two bearings, 0..180.

    Wrapped, so 359 and 23 are 24 degrees apart and not 336. The unwrapped
    subtraction is the bug this exists to prevent, and it fires on exactly the
    measurement design 2 rests on.
    """
    return abs((a_deg - b_deg + 180.0) % 360.0 - 180.0)


def _finite(values: "np.ndarray") -> "np.ndarray":
    """A copy with NaN and the infinities replaced by 0.0.

    Stage 3 returns NaN wherever the granule held its fill, and ONE of them
    poisons an entire FFT: every output is NaN, ``argmax`` returns index 0, and
    the module reports a shift of half the window with a NaN peak -- which,
    since ``NaN >= MIN_PEAK`` is False, comes back as ``None``. The failure is
    silent either way, so it is removed at the door.

    The infinities are swept up with them, which design 5 section 5.1 does not
    ask for. A probability cannot be infinite, but if one ever were it would
    poison the transform in the same way and for the same reason, and
    ``isfinite`` costs no more than ``isnan``.
    """
    array = np.asarray(values, dtype=np.float64)
    return np.where(np.isfinite(array), array, 0.0)


def _parabolic(low: float, mid: float, high: float) -> float:
    """Sub-cell offset of a peak from a three-point parabola through it.

    Returns 0.0 rather than raising where the fit says nothing: three equal
    samples divide by zero, and a fit that lands outside the cell it is fitting
    is describing some other peak. Clamped rather than discarded at exactly
    +/- 0.5, which is where a genuine half-cell shift lands -- the correlation
    surface of one is symmetric about the midpoint and the fit returns the
    bound exactly.

    The denominator is at most zero at a true maximum, so a positive one means
    the caller did not hand this the argmax.
    """
    denominator = low - 2.0 * mid + high
    if denominator == 0.0:
        return 0.0
    offset = 0.5 * (low - high) / denominator
    if not math.isfinite(offset):
        return 0.0
    return max(-0.5, min(0.5, offset))


def _phase_shift(
    earlier: "np.ndarray", later: "np.ndarray"
) -> tuple[float, float, float, float]:
    """Cell displacement from ``earlier`` to ``later``, the peak, and its z.

    Design 5 section 5.1, exactly: mean-subtract, taper, cross-spectrum,
    whiten, invert, take the argmax, refine it with a parabola on each axis.

    THE TAPER IS NOT COSMETIC. Without it the discontinuity at the frame edge
    is the strongest feature in the transform and the correlation locks onto
    the frame rather than onto the sky.

    THE WHITENING IS WHAT MAKES THE PEAK SHARP AND ALSO WHAT MAKES A SYNTHETIC
    TEST FIELD DELICATE. Every frequency is normalised to unit magnitude, so a
    band-limited field's empty high-frequency band becomes full-weight
    numerical noise. Real cloud fields are broadband and this is not a problem
    for them; a test fixture built from smooth blobs returns 0.08 cells and a
    peak of 0.136 for a known 3-cell roll, which reads exactly like a broken
    correlator and is not one.

    ``fftshift`` puts the zero shift at ``rows // 2, cols // 2`` for both
    parities, so the displacement is the argmax minus that, and it can be
    negative. The parabola is skipped on the array border, where there is no
    neighbour on one side; a peak out there is not a shift anybody should
    refine, it is a correlation that found nothing.

    THE PARITY CLAIM IS TESTED DIRECTLY AND HAS TO BE. ``(rows - 1) // 2`` is
    the other plausible centre and it agrees with this one on every crop
    :func:`estimate_motion` can build, because ``_crop_bounds`` returns
    half-extents and ``2 * half + 1`` is always odd. On an EVEN crop the two
    differ by a full cell on each axis -- measured, a 64 x 64 pair rolled by
    (+3, -5) gives (+3.000, -4.997) here and (+2.000, -5.997) under the other
    convention. Only a test that hands this function an even array can tell
    them apart, so there is one.

    THE PEAK ALONE DOES NOT SAY WHETHER IT IS A PEAK, which is why there is a
    fourth return. Its height is a function of how many cells went into the
    transform -- a whitened surface of ``N`` cells has an extreme value near
    ``sqrt(2 ln N) / sqrt(N)`` -- so 0.21 is noise in a 17 cell crop and a
    strong lock in a 201 cell one. ``z`` is the peak's height above the mean of
    its own surface, in standard deviations of that surface, and it is what
    :data:`MIN_PEAK_Z` gates on. A surface with no spread at all -- two flat
    fields, whose whitened cross is identically zero -- yields 0.0 rather than
    dividing by zero. That pair is already refused on :data:`MIN_PEAK`, and the
    explicit 0.0 keeps a RuntimeWarning out of a run that is answering
    correctly.
    """
    rows, cols = earlier.shape
    centred_a = earlier - earlier.mean()
    centred_b = later - later.mean()
    taper = np.outer(np.hanning(rows), np.hanning(cols))

    cross = np.conj(np.fft.rfft2(centred_a * taper)) * np.fft.rfft2(
        centred_b * taper
    )
    # Phase only. The 1e-12 is what keeps a frequency both frames are silent at
    # -- the DC cell always, since both were mean-subtracted -- from dividing by
    # zero; it leaves that frequency contributing nothing instead of a NaN.
    cross /= np.abs(cross) + 1e-12
    surface = np.fft.fftshift(np.fft.irfft2(cross, s=earlier.shape))

    row, col = np.unravel_index(int(np.argmax(surface)), surface.shape)
    peak = float(surface[row, col])

    sub_row = 0.0
    if 0 < row < rows - 1:
        sub_row = _parabolic(
            float(surface[row - 1, col]),
            peak,
            float(surface[row + 1, col]),
        )
    sub_col = 0.0
    if 0 < col < cols - 1:
        sub_col = _parabolic(
            float(surface[row, col - 1]),
            peak,
            float(surface[row, col + 1]),
        )

    spread = float(surface.std())
    z = (peak - float(surface.mean())) / spread if spread > 0.0 else 0.0

    return (
        (row - rows // 2) + sub_row,
        (col - cols // 2) + sub_col,
        peak,
        z,
    )


def _covered(window: GranuleWindow, name: str) -> tuple[int, int, int, int]:
    """The full-grid rectangle a window's array covers, inclusive on all sides."""
    array = window.data[name]
    return (
        window.row0,
        window.row0 + array.shape[0] - 1,
        window.col0,
        window.col0 + array.shape[1] - 1,
    )


def _crop_bounds(
    earlier: GranuleWindow,
    later: GranuleWindow,
    centre: tuple[int, int],
    half_px: int,
) -> tuple[int, int]:
    """Half-extents of the crop, in rows and columns, SYMMETRIC about ``centre``.

    Both windows are cropped to the SAME full-grid rectangle, which is the
    detail the whole measurement rests on: ``row0`` and ``col0`` differ between
    two fetches whenever they clipped differently at the sector edge, and
    cropping each window by its own local index would fold that difference into
    the measured shift. The fetch's geometry would come back as weather, with a
    correlation peak just as convincing as a real one.

    DEPARTURE FROM DESIGN 5 SECTION 5.1, which asks only that the crops be "the
    same shape, centred on the site cell" and does not say what to do when a
    window reaches further one way than the other. Taking the whole overlap
    would put the Hanning taper's peak -- the cells the estimate actually
    weights -- off the site by however much the clipping was asymmetric, and
    design 2 measures the vector changing sign within 400 km. Shrinking to the
    largest symmetric box instead is what makes "local to the site" true of the
    code rather than of the fetch.
    """
    row_lo, row_hi, col_lo, col_hi = _covered(earlier, MASK_PROBABILITY)
    other = _covered(later, MASK_PROBABILITY)
    row_lo = max(row_lo, other[0], 0)
    row_hi = min(row_hi, other[1], earlier.spec.n_rows - 1)
    col_lo = max(col_lo, other[2], 0)
    col_hi = min(col_hi, other[3], earlier.spec.n_cols - 1)

    half_rows = min(centre[0] - row_lo, row_hi - centre[0], half_px)
    half_cols = min(centre[1] - col_lo, col_hi - centre[1], half_px)
    if half_rows < 0 or half_cols < 0:
        raise ValueError(
            "the two windows do not both cover the site cell, so there is no "
            "field over the site to correlate"
        )
    if 2 * half_rows + 1 < MIN_WINDOW_PX or 2 * half_cols + 1 < MIN_WINDOW_PX:
        raise ValueError(
            "the overlap centred on the site is "
            + repr(2 * half_rows + 1)
            + " by "
            + repr(2 * half_cols + 1)
            + " cells, under the "
            + repr(MIN_WINDOW_PX)
            + " cell minimum on each axis: a crop that small has no structure "
            "to correlate and its taper is nearly all taper, so it would "
            "answer with noise rather than refuse. Fetch a wider window"
        )
    return half_rows, half_cols


def _crop(
    window: GranuleWindow,
    row_lo: int,
    row_hi: int,
    col_lo: int,
    col_hi: int,
) -> "np.ndarray":
    """A full-grid rectangle out of one window, NaN and infinities zeroed."""
    array = window.data[MASK_PROBABILITY]
    return _finite(
        array[
            row_lo - window.row0 : row_hi - window.row0 + 1,
            col_lo - window.col0 : col_hi - window.col0 + 1,
        ]
    )


def estimate_motion(
    earlier: GranuleWindow,
    later: GranuleWindow,
    site: Site,
    *,
    half_px: int = 100,
) -> Motion | None:
    """How the cloud field moved between two granules, or ``None``.

    ``None`` -- never a zero vector -- for a pair that cannot be measured:
    either window carrying no ``Cloud_Probabilities``, a separation outside
    ``[MIN_SEPARATION_S, MAX_SEPARATION_S]``, or a correlation peak below
    :data:`MIN_PEAK`. Those are three different facts about the sky and the
    fetch, and design 5 section 4 folds them into one answer deliberately:
    a caller can only do one thing with any of them, which is not draw a
    forecast.

    THREE THINGS RAISE INSTEAD, and the line between them and ``None`` is
    whether the caller made a mistake. A negative ``half_px``, two windows on
    different grids, and an overlap too small to correlate are all "you asked
    for the wrong thing" and would otherwise be reported as an unreadable sky.
    Refusing two different grids is a DEPARTURE FROM DESIGN 5 SECTION 4, which
    lists only three ``None`` cases and no raising ones. The same place is cell
    (454, 1880) of the 2 km packing and cell (90, 376) of the 10 km one, so a
    rectangle taken at matching indices out of the two is a rectangle of two
    different places, and a displacement measured between them is in no unit at
    all. It comes back as a number, and nothing downstream could tell.

    The separation test is written negated so a NaN separation -- which
    ``observed_at`` cannot produce but a hand-built window can -- lands on the
    refusal rather than passing both comparisons.
    """
    if not 2 * half_px + 1 >= MIN_WINDOW_PX:
        raise ValueError(
            "half_px must reach at least "
            + repr(MIN_WINDOW_PX // 2)
            + " cells, for a window of "
            + repr(MIN_WINDOW_PX)
            + " on each axis; got "
            + repr(half_px)
            + ". Refused by name here rather than left to the crop, so a "
            "caller asking for too small a window is not told its fetch fell "
            "short -- and so a negative one does not come back as a window "
            "that does not contain the site"
        )
    if earlier.spec != later.spec:
        raise ValueError(
            "the two windows are on different grids, "
            + repr(earlier.spec.n_rows)
            + "x"
            + repr(earlier.spec.n_cols)
            + " against "
            + repr(later.spec.n_rows)
            + "x"
            + repr(later.spec.n_cols)
            + ": a cell of one is not a cell of the other, so a displacement "
            "measured between them is in no unit at all"
        )
    if (
        MASK_PROBABILITY not in earlier.data
        or MASK_PROBABILITY not in later.data
    ):
        return None

    dt_s = (later.observed_at - earlier.observed_at).total_seconds()
    if not MIN_SEPARATION_S <= dt_s <= MAX_SEPARATION_S:
        return None

    spec = earlier.spec
    centre = lonlat_to_index(spec, site.lat_deg, site.lon_deg)
    if centre is None:
        raise ValueError(
            "the site is behind the satellite's limb, so no granule of this "
            "product holds the field over it"
        )
    if not in_grid(spec, centre[0], centre[1]):
        raise ValueError(
            "the site is outside this product's "
            + repr(spec.n_rows)
            + "x"
            + repr(spec.n_cols)
            + " sector, so the field over it was never sampled"
        )

    half_rows, half_cols = _crop_bounds(earlier, later, centre, half_px)
    row_lo, row_hi = centre[0] - half_rows, centre[0] + half_rows
    col_lo, col_hi = centre[1] - half_cols, centre[1] + half_cols
    di, dj, peak, peak_z = _phase_shift(
        _crop(earlier, row_lo, row_hi, col_lo, col_hi),
        _crop(later, row_lo, row_hi, col_lo, col_hi),
    )
    # Negated, so a NaN peak refuses rather than passing a comparison written
    # the other way. THE NaN COMES FROM OVERFLOW IN THE TRANSFORM and not, as
    # this comment used to claim, from a field of nothing but fill: ``_finite``
    # turns the fill into 0.0 at the door, and a window of nothing but fill
    # measures a peak of exactly 0.0. A reader who checked the old
    # justification would have found it false and deleted the guard.
    if not peak >= MIN_PEAK:
        return None
    # And the peak has to BE a peak. MIN_PEAK is an absolute number against a
    # noise floor that falls as the crop grows, so below about 41 cells it
    # stops gating at all -- 99 percent of unrelated pairs cleared it at
    # 17 x 17. This asks the same question in units of the surface the peak
    # came from, which is the same question at every size.
    #
    # NOT negated for a NaN reason, and saying so because the line above is.
    # ``peak_z`` cannot be NaN: ``std`` is NaN whenever any cell of the surface
    # is, and ``_phase_shift``'s ``spread > 0.0`` is then False, so the z of a
    # poisoned surface is 0.0. Which means this line refuses the NaN peak too,
    # and the negation on the comparison above is now belt and braces rather
    # than the only thing standing between a NaN and a 3500 km/h Motion. Both
    # are kept deliberately; a reader checking either one should find it true.
    if not peak_z >= MIN_PEAK_Z:
        return None

    # THE ONE PLACE CELLS BECOME KILOMETRES, and the two axes are converted
    # separately because under this site they differ by a third. The cell is
    # measured at the site, which is where the estimate is weighted.
    ns_km, ew_km = pixel_size_km(spec, centre[0], centre[1])
    # The minus sign is design 5 section 5.1's likely bug: stage 2 fixes row 0
    # as NORTH, so a pattern that moved to higher row indices moved SOUTH.
    north_kmh = -di * ns_km / dt_s * 3600.0
    east_kmh = dj * ew_km / dt_s * 3600.0
    speed_kmh = math.hypot(north_kmh, east_kmh)

    reason = (
        "over "
        + format(dt_s, ".0f")
        + " s the pattern in a "
        + repr(2 * half_rows + 1)
        + " x "
        + repr(2 * half_cols + 1)
        + " cell window shifted "
        + format(di, "+.2f")
        + " rows and "
        + format(dj, "+.2f")
        + " columns, which over "
        + format(ns_km, ".2f")
        + " x "
        + format(ew_km, ".2f")
        + " km cells is "
        + format(speed_kmh, ".0f")
        + " km/h toward "
        + format(_bearing_deg(north_kmh, east_kmh), ".0f")
        + " deg; correlation peak "
        + format(peak, ".2f")
        # The peak without the window it was measured in is not readable: 0.21
        # is noise in a 17 cell crop and a lock in a 201 cell one. The window
        # is already named above, and this is the same fact in one number.
        + ", standing "
        + format(peak_z, ".0f")
        + " sd above its surface"
    )
    return Motion(
        north_kmh=north_kmh,
        east_kmh=east_kmh,
        speed_kmh=speed_kmh,
        toward_deg=_bearing_deg(north_kmh, east_kmh),
        peak=peak,
        dt_s=dt_s,
        corroborated=False,
        matched_level=None,
        reason=reason,
    )


def _level_clause(level: WindLevel) -> str:
    """One level as a human reads it: label, speed, bearing."""
    return (
        level.label
        + " at "
        + format(level.speed_kmh, ".0f")
        + " km/h toward "
        + format(level.toward_deg, ".0f")
        + " deg"
    )


def corroborate(motion: Motion, column: Iterable[WindLevel]) -> Motion:
    """Whether the wind at some level agrees with the measurement.

    SPEED RATIO AND BEARING ANGLE, SEPARATELY. NOT A VECTOR DIFFERENCE, and
    this is the one design decision in stage 5 that looks like an unnecessary
    complication and is not. The measurement the whole design rests on --
    111.0 km/h toward 359 -- sits 45.3 km/h from its nearest level (250 hPa,
    105.4 km/h toward 23) while agreeing on SPEED to 5 percent. A
    vector-difference threshold tight enough to be worth having would have
    thrown it away. Two loose gates applied separately pass a good measurement
    and still reject nonsense, because a correlator locked to an artifact
    produces a speed no level carries, a direction no level carries, or both.
    A named test pins the distance so nobody simplifies this back.

    AN EMPTY COLUMN IS NOT A REFUTATION. A sonde that did not launch is not
    evidence about the sky, and the reason says so, because stage 6 draws
    "unconfirmed" and "contradicted" differently.

    AN UNCORROBORATED MOTION IS STILL RETURNED, which is the point of returning
    a ``Motion`` rather than a bool: this stage does not silently discard a
    measurement it merely cannot confirm.

    DEPARTURE FROM DESIGN 5 SECTION 5.2, which stops at the first level that
    passes both gates. A column arrives in whatever order its source lists it
    -- surface upward, pressure downward, or a model's own -- so a first-match
    rule makes the level shown to the operator an artifact of that ordering.
    The closest match is reported instead, closest meaning the smaller of the
    two gate residuals taken together, each as a fraction of its own gate so
    neither is privileged. Which level matched changes no verdict; it changes
    what the operator is told matched.

    A level with a non-positive speed is skipped rather than divided by. Calm
    air has no bearing to compare against and corroborates nothing.
    """
    levels = list(column)
    if not levels:
        return replace(
            motion,
            corroborated=False,
            matched_level=None,
            # Set explicitly in every branch rather than left to the default,
            # so corroborating a second time against an empty column takes the
            # flag back down. ``replace`` carries forward whatever the input
            # held, and a stale True here would be the same lie in reverse.
            column_consulted=False,
            reason=motion.reason
            + "; no wind column was available, so this measurement is "
            "unconfirmed rather than contradicted",
        )

    best: tuple[float, WindLevel, float, float] | None = None
    closest: tuple[float, WindLevel] | None = None
    for level in levels:
        if not level.speed_kmh > 0.0:
            continue
        ratio = motion.speed_kmh / level.speed_kmh
        angle_deg = _angle_between_deg(motion.toward_deg, level.toward_deg)
        # Each residual as a fraction of its own gate: 1.0 is exactly on the
        # gate, so the two are comparable and combine without a weight.
        score = math.hypot(
            abs(math.log(ratio)) / math.log(WIND_SPEED_FACTOR)
            if ratio > 0.0
            else math.inf,
            angle_deg / WIND_ANGLE_DEG,
        )
        if closest is None or score < closest[0]:
            closest = (score, level)
        if (
            1.0 / WIND_SPEED_FACTOR <= ratio <= WIND_SPEED_FACTOR
            and angle_deg <= WIND_ANGLE_DEG
            and (best is None or score < best[0])
        ):
            best = (score, level, ratio, angle_deg)

    if best is not None:
        _, level, ratio, angle_deg = best
        return replace(
            motion,
            corroborated=True,
            matched_level=level.label,
            column_consulted=True,
            reason=motion.reason
            + "; matches the "
            + _level_clause(level)
            + ", within a factor "
            + format(max(ratio, 1.0 / ratio), ".2f")
            + " on speed and "
            + format(angle_deg, ".0f")
            + " deg on bearing",
        )

    if closest is None:
        return replace(
            motion,
            corroborated=False,
            matched_level=None,
            column_consulted=True,
            reason=motion.reason
            + "; every level in the wind column reports calm air, which "
            "cannot corroborate a field that is moving",
        )
    return replace(
        motion,
        corroborated=False,
        matched_level=None,
        column_consulted=True,
        reason=motion.reason
        + "; no level in the "
        + repr(len(levels))
        + " level wind column carries both this speed and this bearing, the "
        "closest being "
        + _level_clause(closest[1]),
    )


def _offset_point(point: GeoPoint, north_km: float, east_km: float) -> GeoPoint:
    """A point displaced across the shell it sits on, height unchanged.

    The spherical direct problem, on stage 1's sphere at the shell's own radius
    -- the cloud advects at its height, not at the ground, and using ``R``
    where ``R + h`` belongs shortens a 50 km displacement by 31 m at a 4 km
    deck. Immaterial against a 2.9 km cell and correct is free.

    This is the same formula as the tail of stage 1's :func:`pierce_point`,
    which walks a great-circle arc from the site to the crossing, and there is
    no public name for it to be borrowed under. Its inverse is stage 2's
    ``_great_circle_km``, which :func:`_answer` measures the sample point's
    downrange with: the forecast test displaces a point by exactly 10 km and
    pins the 9.99 km that comes back, so the two cannot quietly drift apart.
    The 0.06 percent is the shell radius, not an error -- ``_great_circle_km``
    measures on the ground and this walks 4 km above it.
    """
    distance_km = math.hypot(north_km, east_km)
    if distance_km == 0.0:
        return point
    bearing = math.atan2(east_km, north_km)
    delta = distance_km / (EARTH_RADIUS_KM + point.height_msl_km)

    phi = math.radians(point.lat_deg)
    lam = math.radians(point.lon_deg)
    lat2 = math.asin(
        math.sin(phi) * math.cos(delta)
        + math.cos(phi) * math.sin(delta) * math.cos(bearing)
    )
    lon2 = lam + math.atan2(
        math.sin(bearing) * math.sin(delta) * math.cos(phi),
        math.cos(delta) - math.sin(phi) * math.sin(lat2),
    )
    wrapped = (math.degrees(lon2) + 180.0) % 360.0 - 180.0
    return GeoPoint(
        lat_deg=math.degrees(lat2),
        lon_deg=180.0 if wrapped == -180.0 else wrapped,
        height_msl_km=point.height_msl_km,
    )


def forecast_at(
    site: Site,
    alt_deg: float,
    az_deg: float,
    mask: GranuleWindow,
    height: GranuleWindow,
    motion: Motion | None,
    ahead_s: float,
    *,
    fov_deg: float = 1.682,
) -> Occlusion:
    """What will be in this look direction ``ahead_s`` from the granule's time.

    Stage 4 answers where the beam meets the cloud layer and what the mask says
    THERE. This displaces the second half of that question and not the first:
    the telescope does not move, so the pierce point does not either; the cloud
    that will be over it is the cloud that is upwind of it now.

    BACKWARD ALONG THE MOTION, NOT FORWARD. Cloud travelling at **v** puts
    whatever is at ``P - v*dt`` over ``P`` at ``t + dt``. Offsetting forward
    answers "where has the cloud that is over me now gone", which is a real
    question and the wrong one, and its answer is wrong by twice the
    displacement -- at 100 km/h over 30 minutes that is 100 km, which at this
    latitude is 34 cells of sky between the answer and the truth.

    ``no_data`` and never an extrapolation past :data:`HORIZON_S`, never a
    number for a lead time before now, and never a guess when no motion was
    measured. The bound is written ``not 0.0 <= ahead_s <= HORIZON_S`` rather
    than as design 5 section 5.3's two comparisons, which is a DEPARTURE and a
    small one: NaN fails ``ahead_s < 0.0`` and ``ahead_s > HORIZON_S`` both, so
    the literal form passes it through to a NaN sample point, and stage 2 then
    raises "cannot convert float NaN to integer" two modules away, naming
    nothing. The negated form refuses it here, where the sentence can name the
    parameter.

    THE RECORD DESCRIBES THE CELL THE PROBABILITY CAME FROM. ``pierce_lat_deg``,
    ``pierce_lon_deg``, ``downrange_km`` and ``cell_km`` are all the UPWIND
    sample point, not the line of sight's crossing; ``crossing_km`` and
    ``beam_m`` are the beam's, which does not move. Mixing the two would give a
    record whose cell size was measured at one place and whose coordinate named
    another, and the reason says plainly how far upwind the mask was read.

    ``basis`` BECOMES ``"forecast"`` AND STOPS SAYING which of stage 4's two
    walks placed the beam. ``crossing_km`` still says it: ``None`` there is the
    fall-through to the mask at stage 4's fallback height, exactly as it is on
    a present-time answer, and a caller that cares must read that field rather
    than the basis.

    THE LOOK DIRECTION IS CHECKED BEFORE THE LEAD TIME, which design 5 section
    5.3 puts the other way round, for the reason stage 4's ``_require_altitude``
    gives about its own ordering: an altitude of 0 is a caller bug, and a build
    that reached the ``ahead_s`` refusal first would answer it with a serene
    "no data" for every direction in a dome. The azimuth check is stage 4's one
    line copied rather than imported, which is not the duplication stage 4
    warns about -- ``isfinite`` is a totality check and not a convention, so
    two copies of it cannot come to disagree the way two copies of a formula or
    a boundary rule would.
    """
    _require_altitude(alt_deg, "alt_deg")
    if not math.isfinite(az_deg):
        raise ValueError("az_deg must be finite, got " + repr(az_deg))
    if not 0.0 <= ahead_s <= HORIZON_S:
        return _no_data(
            "ahead_s must be between 0 and "
            + format(HORIZON_S, ".0f")
            + " s, got "
            + repr(ahead_s)
            + ": past that horizon the cloud pattern this is displacing has "
            "already turned over, and before now there is nothing to forecast"
        )
    if motion is None:
        return _no_data(
            "no cloud motion could be measured from the granule pair, so "
            "nothing can be said about the sky "
            + format(ahead_s / 60.0, ".0f")
            + " min from now"
        )

    now = occlusion_at(site, alt_deg, az_deg, mask, height, fov_deg=fov_deg)
    if now.basis == "no_data":
        # The present-time walk could not place the beam, so there is no point
        # to displace. Its sentence already says which shortfall it was.
        return _no_data(
            "the sky "
            + format(ahead_s / 60.0, ".0f")
            + " min from now cannot be forecast because it cannot be read now: "
            + now.reason
        )

    layer_km = now.crossing_km
    if layer_km is None:
        # A mask-only answer was read at stage 4's fallback height; ask stage 4
        # which one rather than keeping a second copy of the rule. It returns
        # ``None`` only for a site above the whole ladder, and ``occlusion_at``
        # has already answered ``no_data`` in that case, so the branch below is
        # narrowing a type rather than handling a sky.
        layer_km = _fallback_height_km(site, _ladder_km(site))
        if layer_km is None:
            return _no_data(
                "the site is above the whole cloud-top ladder, so there is no "
                "layer to carry cloud across it"
            )

    hours = ahead_s / 3600.0
    displacement_km = motion.speed_kmh * hours
    source = _offset_point(
        GeoPoint(now.pierce_lat_deg, now.pierce_lon_deg, layer_km),
        -motion.north_kmh * hours,
        -motion.east_kmh * hours,
    )
    answer = _answer(
        site,
        alt_deg,
        fov_deg,
        source,
        now.crossing_km,
        mask,
        no_crossing="the walk that placed this beam found no crossing",
    )
    if answer.basis == "no_data":
        return _no_data(
            "the sky "
            + format(ahead_s / 60.0, ".0f")
            + " min from now would be read "
            + format(displacement_km, ".1f")
            + " km upwind, and "
            + answer.reason
        )

    cell = answer.cell_km
    over = (
        "a mask cell of no measurable size"
        if cell is None
        else "a "
        + format(cell[0], ".1f")
        + " x "
        + format(cell[1], ".1f")
        + " km mask cell"
    )
    # THREE STATES AND NOT TWO. Built from ``corroborated`` alone this clause
    # said "no wind level corroborates the motion" for a sonde that never
    # launched, which is the one thing ``corroborate`` goes out of its way not
    # to say: an empty column is not evidence against the measurement, and the
    # operator reading this sentence cannot tell a wind that disagrees from a
    # wind nobody asked.
    if motion.corroborated:
        confirmation = (
            "the motion matches the " + str(motion.matched_level) + " wind"
        )
    elif motion.column_consulted:
        confirmation = "no wind level corroborates the motion"
    else:
        confirmation = (
            "no wind column was available, so the motion is unconfirmed "
            "rather than contradicted"
        )
    return replace(
        answer,
        basis="forecast",
        reason=format(ahead_s / 60.0, ".0f")
        + " min ahead: the cloud now "
        + format(displacement_km, ".1f")
        + " km upwind, carried at "
        + format(motion.speed_kmh, ".0f")
        + " km/h toward "
        + format(motion.toward_deg, ".0f")
        + " deg, reaches the beam, where the mask reads p="
        + format(answer.probability, ".2f")
        + " over "
        + over
        + "; "
        + confirmation,
    )
