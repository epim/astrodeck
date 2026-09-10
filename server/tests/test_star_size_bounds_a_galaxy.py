"""A galaxy is not the frame's source size, and the stars say how big is too big.

THE DEFECT, diagnosed on the real 6252x4176 subs of 2026-09-05/06 and fixed
2026-09-10. ``star_size`` answered 839.61 px at pyramid scale 64 on B_0032 — a
B sub of the M33 field carrying 200 detections whose own median box HFR is
3.69 px. Rendered, the answer is a 840 px circle around M33's disk.

WHAT THE CROPS SHOWED, because the first diagnosis was wrong. The 2026-09-08
note blamed the ranking: a star sitting on nebulosity carrying the galaxy's
pedestal in ``Star.flux`` and so out-ranking the field. On B_0032 that is not
what happens. All 25 of ``_bright_population``'s probes are spread right across
the frame — only one is anywhere near M33 — and the annulus under each sits
0.5 sigma (median) to 1.3 sigma (max) above the frame's own sky. What every
4x crop DOES show is a bright core with a ~30 px comet tail, ecc 0.35-0.78: the
frame is trailed, so the fine path's 1.287 truncation reading is a true
statement about those stars and refusing to answer with the box is right.

What produced 839.61 is the PYRAMID. One level-64 seed grew its aperture to
edge 1720 px and came back mean_r 839.61 with flux 1.075e8 — 13x the brightest
STAR the same pass measured — and ``SIZE_POPULATION_FRAC`` then left it the sole
voter over ten 6-7 px stars, because flux for an extended source scales with
its AREA. ``imaging.defocus.measure_defocus`` failed the same way from the same
cause: on L_0006 the M33 blob was the ONLY source its twelve probes ever
reached, because a truncated claim reserves twice its own edge.

THE FIX is ``imaging.stars.resolved_star_scale``: a source more than
SIZE_MAX_STAR_MULTIPLE times the size of the stars this frame resolves is not
one of this frame's sources. No gate threshold moved. Measured on every full
frame of that night still on disk (grader = ``median_hfr``, before/after are
``star_size`` and ``measure_defocus``):

    frame     grader  star_size before -> after   measure_defocus before -> after
    R_0007      2.73     2.73 stars     2.73        (in focus, not measured)
    S_0005      2.80     2.80 stars     2.80        (in focus, not measured)
    R_0002      2.98     2.98 stars     2.98        (in focus, not measured)
    R_0030      2.94     2.94 stars     2.94        (in focus, not measured)
    S_0017      2.95     2.95 stars     2.95        (in focus, not measured)
    Ha_0018     2.83     2.83 stars     2.83        (in focus, not measured)
    G_0003      3.10     3.10 stars     3.10        (in focus, not measured)
    B_0004      3.38     3.38 stars     3.38        (in focus, not measured)
    B_0032      3.69   839.61 @64       6.63 @4        10.0            10.0
    G_0031      3.78     8.74 @4        8.74 @4       734.0            18.0
    L_0006      4.05     6.26 @4        6.16 @4      1110.0            10.0
    L_0026      4.13     6.19 @4        5.90 @4      1086.0            10.0
    B_0025      4.07     7.74 @8        7.74 @8        62.0            18.0
    L_0001      4.06     7.89 @4        7.89 @4        10.0            10.0

L_0001 is the real donut frame the first autofocus of that night produced and
it is UNCHANGED, which is the point: the bound is 12 x 3.69 = 44 px there, and
nothing in the frame is near it.

NO REAL FRAME IS COMMITTED HERE. They are 50 MB each and carry site headers;
the 512-2048 px crops in ``fixtures/ngc604_20260906`` and the synthetics below
are what the assertions run on.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from astrodeck.imaging.defocus import measure_blob, measure_defocus
from astrodeck.imaging.stars import (
    SIZE_FINE_MAX_BOX_HFR, SIZE_MAX_STAR_MULTIPLE, SIZE_RESOLVED_MIN_STARS,
    detect_stars, median_hfr, resolved_star_scale, star_size,
)

FIXTURES = Path(__file__).parent / "fixtures" / "ngc604_20260906"
SWEEP = Path(__file__).parent / "fixtures" / "focus_sweep"

#: Every off-focus point of the real 2026-07-31 sweep that the size metric is
#: graded on. These are the frames the bound must never touch.
SWEEP_POSITIONS = (4900, 7900, 8900, 9300, 9600, 10200, 10500, 10900, 11900)


def _frame(name: str) -> np.ndarray:
    return fits.getdata(FIXTURES / f"{name}.fits.gz").astype(np.float64)


def _sweep(pos: int) -> np.ndarray:
    return np.load(SWEEP / f"focus_{pos}.npz")["data"].astype(np.float64)


def _ring_field(side: int = 512, *, ring_r: float, n: int = 12,
                amp: float = 2.0e5, seed: int = 11) -> np.ndarray:
    """A field of identical annuli — a real defocused field's shape."""
    rng = np.random.default_rng(seed)
    img = rng.normal(600.0, 12.0, (side, side))
    half = int(ring_r + 6)
    yy, xx = np.mgrid[-half:half + 1, -half:half + 1]
    psf = np.exp(-((np.hypot(yy, xx) - ring_r) ** 2) / (2 * 2.0 ** 2))
    psf /= psf.sum()
    placed: list[tuple[int, int]] = []
    for _ in range(6000):
        if len(placed) >= n:
            break
        y = int(rng.integers(half + 2, side - half - 2))
        x = int(rng.integers(half + 2, side - half - 2))
        if all((y - py) ** 2 + (x - px) ** 2 > (3 * half) ** 2
               for py, px in placed):
            placed.append((y, x))
    for (y, x) in placed:
        img[y - half:y + half + 1, x - half:x + half + 1] += amp * psf
    return img


