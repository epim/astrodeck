"""The autofocus size metric and the frame grader must agree at focus (GN-05).

THE DEFECT, measured on last night's real frames (NGC 604, 2026-09-05/06).
``imaging.stars.star_size`` is what an autofocus sweep fits (``focus.autofocus.
sweep_metric`` -> ``star_size`` -> ``_size_point``); ``median_hfr`` is what
grades every sub that comes out of the run. They are supposed to be the same
number at focus, and a comment in ``_measure_source`` said so. On the same
pixels, thirds of last night's frames read:

    L  grader 4.07/3.94/3.84   size  8.02/8.84/13.14
    R  grader 2.95/2.98/3.09   size  4.06/4.39/ 6.74
    G  grader 3.14/3.21/3.22   size  4.42/6.36/ 3.89

voting with 4-24 "sources" at pyramid scales 2-16 — on a field whose stars are
resolved and whose only extended structure is M33's core. The first autofocus of
the night landed 74 steps off (fixture ``donut_L60``: every star a donut) and
the vertex the sweep fitted disagreed with what the grader then measured.

THE FIX is FINE FIRST: when the unbinned star population is large enough and
compact, ``star_size`` answers with the median of the SAME estimator over the
SAME population ``median_hfr`` votes with, so the two are equal by construction.
The pyramid runs only when there is no trustworthy star population — a real
donut field, or a frame so far out that the box is measuring itself.

These tests grade that on the real frames. Every assertion runs the REAL
functions on real pixels; nothing here is substituted.
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from astrodeck.imaging.stars import detect_stars, median_hfr, star_size

FIXTURES = Path(__file__).parent / "fixtures" / "ngc604_20260906"


def _frame(name: str) -> np.ndarray:
    return fits.getdata(FIXTURES / f"{name}.fits.gz").astype(np.float64)


def _path(size) -> str:
    """Which path answered. An assertion rather than an attribute access so a
    build without the field fails as a missing CONTRACT and not as a traceback:
    `size_advice` and the sweep both need to know whether the number they were
    handed is a star's or a blob's."""
    assert hasattr(size, "source"), (
        "SourceSize carries no `source` field, so nothing says whether the "
        "star population or the pyramid produced this number")
    return size.source


# --------------------------------------------------------------- (a) at focus

def test_the_size_metric_agrees_with_the_grader_on_a_clean_field():
    """clean_R60: guided on a fresh calibration, round stars, nothing resolved.

    At HEAD the grader read 2.61 and the metric 3.05 — 17% apart on the same
    pixels, from ten pyramid sources at scale 2. 15% is the bar GN-05 sets; the
    fine path makes it zero, because it is the same median over the same stars.
    """
    data = _frame("clean_R60")
    grader, _ = median_hfr(data)
    size = star_size(data)
    assert grader is not None and size is not None
    rel = abs(size.radius - grader) / grader
    assert rel < 0.15, (
        f"grader {grader:.2f}px, star_size {size.radius:.2f}px "
        f"({getattr(size, 'source', 'pyramid')}, scale {size.scale}, "
        f"{size.n_sources} voters) — {rel:.0%} apart")


# ------------------------------------------------- (b) resolved galaxy structure

def test_a_resolved_galaxy_does_not_become_the_sources():
    """m33core_G60 is the M33 core / NGC 604 — resolved nebulosity a coarse
    pyramid level counts as one big "source". At HEAD it answered 4.75 px from
    twelve voters at scale 2 while the stars in the same crop measured 3.21.

    The stars are there and they resolve; that is the whole point. So the answer
    must come from them, and no voter may be a scale >= 8 blob of galaxy."""
    data = _frame("m33core_G60")
    stars = detect_stars(data)
    assert len(stars) >= 10, f"only {len(stars)} level-1 stars in the crop"
    grader, _ = median_hfr(data)
    size = star_size(data)
    assert size is not None and grader is not None
    assert size.scale == 1, (
        f"answered at pyramid scale {size.scale} while {len(stars)} level-1 "
        "stars were sitting there")
    assert _path(size) == "stars"
    assert size.scale < 8, "a coarse-scale blob voted"
    rel = abs(size.radius - grader) / grader
    assert rel < 0.15, f"grader {grader:.2f}, size {size.radius:.2f} ({rel:.0%})"


# ------------------------------------------------------------- (c) real donuts

def test_a_real_donut_field_still_measures_and_still_dwarfs_a_focused_one():
    """donut_L60 is the frame the first autofocus of the night produced: 74
    steps off, every star a ring. The rim fragments it yields at level 1 also
    look like "stars" to the detector and box-truncate to HFR 3.94 — so the
    fine path must refuse them, and the pyramid must still answer.

    A metric that reported 3.94 here would tell the sweep it was in focus."""
    donut = star_size(_frame("donut_L60"))
    clean = star_size(_frame("clean_R60"))
    assert donut is not None, "no size on a frame full of donuts"
    assert clean is not None
    assert _path(donut) == "pyramid", (
        f"the donut field answered from the fine path at {donut.radius:.2f}px")
    assert donut.radius > 2.0 * clean.radius, (
        f"donut {donut.radius:.2f}px against clean {clean.radius:.2f}px — "
        "a defocused frame has to read at least twice a focused one")


# --------------------------------------------------- (d) the discriminator itself

def test_the_compactness_discriminator_separates_a_ring_from_a_star():
    """The gate, graded on real pixels rather than on its own definition.

    ``_box_truncation`` measures each of the brightest stars twice — once with
    ``detect_stars``' fixed 15px box, once on its own radial profile out to the
    noise — and reports the ratio. A star that fits in the box reads ~1.0; a
    ring's rim fragment reads what the box is MISSING. The threshold is a
    constant so that changing it is graded by these frames."""
    from astrodeck.imaging.stars import (
        SIZE_FINE_MAX_TRUNCATION, _bg_sigma, _box_truncation)
    reads = {}
    for name in ("clean_R60", "m33core_G60", "donut_L60"):
        data = _frame(name)
        bg, sigma = _bg_sigma(data)
        probe = _box_truncation(data, bg, sigma, detect_stars(data),
                                min(data.shape) / 2.0)
        assert probe is not None, f"{name}: nothing measurable to probe"
        reads[name] = probe[0]
    assert reads["donut_L60"] > SIZE_FINE_MAX_TRUNCATION, (
        f"donut_L60 reads {reads['donut_L60']:.2f}, threshold "
        f"{SIZE_FINE_MAX_TRUNCATION} — a ring is on the compact side of the gate")
    for name in ("clean_R60", "m33core_G60"):
        assert reads[name] < SIZE_FINE_MAX_TRUNCATION, (
            f"{name} reads {reads[name]:.2f}, threshold "
            f"{SIZE_FINE_MAX_TRUNCATION} — a focused star is on the ring side")


def test_the_box_hfr_gate_sits_under_the_boxs_own_ceiling():
    """The second gate, and why it exists. ``_box_truncation`` compares the box
    against a profile centred on the SAME peak, so when the peak is speckle on a
    huge smooth halo (the far wings of a sweep) both measurements are small and
    the ratio reads ~1.0 while the frame is thousands of steps out. What gives
    those frames away is the box HFR itself: it pins near the box's saturation
    ceiling, where the measurement is of the box and not of a star."""
    import json

    from astrodeck.imaging.stars import (
        HFR_BOX_CEILING_FRACTION, HFR_BOX_PX, SIZE_FINE_MAX_BOX_HFR)
    ceiling = HFR_BOX_CEILING_FRACTION * (HFR_BOX_PX // 2)
    assert SIZE_FINE_MAX_BOX_HFR < ceiling, (
        f"{SIZE_FINE_MAX_BOX_HFR} is at or past the box's own ceiling "
        f"{ceiling:.2f} — the gate would admit a saturated measurement")
    for name in ("clean_R60", "m33core_G60"):
        grader, _ = median_hfr(_frame(name))
        assert grader is not None and grader < SIZE_FINE_MAX_BOX_HFR, (
            f"{name} grades {grader} — the gate now refuses a focused field")
    sweep = Path(__file__).parent / "fixtures" / "focus_sweep"
    entries = {e["focuser_position"]: e
               for e in json.loads((sweep / "manifest.json").read_text())["entries"]}
    for pos in (4900, 8900, 9300, 9600, 10200, 10500, 10900):
        grader, _ = median_hfr(np.load(sweep / entries[pos]["file"])["data"])
        assert grader is not None and grader > SIZE_FINE_MAX_BOX_HFR, (
            f"{pos} is {abs(pos - 9900)} steps out and its box HFR "
            f"{grader} clears the gate — the box is measuring itself there")


# -------------------------------------------------------- (e) synthetic control

def _synthetic(side: int = 512, *, sigma: float | None = None,
               ring_r: float | None = None, n: int = 60,
               seed: int = 11) -> np.ndarray:
    """A field of identical sources: sharp Gaussians, or the same field turned
    into rings. Synthetic ON PURPOSE here — the real frames above cannot hold
    the two shapes with everything else held equal, and this is the regression
    that says a future change did not simply widen the fine path until the
    donuts fell in."""
    rng = np.random.default_rng(seed)
    img = rng.normal(600.0, 12.0, (side, side))
    if sigma is not None:
        s = int(max(6, sigma * 5))
        yy, xx = np.mgrid[-s:s + 1, -s:s + 1]
        psf = np.exp(-(yy ** 2 + xx ** 2) / (2 * sigma ** 2))
    else:
        s = int(ring_r + 6)
        yy, xx = np.mgrid[-s:s + 1, -s:s + 1]
        d = np.hypot(yy, xx)
        psf = np.exp(-((d - ring_r) ** 2) / (2 * 2.0 ** 2))
    psf = psf / psf.sum()
    placed: list[tuple[int, int]] = []
    for _ in range(4000):
        if len(placed) >= n:
            break
        y = int(rng.integers(s + 2, side - s - 2))
        x = int(rng.integers(s + 2, side - s - 2))
        if all((y - py) ** 2 + (x - px) ** 2 > (3 * s) ** 2 for py, px in placed):
            placed.append((y, x))
    for (y, x) in placed:
        img[y - s:y + s + 1, x - s:x + s + 1] += rng.uniform(60000, 400000) * psf
    return img


def test_sharp_gaussians_take_the_fine_path_and_rings_take_the_pyramid():
    """The same field, twice: sigma 1.2 px stars, then rings of radius 9 px.

    Flux-weighted mean radius of a sigma=1.2 Gaussian is 1.50 px, which the box
    measures faithfully — so the fine path must answer, and it must agree with
    the grader. Turn each into a ring and the box can only see an arc, so the
    pyramid has to take over and report the RING, not the arc."""
    sharp = _synthetic(sigma=1.2, n=60)
    size = star_size(sharp)
    grader, _ = median_hfr(sharp)
    assert size is not None and grader is not None
    assert size.scale == 1 and _path(size) == "stars", (
        f"a sigma=1.2 field answered at pyramid scale {size.scale}")
    assert abs(size.radius - grader) < 1e-9, (
        f"fine path {size.radius} against grader {grader} — the fine path is "
        "supposed to BE the grader's median")
    assert abs(size.radius - 1.253 * 1.2) < 0.35, (
        f"{size.radius:.2f} is not a sigma=1.2 star (expect "
        f"{1.253 * 1.2:.2f})")

    rings = _synthetic(ring_r=9.0, n=12)
    big = star_size(rings)
    assert big is not None
    assert _path(big) == "pyramid", (
        f"a field of 9px rings answered from the fine path at "
        f"{big.radius:.2f}px — that is one arc of the ring, not the ring")
    assert 6.0 < big.radius < 12.0, (
        f"a 9px ring measured {big.radius:.2f}px")


def test_a_sparse_field_is_left_to_the_pyramid():
    """The count gate. A median over four detections is not a population, and
    the wings of a sweep legitimately yield two or three measurable donuts —
    which is exactly the case the pyramid and SIZE_CONFIDENT_SNR exist for."""
    from astrodeck.imaging.stars import SIZE_FINE_MIN_STARS
    assert SIZE_FINE_MIN_STARS >= 5
    sparse = _synthetic(sigma=1.2, n=4, seed=17)
    stars = detect_stars(sparse)
    assert len(stars) < SIZE_FINE_MIN_STARS, (
        f"fixture drifted: {len(stars)} detections, so this no longer tests "
        "the count gate")
    size = star_size(sparse)
    assert size is None or _path(size) == "pyramid"


# ------------------------------------------------- the sweep still sees a curve

def test_the_fine_path_does_not_flatten_the_sweep():
    """The property the fine path must not cost: on the real focuser sweep the
    metric still rises away from focus. Fine-first only fires where the box is
    faithful, so every off-focus point stays on the pyramid — this is the check
    that says so, on the same fixtures test_focus_metric.py grades."""
    import json
    sweep = Path(__file__).parent / "fixtures" / "focus_sweep"
    entries = {e["focuser_position"]: e
               for e in json.loads((sweep / "manifest.json").read_text())["entries"]}
    for pos in (8900, 9300, 9600, 10200, 10500, 10900):
        data = np.load(sweep / entries[pos]["file"])["data"]
        size = star_size(data)
        assert size is not None, f"{pos} lost its source"
        assert _path(size) == "pyramid", (
            f"{pos} is {abs(pos - 9900)} steps out of focus and answered from "
            f"the fine path at {size.radius:.2f}px")
        assert size.radius > 20.0, f"{pos} measured {size.radius:.2f}px"


def test_the_size_advice_and_the_sweep_gate_still_read_the_fine_answer():
    """``SourceSize`` grew a field; everything hung off it has to keep working.
    ``_size_point`` is the sweep's accept/refuse rule and ``size_advice`` is what
    it prints when a point is refused."""
    from astrodeck.focus.autofocus import MIN_STARS_PER_POINT, _size_point
    from astrodeck.imaging.stars import focus_size, size_advice

    data = _frame("clean_R60")
    size = star_size(data)
    assert size is not None
    value, n = _size_point(size, MIN_STARS_PER_POINT)
    assert value == pytest.approx(size.radius)
    assert n == size.n_sources >= MIN_STARS_PER_POINT
    assert size_advice(size) is None
    assert focus_size(data)[0] == pytest.approx(size.radius)


def test_the_estimator_is_the_grader_s_and_not_a_lookalike():
    """Equality by construction, stated as an equality. Not "within a
    tolerance": the fine path returns the median of ``Star.hfr`` over the
    population ``median_hfr`` votes with, so any drift between the two is a
    second implementation of the rule and the thing GN-05 removed."""
    for name in ("clean_R60", "m33core_G60"):
        data = _frame(name)
        size = star_size(data)
        grader, _ = median_hfr(data)
        assert size is not None and grader is not None
        assert _path(size) == "stars"
        assert size.radius == grader, (
            f"{name}: {size.radius!r} != {grader!r}")
        assert math.isfinite(size.snr) and size.snr > 0.0


# ----------------------------------------- the hole the new metric uncovered

async def test_a_sweep_that_never_turned_round_is_refused_wherever_the_fit_bends():
    """A strictly falling curve is not bracketed, whatever the parabola says.

    Found by GN-05 rather than aimed at: with the size metric answering from
    the star population near focus, the simulator's out-of-window sweep
    (``test_autofocus_minimum_not_bracketed_fails``, true focus 3000 steps past
    the top) produced nine strictly falling points and ``run_autofocus``
    reported SUCCESS at 20520 — a position it had never measured a minimum at.

    A defocus ramp steepens away from focus, so a parabola fitted to one arm can
    come back a > 0 with its vertex a few steps inside the sampled range, and
    the bracket guard only ever asked where the vertex fell. The shape decides
    now; the fit's curvature on a one-armed sample is not evidence.
    """
    from astrodeck.devices.sim import build_sim_rig
    from astrodeck.focus import autofocus as A
    from astrodeck.focus import run_autofocus

    # The curve the simulator actually produced, as a function of position.
    measured = {17800: 5.260, 18150: 4.874, 18500: 3.742, 18850: 3.620,
                19200: 3.560, 19550: 3.369, 19900: 3.179, 20250: 3.083,
                20600: 2.791}
    xs = np.array(sorted(measured), dtype=float)
    ys = np.array([measured[int(p)] for p in xs], dtype=float)
    a, b, _c = np.polyfit(xs, ys, 2)
    assert a > 0 and xs.min() < -b / (2 * a) < xs.max(), (
        "fixture drifted: this curve no longer places a vertex INSIDE the "
        "sampled range, so it no longer tests the hole")
    assert np.all(np.diff(ys) < 0), "the curve has to be strictly falling"

    parts = build_sim_rig()
    cam, foc = parts["camera"], parts["focuser"]
    await cam.connect()
    await foc.connect()
    start = await foc.get_position()

    def metric(frame, min_stars=3):
        pos = int(frame.focuser_position)
        return measured.get(pos, 3.0 + (pos - 20600) ** 2 / 1e6), 500, None

    A_metric = A.sweep_metric
    A.sweep_metric = metric
    try:
        res = await run_autofocus(cam, foc, exposure_s=0.05, gain=200, step=350,
                                  steps_each_side=4, binning=2)
    finally:
        A.sweep_metric = A_metric

    assert res.success is False, (
        f"accepted {res.best_position} from a curve that never turned round")
    assert "bracket" in res.message.lower(), res.message
    assert res.advice and "never turned round" in res.advice, res.advice
    assert await foc.get_position() == start
