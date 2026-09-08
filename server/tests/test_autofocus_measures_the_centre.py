"""The sweep measures the CENTRE of the frame, ONCE per point.

THE MEASURED PROBLEM. One native sweep on astrotown (v0.3.25, 26 MP Player One,
6 s frames, 9 points) took 7-9 minutes on 2026-09-07, and roughly 80% of every
point was measurement on 26 million pixels while the shutter was shut. Each
point ran TWO full-frame passes:

    astrodeck_native.detect_and_measure   27 s  (60-67 s on a 1462-1999 star light)
    imaging.stars.focus_size               5-20 s (8.8-9.5 s on the same lights)

The Rust pass supplied only a star count and a MAD — both of which ``focus_size``
can supply itself — so it is gone from the sweep loop. The probe keeps it,
because the probe's count is what sizes the window.

The window: measured on the rig's own 26 MP frames, the central 0.4 of each axis
costs 1.0-2.2 s against 8.8-9.5 s full-frame, and read 4.51 px against 4.64 px
whole-frame on an L light (3%). On an Ha light it read 3.27 against 3.98 (18%
SMALLER) — corner stars are bigger on this optic, so a windowed sweep measures
the CENTRE's focus, which is what we want. That is also why the window is sized
ONCE from the probe and used for every point: a window that changed mid-sweep
would put a centre-vs-corner step into the middle of the curve.
"""
import math

import numpy as np
import pytest

from astrodeck import providers
from astrodeck.devices.sim import build_sim_rig
from astrodeck.events import bus
from astrodeck.focus.autofocus import MIN_STARS_PER_POINT
from astrodeck.focus.window import (MEASURE_WINDOW_FLOOR, MEASURE_WINDOW_KEEP,
                                    centre_slice, measure_window, window_frame)
from astrodeck.imaging.stars import SourceSize, focus_size, star_size
import astrodeck.focus.native as N

native_only = pytest.mark.skipif(
    not providers.NATIVE_AVAILABLE, reason="astrodeck_native wheel not installed")


# ------------------------------------------------------------ how big a window

def test_a_rich_field_is_cropped_to_the_floor():
    """1148 stars is the count the 2026-09-07 21:39 sweep's field would give at
    bin 1. sqrt(40/1148) = 0.187, so the floor decides — the crop never goes
    below 0.4 of each axis (16% of the pixels) whatever the count says."""
    assert measure_window(1148) == pytest.approx(MEASURE_WINDOW_FLOOR)


def test_a_middling_field_keeps_the_square_root_of_what_it_needs():
    """Star count scales with AREA, so keeping ``keep`` of ``n_full`` stars means
    keeping sqrt(keep/n_full) of each axis."""
    assert measure_window(100) == pytest.approx(math.sqrt(0.4), abs=0.005)
    assert measure_window(100) == pytest.approx(0.632, abs=0.005)
    assert measure_window(160) == pytest.approx(0.5, abs=0.005)


def test_a_field_too_sparse_to_crop_is_measured_whole():
    """Below the keep count there is nothing to give away: cropping a 34-star
    field would leave 5 stars in the window and the point would fall back to
    the whole frame anyway, having paid for both."""
    assert measure_window(34) == 1.0
    assert measure_window(0) == 1.0
    assert measure_window(MEASURE_WINDOW_KEEP - 1) == 1.0


def test_exactly_the_keep_count_is_the_whole_frame():
    """The boundary, stated: sqrt(40/40) is 1.0, so 40 stars is measured whole
    and the two branches agree at the seam rather than stepping."""
    assert measure_window(MEASURE_WINDOW_KEEP) == 1.0


def test_the_floor_is_respected_however_rich_the_field():
    for n in (500, 5_000, 50_000, 1_000_000):
        assert measure_window(n) == pytest.approx(MEASURE_WINDOW_FLOOR), n


def test_a_caller_can_ask_for_a_different_bargain():
    """``keep`` and ``floor`` are arguments, not constants baked into the maths —
    a rig whose fit wants 100 stars a point can say so."""
    assert measure_window(400, keep=100) == pytest.approx(0.5)
    assert measure_window(1000, keep=40, floor=0.1) == pytest.approx(0.2, abs=0.005)


# ----------------------------------------------------------------- the window