def _trailed_field_with_a_halo(side: int = 1024, *, n: int = 60,
                               amp: float = 3.0e5, trail: float = 14.0,
                               sigma: float = 1.6, halo: float = 900.0,
                               halo_sigma: float = 140.0, seed: int = 5,
                               sky: float = 600.0,
                               noise: float = 12.0) -> np.ndarray:
    """B_0032's SHAPE, committable: stars the box cannot quite hold, beside one
    big object of LOW surface brightness.

    The halo peaks at 25 sigma above sky while the stars peak at 184 sigma, so
    nothing is re-ranked and no probe carries a pedestal — which is what the
    real frame measures. The halo is nonetheless 140 px wide, so its aperture
    flux dwarfs every star's and it wins a flux vote outright.
    """
    rng = np.random.default_rng(seed)
    img = rng.normal(sky, noise, (side, side))
    half = int(max(8, trail + 5 * sigma))
    yy, xx = np.mgrid[-half:half + 1, -half:half + 1]
    psf = np.zeros_like(yy, dtype=float)
    for t in np.linspace(0.0, trail, 40):
        psf += np.exp(-(((xx - t) ** 2 + yy ** 2) / (2 * sigma ** 2)))
    psf /= psf.sum()
    placed: list[tuple[int, int]] = []
    for _ in range(9000):
        if len(placed) >= n:
            break
        y = int(rng.integers(half + 2, side - half - 2))
        x = int(rng.integers(half + 2, side - half - 2))
        if all((y - py) ** 2 + (x - px) ** 2 > (3 * half) ** 2
               for py, px in placed):
            placed.append((y, x))
    for (y, x) in placed:
        img[y - half:y + half + 1, x - half:x + half + 1] += amp * psf
    yy, xx = np.mgrid[0:side, 0:side]
    img += halo * np.exp(-((yy - side * 0.42) ** 2 + (xx - side * 0.55) ** 2)
                         / (2 * halo_sigma ** 2))
    return img


# ------------------------------------------- (a) the bound's own precondition

def test_a_frame_that_resolves_no_stars_is_never_bounded():
    """The property that keeps the blast radius at zero for a real sweep.

    ``resolved_star_scale`` counts detections the 15 px box saw WHOLE. A
    defocused field has none — its detections are rim fragments pinned at the
    box's saturation reading — so the bound has nothing to measure against and
    the pyramid is left exactly as it was. Measured compact-detection counts:
    0 at every one of the nine off-focus sweep points, 0 in every synthetic
    ring field, 1 in donut_L60 and 3 in donutfield_L60, against 6-200 on every
    frame whose stars are resolved.
    """
    for pos in SWEEP_POSITIONS:
        img = _sweep(pos)
        stars = detect_stars(img)
        compact = [s for s in stars if s.hfr < SIZE_FINE_MAX_BOX_HFR]
        assert resolved_star_scale(img, stars=stars) is None, (
            f"sweep {pos} is {abs(pos - 9900)} steps out of focus and "
            f"{len(compact)} of its {len(stars)} detections read compact — the "
            "bound would apply to a frame full of donuts")
    for name in ("donut_L60", "donutfield_L60"):
        img = _frame(name)
        assert resolved_star_scale(img) is None, f"{name} was bounded"
    for r in (8.0, 20.0, 45.0, 90.0):
        img = _ring_field(ring_r=r)
        assert resolved_star_scale(img) is None, (
            f"a field of {r:.0f}px rings was bounded")


