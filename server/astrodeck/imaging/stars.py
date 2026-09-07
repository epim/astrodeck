"""Star detection and HFR (half-flux radius) measurement.

HFR is the focus metric: the radius containing half a star's flux. Smaller
is sharper. The autofocus routine fits a curve of median HFR vs focuser
position and drives to the minimum.

Detection is a fast classic pipeline: background subtraction, threshold at
k-sigma, local-maximum seeding, centroid + HFR on a small cutout. Good enough
for focusing and star counts; not a photometry tool.

Two size measurements live here and they are NOT interchangeable:

* ``detect_stars`` measures each star in a fixed ``HFR_BOX_PX`` cutout. Cheap,
  per-star, and correct while the star fits — the overlay, the star count, the
  cloud detector and the preview HFR readout all want that. Its HFR SATURATES
  around 0.77 * HFR_BOX_PX/2 and it is not a focus metric off-focus; see
  HFR_BOX_CEILING_FRACTION for why growing the box does not rescue it, and
  HFR_BOX_PX_CEILING for what the arithmetic can produce at all.
* ``star_size`` / ``focus_size`` measure ONE number for the frame. This is the
  AUTOFOCUS metric, because a sweep spends most of its points outside the regime
  where a cutout of any fixed size can see the star. It answers two ways and
  ``SourceSize.source`` says which:

  - ``"stars"`` — near focus, where the box above IS faithful, it returns
    ``detect_stars``' own median over ``detect_stars``' own bright population,
    so ``star_size(d).radius == median_hfr(d)[0]``. Equal by construction, not
    by calibration; see the FINE FIRST block for the frames that forced it.
  - ``"pyramid"`` — otherwise, by finding sources on a pyramid of downsampled
    copies and measuring each on its own azimuthally-median radial profile — an
    aperture set by the SOURCE, and escalated onto a binned copy when the source
    outgrows what is affordable at full resolution, so the frame itself is the
    only limit. See the section header further down for the sky data that
    forced it.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


@dataclass
class Star:
    x: float
    y: float
    flux: float
    hfr: float
    peak: float
    ecc: float = 0.0      # eccentricity 0..1 (unsaturated mid-bright only; else 0.0)
    theta: float = 0.0    # major-axis position angle, radians (0.0 when ecc omitted)


#: cap on detected stars (detect_stars) and on overlay marks (star_marks). The
#: marks cap MUST be >= the detect cap so the overlay never silently drops stars
#: the detector already found; star_marks asserts this coupling (P3-5).
DEFAULT_MAX_STARS = 200
DEFAULT_MAX_MARKS = 400

#: A star SPREADS; a hot pixel does not. Minimum ratio of (background-subtracted)
#: flux in the 8 immediate neighbours to the flux in the peak pixel itself.
#:
#: Measured on a real uncooled frame (Poseidon-M Pro, CCD-TEMP +15.7 C, 8 s,
#: gain 300, no dark): 158 of the 200 peaks this function returned had their
#: neighbours holding under HALF the centre's flux — they were single hot
#: pixels, and this function was calling every one of them a star. That count
#: feeds the preview HFR readout, the Bahtinov aid and the CLOUD DETECTOR, and
#: it is what made a diagnosis of the native detector ("2 stars where Python
#: finds 200") point at the wrong component for a day.
#:
#: Calibration: a Gaussian PSF puts ~5.8x the peak's flux into its 8 neighbours
#: at sigma=1.5 px, and still ~2.0x at a badly undersampled sigma=0.7 px. A hot
#: pixel puts ~0. Anything below 0.75 cannot be a resolved source at any
#: sampling this instrument produces.
MIN_NEIGHBOUR_FLUX_RATIO = 0.75

#: Minimum significance of a detection's WHOLE cutout, in sigma, against what
#: noise alone would put in a region that size. The peak tests above judge one
#: pixel; this judges the source.
#:
#: 8 sigma, from two real frames of the same camera and exposure
#: (server/tests/fixtures/star_noise): on a fully overcast frame containing no
#: sources the selected peaks reach only 5.7 sigma at the 99th percentile, while
#: a real star field sits at 11.7 median and 285 at the 99th. 8 clears the whole
#: noise population with margin and still keeps the great majority of real
#: stars. Being in sigma, it needs no per-rig recalibration.
MIN_APERTURE_SNR = 8.0

#: The cutout ``detect_stars`` measures each star in. A MODULE CONSTANT, not a
#: parameter: it was a caller-settable ``box=`` argument that no caller in the
#: tree ever set, and the one thing moving it visibly did was move a rejection
#: threshold (``hfr > box // 2``) that could not fire — see HFR_BOX_PX_CEILING.
#: The comment below already records that growing it was tried and is wrong, so
#: what it offered was the appearance of a tuning dial over a decision this
#: module has made.
HFR_BOX_PX = 15

#: WHAT THE MEASUREMENT CAN ARITHMETICALLY PRODUCE, derived from the geometry
#: rather than from a threshold somebody picked (#192; the guide crate's twin is
#: 131d446).
#:
#: ``hfr`` is Σ(r·cut)/Σ(cut) — the flux-weighted MEAN distance from the
#: flux-weighted centroid — evaluated over a square cutout of side
#: L = HFR_BOX_PX - 1 = 14 px. The centroid is mass-weighted, so it lies in the
#: convex hull of the support, i.e. inside that square; the mean distance from a
#: point inside a square to mass inside the same square is maximised by putting
#: half the mass in each of two OPPOSITE CORNERS, which puts the centroid at the
#: centre and every unit of mass at half the diagonal. So
#:
#:     sup(hfr) = L / sqrt(2) = 9.90 px
#:
#: and, exactly as in the guide crate, it is approached only by a degenerate
#: two-point-mass configuration no stellar profile resembles.
HFR_BOX_PX_CEILING = (HFR_BOX_PX - 1) / math.sqrt(2)

#: What ``detect_stars``' HFR can and cannot say, so that no caller mistakes it
#: for a focus metric off-focus. A flat cutout of half-width h — pure
#: background, the most spread-out thing that can still pass the "peak is at the
#: centre" test — reads (sqrt2 + ln(1+sqrt2))/3 = 0.765h, and a real star reads
#: less, so the measurement SATURATES near 0.77h rather than being bounded
#: there: the arithmetic bound is HFR_BOX_PX_CEILING, nearly twice as far away,
#: and real detections do creep past 0.77h (measured maximum over the whole
#: fixture sweep: 0.789h, because ``local_bg`` is the border MEDIAN and the
#: clip at zero leaves the corners — the largest radii — systematically
#: positive). So the default box=15 saturates near 5 px, and it does so long
#: before that. Measured here on Gaussians of known width, box=15:
#:
#:     sigma  1.6 -> HFR 2.00   (true 2.00)
#:     sigma  3.0 -> HFR 3.37   (true 3.76)
#:     sigma  5.0 -> HFR 3.91   (true 6.26)
#:     sigma  8.0 -> HFR 4.13   (true 10.02)
#:     sigma 12.0 -> HFR 4.20   (true 15.04)
#:
#: which is the sky reading of "HFR 4.5" on an 880 px donut field, reproduced
#: on demand: past HFR ~4 this function returns the BOX's size, not the star's.
#:
#: Growing the box does not rescue it, and that was tried before this comment
#: was written. A wider cutout on a dense field swallows the neighbours and
#: measures the CLUSTER (simulator stars of sigma~1 came back at HFR 40), and
#: clipping (pixel - background) at zero keeps the positive half of the noise,
#: ~0.4 sigma per pixel, sitting at the box's largest radii — negligible at
#: 15 px, decisive at 127. An off-focus source needs an aperture defined by the
#: SOURCE and not by a constant. That is ``star_size``, below, and it is what an
#: autofocus sweep must use.
HFR_BOX_CEILING_FRACTION = 0.77


def _ecc_theta(ixx: float, iyy: float, ixy: float) -> tuple[float, float]:
    """Second-moment eccentricity and major-axis position angle.

    ecc in [0,1] (0 = round); theta in radians, (−π/2, π/2], measured from +x.
    Degenerate/negative covariance → (0.0, 0.0)."""
    mean = (ixx + iyy) / 2.0
    common = math.hypot((ixx - iyy) / 2.0, ixy)
    lam1 = mean + common            # major eigenvalue
    lam2 = mean - common            # minor eigenvalue
    if lam1 <= 0.0:
        return 0.0, 0.0
    ratio = min(1.0, max(0.0, lam2 / lam1))
    ecc = math.sqrt(1.0 - ratio)
    theta = 0.5 * math.atan2(2.0 * ixy, ixx - iyy)
    return float(ecc), float(theta)


def detect_stars(data: np.ndarray, k_sigma: float = 5.0,
                 max_stars: int = DEFAULT_MAX_STARS) -> list[Star]:
    box = HFR_BOX_PX
    img = data.astype(np.float64)
    # Robust background: median + MAD
    bg = float(np.median(img))
    noise = float(np.median(np.abs(img - bg))) * 1.4826
    if noise <= 0:
        noise = max(1.0, img.std())
    thresh = bg + k_sigma * noise

    h, w = img.shape
    half = box // 2
    sub = img - bg

    # Candidate peaks: strictly local maxima above threshold (coarse grid scan
    # keeps this O(pixels) without scipy).
    ys, xs = np.where(img > thresh)
    if len(ys) == 0:
        return []
    order = np.argsort(img[ys, xs])[::-1]
    ys, xs = ys[order], xs[order]

    stars: list[Star] = []
    used = np.zeros((h, w), dtype=bool)
    for y, x in zip(ys, xs):
        if len(stars) >= max_stars:
            break
        if used[y, x] or y < half or x < half or y >= h - half or x >= w - half:
            continue
        cut = sub[y - half:y + half + 1, x - half:x + half + 1]
        if cut[half, half] < cut.max() * 0.95:
            continue  # not the local peak
        # Hot-pixel rejection. A resolved source shares flux with its immediate
        # neighbours; a hot pixel is one bright cell surrounded by background.
        centre = float(sub[y, x])
        if centre > 0.0:
            ring8 = float(sub[y - 1:y + 2, x - 1:x + 2].sum()) - centre
            if ring8 < MIN_NEIGHBOUR_FLUX_RATIO * centre:
                continue
        used[max(0, y - half):y + half + 1, max(0, x - half):x + half + 1] = True

        # Local background from the cutout border, then flux-weighted mean
        # radius as the HFR metric — smooth and robust to undersampling,
        # unlike the cumulative half-flux threshold.
        border = np.concatenate([cut[0], cut[-1], cut[1:-1, 0], cut[1:-1, -1]])
        local_bg = float(np.median(border))
        # Is this a SOURCE, or is it noise that happened to peak?
        #
        # The peak test above (k_sigma, plus the hot-pixel neighbour ratio) asks
        # about one pixel and its ring. Noise clears both: on a real, completely
        # overcast frame with no stars in it at all, this function returned 200
        # "stars" while the native detector returned 2 — and that count feeds the
        # preview readout, the Bahtinov aid and the CLOUD DETECTOR, so a cloud
        # detector could see two hundred stars through solid overcast.
        #
        # A noise peak is a couple of excess pixels; a star is a whole PSF. So
        # judge the WHOLE cutout's flux against what noise alone would put in a
        # region that size. Measured on two real frames from the same camera and
        # exposure (server/tests/fixtures/star_noise), aperture SNR came out:
        #     blank overcast : median 2.5, 99th percentile 5.7
        #     real star field: median 11.7, 99th percentile 285
        # so the bar below removes every one of the 143 false detections and
        # keeps 72 of the real ones. It is in units of sigma, so it carries to
        # any rig without recalibration.
        #
        # Computed BEFORE the clip: clipping keeps the positive half of the
        # noise and would bias the very quantity being tested.
        aper = float((cut - local_bg).sum())
        if aper < MIN_APERTURE_SNR * noise * math.sqrt(cut.size):
            continue
        cut = (cut - local_bg).clip(0)
        total = float(cut.sum())
        if total <= 0:
            continue
        yy, xx = np.mgrid[0:box, 0:box]
        cx = float((xx * cut).sum() / total)
        cy = float((yy * cut).sum() / total)
        r = np.hypot(xx - cx, yy - cy)
        hfr = float((r * cut).sum() / total)
        # THERE IS NO UPPER GATE HERE ANY MORE, and that is the fix rather than
        # an omission (#192). It used to read `hfr > half` — reject anything
        # bigger than the cutout's half-width, 7.0 px — and it had never
        # rejected a detection. Two independent reasons, and the second is why
        # it was deleted instead of being re-derived downward:
        #
        #   * IT SAT ABOVE EVERYTHING REACHABLE. Over the real focus sweep
        #     (4900..14900, plus the star-field and overcast frames) 204
        #     detections span 3.52..5.52 px. The gate was above all of it.
        #   * NO VALUE BELOW IT SEPARATES ANYTHING EITHER. A near-focus frame
        #     produces 3.52..4.99 and a frame 1000 steps off focus produces
        #     4.26..5.52 — the two populations OVERLAP across 4.26..4.99,
        #     because this measurement saturates (HFR_BOX_CEILING_FRACTION)
        #     long before the star stops growing. Any threshold that rejected a
        #     bloated star would reject sharp ones from the same frame. "Too
        #     big to be a star" is not a question a saturating measurement can
        #     answer, so the honest thing is not to pretend to ask it; the
        #     question belongs to `star_size`, whose aperture is set by the
        #     source.
        #
        # The floor stays: `total` can be dominated by one pixel, and an HFR of
        # ~0 is a division artefact rather than an infinitely sharp star.
        if hfr <= 0.05:
            continue
        # Second moments on the same background-subtracted cutout → real
        # eccentricity + major-axis PA (~5 cheap reductions, arrays already
        # in scope). Population policy (unsaturated mid-bright) lives in
        # star_marks; every detection carries a value here.
        dx = xx - cx
        dy = yy - cy
        ixx = float((dx * dx * cut).sum() / total)
        iyy = float((dy * dy * cut).sum() / total)
        ixy = float((dx * dy * cut).sum() / total)
        ecc, theta = _ecc_theta(ixx, iyy, ixy)
        stars.append(Star(
            x=x - half + cx, y=y - half + cy, flux=total,
            hfr=hfr, peak=float(img[y, x]), ecc=ecc, theta=theta,
        ))
    return stars


def _bright_population(stars: list[Star]) -> list[Star]:
    """The stars that get a vote, brightest first.

    Faint detections near the threshold measure the noise floor, not the PSF —
    their flux-weighted radius plateaus at the cutout's noise radius. Bright
    stars are the focus signal, so only the top-flux quartile (5..25 stars)
    votes.

    ONE definition, because two consumers must not drift: ``_median_hfr_from``
    (the grader) and ``star_size``'s fine path (the autofocus metric) answer
    with the median over THIS list, which is what makes them the same number at
    focus rather than two numbers that were once calibrated to each other.
    """
    by_flux = sorted(stars, key=lambda s: -s.flux)
    n = max(min(len(by_flux), 5), min(len(by_flux) // 4, 25))
    return by_flux[:n]


def _median_hfr_from(stars: list[Star], min_stars: int = 3) -> tuple[float | None, int]:
    """Median HFR over the brightest stars, from an already-detected list."""
    if len(stars) < min_stars:
        return None, len(stars)
    pop = _bright_population(stars)
    return float(np.median([s.hfr for s in pop])), len(stars)


def median_hfr(data: np.ndarray, min_stars: int = 3) -> tuple[float | None, int]:
    """Return (median HFR over the brightest stars, star count).

    The PREVIEW readout. ``focus_size`` is what an autofocus sweep must use:
    this one still asks "how big are the stars", and a sweep spends most of its
    points where there are no stars, only donuts."""
    return _median_hfr_from(detect_stars(data), min_stars)


# ---------------------------------------------------------------------------
# SCALE-FREE SOURCE SIZE — the autofocus metric.
#
# Measured on the sky 2026-07-31 with the fixed-box HFR above, 4 s at gain 220
# bin 1, eleven points of 250 steps around a true focus of 9900:
#
#      9900 -> HFR 4.44 (911 stars)   <- true focus
#     10150 -> HFR 2.27  (22 stars)
#     10400 -> HFR 2.13   (3 stars)
#     10650 -> HFR 2.10   (7 stars)
#     11150 -> HFR 2.62   (7 stars)
#     -> autofocus failed: not_enough_spread
#
# Both halves of that are fatal and both come from the cutout. Detection
# collapses, because a star's light spreads over an annulus that grows ~0.18 px
# per focuser step and its surface brightness falls with the square of that; and
# the measurement INVERTS, because what is left inside the box is one arc of the
# ring, which is small. A focus metric that shrinks as you leave focus does not
# merely fail, it aims the search away from the answer.
#
# So the size is measured differently here:
#   * SOURCES ARE FOUND ON A PYRAMID of mean-binned copies, coarsest first. A
#     180 px donut is a single filled blob once the cells are ~1/3 of it across,
#     and binning an extended source by k lifts its per-pixel SNR by k. Coarse
#     first also means one donut is claimed ONCE, instead of arriving as the
#     130-713 ring fragments both detectors reported that night.
#   * EACH SOURCE IS MEASURED ON ITS AZIMUTHALLY-MEDIAN RADIAL PROFILE, with the
#     aperture grown until the profile falls into the noise. The median across
#     each annulus is what makes it robust: a donut's ring fills its annulus, a
#     neighbouring star occupies a few degrees of it and is ignored.
#   * THE APERTURE'S ONLY CEILING IS THE FRAME. It used to be a constant
#     (SIZE_R_CAP, 512 px) and that constant made the metric invert exactly the
#     way the 15 px box did — see SIZE_COARSE_BINS for the measurement. A source
#     too big for the full-resolution budget is now re-measured on a binned
#     copy, where the same budget spans that many times as much sky.
#
# Verified against server/tests/fixtures/focus_sweep (a real monotonic sweep of
# the same field). Measured across the band an autofocus sweep samples:
#
#     8900 -> 76.25   9300 -> 51.29   9600 -> 23.83   9900 -> 4.61
#    10200 -> 26.51  10500 -> 52.85  10900 -> 81.49     (px, one minimum)
#
# a clean V rising 0.074/0.078 px per step, whose two arms cross at 9896 —
# four steps from the focus established independently from the full frames, and
# 4.61 px at focus against the 4.44 those full frames measured with the box.
# test_focus_metric.py is that check and it is the acceptance test for this code.
#
# WHAT IT COSTS, because a sweep runs this once per point and a point that
# arrives after the mount has drifted is not a point. Measured on this machine,
# per frame: 3 ms on the 110px in-focus fixture crop, 40-90 ms on the 650px
# off-focus ones, 506 ms for the whole 15-frame fixture sweep, 670 ms on a
# synthetic 26 MP frame carrying 900 sharp stars, and 470 ms on a 2000px frame
# holding a single 500px-radius donut (the binned re-measure is what keeps that
# last one from being the 7.9 s an uncapped full-resolution aperture costs).
# Against a 4 s sweep exposure that is noise.
#
# The hard limit this CANNOT beat: past ~1500 steps out, a 4 s exposure did not
# record the star at all (~6 ADU/px above background, which loses to a hot
# pixel). Where there is no source, ``star_size`` returns None and
# ``size_advice`` says what to change. No metric recovers a signal that was
# never captured, and pretending otherwise is how a sweep ends up fitting noise.
# ---------------------------------------------------------------------------

#: Coarsest binning level in the detection pyramid. Level k finds sources up to
#: roughly 3*k px across as single blobs, so 64 covers a ~200 px donut — the
#: size a +/-1000 step sweep actually produces on this rig.
#:
#: Raising this does NOT extend the range, and that was measured rather than
#: assumed: at 256 a physical 200 px-radius annulus read 105 px instead of 145,
#: and a 500 px one 184 instead of 365. A seed from level k is only accurate to
#: k/2 px, so a coarser level buys reach at the cost of a centre, and an
#: off-centre radial profile smears one source across many radii and reads LOW.
#: Reach past this level comes from SIZE_COARSE_BINS, which re-measures a source
#: that has already been found and centred — not from seeing it more coarsely.
SIZE_MAX_LEVEL = 64

#: A binned level is only used while it still has this many cells per side.
#: Below that the "background" is the source and every statistic is a fiction.
SIZE_MIN_CELLS = 8

#: Cap on sources measured per frame. The metric is a median over the brightest
#: few; measuring 500 faint ones costs a second and moves the median by nothing.
SIZE_MAX_SOURCES = 24

#: Aperture radius budget AT FULL RESOLUTION, px. NOT a ceiling on the answer:
#: a source whose light reaches this far is re-measured on a binned copy (see
#: SIZE_COARSE_BINS). It is a COST limit — a radial profile over a 2r window is
#: O(r^2), and simply raising this to 4096 took one frame holding an 800 px
#: donut from 1.5 s to 7.9 s, which no sweep can afford.
SIZE_R_CAP = 512

#: Binning steps for re-measuring a source that outgrew the full-resolution
#: budget: 4 reaches a ~2000 px aperture, 16 reaches the frame. Measured on a
#: physical f/4 defocus annulus (35% central obstruction, spider vanes,
#: seeing-softened edges), flux-weighted mean radius in px:
#:
#:     true               218.1  290.9  363.6  436.3  581.7
#:     with a 512 cap     218.7  273.6  243.0  232.9  263.7   <- INVERTS
#:     escalated          218.9  292.0  365.0  438.0  583.9
#:
#: The middle row is the defect a review caught here, and it is the SAME defect
#: as the 15 px box in ``detect_stars``: once the donut outgrew the aperture it
#: arrived as several arcs, each measuring the annulus's WIDTH rather than its
#: radius, and the median over them SHRANK as the star grew. A metric that
#: shrinks as you leave focus does not merely fail to find focus, it aims the
#: search away from it. The rig's own 880 px-across donuts are radius 440 at
#: bin 1, i.e. squarely inside the inverted part of that row.
SIZE_COARSE_BINS = (4, 16)

#: Escalate to SIZE_COARSE_BINS once the aperture has grown to this fraction of
#: the full-resolution budget. Below it the full-res answer is already good to
#: ~0.5% (the 218 column above), so escalating would buy nothing but time.
SIZE_ESCALATE_FRAC = 0.75

#: THE SHAPE THIS CANNOT MEASURE, stated because it is a real hole and not one
#: the fixture would ever show. An annulus far THINNER than this instrument's
#: (a 6 px ring, against the 0.35..1.0 R annulus a 35%-obstructed f/4 produces)
#: reads its ring's WIDTH, ~3 px, at every radius: the azimuthal median cannot
#: see a ring from a point on its rim, because the annulus at distance d meets
#: the ring in two arcs of angular width ~w/d and more than half of it is sky.
#:
#: Left alone deliberately. Widening the search for the centre until the annular
#: MEAN runs out does fix it, and it was built and measured and then thrown
#: away: on the shape this rig ACTUALLY produces it made things worse (a
#: 200 px-radius annulus fell from 146.0 to 104.3, real fixture 11900 from 167
#: to 232), because widening also merges a crowded field into one blob. A metric
#: that is right on the shape in front of the telescope beats one that is right
#: on a shape it will never see, and the fixture is the arbiter.
SIZE_THIN_RING_LIMIT_PX = 6

#: A source's aperture flux must beat this many sigma of its own aperture noise
#: (sigma * sqrt(pixels)). At 5 a pure-noise 6.5 MP frame produced one phantom
#: "source" of r=1.8 px — a fabricated size on the curve at a position where the
#: truth was "nothing was recorded here". At 10 it produces none, and the
#: DOMINANT source of every usable fixture frame — the one that decides the
#: answer — clears the bar between 19x and 165x, so nothing real is at risk.
SIZE_MIN_APERTURE_SNR = 10.0

#: Sources fainter than this fraction of the brightest one do not vote. Their
#: profiles are noise-dominated and they drag the median toward the noise floor
#: — the same reason ``_median_hfr_from`` only lets the top flux quartile vote.
SIZE_POPULATION_FRAC = 0.1

#: A single source measured at this aperture SNR is a better focus point than
#: three faint ones, so it satisfies ``focus_size``'s min_stars on its own. The
#: count gate exists to keep a HOT PIXEL out of the fit; the resolved-source
#: test now does that job directly and by construction.
SIZE_CONFIDENT_SNR = 50.0


# ---------------------------------------------------------------------------
# FINE FIRST (GN-05). The pyramid above walks coarse -> fine, and on a frame
# whose stars RESOLVE that is the wrong order: a coarse level claims resolved
# structure as one "source" before level 1 is ever looked at. Measured on the
# same pixels, thirds of the frames of 2026-09-05/06:
#
#     L  grader 4.07/3.94/3.84   size  8.02/8.84/13.14
#     R  grader 2.95/2.98/3.09   size  4.06/4.39/ 6.74
#     G  grader 3.14/3.21/3.22   size  4.42/6.36/ 3.89
#
# voting with 4-24 sources at scales 2-16, on a field whose only extended thing
# is M33's core. The sweep fits the right-hand column and every sub is graded
# with the left-hand one, so the autofocus vertex and the frame grader were
# describing different objects.
#
# So `star_size` now looks at the UNBINNED star population FIRST, and when that
# population is large enough and compact it answers with the median of the same
# estimator over the same stars `median_hfr` votes with. At focus the two are
# equal by construction rather than by calibration. The pyramid runs only when
# there is no trustworthy star population, which is what it was built for.
#
# The two gates below are what "trustworthy" means, and they are two rather
# than one because they fail in opposite directions -- measured tables at each.
#
# WHAT IT COSTS, measured on this machine on a 3126x2088 frame (a sweep runs
# this eleven times, on a Pi):
#
#     300 sharp stars  -> 251 ms before, 222 ms after   the fine path ANSWERS
#     300 donuts r=12  -> 215 ms before, 413 ms after   \  it screens, the
#     300 donuts r=30  -> 224 ms before, 466 ms after    > pyramid still runs
#     100 donuts r=60  -> 236 ms before, 519 ms after   /
#
# Near focus it is cheaper than what it replaced: the pyramid's coarse levels
# are not walked at all. Off focus it is one extra `detect_stars` -- the SAME
# O(pixels) pass the capture path already makes on every sub -- and that is
# deliberately not shaved by capping `max_stars` for the screen, because a cap
# would give the fine path a different population from `median_hfr`'s and the
# equality above is the whole point. The sweep exposes the next point while this
# one is measured (`focus.pipeline`), so the extra ~250 ms sits under a 4 s
# exposure rather than beside it.
# ---------------------------------------------------------------------------

#: Level-1 detections needed before the fine path will answer. A median over
#: four detections is not a population, and the wings of a sweep legitimately
#: yield two or three measurable donuts -- the case the pyramid and
#: SIZE_CONFIDENT_SNR exist for. Measured: every real fixture that must take the
#: fine path carries 19-60 detections; every synthetic frame in the suite that
#: must NOT (a 5-star Gaussian field, a single physical annulus, the 110px
#: in-focus crop) carries 1-5.
SIZE_FINE_MIN_STARS = 10

#: Gate 1: the median box HFR of the voting population, above which the box is
#: measuring ITSELF rather than a star. `detect_stars` sums a fixed 15px cutout,
#: so once the source outgrows it the flux-weighted radius converges on the
#: box's own geometry (HFR_BOX_CEILING_FRACTION) no matter how big the star is —
#: and a peak that is really speckle on a huge smooth halo reads the same, which
#: is why gate 2 alone cannot catch it. Measured (median box HFR, px):
#:
#:     focused, must take the fine path   clean_R60 2.61  m33core_G60 3.21
#:                                        sigma=1.2 synth 1.49  sigma=2 synth 2.48
#:     out of focus, must not             sweep 10500 4.26  9300 4.36  11900 4.39
#:                                        8900 4.56  4900 4.63  10200 4.68  10900 4.88
#:                                        9px-ring synth 4.70  donut fields 4.44-4.96
#:
#: 3.8 sits 18% above the highest frame that must pass and 12% below the lowest
#: that must not, and 70% of the box's own ceiling (0.77 * 15//2 = 5.39).
SIZE_FINE_MAX_BOX_HFR = 3.8

#: How far the truncation probe's aperture may grow: ONE box width, i.e. 2.1x
#: the 7 px half-width the box actually measures in.
#:
#: This cap is half of the 2026-09-07 repair. The probe used to let the aperture
#: run to SIZE_R_CAP, and on a bright star it duly followed the halo out past
#: 100 px while the 15 px box stayed where it was. That is a measurement of
#: BRIGHTNESS, not of defocus, and it is why the ratio below read one thing on a
#: 512 px crop and another on the full frame the same crop came out of: R_0030
#: reads 1.14 capped here and 1.53 uncapped, on the same stars. The question the
#: gate asks is "what is the box missing", which is a question about the source
#: near the box, so the answer is taken near the box.
SIZE_TRUNCATION_CAP_PX = float(HFR_BOX_PX)

#: Gate 2: how much of the source the box is MISSING, as the ratio of the
#: radial-profile mean radius (aperture set by the source, `_measure_source`,
#: capped at SIZE_TRUNCATION_CAP_PX) to the box's answer for the same star,
#: medianed over the GRADER'S OWN voting population (`_bright_population`).
#:
#: BOTH of those were different until 2026-09-07 and both differences were
#: brightness artefacts. The probe read the brightest FIVE detections with a
#: free aperture, which on a 512 px crop is five ordinary stars and on a full
#: 6248x4176 frame is five near-saturated ones. Measured on last night's WHOLE
#: frames against the then-threshold of 1.20:
#:
#:     clean R_0007 1.35   clean S_0005 1.30   clean R_0030 1.99
#:     M33 field G_0003 1.37                   donut L_0001 2.11
#:
#: so the fine path never fired on a full frame at all: the sweep answered 3.48
#: against the grader's 2.73 on R_0007, 4.60 at pyramid scale 16 on G_0003, and
#: 684 px on the rig at true focus at 00:31 on 2026-09-07.
#:
#: Measured with the population and the cap above (2026-09-07):
#:
#:     must take the fine path
#:       full 6248x4176   R_0007 1.07  S_0005 1.09  R_0030 1.14  G_0003 1.11
#:       wide crop        m33field_G60 (2048x1536) 1.14
#:       512 crop         clean_R60 1.00  m33core_G60 1.05
#:       synthetic        sigma=1.2 1.10  sigma=1.5 1.04  sigma=2 1.03
#:                        200-sharp-star field 1.03
#:     must not
#:       full 6248x4176   donut L_0001 1.82
#:       wide crop        donutfield_L60 (1024x1024) 1.80
#:       512 crop         donut_L60 1.48
#:       synthetic        Gaussian sigma=4 1.34 (true 5.01, box 3.75)
#:                        sigma=12 1.58   9px-ring field 2.02
#:
#: 1.25 sits 10% above the highest frame that must pass and 7% below the lowest
#: that must not, and the two sides no longer depend on the frame's SIZE, which
#: is what the old bar could not survive. The binding case on the far side is
#: still the sigma=4 Gaussian, whose TRUE radius is 5.01 px while the box says
#: 3.75 -- a 25% error, i.e. exactly the point at which the box stops being a
#: faithful description of the star.
#:
#: The frames BETWEEN the two lists are the trailed ones, and they land on the
#: pyramid as they did before: jump_R60 1.24, staircase_G60 1.31.
SIZE_FINE_MAX_TRUNCATION = 1.25

#: Gate 3: WHERE the radial profile peaks, medianed over the same probes. A star
#: peaks at its own centre whatever its brightness; a defocused annulus peaks on
#: its rim. That makes this the one reading in the set that a halo cannot move,
#: and it catches the donut fields gate 2 cannot see at all -- a synthetic
#: 45 px-radius donut field reads a truncation ratio of 1.01, because the box
#: and the capped profile are both measuring the same rim fragment.
#: `_profile_edge` smooths the profile with a 3-wide kernel, so a source that
#: peaks at r=0 reports 1, not 0. Measured (median peak radius, px):
#:
#:     must take the fine path   1.0 on EVERY frame measured, at every size:
#:                               R_0007 S_0005 R_0030 G_0003 B_0004 L_0026
#:                               L_0006 Ha_0018 (full 6248x4176), m33field_G60,
#:                               clean_R60 m33core_G60 jump_R60 doubleblob_L60
#:                               pedrift_L60 staircase_G60 tail_Ha300 (crops),
#:                               sigma=1.2/1.5/2/4/12 synthetics
#:     must not                  donut L_0001 6.0   donutfield_L60 6.0
#:                               donut_L60 3.0      9px-ring synth 9.0
#:                               synthetic donut fields r=8/20/45/90: 4/2/2/2
#:
#: 2 is the only value between them, and every frame that must pass reads the
#: floor. (Sweep points read 1.0-2.0 here -- their seeds sit inside a blob far
#: bigger than the probe -- and gate 1 holds all of them at box HFR 4.26-4.88.)
SIZE_FINE_MAX_PEAK_R = 2.0


@dataclass
class SourceSize:
    """One frame's answer to "how big are the sources", in data pixels."""
    radius: float       #: flux-weighted mean radius, median over the bright sources
    n_sources: int      #: sources that voted
    n_found: int        #: resolved sources measured at all (>= n_sources)
    snr: float          #: brightest source's aperture flux / (sigma*sqrt(pixels))
    scale: int          #: pyramid level it was found at — 1 = a star, 64 = a donut
    lower_bound: bool   #: the aperture ran into the frame edge; radius is a FLOOR
    #: which measurement answered: "stars" = the fine path, i.e. the median box
    #: HFR over the population ``median_hfr`` grades with, so the two numbers are
    #: the same one; "pyramid" = a source found on a binned copy and measured on
    #: its own radial profile. Defaulted so every existing construction (and
    #: every test that builds a SourceSize by hand) keeps working.
    source: str = "pyramid"