def test_the_window_is_centred():
    ys, xs = centre_slice((1000, 2000), 0.4)
    assert (ys.start, ys.stop) == (300, 700)
    assert (xs.start, xs.stop) == (600, 1400)
    # equal margins on both sides is the whole claim: an off-centre crop would
    # measure a different part of the field than the probe did.
    assert ys.start == 1000 - ys.stop
    assert xs.start == 2000 - xs.stop


def test_the_whole_frame_is_the_whole_frame():
    ys, xs = centre_slice((913, 1217), 1.0)
    a = np.arange(913 * 1217, dtype=np.uint16).reshape(913, 1217)
    assert a[ys, xs].shape == a.shape
    assert a[ys, xs] is not None


def test_an_odd_sized_frame_still_yields_a_usable_window():
    """No off-by-one that empties the window on an odd axis."""
    a = np.zeros((999, 1001), dtype=np.uint16)
    ys, xs = centre_slice(a.shape, 0.4)
    assert a[ys, xs].shape == (400, 400)   # 999*0.4 rounds to 400, not 399


def test_the_window_is_a_view_and_copies_nothing():
    """A 26 MP frame is 52 MB; a copy per point is 52 MB of memcpy the sweep
    does not need, and the measurement only reads."""
    a = np.zeros((1000, 2000), dtype=np.uint16)
    ys, xs = centre_slice(a.shape, 0.4)
    assert a[ys, xs].base is a


@native_only
async def test_the_windowed_frame_keeps_everything_but_the_pixels():
    """The measurement seam is handed a FRAME, not an array — substitutes read
    ``frame.focuser_position`` off it to know which point they are looking at
    (test_the_sweep_exposes_ahead). So the windowed frame must be the same frame
    with a smaller view, not a new object missing its provenance."""
    parts = build_sim_rig()
    cam = parts["camera"]
    await cam.connect()
    frame = await cam.expose(0.05, 200, 30, binning=1)
    small = window_frame(frame, 0.4)
    assert small.data.shape == (int(round(912 * 0.4)), int(round(1216 * 0.4)))
    assert small.data.base is frame.data
    assert small.exposure_s == frame.exposure_s
    assert small.gain == frame.gain
    assert small.binning == frame.binning
    assert small.focuser_position == frame.focuser_position
    assert frame.data.shape == (912, 1216), "the original frame was mutated"
    # frac 1.0 hands the frame straight back rather than building a copy of it
    assert window_frame(frame, 1.0) is frame


# ------------------------------------------------------- does it measure the same

H, W = 1500, 2000


def _render(n_stars: int, hfr_px: float, seed: int,
            spread: float = 0.0) -> np.ndarray:
    """A synthetic light: Gaussian stars on a 300 ADU sky with shot + read noise.

    Same construction as the cost benchmark this change was measured with, at a
    size a test can afford. ``spread`` scatters the per-star radius by that
    fraction — a field whose stars genuinely disagree, which is the only way to
    test that the measured scatter is measured.
    """
    rng = np.random.default_rng(seed)
    base = hfr_px / 1.177        # half-flux radius of a 2D Gaussian = 1.177 sigma
    img = np.full((H, W), 300.0, dtype=np.float64)
    xs = rng.uniform(0, W, n_stars)
    ys = rng.uniform(0, H, n_stars)
    flux = 10 ** rng.uniform(3.5, 6.3, n_stars)      # 3e3 .. 2e6 ADU total
    sigmas = base * (1.0 + spread * rng.uniform(-1.0, 1.0, n_stars))
    for x, y, f, sigma in zip(xs, ys, flux, sigmas):
        r = int(max(4, sigma * 4))
        x0, x1 = max(0, int(x) - r), min(W, int(x) + r + 1)
        y0, y1 = max(0, int(y) - r), min(H, int(y) + r + 1)
        if x0 >= x1 or y0 >= y1:
            continue
        gx = np.arange(x0, x1) - x
        gy = (np.arange(y0, y1) - y)[:, None]
        img[y0:y1, x0:x1] += (f * np.exp(-(gx ** 2 + gy ** 2) / (2 * sigma ** 2))
                              / (2 * math.pi * sigma ** 2))
    img = (rng.poisson(np.clip(img, 0, None)).astype(np.float64)
           + rng.normal(0, 3.0, img.shape))
    return np.clip(img, 0, 65535).astype(np.uint16)