def test_a_frame_that_resolves_stars_reports_their_scale():
    """The other side, and the number the bound is a multiple of."""
    for name, lo, hi in (("clean_R60", 2.0, 3.5), ("m33field_G60", 2.5, 4.0),
                         ("jump_R60", 2.0, 3.8), ("m33core_G60", 2.5, 4.0)):
        scale = resolved_star_scale(_frame(name))
        assert scale is not None, f"{name} resolves no stars"
        assert lo < scale < hi, f"{name}: compact median {scale:.2f}px"
    assert SIZE_RESOLVED_MIN_STARS >= 5, (
        "below five detections there is no population to take a median of")


# --------------------------------------- (b) the failure, at the seam it broke

def test_a_big_faint_object_does_not_become_a_trailed_frames_size(monkeypatch):
    """B_0032's shape, and the assertion is load-bearing: with the bound
    disabled the same frame comes back two orders of magnitude bigger.

    Sixty trailed stars the box reads at 3.85 px — so the fine path is refused,
    correctly — beside one 140 px halo at a twenty-fifth of their peak
    brightness. At the 2026-09-08 HEAD ``star_size`` answered 175.12 px from a
    single level-64 voter and ``measure_defocus`` published r80 242 px.
    """
    img = _trailed_field_with_a_halo()
    grader, n = median_hfr(img)
    stars = detect_stars(img)
    assert grader is not None and n >= 50, f"fixture drifted: {n} detections"
    assert grader >= SIZE_FINE_MAX_BOX_HFR, (
        f"fixture drifted: the box reads {grader:.2f}px, so the fine path is "
        "no longer refused and this frame no longer reaches the pyramid")

    size = star_size(img)
    assert size is not None
    assert size.radius == pytest.approx(grader), (
        f"answered {size.radius:.2f}px ({size.source}, scale {size.scale}) on "
        f"a frame whose {n} stars grade {grader:.2f}px")
    blob = measure_defocus(img, stars=stars)
    assert blob is None or blob.r80 < SIZE_MAX_STAR_MULTIPLE * grader, (
        f"the defocus readout published r80 {blob.r80:.0f}px on a frame whose "
        f"stars are {grader:.2f}px")

    # THE SAME FRAME WITH THE BOUND REMOVED — both consumers at once, since
    # ``resolved_star_scale`` is the only route to it. If this half stops
    # reproducing the defect, the assertions above have stopped grading
    # anything.
    import astrodeck.imaging.stars as S
    monkeypatch.setattr(S, "_resolved_star_scale", lambda *a, **k: None)
    unbounded = star_size(img)
    assert unbounded is not None
    assert unbounded.radius > 20.0 * grader and unbounded.scale >= 8, (
        f"without the bound this frame answers {unbounded.radius:.2f}px at "
        f"scale {unbounded.scale} — it no longer reproduces the defect, so "
        "the assertions above are not being tested by anything")
    unbounded_blob = measure_defocus(img, stars=stars)
    assert unbounded_blob is not None
    assert unbounded_blob.r80 > 20.0 * grader, (
        f"without the bound the defocus readout publishes "
        f"{unbounded_blob.r80:.0f}px, which is not the defect either")


def test_the_bound_reads_a_shape_and_not_a_brightness():
    """Every number in this path is a ratio or a multiple of sigma, so a gain
    change and a sky pedestal must move none of them — the property
    ``test_star_gate_is_brightness_independent`` exists for, extended to the
    bound added on 2026-09-10."""
    img = _trailed_field_with_a_halo()
    base_scale = resolved_star_scale(img)
    base = star_size(img)
    assert base_scale is not None and base is not None
    for gain, sky in ((0.0137, 0.0), (6.3, 0.0), (997.0, 0.0),
                      (1.0, 5000.0), (6.3, 12345.0)):
        other = img * gain + sky
        assert resolved_star_scale(other) == pytest.approx(
            base_scale, rel=1e-6), f"the scale moved at gain {gain}, sky {sky}"
        got = star_size(other)
        assert got is not None and got.source == base.source
        assert got.radius == pytest.approx(base.radius, rel=1e-6), (
            f"gain {gain} on a +{sky} sky moved the answer from "
            f"{base.radius} to {got.radius}")


# ---------------------------------- (c) what the bound must NOT have changed