def _mean_binned(a: np.ndarray, k: int) -> np.ndarray:
    if k == 1:
        return a
    h, w = a.shape[0] // k * k, a.shape[1] // k * k
    return a[:h, :w].reshape(h // k, k, w // k, k).mean(axis=(1, 3))


def _bg_sigma(a: np.ndarray, sample: int = 250_000) -> tuple[float, float]:
    """Robust (background, sigma). Subsampled above ``sample`` pixels: an exact
    median of 26 million pixels costs ~200 ms and shifts the answer by less than
    the noise it is measuring."""
    flat = np.asarray(a).ravel()
    if flat.size > sample:
        flat = flat[::flat.size // sample]
    bg = float(np.median(flat))
    sig = float(np.median(np.abs(flat - bg))) * 1.4826
    if sig <= 0.0:
        sig = max(1e-6, float(flat.std()))
    return bg, sig


def _box3(b: np.ndarray) -> np.ndarray:
    """3x3 mean. Applied only to BINNED levels: it is what fills the hole in a
    ring so the coarse local maximum lands on the donut's centre instead of on
    its rim, and at level 1 it would cost more than the whole rest of the pass."""
    pad = np.pad(b, 1, mode="edge")
    return sum(pad[i:i + b.shape[0], j:j + b.shape[1]]
               for i in range(3) for j in range(3)) / 9.0


def _size_levels(shape: tuple[int, int]) -> list[int]:
    ks, k = [], 1
    while k <= SIZE_MAX_LEVEL and min(shape[0] // k, shape[1] // k) >= SIZE_MIN_CELLS:
        ks.append(k)
        k *= 2
    return ks


def _seed_positions(img: np.ndarray, k: int, k_sigma: float,
                    limit: int) -> list[tuple[float, float]]:
    """Local maxima at binning level ``k``, returned in FULL-frame pixels."""
    b = _mean_binned(img, k)
    det = _box3(b) if k > 1 else b
    bg, sig = _bg_sigma(det)
    ys, xs = np.where(det > bg + k_sigma * sig)
    if len(ys) == 0:
        return []
    order = np.argsort(det[ys, xs])[::-1]
    ys, xs = ys[order], xs[order]
    out: list[tuple[float, float]] = []
    used = np.zeros(b.shape, dtype=bool)
    for y, x in zip(ys, xs):
        if used[y, x]:
            continue
        used[max(0, y - 2):y + 3, max(0, x - 2):x + 3] = True
        out.append(((y + 0.5) * k, (x + 0.5) * k))
        if len(out) >= limit:
            break
    return out


def _radial_median_profile(img: np.ndarray, cy: float, cx: float, win: int,
                           bg_fallback: float
                           ) -> tuple[np.ndarray, np.ndarray, float] | None:
    """``(profile, samples_per_radius, background)`` about ``(cy, cx)``.

    ``profile`` is background-subtracted and indexed by integer radius. Each
    entry is the MEDIAN over that annulus, not the mean: that is what lets one
    source be measured in a frame that contains others. Summing a window's flux
    instead is exactly how ``measure_blob`` came to report a 445 px blob on a
    frame holding 200 sharp stars — it was measuring the field, not a source.

    The window is clipped to the frame, so annuli near an edge are partial;
    ``samples_per_radius`` carries that so the caller can scale its noise bar.
    """
    h, w = img.shape
    i0, i1 = int(max(0, cy - win)), int(min(h, cy + win + 1))
    j0, j1 = int(max(0, cx - win)), int(min(w, cx + win + 1))
    if i1 - i0 < 6 or j1 - j0 < 6:
        return None
    yy = (np.arange(i0, i1) - cy)[:, None]
    xx = (np.arange(j0, j1) - cx)[None, :]
    ri = np.sqrt(yy * yy + xx * xx).astype(np.int32).ravel()
    vals = img[i0:i1, j0:j1].ravel()
    inside = ri <= win
    ri, vals = ri[inside], vals[inside]
    if ri.size == 0:
        return None
    nbins = int(win) + 1
    # One lexsort gives every annulus's median at once; nbins separate
    # np.median calls cost ~4x more on the frames that matter.
    order = np.lexsort((vals, ri))
    ri_s, v_s = ri[order], vals[order]
    bounds = np.searchsorted(ri_s, np.arange(nbins + 1))
    cnt = np.diff(bounds).astype(np.float64)
    mid = np.clip((bounds[:-1] + bounds[1:] - 1) // 2, 0, max(len(v_s) - 1, 0))
    prof = np.where(cnt > 0, v_s[mid], np.nan)
    if int(np.isfinite(prof).sum()) < 6:
        return None
    # Background from the outer quarter of the window, where the source is not.
    tail = prof[int(nbins * 0.75):]
    tail = tail[np.isfinite(tail)]
    bg = float(np.median(tail)) if len(tail) >= 3 else bg_fallback
    return np.where(np.isfinite(prof), prof - bg, 0.0), cnt, bg


def _profile_edge(prof: np.ndarray, cnt: np.ndarray,
                  sigma: float) -> tuple[int, int]:
    """``(edge radius, peak radius)``: walk out from the profile's maximum and
    stop where the annulus is no longer above the noise. THIS is "grow the
    aperture until the enclosed flux converges" — one annulus at a time, with a
    bar that scales as sigma/sqrt(samples) so a big, thin annulus is judged as
    strictly as a small one. Starting from the peak rather than from r=0 is what
    makes it work on a donut, whose centre is dark."""
    smooth = np.convolve(prof, np.ones(3) / 3.0, mode="same")
    peak = int(np.argmax(smooth))
    bar = 2.5 * sigma / np.sqrt(np.maximum(cnt, 1.0))
    for i in range(peak + 1, len(prof)):
        if smooth[i] < bar[i]:
            return i, peak
    return len(prof) - 1, peak


def _recentre(img: np.ndarray, bg: float, sigma: float, cy: float, cx: float,
              r: float) -> tuple[float, float]:
    """Flux-weighted centre inside radius ``r``. A coarse-level seed lands
    within half a bin of the truth, and for a ring it can land on the rim; an
    off-centre profile smears the ring across many radii and reads too big."""
    h, w = img.shape
    R = int(max(2, r))
    i0, i1 = int(max(0, cy - R)), int(min(h, cy + R + 1))
    j0, j1 = int(max(0, cx - R)), int(min(w, cx + R + 1))
    v = np.clip(img[i0:i1, j0:j1] - bg - 3.0 * sigma, 0.0, None)
    total = float(v.sum())
    if total <= 0.0:
        return cy, cx
    ys = np.arange(i0, i1)[:, None]
    xs = np.arange(j0, j1)[None, :]
    return float((ys * v).sum() / total), float((xs * v).sum() / total)


def _lock_on(img: np.ndarray, bg: float, sigma: float, cy: float, cx: float,
             r: float, iters: int = 4) -> tuple[float, float]:
    """Walk the centre onto the source before measuring it.

    ``_recentre`` once is not enough on a ring. A pyramid seed lands on the
    brightest CELL, which for a donut is the RING and never the dark middle;
    the flux-weighted centre inside a radius that spans the whole ring IS the
    ring's centre, but one step at the ring's own width leaves you still on the
    rim, and the radial profile then describes the ring's WIDTH. Measured on a
    400 px-radius annulus: 273.6 px with one step, 291.6 with this, truth 290.9.

    Refusing to move further than ``r`` from the seed is what keeps this a
    refinement and not a search. Without that guard a blob truncated by the
    frame edge walked onto a brighter compact star elsewhere, and the fixture
    frame 5000 steps out of focus came back at 6.7 px — i.e. in focus.
    """
    sy, sx = cy, cx
    for _ in range(iters):
        ny, nx = _recentre(img, bg, sigma, cy, cx, r)
        if math.hypot(ny - sy, nx - sx) > r:
            break                       # that is a different source, not ours
        step = math.hypot(ny - cy, nx - cx)
        cy, cx = ny, nx
        if step < 0.05 * r:
            break                       # settled
    return cy, cx


def _binned_view(img: np.ndarray, b: int,
                 cache: dict) -> tuple[np.ndarray, float, float] | None:
    """A ``b``-binned copy of the frame with its OWN robust statistics, built
    once per frame per level. Mean-binning divides the noise by b, so reusing
    the full-resolution sigma would judge every binned annulus b times too
    strictly and end the aperture early — which is the failure this whole
    escalation exists to remove."""
    if b not in cache:
        s = _mean_binned(img, b)
        bg, sig = _bg_sigma(s)
        cache[b] = (s, bg, sig)
    s, bg, sig = cache[b]
    return None if min(s.shape) < 16 else (s, bg, sig)


def _measure_at_bin(img: np.ndarray, cache: dict, b: int, cy: float, cx: float,
                    win0: float, r_cap: float) -> dict | None:
    """``_measure_source`` on a ``b``-binned copy, scaled back to full-frame px.

    Aperture SNR survives the trip unchanged — binning divides sigma by b and
    the aperture radius by b while dividing the flux by b^2 — so a donut judged
    on a binned copy clears exactly the bar it would have cleared at full
    resolution, and SIZE_MIN_APERTURE_SNR keeps meaning what it means.
    """
    got = _binned_view(img, b, cache)
    if got is None:
        return None
    sub, bg, sigma = got
    by, bx = (cy + 0.5) / b - 0.5, (cx + 0.5) / b - 0.5
    m = _measure_source(sub, bg, sigma, by, bx, max(8.0, win0 / b),
                        max(8.0, r_cap / b))
    if m is None:
        return None
    ny, nx = _lock_on(sub, m["bg"], sigma, by, bx, m["edge"])
    better = _measure_source(sub, bg, sigma, ny, nx, max(8.0, m["edge"] * 1.5),
                             max(8.0, r_cap / b))
    if better is not None and better["flux"] >= m["flux"]:
        m = better
    out = dict(m)
    out["y"] = (m["y"] + 0.5) * b - 0.5
    out["x"] = (m["x"] + 0.5) * b - 0.5
    for key in ("edge", "mean_r", "r80"):
        out[key] = m[key] * b
    out["flux"] = m["flux"] * b * b
    return out


def _measure_source(img: np.ndarray, bg: float, sigma: float, cy: float,
                    cx: float, win0: float, r_cap: float) -> dict | None:
    """Measure one source, doubling the analysis window until its edge fits."""
    h, w = img.shape
    win = float(min(win0, r_cap))
    state = None
    for _ in range(6):
        got = _radial_median_profile(img, cy, cx, int(win), bg)
        if got is None:
            return None
        prof, cnt, local_bg = got
        edge, peak = _profile_edge(prof, cnt, sigma)
        state = (prof, cnt, local_bg, edge, peak, win)
        if edge < 0.7 * win or win >= r_cap:
            break
        win = min(win * 2.0, r_cap)
    prof, cnt, local_bg, edge, peak, win = state
    if edge < 1:
        return None
    # Bin r holds every pixel whose radius floors to r, i.e. the annulus
    # [r, r+1), so it stands at r+0.5 and not at r. Half a pixel sounds
    # negligible and is not: at focus it is 12% of the whole answer, and it
    # biases EVERY size low by the same half pixel, which is how a metric ends
    # up disagreeing with the HFR it is supposed to be continuous with.
    radii = np.arange(len(prof), dtype=np.float64) + 0.5
    weight = np.clip(prof[:edge + 1], 0.0, None) * cnt[:edge + 1]
    flux = float(weight.sum())
    if flux <= 0.0:
        return None
    # Flux-weighted MEAN radius — the same ESTIMATOR detect_stars uses, over a
    # different APERTURE, and that difference is not small (GN-05). This one
    # grows until the radial profile falls into the noise; detect_stars stops at
    # the border of a fixed HFR_BOX_PX cutout. A comment here used to claim the
    # two therefore "agree at focus". They did not: on the real frames of
    # 2026-09-05/06 the grader read 2.61-4.24 px where this path answered
    # 3.05-19.23 on the same pixels, because a coarse pyramid level had claimed
    # resolved structure before level 1 was looked at.
    #
    # Agreement is now made rather than asserted: `star_size` answers with
    # detect_stars' own median whenever the star population is trustworthy, and
    # this measurement is the PYRAMID path's estimator — for donuts and for the
    # far wings of a sweep, where a cutout of any fixed size cannot see the
    # source at all. `_box_truncation` is where the two are compared, and it is
    # the ratio between them that decides which one answers.
    mean_r = float((radii[:edge + 1] * weight).sum() / flux)
    # r80 over the SAME aperture — the coarse-focus readout (imaging.defocus)
    # wants an enclosing radius rather than a mean, and computing it here is how
    # both numbers stay descriptions of one source instead of of one window.
    cum = np.cumsum(weight)
    r80 = float(radii[min(int(np.searchsorted(cum, 0.8 * flux)), edge)])
    reach = min(cy, cx, h - 1 - cy, w - 1 - cx)
    # Significance of the WHOLE aperture, not of its brightest pixel. A donut a
    # thousand steps out peaks only ~4 sigma above sky per pixel and would fail
    # any peak test, while its 60000-pixel aperture holds the flux of a bright
    # star: 500 sigma. Judging it on the peak is judging surface brightness,
    # which is exactly the quantity defocus destroys.
    snr = flux / (sigma * math.sqrt(math.pi * max(edge, 1.0) ** 2))
    return {"y": cy, "x": cx, "edge": float(edge), "peak_r": peak,
            "mean_r": mean_r, "r80": r80, "flux": flux,
            "peak": float(prof.max()), "bg": local_bg, "snr": float(snr),
            "truncated": bool(edge >= 0.95 * win or edge > reach)}


def _is_resolved(img: np.ndarray, bg: float, cy: float, cx: float,
                 r: float) -> bool:
    """The hot-pixel physics of MIN_NEIGHBOUR_FLUX_RATIO, applied at any scale:
    the brightest pixel inside the aperture has to share flux with its
    neighbours. Binning does not remove a hot pixel — a 60000 ADU cell is still
    3700 ADU above sky after a 4x4 mean — so a pyramid without this test finds a
    'source' on every dark frame."""
    h, w = img.shape
    R = max(2, int(r))
    i0, i1 = max(0, int(cy) - R), min(h, int(cy) + R + 1)
    j0, j1 = max(0, int(cx) - R), min(w, int(cx) + R + 1)
    sub = img[i0:i1, j0:j1]
    if sub.size == 0:
        return False
    iy, ix = np.unravel_index(int(np.argmax(sub)), sub.shape)
    y, x = i0 + iy, j0 + ix
    if y < 1 or x < 1 or y >= h - 1 or x >= w - 1:
        return True                       # cannot judge at the border; keep it
    centre = float(img[y, x]) - bg
    if centre <= 0.0:
        return True
    ring8 = float(img[y - 1:y + 2, x - 1:x + 2].sum()) - 9.0 * bg - centre
    return ring8 >= MIN_NEIGHBOUR_FLUX_RATIO * centre


#: How many binned pixels a source must span before a measurement taken at that
#: bin level is believed. Below this the answer is the bin's own resolution
#: floor rather than the object (a 6 px star measured at bin 64 reports 32 px),
#: so the seed is deferred to a finer level instead of claimed.
SIZE_BIN_TRUST = 1.5


def _box_truncation(img: np.ndarray, bg: float, sigma: float,
                    stars: list[Star], cap: float
                    ) -> tuple[float, float, float] | None:
    """``(how much the 15px box is missing, brightest probe's aperture SNR,
    how far out the profile peaks)``, over the GRADER'S voting population.

    Measures each of ``_bright_population``'s stars a SECOND time — same pixels,
    same centre, but with the aperture grown until the radial profile falls into
    the noise instead of stopping at the cutout's edge — and reports the median
    of both readings. They answer the two questions the fine path turns on: a
    star that fits inside ``HFR_BOX_PX`` reads a ratio of ~1.0 and peaks at its
    own centre, while a defocused ring read off one arc of its rim reads however
    much of the ring the box could not see, and peaks on the rim. See
    SIZE_FINE_MAX_TRUNCATION and SIZE_FINE_MAX_PEAK_R for the measured tables.

    THE POPULATION IS THE GRADER'S, not "the brightest five", and the aperture
    is capped at SIZE_TRUNCATION_CAP_PX. Both were the 2026-09-07 defect: on a
    full frame the brightest five are near-saturated stars whose halos the free
    aperture followed, so the ratio measured how bright the field was and the
    fine path never fired on a whole frame at all. Sharing
    ``_bright_population`` with ``_median_hfr_from`` also means a frame is
    screened on the same stars it is graded on rather than on a different five.

    ``_lock_on`` before the second measurement for the same reason
    ``_measure_at_bin`` does it: a rim fragment's seed is on the ring, and a
    profile taken from a point on a ring describes the ring's width. Walking
    onto the centre first is what makes a donut read as a donut here.

    ``None`` when nothing could be measured at all — the caller then falls
    through to the pyramid rather than guessing.
    """
    probe_cap = float(min(cap, SIZE_TRUNCATION_CAP_PX))
    ratios: list[float] = []
    peaks: list[float] = []
    snr = 0.0
    for s in _bright_population(stars):
        if s.hfr <= 0.0:
            continue
        m = _measure_source(img, bg, sigma, s.y, s.x, 8.0, probe_cap)
        if m is None:
            continue
        ny, nx = _lock_on(img, m["bg"], sigma, s.y, s.x, m["edge"])
        better = _measure_source(img, bg, sigma, ny, nx,
                                 max(8.0, m["edge"] * 1.5), probe_cap)
        if better is not None and better["flux"] >= m["flux"]:
            m = better
        ratios.append(float(m["mean_r"]) / float(s.hfr))
        peaks.append(float(m["peak_r"]))
        snr = max(snr, float(m["snr"]))
    if not ratios:
        return None
    return float(np.median(ratios)), snr, float(np.median(peaks))


def _fine_size(img: np.ndarray, bg: float, sigma: float, k_sigma: float,
               cap: float,
               stars: list[Star] | None = None) -> SourceSize | None:
    """The frame's size from its UNBINNED stars, or ``None`` to use the pyramid.

    ONE detection pass — the same one ``median_hfr`` makes — and the answer is
    its median, so ``star_size(data).radius == median_hfr(data)[0]`` whenever
    this path answers. That equality is the point of GN-05: the number the sweep
    fits and the number every sub is graded with have to describe the same
    thing, and a comment claiming they did was the only thing holding it up.

    ``stars`` lets a caller that has already detected them hand the list over
    (the preview path grades every sub before it asks this question), so the
    O(pixels) pass is made once per frame rather than once per consumer.
    """
    if stars is None:
        stars = detect_stars(img, k_sigma=k_sigma)
    if len(stars) < SIZE_FINE_MIN_STARS:
        return None                     # not a population; the pyramid's job
    pop = _bright_population(stars)
    radius = float(np.median([s.hfr for s in pop]))
    if radius >= SIZE_FINE_MAX_BOX_HFR:
        return None                     # the box is measuring itself (gate 1)
    probe = _box_truncation(img, bg, sigma, stars, cap)
    if probe is None:
        return None
    truncation, snr, peak_r = probe
    if peak_r >= SIZE_FINE_MAX_PEAK_R:
        return None                     # an annulus, not a star (gate 3)
    if truncation >= SIZE_FINE_MAX_TRUNCATION:
        return None                     # rim fragments, not stars (gate 2)
    return SourceSize(radius=radius, n_sources=len(pop), n_found=len(stars),
                      snr=snr, scale=1, lower_bound=False, source="stars")


def compact_star_population(data: np.ndarray, *,
                            stars: list[Star] | None = None,
                            k_sigma: float = 5.0) -> SourceSize | None:
    """This frame's stars WHEN THEY ARE A TRUSTWORTHY POPULATION, else ``None``.

    ONE definition of "the optics are resolving this field, and the 15 px box is
    a faithful description of what they resolved" — and it is ``star_size``'s
    own fine path rather than a second opinion about it. Two callers need that
    question answered and they must not come to different answers:

    * the autofocus sweep, which answers with the grader's median wherever this
      is not None and with the binning pyramid where it is;
    * ``imaging.defocus``, which must not report a defocus blob on a frame whose
      stars are sitting right there. On 2026-09-07 at 01:17 the preview told a
      user running a 60 s L sub of NGC 604 — in focus, HFR 3.32, 1294 stars —
      that the blob was "2268 px across, further out than an autofocus sweep can
      bracket", because the blob measurer had found M33 and nothing asked the
      stars.

    ``stars`` is the caller's existing detection pass; omit it and one is made.
    """
    img = np.asarray(data, dtype=np.float64)
    if img.ndim != 2 or min(img.shape) < 16:
        return None
    bg, sigma = _bg_sigma(img)
    cap = float(min(SIZE_R_CAP, min(img.shape) / 2.0))
    return _fine_size(img, bg, sigma, k_sigma, cap, stars=stars)


def star_size(data: np.ndarray, *, k_sigma: float = 5.0,
              max_sources: int = SIZE_MAX_SOURCES,
              r_cap: float = SIZE_R_CAP) -> SourceSize | None:
    """How big are the sources in this frame, in data pixels — or ``None``.

    TWO measurements, and ``SourceSize.source`` says which one answered.

    * ``"stars"`` — FINE FIRST. When the unbinned population is large enough and
      compact (SIZE_FINE_MIN_STARS, SIZE_FINE_MAX_BOX_HFR,
      SIZE_FINE_MAX_TRUNCATION, SIZE_FINE_MAX_PEAK_R — see
      ``compact_star_population``), the answer IS ``median_hfr``: the same median
      of the same estimator over the same stars. Near focus that is what the
      sweep should be fitting, because it is what every sub is then graded with.
    * ``"pyramid"`` — sources found on a pyramid of binned copies and measured
      on their own radial profiles. The regime the fine path cannot reach: real
      donuts, and the sparse far wings of a sweep where nothing resolves.

    ``None`` means "no measurable source", which on a wide sweep is the literal
    truth and not a failure of the code: see ``size_advice``.
    """
    img = np.asarray(data, dtype=np.float64)
    if img.ndim != 2 or min(img.shape) < 16:
        return None
    bg, sigma = _bg_sigma(img)
    half = min(img.shape) / 2.0
    cap = float(min(r_cap, half))
    fine = _fine_size(img, bg, sigma, k_sigma, cap)
    if fine is not None:
        return fine
    binned: dict[int, tuple[np.ndarray, float, float]] = {}
    found: list[dict] = []
    claimed: list[tuple[float, float, float]] = []
    for k in reversed(_size_levels(img.shape)):
        # Coarse levels are a handful of big blobs; level 1 is a star field.
        limit = 6 if k >= 8 else (40 if k > 1 else 60)
        for (y, x) in _seed_positions(img, k, k_sigma, limit):
            if len(found) >= max_sources:
                break
            if any((y - c[0]) ** 2 + (x - c[1]) ** 2 < c[2] ** 2 for c in claimed):
                continue        # already measured as part of a bigger source
            if not _is_resolved(img, bg, y, x, 3):
                continue
            if k > 1:
                # MEASURE WHERE THE SEED WAS FOUND. A coarse seed used to be
                # measured at FULL resolution with an aperture of 8*k px, which
                # is the single most expensive thing this function does and is
                # done for a structure that is k px wide by construction. On a
                # 3126x2088 frame, screening coarse seeds correctly without this
                # cost 23x (235 -> 5362 ms) - eleven times a sweep, on a Pi.
                # `_measure_at_bin` does the same measurement on the cached
                # k-binned copy and scales back, for ~k^2 less, and it already
                # locks on internally.
                m = _measure_at_bin(img, binned, k, y, x, 8.0 * k, cap)
                if m is None:
                    continue
                # TOO COARSE FOR THIS SOURCE. At bin k a radius cannot come back
                # smaller than about one binned pixel, so a tight star measured
                # at k=64 reports 32 px - the size of the bin, not of the star.
                # Skip without claiming and let a finer level have it; the
                # source is not lost, only deferred to a resolution that can
                # actually see it.
                if m["mean_r"] < SIZE_BIN_TRUST * k:
                    continue
            else:
                m = _measure_source(img, bg, sigma, y, x, 8.0, cap)
                if m is None:
                    continue
                ny, nx = _lock_on(img, m["bg"], sigma, y, x, m["edge"])
                better = _measure_source(img, bg, sigma, ny, nx,
                                         max(8.0, m["edge"] * 1.5), cap)
                if better is not None and better["flux"] >= m["flux"]:
                    m = better
            # The APERTURE stopped us, not the frame: re-measure the same source
            # coarsely rather than report the fragment we can afford to see. The
            # frame stopping us is a different answer (``truncated`` -> a floor),
            # and binning cannot rescue light that was never on the sensor.
            if m["edge"] >= SIZE_ESCALATE_FRAC * cap and cap < half:
                for b in SIZE_COARSE_BINS:
                    cm = _measure_at_bin(img, binned, b, m["y"], m["x"],
                                         m["edge"] * 3.0, min(r_cap * b, half))
                    if cm is None:
                        continue
                    if cm["mean_r"] > m["mean_r"]:
                        m = cm
                    if not cm["truncated"]:
                        break   # this level held the whole source; go no coarser
            if m["snr"] < SIZE_MIN_APERTURE_SNR:
                continue
            # Over the WHOLE aperture, not half of it. A donut's middle is dark,
            # so half the aperture can land entirely inside the hole, where the
            # brightest pixel is noise and fails the neighbour test — this guard
            # was throwing away the one correctly centred measurement of a ring
            # and leaving only the rim fragments, which is how a 40 px ring came
            # back as 3.3 px. The aperture IS the source we are claiming, so it
            # is the region whose brightest pixel has to be a resolved one.
            if not _is_resolved(img, bg, m["y"], m["x"], max(3.0, m["edge"])):
                continue
            m["scale"] = k
            claimed.append((m["y"], m["x"], max(3.0, m["edge"])))
            found.append(m)
    if not found:
        return None
    solid = [m for m in found if not m["truncated"]]
    if not solid:
        # Every source ran off the edge of the frame. Report the biggest as a
        # floor rather than nothing: "at least this big" is still the right
        # answer to "which way is focus", and lower_bound says not to trust the
        # magnitude.
        big = max(found, key=lambda m: m["mean_r"])
        return SourceSize(radius=big["mean_r"], n_sources=1, n_found=len(found),
                          snr=big["snr"], scale=int(big["scale"]),
                          lower_bound=True)
    solid.sort(key=lambda m: -m["flux"])
    brightest = solid[0]
    voters = [m for m in solid if m["flux"] >= SIZE_POPULATION_FRAC * brightest["flux"]]
    return SourceSize(
        radius=float(np.median([m["mean_r"] for m in voters])),
        n_sources=len(voters), n_found=len(found),
        snr=brightest["snr"], scale=int(brightest["scale"]), lower_bound=False)


def size_advice(size: SourceSize | None, *,
                exposure_s: float | None = None) -> str | None:
    """What to change, when the number is missing or is not to be trusted.

    ``None`` when the measurement stands on its own. Everything here is
    something only this module knows, so the caller does not have to guess a
    cause — guessing one is what sent the user outside to check the sky on a
    clear night."""
    if size is None:
        longer = (f" Try {exposure_s * 4:g}s instead of {exposure_s:g}s."
                  if exposure_s else "")
        return ("No source rose above the noise anywhere in the frame. Far from "
                "focus a star's light is spread over hundreds of pixels, so a "
                "wide sweep needs a far longer exposure than a near-focus one — "
                "this rig recorded nothing at all past about 1500 steps at 4s."
                + longer)
    if size.lower_bound:
        return (f"The blob runs past the edge of the frame, so {size.radius:.0f}px "
                "is a floor and not a measurement. Move back toward focus, or "
                "sweep with a wider field (lower binning), before trusting the "
                "size.")
    return None


#: A pixel at/above this fraction of the container's ceiling counts as clipped.
#: 0.98 rather than exact equality so a 12/14-bit sensor left-shifted into a
#: 16-bit container (rails at 65520/65532, not 65535) still registers.
SATURATION_LEVEL_FRAC = 0.98

#: Clipped-pixel fraction at/above which a frame is overexposed for star
#: MEASUREMENT. Metered against every frame this rig captured over two weeks
#: (rich fields, 10 s exposures, gain 300): the worst healthy frame clipped
#: 0.0015% of its sampled pixels — bright star cores and nothing else — so
#: 0.2% sits two orders of magnitude above the healthy side while a frame
#: whose stars have merged into railed blobs sits far above it. The number a
#: caller should compare against ``saturation_fraction``.
OVEREXPOSED_FRAC = 0.002


def saturation_fraction(data: np.ndarray) -> float:
    """Fraction of sampled pixels sitting at the container's ceiling.

    The one question this answers: did the sensor run out of scale? An
    unmeasurable frame cannot say WHY it is unmeasurable — "0 stars" reads the
    same off a starless field and off a field so overexposed its stars merged
    into one railed blob, and on 2026-08-06 that ambiguity told an operator
    under 200 visible stars to expose LONGER. This is the cheap fact that
    separates the two, sampled every 8th pixel (~64x cheaper, and clipping
    that matters is never confined to one pixel in 64).
    """
    px = np.asarray(data)[::8, ::8]
    if px.size == 0:
        return 0.0
    if np.issubdtype(px.dtype, np.integer):
        ceiling = float(np.iinfo(px.dtype).max)
    else:
        # Float frames carry no container ceiling. A normalized [0,1] frame
        # rails at 1.0; anything else can only report pixels at its own max —
        # which stays 0-ish on healthy frames (a lone hot pixel IS the max).
        top = float(px.max())
        ceiling = 1.0 if top <= 1.0 else top
    return float(np.mean(px.astype(np.float64)
                         >= SATURATION_LEVEL_FRAC * ceiling))


#: The smallest half-flux radius that can be a STAR rather than a PIXEL.
#:
#: Below one pixel the flux is contained in a single pixel, which is not a
#: resolved image of anything at any binning. It matters because the number is
#: SMALL: on a defocused frame the detector finds a few noise peaks, reports
#: half a pixel, and that outranks the true minimum instead of merely adding
#: noise to it.
#:
#: Measured 2026-08-17 (NGC 7129, Ha, 24 s gain 200 bin 1): focus was 2.96 px
#: from 1172 stars at 11135, and the sweep walked out of its own range chasing
#: 0.50 / 0.51 / 0.52 px from four to twelve "stars" until nothing was
#: measurable. See #219.
MIN_SIZE_PX = 1.0


def focus_size(data: np.ndarray, min_stars: int = 3) -> tuple[float | None, int]:
    """``(size in px, sources behind it)`` — the drop-in an autofocus sweep wants.

    Same shape as ``median_hfr``, and near focus the SAME NUMBER — ``star_size``
    answers there with the grader's own median over the grader's own population
    (GN-05) — but it keeps rising all the way out instead of turning over once
    the star outgrows a cutout. ``min_stars`` still guards the fit, with one
    deliberate exception:
    a single source measured at SIZE_CONFIDENT_SNR is admitted alone, because
    at 1000 steps out a rich field legitimately yields two or three measurable
    donuts and dropping those points is precisely how the sweep came back
    'not_enough_spread' with a perfect V sitting in the data."""
    size = star_size(data)
    if size is None:
        return None, 0
    if size.n_sources < min_stars and size.snr < SIZE_CONFIDENT_SNR:
        return None, size.n_sources
    # A half-flux radius inside one pixel is a pixel, not a star. Mirrors
    # `focus.autofocus._size_point` - the two are one rule in two places by
    # design, and a floor on only one of them is how they drift (#219).
    if size.radius < MIN_SIZE_PX:
        return None, size.n_sources
    return size.radius, size.n_sources


def _mid_bright_gate(stars: list[Star], full_well: int | None) -> tuple[float, float | None]:
    """``(peak floor, saturation ceiling|None)`` for the "trusted mid-bright"
    population: brighter than 5% of the median peak (above the noise-floor
    detections) and below 90% of full well (not a flat-top). ONE definition,
    shared by ``star_marks`` (which attaches ecc/theta only there) and
    ``star_flux_median`` (which averages flux only there)."""
    floor = float(np.median([s.peak for s in stars])) * 0.05 if stars else 0.0
    sat = (full_well * 0.9) if full_well else None
    return floor, sat


def star_flux_median(stars: list[Star], *, full_well: int | None = None) -> float | None:
    """Median background-subtracted flux (ADU) over the trusted mid-bright stars.

    The client turns this into an honest per-SUB SNR (flux x e-/ADU against the
    sub's sky+read noise) — never a stacked-SNR claim. Deliberately a standalone
    helper rather than a 4th ``measure_stars`` return value: the hot measure path
    keeps its 3-tuple contract and every existing call site is untouched.

    ``None`` when no star passes the gate (empty list, all saturated, or all at
    the noise floor) — the caller then omits the key and the client shows
    nothing rather than a number built from noise."""
    if not stars:
        return None
    floor, sat = _mid_bright_gate(stars, full_well)
    fluxes = [float(s.flux) for s in stars
              if s.peak > floor and (sat is None or s.peak < sat)]
    return float(np.median(fluxes)) if fluxes else None


def star_marks(stars: list[Star], *, full_well: int | None = None,
               max_marks: int = DEFAULT_MAX_MARKS) -> list[dict]:
    """Compact per-star overlay payload: ``[{x, y, hfr[, ecc, theta]}]``.

    Coords are in ``frame.data`` pixel space (the detector ran there); the
    client scales by ``display_width / data_width`` (spec finding #2). Rounded
    to keep the event small. ``ecc``/``theta`` are only measured for the
    unsaturated, mid-bright population ``median_hfr`` already trusts — never on
    saturated flat-top stars (spec finding #5), where they stay 0.0 and are
    omitted.

    ``max_marks`` MUST be >= ``detect_stars``' ``max_stars`` so the overlay never
    silently drops a star the detector found — otherwise a future bump of
    ``max_stars`` above ``max_marks`` would diverge ``len(marks)`` from the star
    count. The default cap pair (DEFAULT_MAX_MARKS=400 >= DEFAULT_MAX_STARS=200)
    holds this invariant by construction (P3-5).
    """
    assert max_marks >= DEFAULT_MAX_STARS, (
        "star_marks max_marks must be >= detect_stars max_stars so the overlay "
        "never drops detected stars")
    marks: list[dict] = []
    floor, sat = _mid_bright_gate(stars, full_well)
    for s in sorted(stars, key=lambda s: -s.flux)[:max_marks]:
        m = {"x": round(float(s.x), 1), "y": round(float(s.y), 1),
             "hfr": round(float(s.hfr), 2)}
        unsaturated_mid = (s.peak > floor and (sat is None or s.peak < sat))
        if s.ecc and unsaturated_mid:
            m["ecc"] = round(float(s.ecc), 3)
            m["theta"] = round(float(s.theta), 3)
        marks.append(m)
    return marks


def frame_eccentricity(marks: list[dict]) -> float | None:
    """Representative frame eccentricity: median of the trusted marks' ``ecc``
    (the mid-bright unsaturated population ``star_marks`` already attached ecc
    to). ``None`` when no star carried an ecc — the gate then abstains."""
    eccs = [m["ecc"] for m in marks if "ecc" in m]
    return float(np.median(eccs)) if eccs else None


# ---------------------------------------------------------------------------
# Sensor-tilt / corner-vs-center optical-aberration inspector (PRO-13).
#
# Purely additive aggregation over the SAME `marks` frame_eccentricity reads —
# no new detection pass, no reject gate. Bins marks into a cols x rows grid by
# `data`-space x,y and classifies the spatial HFR/ecc/theta pattern into one of
# uniform / tilt / coma / tracking. See docs/superpowers/specs/
# 2026-07-23-tilt-inspector-design.md for the full design + rationale.
# ---------------------------------------------------------------------------

#: a zone needs >= this many binned marks to report hfr/ecc/theta (else None).
_TILT_MIN_ZONE_STARS = 3
#: need >= this many populated zones (of grid*grid) to classify at all.
_TILT_MIN_POPULATED = 4
#: relative HFR spread (max-min)/median below this (+ round) => uniform.
_TILT_FLAT_TOL = 0.15
#: mean ecc below this => round.
_TILT_ROUND_TOL = 0.20
#: mean ecc at/above this => elongated enough for tracking.
_TILT_ELONG_TOL = 0.35
#: axis_spread below this => one common elongation direction.
_TILT_AXIS_ALIGN_TOL = 0.25
#: fraction of zones radially aligned => radial (coma).
_TILT_RADIAL_FRAC_TOL = 0.55
#: radians (~29deg): theta-vs-radial alignment tolerance.
_TILT_RADIAL_ANGLE = 0.5
#: (corner_mean - center)/center at/above => corners degraded (coma).
_TILT_RADIAL_EXCESS = 0.25
#: normalized planar HFR gradient at/above => asymmetric tilt.
_TILT_GRAD_TOL = 0.20


def _axis_mean(thetas: list[float]) -> float:
    """Doubled-angle circular mean of axial angles (mod pi), radians."""
    c = float(np.mean(np.cos(2.0 * np.asarray(thetas))))
    s = float(np.mean(np.sin(2.0 * np.asarray(thetas))))
    return 0.5 * math.atan2(s, c)


def _axis_spread(thetas: list[float]) -> float:
    """Doubled-angle circular spread: 0 (all one axis) .. 1 (scattered)."""
    c = float(np.mean(np.cos(2.0 * np.asarray(thetas))))
    s = float(np.mean(np.sin(2.0 * np.asarray(thetas))))
    return float(1.0 - math.hypot(c, s))


def _axis_diff(a: float, b: float) -> float:
    """Acute angle between two axes (mod pi), 0..pi/2."""
    d = abs(a - b) % math.pi
    return min(d, math.pi - d)


def _bin_zones(marks: list[dict], width: float, height: float,
               cols: int, rows: int) -> list[dict]:
    """Row-major zones: ``[{'hfr','ecc','theta','n'}, ...]`` (len == rows*cols).

    ``hfr`` is the zone's median (present on every mark); ``ecc`` is the mean
    of the trusted subset that carries it; ``theta`` is their doubled-angle
    circular mean. Zones with ``n < _TILT_MIN_ZONE_STARS`` report
    ``hfr/ecc/theta = None`` but keep ``n`` (honest: too few stars to trust).
    """
    buckets: list[list[dict]] = [[] for _ in range(cols * rows)]
    for m in marks:
        x, y = m["x"], m["y"]
        if not (0.0 <= x < width and 0.0 <= y < height):
            continue
        c = min(cols - 1, int(x / width * cols))
        r = min(rows - 1, int(y / height * rows))
        buckets[r * cols + c].append(m)
    zones = []
    for b in buckets:
        n = len(b)
        if n >= _TILT_MIN_ZONE_STARS:
            hfr = float(np.median([m["hfr"] for m in b]))
            eccs = [m["ecc"] for m in b if "ecc" in m]
            thetas = [m["theta"] for m in b if "theta" in m]
            ecc = float(np.mean(eccs)) if eccs else None
            theta = _axis_mean(thetas) if thetas else None
        else:
            hfr = ecc = theta = None
        zones.append({
            "hfr": round(hfr, 2) if hfr is not None else None,
            "ecc": round(ecc, 3) if ecc is not None else None,
            "theta": round(theta, 3) if theta is not None else None,
            "n": n,
        })
    return zones


def _zone_center_frac(i: int, cols: int, rows: int) -> tuple[float, float]:
    """Fractional (x,y) in [0,1] of zone ``i``'s center, row-major."""
    r, c = divmod(i, cols)
    return ((c + 0.5) / cols, (r + 0.5) / rows)


def _classify(zones: list[dict], cols: int, rows: int) -> tuple[str, float, int | None]:
    """Classify the zone map. Returns ``(pattern, severity, worst_zone)``,
    ``pattern in {'uniform','tilt','coma','tracking'}``. Caller guarantees
    >= _TILT_MIN_POPULATED populated zones."""
    pop = [(i, z) for i, z in enumerate(zones) if z["hfr"] is not None]
    hfrs = [z["hfr"] for _, z in pop]
    med = float(np.median(hfrs))
    rel_spread = (max(hfrs) - min(hfrs)) / med if med > 0 else 0.0
    worst = max(pop, key=lambda iz: iz[1]["hfr"])[0]

    eccs = [z["ecc"] for _, z in pop if z["ecc"] is not None]
    mean_ecc = float(np.mean(eccs)) if eccs else 0.0
    thetas = [z["theta"] for _, z in pop if z["theta"] is not None]
    axis_spread = _axis_spread(thetas) if len(thetas) >= 2 else 1.0

    # planar HFR gradient (asymmetry): first/last populated column & row means.
    col_means: list[list[float]] = [[] for _ in range(cols)]
    row_means: list[list[float]] = [[] for _ in range(rows)]
    for i, z in pop:
        r, c = divmod(i, cols)
        col_means[c].append(z["hfr"])
        row_means[r].append(z["hfr"])

    def _span(groups: list[list[float]]) -> float:
        ms = [float(np.mean(g)) for g in groups if g]
        return (ms[-1] - ms[0]) if len(ms) >= 2 else 0.0

    tilt_mag = math.hypot(_span(col_means), _span(row_means)) / med if med > 0 else 0.0

    # radial excess: geometric corners vs the center cell.
    center_i = (rows // 2) * cols + (cols // 2)
    corner_ix = [0, cols - 1, (rows - 1) * cols, rows * cols - 1]
    center = zones[center_i]["hfr"]
    ch = [zones[i]["hfr"] for i in corner_ix if zones[i]["hfr"] is not None]
    radial_excess = ((float(np.mean(ch)) - center) / center
                      if (center and center > 0 and ch) else 0.0)

    # radial alignment of elongation axes.
    aligned = total = 0
    for i, z in enumerate(zones):
        if i == center_i or z["theta"] is None:
            continue
        zx, zy = _zone_center_frac(i, cols, rows)
        radial = math.atan2(zy - 0.5, zx - 0.5)
        total += 1
        if _axis_diff(z["theta"], radial) < _TILT_RADIAL_ANGLE:
            aligned += 1
    radial_frac = aligned / total if total else 0.0

    if rel_spread < _TILT_FLAT_TOL and mean_ecc < _TILT_ROUND_TOL:
        pattern = "uniform"
    elif (mean_ecc >= _TILT_ELONG_TOL and axis_spread < _TILT_AXIS_ALIGN_TOL
          and radial_frac < _TILT_RADIAL_FRAC_TOL):
        pattern = "tracking"
    elif (radial_excess >= _TILT_RADIAL_EXCESS
          and (radial_frac >= _TILT_RADIAL_FRAC_TOL or tilt_mag < _TILT_GRAD_TOL)):
        pattern = "coma"
    elif tilt_mag >= _TILT_GRAD_TOL:
        pattern = "tilt"
    else:
        pattern = "uniform"
    return pattern, rel_spread, worst


def frame_tilt(marks: list[dict], width: float, height: float,
               *, grid: int = 3) -> dict | None:
    """Zone map + pattern classification for the tilt/aberration inspector,
    over the same ``marks`` ``frame_eccentricity`` reads (no new detection
    pass). ``None`` when too few zones have enough stars — the client then
    shows nothing (abstain, exactly like ``frame_eccentricity`` -> ``None``).

    Block shape: ``{'cols','rows','zones':[{hfr,ecc,theta,n}...],'pattern',
    'severity','worst_zone'}``.
    """
    if not marks or width <= 0 or height <= 0:
        return None
    cols = rows = grid
    zones = _bin_zones(marks, width, height, cols, rows)
    if sum(1 for z in zones if z["hfr"] is not None) < _TILT_MIN_POPULATED:
        return None
    pattern, severity, worst = _classify(zones, cols, rows)
    return {"cols": cols, "rows": rows, "zones": zones,
            "pattern": pattern, "severity": round(severity, 3),
            "worst_zone": worst}


def measure_stars(stars: list[Star], *, full_well: int | None = None,
                  min_stars: int = 3) -> tuple[float | None, int, list[dict]]:
    """Derive ``(median_hfr, star_count, star_marks)`` from an already-detected
    star list — so a caller that also needs the raw ``Star`` objects (e.g. cloud
    detection, which inspects per-star peaks) runs ``detect_stars`` exactly once
    and feeds every consumer from that one pass."""
    hfr, count = _median_hfr_from(stars, min_stars)
    return hfr, count, star_marks(stars, full_well=full_well)


def measure_frame(data: np.ndarray, *, full_well: int | None = None,
                  min_stars: int = 3) -> tuple[float | None, int, list[dict]]:
    """One detection pass feeding both the focus metric and the overlay.

    Returns ``(median_hfr, star_count, star_marks)`` so the capture hot path
    never detects twice (spec §6 / §4.6)."""
    return measure_stars(detect_stars(data), full_well=full_well, min_stars=min_stars)


def grade_frame(data: np.ndarray, *, full_well: int | None = None,
                stars: list[Star] | None = None,
                min_stars: int = 3) -> dict:
    """Every per-frame quality number the sequence engine grades a sub on, from
    ONE detection pass: ``{hfr, stars, star_list, star_flux_median, ecc, tilt}``.

    A key is present only when it could be measured -- an honest abstain, the
    same contract each underlying helper already has: no star passes the
    mid-bright gate and there is no ``star_flux_median``; no mark carries an
    ecc and there is no ``ecc``; too few populated zones and there is no
    ``tilt``. ``star_list`` is always present (possibly empty).

    WHY THIS EXISTS AS A FUNCTION. ``hub._publish_preview`` computed these six
    inline, interleaved with JPEG rendering and histograms, and
    ``engine._check_quality`` grades the dict that came out of it. So the gate
    could only ever be tested against a hand-built ``info``, and on 2026-09-06
    every trailed sub of the night was accepted by a gate whose tests were all
    green. Lifting the pure half out is what lets a test put a real trailed
    frame through the real grader and the real gate.

    ``stars`` lets a caller that already ran ``detect_stars`` (the hub, which
    also feeds cloud detection and the live stacker from that one pass) reuse
    it rather than detect twice.
    """
    if stars is None:
        stars = detect_stars(data)
    hfr, count, marks = measure_stars(stars, full_well=full_well,
                                      min_stars=min_stars)
    out: dict = {"star_list": marks}
    if hfr is not None:
        out["hfr"] = round(float(hfr), 2)
        out["stars"] = int(count)
    fmed = star_flux_median(stars, full_well=full_well)
    if fmed is not None:
        out["star_flux_median"] = round(fmed, 1)
    fecc = frame_eccentricity(marks)
    if fecc is not None:
        out["ecc"] = round(fecc, 3)
    tilt = frame_tilt(marks, float(data.shape[1]), float(data.shape[0]))
    if tilt is not None:
        out["tilt"] = tilt
    return out