@pytest.mark.parametrize("hfr", [2.5, 8.0])
def test_the_window_measures_what_the_whole_frame_measures(hfr):
    """THE CLAIM THE SPEEDUP RESTS ON, in the regime the fit actually leans on.

    Near focus and through moderate defocus the central 0.4 of each axis reads
    the same size as all 3 MP of it. Measured here (1200 stars, 1500x2000):
    2.639 -> 2.638 px at HFR 2.5, 9.413 -> 8.992 px at HFR 8 — 0.04% and 4.5%,
    against the 3% the rig's own L light showed.
    """
    img = _render(1200, hfr, seed=1200 * 7 + int(hfr))
    full, n_full, _ = focus_size(img)
    crop, n_crop, _ = focus_size(img[centre_slice(img.shape, 0.4)])
    assert full is not None and crop is not None, (full, crop)
    assert n_crop >= MIN_STARS_PER_POINT, n_crop
    assert crop == pytest.approx(full, rel=0.10), (
        f"HFR {hfr}: window {crop:.3f} px against {full:.3f} px whole-frame "
        f"({100 * (crop / full - 1):+.1f}%)")


def test_far_out_the_metrics_own_scatter_is_larger_than_the_crops_effect():
    """THE WING, where the 10% claim does NOT hold on a frame this size — and
    why that is a fact about the metric rather than about the crop.

    At HFR 25 a 600x800 window of this field holds three donuts. Measured over
    four seeds on a frame nearly three times this area (3200x4400, 4000 stars),
    the whole-frame metric itself ranged 29.35 .. 37.19 px on the SAME physical
    defocus, and window-vs-whole ran -15.9% .. +13.0% — the crop's effect is
    inside the metric's own repeatability there.

    That regime is the one the fit weights least (1/sigma^2 with sigma from a
    handful of voters), it is where the per-point fallback fires, and on the rig
    0.4 of a 26 MP frame is 4.1 MP rather than 0.5 MP. So the test states what
    IS true here: both answers say "far out", to within a quarter.
    """
    img = _render(1200, 25.0, seed=1200 * 7 + 25)
    full, _n, _s = focus_size(img)
    crop, n_crop, _sc = focus_size(img[centre_slice(img.shape, 0.4)])
    assert full is not None and full > 20.0, full
    if crop is None:
        # The honest other outcome: too few donuts in the window to be a
        # measurement at all, which is exactly what the loop's fallback catches.
        assert n_crop < MIN_STARS_PER_POINT, n_crop
        return
    assert crop == pytest.approx(full, rel=0.25), (
        f"window {crop:.2f} px against {full:.2f} px whole-frame "
        f"({100 * (crop / full - 1):+.1f}%)")


# --------------------------------------------------------------- the scatter

def test_the_size_carries_the_scatter_of_the_stars_that_voted():
    """``point_sigma`` needs the spread of THIS frame's sources, and the Rust
    detector was the only thing supplying it. Now the size measures its own: a
    field whose stars disagree must report a larger MAD than one whose agree.

    Built rather than borrowed: the two frames are the same field at the same
    focus, and the only difference is that one field's stars all have the same
    radius and the other's are scattered 60% about it. So the MAD is measuring
    the population, not the seed.
    """
    tight = star_size(_render(600, 3.0, seed=31, spread=0.0))
    loose = star_size(_render(600, 3.0, seed=31, spread=0.6))
    assert tight is not None and loose is not None
    assert tight.mad is not None and loose.mad is not None
    assert loose.mad > 4 * tight.mad, (loose.mad, tight.mad)
    # and it is the number the fit weights by, at the same size and the same
    # sample count — the whole difference between these two σ is the scatter
    assert (N.point_sigma(3.0, loose.mad, 25)
            > N.point_sigma(3.0, tight.mad, 25))


def test_a_single_source_reports_no_scatter_rather_than_zero():
    """One donut has no MAD. Reporting 0.0 would make it the most certain point
    in the sweep — the 2026-07-31 failure ``point_sigma``'s floor exists for —
    so it reports None and the loop falls back to its documented 5%."""
    size = SourceSize(radius=40.0, n_sources=1, n_found=1, snr=99.0, scale=8,
                      lower_bound=True)
    assert size.mad is None