@pytest.mark.parametrize("name", ["donut_L60", "donutfield_L60"])
def test_a_real_donut_field_still_goes_to_the_pyramid_and_reports_large(name):
    """The regime the pyramid exists for. The bound cannot reach these frames
    (they resolve 1 and 3 compact detections), and the check is on the answer
    rather than on that reason: a defocused field must still read far above a
    focused one, or the sweep is told a donut is in focus."""
    donut = star_size(_frame(name))
    clean = star_size(_frame("clean_R60"))
    assert donut is not None and clean is not None
    assert donut.source == "pyramid", (
        f"{name} answered from the fine path at {donut.radius:.2f}px")
    assert donut.radius >= 3.0 and donut.radius > 2.0 * clean.radius, (
        f"{name}: {donut.radius:.2f}px against {clean.radius:.2f}px in focus")


@pytest.mark.parametrize("ring_r", [8.0, 20.0, 45.0, 90.0])
def test_a_synthetic_donut_field_still_measures_the_ring(ring_r):
    """The same, with the truth known: a field of annuli, none of which the
    bound may touch."""
    size = star_size(_ring_field(ring_r=ring_r))
    assert size is not None and size.source == "pyramid", (
        f"a field of {ring_r:.0f}px rings answered from the fine path")
    assert size.radius >= 3.0, (
        f"a {ring_r:.0f}px ring measured {size.radius:.2f}px")


def test_the_sweep_curve_is_untouched():
    """The acceptance test for this metric is ``test_focus_metric.py``; this is
    the narrower statement that the 2026-09-10 bound is why it still passes.
    Each of these points reads what it read before, to the last digit."""
    want = {8900: 74.85, 9300: 44.19, 9600: 41.79, 10200: 25.91,
            10500: 51.81, 10900: 79.76}
    for pos, expect in want.items():
        size = star_size(_sweep(pos))
        assert size is not None and size.source == "pyramid"
        assert size.radius == pytest.approx(expect, abs=0.01), (
            f"sweep {pos} moved from {expect} to {size.radius:.2f}")


def test_coarse_focus_is_never_bounded():
    """``focus.coarse`` calls ``measure_blob`` and must be told a 400 px donut
    is a 400 px donut. The bound is ``measure_defocus``'s alone, so the default
    has to stay open — and the blob a real sweep point holds is still there."""
    import inspect
    default = inspect.signature(measure_blob).parameters["max_r80"].default
    assert default is None, (
        "measure_blob now bounds itself by default, so coarse focus would "
        "refuse the donut it exists to measure")
    for pos in (8900, 10900):
        blob = measure_blob(_sweep(pos))
        assert blob is not None and blob.r80 > 50.0, (
            f"sweep {pos}: measure_blob reports "
            f"{None if blob is None else blob.r80}")


# ------------------------------------------ (d) the preview's own regression

def test_the_defocus_readout_stays_silent_on_a_resolved_field():
    """What the Focus panel publishes, on the same ramp as
    ``test_star_gate_is_brightness_independent``'s galaxy sweep.

    ``measure_defocus`` feeding ``hub`` is what told a user "far out of focus —
    blob is 2268 px across — run coarse focus first" on an in-focus 60 s L sub
    of NGC 604. A frame carrying sixty measurable stars must never produce a
    defocus reading out of scale with them, at any galaxy brightness.
    """
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    from test_star_gate_is_brightness_independent import _field
    for galaxy in (0.0, 5e3, 1e4, 2e4, 5e4, 1e5, 3e5):
        img = _field(sigma=1.5, n=60, amp=2e5, galaxy=galaxy)
        stars = detect_stars(img)
        grader, n = median_hfr(img)
        assert grader is not None and n >= 60
        blob = measure_defocus(img, stars=stars)
        assert blob is None or blob.r80 <= SIZE_MAX_STAR_MULTIPLE * grader, (
            f"galaxy peak {galaxy:.0f}: the preview would publish r80 "
            f"{blob.r80:.0f}px on a frame of {n} stars grading "
            f"{grader:.2f}px")


@pytest.mark.parametrize("pos", [8900, 9300, 10500, 10900])
def test_the_defocus_readout_still_reports_a_real_defocus_blob(pos):
    """The other half, and the one a bound is most likely to break: on a
    genuinely defocused frame the preview must still say how big the blob is.
    ``focus.coarse`` has no second instrument."""
    blob = measure_defocus(_sweep(pos))
    assert blob is not None, (
        f"sweep {pos} is {abs(pos - 9900)} steps out of focus and the defocus "
        "readout went silent")
    assert blob.r80 > 20.0, f"sweep {pos}: r80 {blob.r80:.0f}px"
