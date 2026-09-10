"""Measuring DEFOCUS, when there are no stars to measure.

Far from focus a star is not a point — it is an annulus. On this rig at
2026-07-31 the annulus was ~880 px across, with the secondary obstruction and
four spider vanes plainly visible. Nothing in the star-detection path can cope
with that, and both detectors failed in opposite directions:

* ``imaging.stars.detect_stars`` reported 130-713 "stars" — fragments of the
  donut RING. Worse, its measurement box is 15 px, so it cannot span an 880 px
  donut at all and its HFR read ~4-5 on every frame regardless of true focus.
  A metric that returns the same number at every focuser position is not a
  focus metric.
* the native Rust detector correctly reported 0-2: there genuinely are no stars.

So coarse focus must not ask "how many stars", it must ask "how big is the
blob". That question has an answer at every focuser position from the far end of
the travel down to perfect focus, which is exactly the property a coarse-focus
metric needs.

The measurement is a radial profile around the brightest source and the radius
enclosing 80% of its flux. Deliberately NOT a threshold-and-segment approach:
thresholding breaks a donut ring into arcs (measured — a 900 px donut came back
as a 44x312 px fragment), which is the same failure that fooled the star
detector.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# ONE definition of "where does this source end" and one of "is this a hot
# pixel", shared with the star metric. Two answers to those questions is how
# the same frame came back as "0-2 stars", "713 stars" and "a 445 px blob" on
# the same night, each from a different piece of code.
from .stars import (
    SIZE_MIN_APERTURE_SNR, _bg_sigma, _is_resolved, _measure_source, _recentre,
    _seed_positions,
)

#: Work at 1/4 resolution. A donut is hundreds of pixels across, so quarter-res
#: costs nothing real and makes a 26 MP frame cheap to measure.
BIN = 4

#: Half-width of the window searched around the brightest point, in FULL-frame
#: pixels. Must comfortably exceed the largest blob worth measuring; beyond this
#: the measurement saturates and reports "at least this big", which is still the
#: right answer for "you are miles out".
WINDOW_PX = 1200

#: Below this r80 the blob is small enough that real star detection can take
#: over — hand off to the V-curve autofocus rather than keep bisecting.
HANDOVER_R80_PX = 12.0

#: A pixel counts toward the source only this far above background.
#: 5 sigma, the same bar imaging.stars uses. At 2 sigma about 2% of a
#: 360k-pixel window survives and that noise outweighs a point source
#: entirely — a FOCUSED star measured r80=316px, i.e. the metric read
#: maximum defocus exactly when it was in focus. At 5 sigma the expected
#: noise survivors are a fraction of one pixel, while the real donut ring
#: (measured peak SNR 104) clears it with room to spare.
SOURCE_THRESHOLD_SIGMA = 5.0

#: Brightest candidate peaks probed before giving up on finding a source. A
#: hot pixel outshines any blob, so the first few are usually rejects.
PROBE_PEAKS = 12


@dataclass
class BlobSize:
    """One defocus measurement."""
    r80: float          #: radius enclosing 80% of the source's flux, full-frame px
    peak: float         #: background-subtracted peak, ADU
    snr: float          #: peak / robust background sigma
    x: int              #: brightest-source centre, full-frame px
    y: int
    background: float
    sigma: float
    #: Separately resolved sources in the frame. 1 is the coarse-focus regime —
    #: one huge annulus and nothing else. Many means the optics are resolving
    #: the field, so ``r80`` describes one of its stars rather than a defocus
    #: blob; the number is still true, it just stops being big.
    n_sources: int = 1
    #: The blob ran off the edge of the frame, so ``r80`` is a FLOOR. Still the
    #: right answer to "which way is focus" — "at least this big" is what the
    #: module docstring promises — but not a number to extrapolate a distance
    #: from. ``focus_from_two`` on two floors invents a focus position.
    lower_bound: bool = False

    @property
    def diameter(self) -> float:
        return 2.0 * self.r80

    @property
    def ready_for_autofocus(self) -> bool:
        return self.r80 <= HANDOVER_R80_PX


def _binned(a: np.ndarray, k: int = BIN) -> np.ndarray:
    h, w = a.shape[0] // k * k, a.shape[1] // k * k
    return a[:h, :w].reshape(h // k, k, w // k, k).mean(axis=(1, 3))


def measure_blob(data: np.ndarray, *, bin_: int = BIN,
                 window_px: int = WINDOW_PX,
                 max_r80: float | None = None) -> BlobSize | None:
    """Size of the DOMINANT source, or None when nothing rose above the noise.

    Works on a donut and on a star with the same code and the same units, which
    is what lets ONE number track focus across the whole range.

    ONE SOURCE, never a window's worth of them. That is the fix for the frame
    that carried 200 stars at median HFR 4.49 px and came back "r80 445 px —
    891 px across": the old code summed every pixel above 5 sigma inside a
    1200 px window, so what it measured was the SPREAD OF THE FIELD. Downstream
    that outranks HFR (lib/focusVerdict.ts lets defocus_r80 > 25 win), and it
    produced a fabricated 13 mm defocus in front of the user on 2026-07-31.
    The same frame now measures 2 px, because one sharp star IS 2 px.

    It deliberately does NOT decline on a crowded frame, and a first attempt at
    this fix did. Nothing falls back: ``focus.coarse`` has no second instrument,
    it treats ``None`` as "nothing measurable" and aborts the whole routine with
    "check the sky, the cover, and that the camera is exposing" — sending the
    user outside on a frame full of stars, the exact anti-pattern this repair
    pass exists to remove. Measured with the decline in place, a 60-donut field
    returned None at EVERY defocus radius from 0 to 120 px, so coarse focus
    could not complete a single handover. Multiplicity is reported instead, in
    ``n_sources``; defocus is a property of the whole optical train, so the
    dominant source's size is a good answer whether it has company or not.

    ``None`` means only what it says: no source anywhere in the frame cleared
    the noise. Nothing to measure is the one thing this cannot measure.

    ``max_r80`` skips sources bigger than that, in FULL-frame px. Default
    ``None`` — ``focus.coarse`` calls this and must never be told a 400 px
    donut is too big to be its blob. ``measure_defocus`` is the one caller that
    passes it, and its docstring holds the measurements; a skipped source also
    does not CLAIM its region, so the seeds behind it still get measured (on
    L_0006 the M33 blob was the only source the twelve probes ever reached,
    because a truncated claim reserves twice its own edge).
    """
    a = np.asarray(data, dtype=np.float32)
    if a.ndim != 2 or a.shape[0] < 8 * bin_ or a.shape[1] < 8 * bin_:
        return None
    b = _binned(a, bin_)
    bg = float(np.median(b))
    sigma = float(1.4826 * np.median(np.abs(b - bg))) or 1e-6
    # The hot-pixel test needs FULL resolution: at quarter-res a focused star
    # and a hot pixel are both one cell, and only the unbinned neighbourhood
    # tells them apart. (This is why the binned frame alone cannot police it.)
    bg_full, _ = _bg_sigma(a)

    win0 = float(max(4, window_px // (2 * bin_)))
    r_cap = float(min(b.shape) / 2.0)
    sources: list[dict] = []
    claimed: list[tuple[float, float, float]] = []
    for (sy, sx) in _seed_positions(b, 1, SOURCE_THRESHOLD_SIGMA, PROBE_PEAKS):
        if any((sy - c[0]) ** 2 + (sx - c[1]) ** 2 < c[2] ** 2 for c in claimed):
            continue            # a rim peak of a blob already measured
        if not _is_resolved(a, bg_full, sy * bin_, sx * bin_, 2 * bin_):
            continue            # a hot pixel outshines every blob; skip it
        m = _measure_source(b, bg, sigma, sy, sx, win0, r_cap)
        if m is None:
            continue
        # A seed lands on the RING of a donut, never in its dark centre; the
        # profile only describes the blob once it is centred on it.
        ny, nx = _recentre(b, m["bg"], sigma, sy, sx, m["edge"])
        better = _measure_source(b, bg, sigma, ny, nx,
                                 max(win0, m["edge"] * 1.5), r_cap)
        if better is not None:
            m = better
        if m["snr"] < SIZE_MIN_APERTURE_SNR:
            continue
        if max_r80 is not None and m["r80"] * bin_ > max_r80:
            continue            # not a defocus blob on a frame that resolves
        # A source whose aperture hit the frame edge is bigger than we measured,
        # so it claims further out than we measured. Without this a blob that
        # runs off the frame comes back as three "separate sources" — its own
        # rim, counted twice — and the multi-source guard below then refuses a
        # frame that holds exactly one (very large) blob.
        claimed.append((m["y"], m["x"],
                        max(3.0, m["edge"] * (2.0 if m["truncated"] else 1.0))))
        sources.append(m)
    if not sources:
        return None
    m = max(sources, key=lambda s: s["flux"])
    return BlobSize(r80=float(m["r80"] * bin_), peak=float(m["peak"]),
                    snr=float(m["peak"] / sigma),
                    x=int(m["x"] * bin_), y=int(m["y"] * bin_),
                    background=bg, sigma=sigma,
                    lower_bound=bool(m["truncated"]),
                    n_sources=len(sources))


def measure_defocus(data: np.ndarray, *, stars=None) -> BlobSize | None:
    """The defocus blob, or ``None`` when the frame is a RESOLVED STAR FIELD.

    ``measure_blob`` answers "how big is the dominant source" and must keep
    answering that on every frame — declining is what bricked coarse focus once
    already, and its docstring says why. But "how big is the dominant source" is
    not the same question as "how far out of focus is this rig", and the preview
    path was publishing the first as an answer to the second.

    MEASURED 2026-09-07 01:17, on the rig. A 60 s L sub of NGC 604 that the
    run's own grader read at HFR 3.32 with 1294 stars came back from
    ``measure_blob`` at r80 1134 px, and the Focus panel said "Far out of focus
    — blob is 2268 px across — further out than an autofocus sweep can bracket.
    Run coarse focus first". The blob was M33. Re-measured on the whole frames
    that survived the night, ``measure_blob`` alone reads:

        R_0007  clean, grader 2.73  → r80 1154      L_0026 clean-ish 4.13 → 1086
        R_0030  clean, grader 2.94  → r80 1266      S_0005 clean 2.80    → 6
        m33field_G60 (2048x1536 crop of G_0003, grader 3.43) → r80 466

    so this is not a galaxy-only failure: on a wide field the dominant source is
    whatever extended light happens to be brightest, and it is not the PSF.

    A FRAME WITH A HEALTHY STAR POPULATION IS NOT FAR OUT OF FOCUS, whatever a
    blob measurer says, and ``stars.compact_star_population`` is the ONE place
    that judges "healthy" — the same gates, on the same stars, that decide
    whether the autofocus sweep may answer with the grader's HFR. A true
    coarse-defocus frame fails them: every donut frame measured (real L_0001
    4.06, donutfield_L60 4.01, donut_L60 3.94, synthetic donut fields 4.51-5.00)
    grades above SIZE_FINE_MAX_BOX_HFR, and its rim fragments peak off-centre.

    NOT A COUNT TEST, and the brief asked for one. A count cannot do this job
    and the fixtures say so: the real donut frame L_0001 yields 200 detections
    (rim fragments) and the 512 px donut crop yields 12, while a clean crop
    yields 19 — so no bar between 12 and 200 separates them. What separates them
    is the SHAPE of what was detected, which is what the gates measure. The only
    count here is SIZE_FINE_MIN_STARS (10), the bar below which a median is not
    a population.

    THE GATE ALONE WAS NOT ENOUGH, and the frames of that same night say so.
    It is one bit — "are these stars healthy" — and a frame can fail it for a
    reason that has nothing to do with defocus. Measured on the whole 6252x4176
    subs (``measure_blob``'s answer, then this function's, against what the
    frame's own stars grade):

        G_0031  stars 3.78, gate refused (truncation 1.43) -> r80  734 -> 18
        L_0006  stars 4.05, gate refused (box HFR 4.05)    -> r80 1110 -> 10
        L_0026  stars 4.13, gate refused (box HFR 4.13)    -> r80 1086 -> 10
        B_0025  stars 4.07, gate refused (box HFR 4.07)    -> r80   62 -> 18

    All four are trailed or slightly bloated and all four are IN FOCUS; the
    734-1110 px readings are M33 and the sky gradient, published to the Focus
    panel as "far out of focus, run coarse focus first". So the blob is bounded
    by the same fact ``star_size``'s pyramid is bounded by: a source more than
    ``SIZE_MAX_STAR_MULTIPLE`` times the size of the stars this frame RESOLVES
    is not one of its sources, let alone its defocus (``resolved_star_scale``,
    which is the weaker of the module's two questions and the one L_0026 needs).

    THE BOUND IS ON r80, the number this function publishes, and not on the
    source's internal mean radius. Bounding the internal one has a wider margin
    (highest reading that must pass 9.4x against a lowest that must not of 27x,
    where r80 gives 10.3x against 17x) and it would let this function publish a
    figure that violates its own bound — 62 px on B_0025, which
    ``lib/focusVerdict.ts`` turns into "far out of focus" at anything over 25.
    A bound on the published number cannot do that.

    Every genuinely defocused frame is untouched, because it resolves no stars
    to bound against: the nine off-focus sweep fixtures still read r80 26-94,
    donut_L60 and donutfield_L60 still read 10, pedrift_L60 34, the synthetic
    ring fields 6-10, and ``test_defocus``' hard-edged 60-donut fields 10-222.

    WHAT IT COSTS, because ``hub`` runs this on every preview sub. Nothing on an
    in-focus frame (14 ms on R_0007: the gate returns before any of this). On
    the four frames above it is 240-620 ms MORE — 318 -> 807 ms on L_0006 —
    because dropping the M33 blob is exactly what lets the other eleven probes
    be measured at all instead of falling inside its claim. That is against a
    60-300 s sub, in a thread, on frames where the readout was previously wrong
    by two orders of magnitude.

    ``stars`` is the caller's existing ``detect_stars`` pass — the preview path
    grades every sub before it asks this — so the frame is scanned once.

    ``focus.coarse`` deliberately does NOT come through here: it calls
    ``measure_blob``, because on a frame it cannot measure it must say "nothing
    bright enough to measure" and stop, not "your stars look fine". It therefore
    passes no ``max_r80`` either, and a coarse-focus donut is never bounded.
    """
    from .stars import (SIZE_MAX_STAR_MULTIPLE, compact_star_population,
                        detect_stars, resolved_star_scale)
    img = np.asarray(data)
    if img.ndim != 2:
        return None
    if stars is None:
        stars = detect_stars(img.astype(np.float64))
    if compact_star_population(img, stars=stars) is not None:
        return None
    scale = resolved_star_scale(img, stars=stars)
    return measure_blob(img, max_r80=(None if scale is None
                                      else SIZE_MAX_STAR_MULTIPLE * scale))


def focus_from_two(p1: int, r1: float, p2: int, r2: float) -> float | None:
    """Extrapolate the in-focus position from two blob measurements.

    A defocused star's diameter grows LINEARLY with distance from focus,
    ``D = k * |x - x_focus|``, so two points give both the slope and the
    crossing — with no knowledge of the aperture, the f-ratio or the step size.
    That is a handful of exposures instead of a blind sweep of the whole travel.

    None when the two measurements are indistinguishable (the step was too small
    to see) or the geometry is degenerate.
    """
    if p1 == p2:
        return None
    slope = (r2 - r1) / (p2 - p1)
    if abs(slope) < 1e-9:
        return None
    x = p1 - r1 / slope
    if not np.isfinite(x):
        return None
    return float(x)


def shrinking(r1: float, r2: float, *, noise_px: float = 2.0) -> bool | None:
    """Did the blob get smaller? None when the change is within measurement
    noise, which means "move further before deciding" rather than "no"."""
    d = r2 - r1
    if abs(d) <= noise_px:
        return None
    return d < 0.0