# ---------------------------------------------------- one Rust pass per RUN

async def _connected_sim():
    parts = build_sim_rig()
    cam, foc = parts["camera"], parts["focuser"]
    await cam.connect()
    await foc.connect()
    return parts["_rig"], cam, foc


def _count_detects(monkeypatch):
    calls = {"n": 0, "shapes": []}
    real = N._native.detect_and_measure

    def counted(data, params):
        calls["n"] += 1
        calls["shapes"].append(tuple(np.asarray(data).shape))
        return real(data, params)

    monkeypatch.setattr(N._native, "detect_and_measure", counted)
    return calls


@native_only
async def test_the_rust_detector_runs_once_a_run_not_once_a_point(monkeypatch):
    """THE COST, DELETED. ``detect_and_measure`` is 27 s a point on the rig's
    26 MP frames (60-67 s on a rich light) and everything the sweep used it for
    — a star count and a MAD — the size metric supplies itself. It belongs to
    the probe, whose count sizes the window and whose gate decides whether the
    sweep is worth attempting at all.

    Counted rather than timed: a wall-clock threshold would flake on a loaded
    machine, and the claim is "it is not called", not "it is fast".
    """
    _rig, cam, foc = await _connected_sim()
    calls = _count_detects(monkeypatch)

    res = await N.run_native_autofocus(cam, foc, exposure_s=0.05, gain=200,
                                       step=350, steps_each_side=4, binning=1)

    assert res.success, res.message
    assert len(res.points) >= 5, "the sweep barely ran; this proves little"
    assert calls["n"] == 1, (
        f"the Rust detector ran {calls['n']} times for a {len(res.points)}-point "
        f"sweep — on the rig that is {calls['n'] * 27} s of CPU with the shutter "
        f"shut")


@native_only
async def test_the_probe_still_gets_its_full_frame_rust_pass(monkeypatch):
    """The other half: the pass that was deleted from the loop must NOT have
    been deleted from the probe. Its count is the sparse-field gate AND the
    window's size, and both need the whole frame."""
    _rig, cam, foc = await _connected_sim()
    calls = _count_detects(monkeypatch)

    await N.run_native_autofocus(cam, foc, exposure_s=0.05, gain=200,
                                 step=350, steps_each_side=4, binning=1)

    assert calls["n"] == 1
    assert calls["shapes"] == [(912, 1216)], calls["shapes"]


# ------------------------------------------------- which pixels each point sees

def _sizes(monkeypatch, answer):
    """Record the shape every measurement saw, answering with ``answer(shape)``."""
    seen: list[tuple[int, int]] = []

    def fake(data, min_stars=3):
        shape = tuple(np.asarray(data).shape)
        seen.append(shape)
        return answer(shape)

    monkeypatch.setattr(N, "focus_size", fake)
    return seen


def _probe(monkeypatch, n0: int):
    monkeypatch.setattr(
        N._native, "detect_and_measure",
        lambda data, params: ([], {"star_count": n0, "hfr_median": 3.0,
                                   "hfr_mad": 0.2}))


def _flat(n_sources=25, radius=3.4, mad=0.2, n_found=400):
    size = SourceSize(radius=radius, n_sources=n_sources, n_found=n_found,
                      snr=50.0, scale=1, lower_bound=False, source="stars",
                      mad=mad)
    return (radius, n_sources, size)


@native_only
async def test_a_rich_field_is_measured_on_the_centre_at_every_point(monkeypatch):
    """1148 stars at the probe, so every sweep point is measured on 16% of the
    pixels — and on the SAME 16% at every point, because the window is sized
    once from the probe. A window that changed mid-sweep would put the
    centre-vs-corner step (18% on the rig's Ha light) into the curve itself.
    """
    _rig, cam, foc = await _connected_sim()
    _probe(monkeypatch, 1148)
    seen = _sizes(monkeypatch, lambda shape: _flat())

    await N.run_native_autofocus(cam, foc, exposure_s=0.05, gain=200,
                                 step=350, steps_each_side=4, binning=1)

    want = (int(round(912 * 0.4)), int(round(1216 * 0.4)))
    assert seen, "nothing was measured at all"
    assert set(seen) == {want}, seen
    assert (912, 1216) not in seen, "a point paid for the whole frame"


@native_only
async def test_a_sparse_field_is_measured_whole_at_every_point(monkeypatch):
    """34 stars: cropping would leave five in the window, so it does not crop.
    The whole point of sizing the window from the probe is that this decision is
    made once, from a measurement, before any point pays for it."""
    _rig, cam, foc = await _connected_sim()
    _probe(monkeypatch, 34)
    seen = _sizes(monkeypatch, lambda shape: _flat())

    await N.run_native_autofocus(cam, foc, exposure_s=0.05, gain=200,
                                 step=350, steps_each_side=4, binning=1)

    assert seen, "nothing was measured at all"
    assert set(seen) == {(912, 1216)}, seen


@native_only
async def test_a_window_that_comes_back_thin_falls_back_to_the_whole_frame(
        monkeypatch):
    """The wings are where a window can genuinely run out of sources, and there
    a point measured from two donuts is worse than a slow one measured from
    twenty. The fallback re-measures THAT point whole.

    It fires only at the wings — near focus a window that held 40 stars at the
    probe still holds them — which is why it is allowed to change the pixels
    mid-sweep at all: out there defocus dominates the size, and the centre-vs-
    corner offset adds in quadrature (sqrt(20^2 + 1.5^2) = 20.06). Near focus it
    would be a step in the curve, so do not "fix" this into a per-point choice.
    """
    _rig, cam, foc = await _connected_sim()
    _probe(monkeypatch, 1148)
    window_shape = (int(round(912 * 0.4)), int(round(1216 * 0.4)))
    seen = _sizes(
        monkeypatch,
        lambda shape: ((None, 1, None) if shape == window_shape
                       else _flat(n_sources=20, radius=7.5, n_found=120)))

    q = bus.subscribe()
    try:
        res = await N.run_native_autofocus(cam, foc, exposure_s=0.05, gain=200,
                                           step=350, steps_each_side=4,
                                           binning=1)
    finally:
        bus.unsubscribe(q)

    assert window_shape in seen and (912, 1216) in seen, seen
    # the whole-frame answer is the one that reached the curve; nothing dropped
    assert res.points, "every point was dropped instead of re-measured"
    assert all(h == pytest.approx(7.5) for _p, h in res.points), res.points
    logs = [e.data.get("message", "") for e in _drain(q) if e.type == "log"]
    assert any("whole frame" in m for m in logs), logs


def _drain(q):
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


@native_only
async def test_the_window_it_chose_is_in_the_log(monkeypatch):
    """A sweep whose points are measured on a fraction of the frame has to SAY
    which fraction: a morning log that cannot tell a 16%-of-pixels sweep from a
    whole-frame one cannot explain a size that disagrees with the grader's."""
    _rig, cam, foc = await _connected_sim()
    _probe(monkeypatch, 1148)
    _sizes(monkeypatch, lambda shape: _flat())

    q = bus.subscribe()
    try:
        await N.run_native_autofocus(cam, foc, exposure_s=0.05, gain=200,
                                     step=350, steps_each_side=4, binning=1)
    finally:
        bus.unsubscribe(q)

    said = [e.data.get("message", "") for e in _drain(q) if e.type == "log"]
    line = next((m for m in said if "central" in m), None)
    assert line, said
    assert "40%" in line, line
    assert "1148" in line, line


@native_only
async def test_a_whole_frame_sweep_says_why_it_did_not_crop(monkeypatch):
    _rig, cam, foc = await _connected_sim()
    _probe(monkeypatch, 34)
    _sizes(monkeypatch, lambda shape: _flat())

    q = bus.subscribe()
    try:
        await N.run_native_autofocus(cam, foc, exposure_s=0.05, gain=200,
                                     step=350, steps_each_side=4, binning=1)
    finally:
        bus.unsubscribe(q)

    said = [e.data.get("message", "") for e in _drain(q) if e.type == "log"]
    line = next((m for m in said if "whole frame" in m), None)
    assert line and "34 stars" in line, said


# ------------------------------------------------------ the scatter, in the loop

@native_only
@pytest.mark.parametrize("mad,other", [(1.5, 0.02)])
async def test_the_measured_scatter_reaches_the_points_own_sigma(
        monkeypatch, mad, other):
    """σ is what the engine weights by (1/σ²) and what the chart's whisker draws,
    and until now it came from the Rust detector's MAD for the Rust detector's
    own estimator. It now comes from the size metric's own voters — so a frame
    whose stars disagree must produce a wider σ than one whose agree, with
    everything else about the two runs identical."""
    sigmas = []
    for scatter in (mad, other):
        _rig, cam, foc = await _connected_sim()
        _probe(monkeypatch, 1148)
        _sizes(monkeypatch, lambda shape, s=scatter: _flat(mad=s))
        q = bus.subscribe()
        try:
            await N.run_native_autofocus(cam, foc, exposure_s=0.05, gain=200,
                                         step=350, steps_each_side=4, binning=1)
        finally:
            bus.unsubscribe(q)
        pts = [p for e in _drain(q) if e.type == "focus"
               for p in (e.data.get("points") or [])]
        assert pts, "no point was ever published"
        sigmas.append(pts[-1]["sigma"])

    wide, tight = sigmas
    assert wide > tight, sigmas
    # not merely different: 1.5 px of scatter against 0.02 px is the difference
    # between a rumour and a measurement, and the weighting is 1/σ².
    assert wide / tight > 5, sigmas


@native_only
async def test_a_point_is_weighted_by_its_voters_and_judged_by_its_stars(
        monkeypatch):
    """THE TWO COUNTS, kept apart.

    ``focus_size`` reports how many sources VOTED for the median (5..25 by
    construction — the top flux quartile, capped) and how many were RESOLVED at
    all. The Rust detector used to supply one number for both jobs and it was
    the second kind, so:

    * σ and MIN_STARS_PER_POINT take the VOTERS, because √n in ``point_sigma``
      is the sample size of a median and 1000 was never it;
    * ``counts`` — what THIN_POINT_STARS (10), RICH_FIELD_STARS (50) and
      ``curve_verdict``'s tip gate are measured against — takes the RESOLVED
      count, because those thresholds were calibrated on detection counts
      (NGC 5907, 2026-08-08: 460 at the best point, 2 at the eleventh).

    Feeding the voters to the second group would quietly quadruple "thin" (10
    voters needs 40 detections) and put RICH_FIELD_STARS out of reach entirely,
    which is the #114 wrong turn returning through the arithmetic.
    """
    _rig, cam, foc = await _connected_sim()
    _probe(monkeypatch, 1148)
    # 25 voted, 400 were there — deliberately far apart so the log cannot be
    # printing one number twice.
    _sizes(monkeypatch, lambda shape: _flat(n_sources=25, n_found=400))

    q = bus.subscribe()
    try:
        await N.run_native_autofocus(cam, foc, exposure_s=0.05, gain=200,
                                     step=350, steps_each_side=4, binning=1)
    finally:
        bus.unsubscribe(q)

    said = [e.data.get("message", "") for e in _drain(q) if e.type == "log"]
    point = next((m for m in said if "-> HFR" in m), None)
    assert point, said
    assert "400 stars" in point, point
    assert "25 sized" in point, point
    # and the curve report — the morning's forensic record — carries the
    # resolved count, the one its thresholds mean
    curve = next((m for m in said if "V-curve" in m), None)
    assert curve and "/400" in curve, curve


@native_only
async def test_a_metric_that_reports_no_scatter_still_gets_a_sigma(monkeypatch):
    """A substitute (and the pyramid path's single-source answer) supplies no
    MAD. The documented 5% fallback has to still apply — a missing scatter must
    not become a zero one, which would make the point an infinite-weight anchor.
    """
    _rig, cam, foc = await _connected_sim()
    _probe(monkeypatch, 1148)
    # a bare pair, exactly what an older substitute returns
    monkeypatch.setattr(N, "focus_size", lambda data, min_stars=3: (3.4, 25))

    q = bus.subscribe()
    try:
        await N.run_native_autofocus(cam, foc, exposure_s=0.05, gain=200,
                                     step=350, steps_each_side=4, binning=1)
    finally:
        bus.unsubscribe(q)

    pts = [p for e in _drain(q) if e.type == "focus"
           for p in (e.data.get("points") or [])]
    assert pts
    expected = N.point_sigma(3.4, 0.05 * 3.4, 25)
    assert pts[-1]["sigma"] == pytest.approx(expected)
    assert pts[-1]["sigma"] > 0
