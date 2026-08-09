"""V-curve autofocus.

Sweep the focuser through a window around the current position, measure
median HFR at each step, fit a parabola to the V-curve, move to the minimum,
and verify. Publishes progress on the event bus so the UI can draw the curve
live.
"""
from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field

import numpy as np

from ..devices.base import Camera, DeviceError, Focuser
from ..events import bus
from ..imaging.stars import (OVEREXPOSED_FRAC, focus_size, median_hfr,
                             saturation_fraction, size_advice, star_size)


#: A median HFR over fewer than this many stars is one detection's opinion: with
#: two samples the "median" is their mean and the MAD is a coin toss, so a single
#: hot pixel or cosmic ray IS the measurement. Points this thin are refused
#: rather than fitted — on 2026-07-31 a sweep recorded 1-5 star samples with the
#: same authority as a 900-star one and fitted the resulting noise.
MIN_STARS_PER_POINT = 3

#: How many times in a row one sweep position may be dropped before the run is
#: refused instead of retried.
#:
#: The sweep engine advances ONLY when a measurement is added, so dropping a
#: point leaves it asking for the SAME position on the next iteration — and the
#: driver loop is a bare ``while True``. Measured on the rig 2026-08-08 during a
#: per-filter offset run: the SII slot reached position 9077, could not measure
#: it (frame median 237, max ~310 — a narrowband frame with almost no signal),
#: and re-exposed that one position indefinitely. Fourteen consecutive
#: "dropping 9077" lines in the log, the focuser stationary, the whole run
#: unable to finish or fail, and no route that could cancel it.
#:
#: Three is enough to ride out a satellite, a gust or a passing cloud on one
#: point, and short enough that a filter with no measurable stars fails while
#: someone is still awake to read the reason.
MAX_DROPS_PER_POSITION = 3

#: A sweep whose HFR moves less than this FRACTION of its own minimum across the
#: whole swept range carries no focus information, whichever way the quadratic's
#: leading coefficient happens to round. 5% is far below any real V — the rig's
#: own Oiii sweep on 2026-08-08 ran 1.67 to 44.70 px — and far above the noise a
#: genuine curve shows between neighbouring points.
FLAT_SWING_FRAC = 0.05

#: Absolute floor for the same test, so a sweep whose minimum HFR is itself tiny
#: cannot make the relative threshold vanish.
FLAT_SWING_FLOOR_PX = 0.02


def is_flat_sweep(hfrs) -> bool:
    """Does this sweep carry any focus information at all?

    A PURE FUNCTION on purpose. The end-to-end behaviour it guards is
    platform-dependent — the quadratic's leading coefficient on flat data rounds
    negative on one BLAS and positive on another, so a full-sweep test of the
    flat case passes for free on the machine where it already worked and can
    only fail on the one where it did not. This is the decision itself, provable
    anywhere.

    Empty or single-point input is flat: there is nothing to see a V in.
    """
    values = [float(h) for h in hfrs]
    if len(values) < 2:
        return True
    swing = max(values) - min(values)
    return swing <= max(FLAT_SWING_FRAC * min(values), FLAT_SWING_FLOOR_PX)

#: Measured, but from few enough stars to be worth naming in the advice: a run
#: that "succeeded" on a handful of these is thinner evidence than its R²
#: suggests. What it means for the FIT depends on the company the point keeps —
#: see ``thin_points_phrase``.
THIN_POINT_STARS = 10


# --------------------------------------------------------------------------
# WHETHER A SWEEP FOUND FOCUS IS A QUESTION ABOUT THE CURVE, NOT ABOUT R².
#
# The rig, 2026-08-08, Oiii slot of a per-filter offset run: HFR 1.67 px at
# position 11173 from 321 stars, rising to 44.70 px at BOTH ends of the swept
# range. Thrown away, with the run's record quoting hyperbolic R² 0.743 against
# a 0.70 gate. Twelve of thirteen autofocus attempts on this rig have died with
# a fit-statistic verdict of one kind or another.
#
# R² IS THE WRONG STATISTIC FOR THIS SHAPE, and the failure is systematic
# rather than unlucky. R² is 1 − Σresid²/Σ(y−ȳ)², so it is dominated by the
# region with the largest excursion — the WINGS, which here are 26× taller than
# the tip and the one part of the curve nobody wants a number from. The vertex
# sits where the residuals are smallest and contributes almost nothing to the
# score. So the statistic rises and falls with how faithfully the model tracks
# the far wings, while the quantity the run exists to produce is decided
# elsewhere.
#
# Measured here, with the shipped engine (`fit_focus_curve`, hyperbolic), on a
# PERFECT symmetric V whose wings saturate at 44.70 as the defocused blobs
# outgrow what the size metric can measure — the shape that makes both ends of
# a sweep read the same number, as this one's did:
#
#     wings saturate over ...   hyperbolic R²   fitted minimum
#     no saturation                   0.996          11173
#     the outer 1 point/side          0.850          11173
#     the outer 2 points/side         0.406          11173
#     the outer 3 points/side        −0.611          11173
#
# The fit gets the answer exactly right in every row and its score walks from
# "excellent" to NEGATIVE. There is no threshold on this number that admits the
# fourth row and rejects noise, because the number is not measuring the thing.
# Driving the engine's own state machine over the second row's curve (the one
# that reproduces all three published values) returns `failed`,
# `r_squared_below_threshold` — the finding, reproduced without a telescope.
# Note the 0.743 in the record is not itself the number the gate saw: the gate
# scores the engine's own accumulated points and the log line re-fits the
# host's list, which is one duplicate verification point longer. Two statistics
# of the same run disagreeing about whether it succeeded is its own argument
# for asking the curve instead.
#
# WHAT THIS ASKS INSTEAD is what a person reads off the chart:
#   * a minimum INSIDE the sampled range, not extrapolated off one arm;
#   * both wings actually rising away from it, and not falling back;
#   * a depth large against the curve's own roughness, so the dip is structure
#     rather than scatter;
#   * enough stars at the tip that the best point is a measurement;
#   * and only then a LOCAL parabola over the tip and its neighbours to place
#     the vertex — local, because a V-curve's arms are straight and a global
#     parabola through them is a bad model that biases the vertex.
#
# A PURE FUNCTION, for the reason ``is_flat_sweep`` above is one: the decision
# it makes lives inside a Rust state machine driven by a camera and a focuser,
# so an end-to-end test of it can only run where a rig or a wheel exists, and
# on 2026-08-08 exactly that gap let a guard read the sign of a numerically
# zero coefficient and pass on Linux while failing on Windows. This is the
# decision itself, checkable anywhere, against curves this project measured.
# --------------------------------------------------------------------------

#: Fewer than this and there is not enough curve to overrule a fit statistic:
#: four points can be fitted but they leave at most one sample on a wing, and a
#: wing of one cannot be said to rise rather than wobble.
MIN_ACCEPT_POINTS = 5

#: How far each wing's OUTER END must stand above the minimum, as a fraction of
#: the minimum itself, and as an absolute floor for a sweep whose focus is
#: already sharp. Deliberately 10x ``FLAT_SWING_FRAC``: that constant asks
#: whether a sweep carries any information at all, this one asks whether it
#: carries enough to overrule the engine, and those are different bars. The rig
#: Oiii curve clears it by a factor of 51 (43.03 px of rise against 0.84 needed).
WING_RISE_FRAC = 0.5
WING_RISE_FLOOR_PX = 0.5

#: How far a wing may fall back as it travels OUTWARD from the minimum, as a
#: fraction of that wing's own total rise, before the curve stops being a V.
#: Not zero: one point in a real sweep can sit low for a satellite, a gust or a
#: thinner patch of cloud, and refusing every curve with a single dip in it
#: would put us back where the R² gate was. A quarter of the wing's rise is far
#: more than a measurement wobble and far less than an arm turning round.
WING_DIP_FRAC = 0.25

#: How many times the curve's own roughness the minimum must be deep by.
#: Roughness is measured as each interior point's distance from the midpoint of
#: its two neighbours (the tip excluded — the corner of a V is real curvature,
#: not noise), so a straight arm contributes zero however steep it is and only
#: scatter counts. 5x is the line between a dip that is structure and one that
#: is the noise floor arranged conveniently.
DEPTH_OVER_ROUGHNESS = 5.0

#: Half-width of the window the vertex is fitted in: the measured minimum plus
#: up to this many neighbours on each side. Matches the legacy path's local
#: refit, for the same reason — V-curves are hyperbolic, so a parabola is a good
#: model only near the tip.
LOCAL_HALF = 2


@dataclass(frozen=True)
class CurveVerdict:
    """Does this sweep's curve locate a focus position, and where.

    ``reason`` always states what was measured — accepted or not — because the
    caller logs it either way and "rejected" on its own is the one-word verdict
    this whole exercise exists to replace.
    """
    accepted: bool
    best_position: float | None
    reason: str
    #: The smaller of the two wings' rise above the minimum (px), and the
    #: curve's roughness (px). Carried so a caller can quote the evidence
    #: without recomputing it.
    depth: float = 0.0
    roughness: float = 0.0


def curve_verdict(points, counts=None) -> CurveVerdict:
    """Judge a measured V-curve on its own shape. PURE — see the block above.

    ``points`` is ``[(position, hfr), …]`` in any order; ``counts`` the stars
    behind each point, parallel to ``points`` (missing/short reads as 0, which
    fails the star test rather than passing it silently).
    """
    counts = list(counts or [])
    counts += [0] * max(0, len(points) - len(counts))
    rows = sorted((float(p), float(h), float(n))
                  for (p, h), n in zip(points, counts))
    n_pts = len(rows)
    if n_pts < MIN_ACCEPT_POINTS:
        return CurveVerdict(False, None,
                            f"{n_pts} measured point{'' if n_pts == 1 else 's'} "
                            f"— a curve needs {MIN_ACCEPT_POINTS} to be judged "
                            f"on its shape")
    xs = np.array([r[0] for r in rows], dtype=np.float64)
    ys = np.array([r[1] for r in rows], dtype=np.float64)
    ns = np.array([r[2] for r in rows], dtype=np.float64)

    i = int(np.argmin(ys))
    y_min = float(ys[i])
    if i == 0 or i == n_pts - 1:
        # Extrapolated, not measured: the sweep ran out of room before the
        # stars stopped shrinking, so focus is outside the window.
        side = "below" if i == 0 else "above"
        return CurveVerdict(
            False, None,
            f"the smallest size measured, {y_min:.2f}px, is at {int(xs[i])} — "
            f"the {side} end of the swept range, so focus was never bracketed")

    need = max(WING_RISE_FRAC * y_min, WING_RISE_FLOOR_PX)
    left = float(ys[0]) - y_min
    right = float(ys[-1]) - y_min
    if left < need or right < need:
        return CurveVerdict(
            False, None,
            f"the wings rise only {left:.2f}px / {right:.2f}px above a "
            f"{y_min:.2f}px minimum ({need:.2f}px needed on both) — too shallow "
            f"to be a V")

    for side, walk, rise in (("left", range(i, -1, -1), left),
                             ("right", range(i, n_pts), right)):
        seq = ys[list(walk)]
        steps = np.diff(seq)
        if steps.size and float(steps.min()) < -WING_DIP_FRAC * rise:
            return CurveVerdict(
                False, None,
                f"the {side} wing falls back {-float(steps.min()):.2f}px on its "
                f"way out of a {rise:.2f}px rise — an arm that turns round is "
                f"not one arm of a V")

    if ns[i] < THIN_POINT_STARS:
        return CurveVerdict(
            False, None,
            f"the best point was measured from {int(ns[i])} star"
            f"{'' if ns[i] == 1 else 's'} — under {THIN_POINT_STARS}, the tip "
            f"is one detection's opinion however clean the curve looks")

    rough = [abs(float(ys[k]) - 0.5 * (float(ys[k - 1]) + float(ys[k + 1])))
             for k in range(1, n_pts - 1) if k != i]
    roughness = float(np.median(rough)) if rough else 0.0
    depth = min(left, right)
    if roughness > 0 and depth < DEPTH_OVER_ROUGHNESS * roughness:
        return CurveVerdict(
            False, None,
            f"the minimum is only {depth:.2f}px deep against {roughness:.2f}px "
            f"of scatter between neighbouring points (x{depth / roughness:.1f}, "
            f"x{DEPTH_OVER_ROUGHNESS:.0f} needed) — a dip in the noise",
            depth, roughness)

    i0, i1 = max(0, i - LOCAL_HALF), min(n_pts, i + LOCAL_HALF + 1)
    xl, yl = xs[i0:i1], ys[i0:i1]
    wl = np.sqrt(np.maximum(ns[i0:i1], 1.0))
    if len(xl) < 3:  # pragma: no cover - unreachable while MIN_ACCEPT_POINTS ≥ 5
        return CurveVerdict(False, None,
                            "too few points around the minimum to place a vertex",
                            depth, roughness)
    a, b, _c = np.polyfit(xl, yl, 2, w=wl)
    if a <= 0:
        return CurveVerdict(
            False, None,
            "the points around the minimum do not curve upward, so they place "
            "no vertex", depth, roughness)
    vertex = float(-b / (2 * a))
    if not (float(xl.min()) < vertex < float(xl.max())):
        return CurveVerdict(
            False, None,
            f"the vertex fitted near the minimum lands at {int(vertex)}, "
            f"outside the {int(xl.min())}..{int(xl.max())} points it was fitted "
            f"to", depth, roughness)

    return CurveVerdict(
        True, vertex,
        f"a {depth:.2f}px-deep minimum at {int(round(vertex))}, "
        f"{depth / roughness:.0f}x the {roughness:.2f}px scatter between "
        f"neighbouring points, bracketed by wings rising to {float(ys[0]):.2f}px "
        f"and {float(ys[-1]):.2f}px, measured from {int(ns[i])} stars"
        if roughness > 0 else
        f"a {depth:.2f}px-deep minimum at {int(round(vertex))} on a curve with "
        f"no scatter between neighbouring points, bracketed by wings rising to "
        f"{float(ys[0]):.2f}px and {float(ys[-1]):.2f}px, measured from "
        f"{int(ns[i])} stars",
        depth, roughness)


def sweep_metric(data, min_stars: int = MIN_STARS_PER_POINT
                 ) -> tuple[float | None, int, str | None]:
    """One sweep point: ``(size px | None, sources, the metric's own advice)``.

    THE seam the sweep measures through, deliberately a single named function:
    it is what tests substitute, and it is where the choice of metric lives so
    that choice cannot silently differ between the curve and the final verify.

    ``star_size`` rather than the ``focus_size`` wrapper because the SourceSize
    is needed twice — once for the number, once so ``size_advice`` can say what
    the metric actually saw when a point is refused, instead of the sweep
    inferring a cause it cannot know.
    """
    size = star_size(data)
    value, n = _size_point(size, min_stars)
    return value, n, (size_advice(size) if value is None else None)


def _size_point(size, min_stars: int) -> tuple[float | None, int]:
    """``(size px | None, sources)`` for one sweep point, from a ``SourceSize``.

    Mirrors ``imaging.stars.focus_size`` exactly — the sweep calls ``star_size``
    directly so it can also hand the SourceSize to ``size_advice``, and this
    keeps the accept/refuse rule in ONE shape rather than two that can drift.
    """
    from ..imaging.stars import SIZE_CONFIDENT_SNR
    if size is None:
        return None, 0
    if size.n_sources < min_stars and size.snr < SIZE_CONFIDENT_SNR:
        return None, size.n_sources
    return size.radius, size.n_sources


def sweep_levers(exposure_s: float, binning: int) -> str:
    """The knobs that make a too-sparse field measurable, in the order that
    helps: exposure first (it costs only time), then binning (it costs
    resolution the sweep does not need), then the one thing only the user can
    judge. Shared by both sweep paths so the advice names the SAME numbers the
    run actually used rather than a generic "try again"."""
    return (f"a longer exposure than {exposure_s:g}s"
            + (f", bin 1 instead of {binning}" if binning > 1 else "")
            + ", or a richer field")


def overexposure_levers(exposure_s: float, gain: int) -> str:
    """The knobs that bring a clipped frame back on scale — the OPPOSITE
    direction from ``sweep_levers``, kept beside it so the choice between the
    two sentences is always made where both are visible. On 2026-08-06 a sweep
    at 3s/gain 200 saturated a rich field into unmeasurable merged blobs and
    then advised a longer exposure; the operator's own 2s/gain 120 frame of the
    same sky held 200 measurable stars."""
    return f"a shorter exposure than {exposure_s:g}s or less gain than {gain}"


def overexposure_phrase(clipped: list[tuple[int, float]]) -> str:
    """How many dropped frames were actually clipped, with the worst fraction —
    the evidence that flips the advice from "more light" to "less"."""
    worst = max(f for _, f in clipped)
    n = len(clipped)
    return (f"{n} of the dropped frame{'' if n == 1 else 's'} "
            f"{'was' if n == 1 else 'were'} overexposed (up to {worst:.1%} of "
            f"pixels at full scale) — stars merging into saturated blobs, not "
            f"going missing")


def dropped_points_phrase(dropped: list[tuple[int, int]], attempted: int) -> str:
    """How many sweep points went unmeasurable, naming the thinnest few.

    The positions and counts are the evidence: "not enough stars" is what the
    user was told on 2026-07-31 while the field had 8 of them, and the number
    that actually mattered — how many the sweep could still measure once
    defocused — was never shown."""
    shown = sorted(dropped, key=lambda d: d[1])[:3]
    detail = ", ".join(f"{p} ({c} star{'' if c == 1 else 's'})" for p, c in shown)
    more = "…" if len(dropped) > len(shown) else ""
    return (f"{len(dropped)} of {attempted} sweep points had fewer than "
            f"{MIN_STARS_PER_POINT} measurable stars ({detail}{more}) and were "
            f"dropped rather than fitted")


def thin_points_phrase(counts: list[int]) -> str | None:
    """How much of the fitted curve rests on samples too thin to trust, said in
    terms that match what the fitter ACTUALLY does with them. None when nothing
    is thin.

    Both fitters weight *relatively* — numpy's ``polyfit(w=√n)`` and the
    engine's ``1/σ²`` are both invariant to scaling every weight alike — so
    "the fit barely leans on them" is true only of a thin point sitting beside a
    richer one. When EVERY point is equally thin the weighting cancels exactly
    and the fit leans on them completely. The old copy asserted the reassuring
    half in both cases, and asserted it hardest in the run where it was false:
    nine 8-star points, a flat curve, and a sentence telling the user the fit
    had discounted the only evidence it had. A line of advice that is wrong
    about our own arithmetic is worth less than no line at all.
    """
    thin = [n for n in counts if n < THIN_POINT_STARS]
    if not thin:
        return None
    if len(thin) == len(counts):
        return (f"Every one of the {len(counts)} fitted points came from fewer "
                f"than {THIN_POINT_STARS} stars, so the whole curve rests on "
                f"thin samples — weighting cannot discount them when it has "
                f"nothing richer to weigh them against.")
    return (f"{len(thin)} of {len(counts)} fitted points came from fewer than "
            f"{THIN_POINT_STARS} stars against a richest of {max(counts)}, so "
            f"the fit discounts them.")


@dataclass
class AutofocusResult:
    success: bool
    best_position: int
    best_hfr: float | None
    points: list[tuple[int, float]] = field(default_factory=list)
    message: str = ""
    #: What THIS run worked out that the user can act on: the exposure/binning
    #: that would have made it measurable, the points it had to drop. ``None``
    #: when the run learned nothing specific — an honest silence, so the panel
    #: falls back to generic copy rather than us inventing a cause. Generic copy
    #: keyed off the failure kind is exactly what told a user under a 2% cloud
    #: sky to "check the sky is clear" on 2026-07-31, while this same run had
    #: already worked out — and logged where nobody could see it — that the
    #: field was too sparse for the exposure and binning it was given.
    advice: str | None = None


async def run_autofocus(camera: Camera, focuser: Focuser, *,
                        exposure_s: float = 2.0, gain: int = 120,
                        step: int = 350, steps_each_side: int = 4,
                        binning: int = 2, expose_guard=None,
                        hub=None, provider=None) -> AutofocusResult:
    """Run a V-curve autofocus sweep.

    ``expose_guard`` (optional): an async context-manager *factory* taking one
    label argument, used to serialize the single camera against the live loop /
    single capture / sequence exposures (hub-level capture guard). When None the
    exposures run unguarded (direct unit-test / native-autofocus paths).

    Provider routing (spec §5): who runs autofocus is a per-capability choice
    (auto|backend|astrodeck). Callers that have a ``hub`` pass it (or a resolved
    ``provider`` ProviderChoice) so the provider layer decides: ``backend`` →
    the connected backend's own autofocus (NINA); ``astrodeck`` → the native Rust
    V-curve engine. Callers WITHOUT a hub/provider (direct unit tests) fall
    through to the legacy behaviour below, so the existing signature and its
    default numpy sweep keep working unchanged.
    """
    # --- provider routing ---------------------------------------------------
    choice = provider
    if choice is None and hub is not None:
        # Lazy import to avoid an import cycle (providers → devices → …).
        from ..providers import resolve
        # May raise a user-presentable DeviceError when nothing can run AF.
        choice = resolve("autofocus", hub)
    if choice is not None:
        if choice.kind == "backend":
            return await _run_native_autofocus(focuser)
        if choice.kind == "astrodeck":
            from ..providers import NATIVE_AVAILABLE
            if not NATIVE_AVAILABLE:
                raise DeviceError("native engine not installed")
            from .native import run_native_autofocus
            return await run_native_autofocus(
                camera, focuser, exposure_s=exposure_s, gain=gain, step=step,
                steps_each_side=steps_each_side, binning=binning,
                expose_guard=expose_guard)

    # --- legacy path (no provider context) ----------------------------------
    if getattr(focuser, "supports_native_autofocus", False):
        return await _run_native_autofocus(focuser)

    async def _expose():
        # Offload the frame's blocking work under the shared capture guard so two
        # capture paths can never expose the one camera at once (interleaved
        # imageready polls download each other's frames / raise InvalidOperation).
        guard = expose_guard("autofocus") if expose_guard is not None \
            else contextlib.nullcontext()
        async with guard:
            return await camera.expose(exposure_s, gain, 30, binning=binning)

    start_pos = await focuser.get_position()
    positions = [start_pos + step * i
                 for i in range(-steps_each_side, steps_each_side + 1)]
    positions = [max(0, min(focuser.max_position, p)) for p in positions]

    points: list[tuple[int, float]] = []
    #: Stars behind each ACCEPTED point, parallel to ``points`` — the fit weight
    #: (a five-star median must not pull as hard as a five-hundred-star one).
    counts: list[int] = []
    #: (position, star count) for every point too thin to be a measurement.
    dropped: list[tuple[int, int]] = []
    #: (position, saturated fraction) for the subset of ``dropped`` frames that
    #: were CLIPPED — stars merged into railed blobs, not missing — which flips
    #: the advice from "more light" to "less".
    clipped: list[tuple[int, float]] = []
    #: The size metric's OWN account of the first point it could not measure.
    #: Only the metric knows whether it found nothing at all or found one source
    #: too faint to trust, and that distinction is the difference between "expose
    #: longer" and "you are pointed at nothing".
    metric_note: str | None = None
    bus.publish("focus", state="running", points=[], best=None)

    def _thin_advice(extra: str = "") -> str | None:
        """The run's own account of what it could and could not measure.

        ``None`` when nothing was dropped and there is no ``extra``: a sweep that
        measured everything cleanly and still failed has no business guessing at
        a cause, and a guessed cause is what sent the user out to a clear sky."""
        # The metric's own observation leads: it is the only party that looked
        # at the pixels, so it outranks anything inferred from the star counts.
        # EXCEPT when the pixels were clipped — the metric's "no source rose
        # above the noise, expose longer" is exactly what a railed frame looks
        # like from inside a star detector, and repeating it beside the
        # overexposure finding would hand the user both directions at once.
        note = None if clipped else metric_note
        bits = [b for b in (extra, note) if b]
        # Dropped points and thin points are the same shortage seen at two
        # depths, so only the louder one speaks.
        thin = None if dropped else thin_points_phrase(counts)
        if dropped:
            bits.append(dropped_points_phrase(dropped, len(positions)) + ".")
            if clipped:
                bits.append(overexposure_phrase(clipped) + ".")
        elif thin:
            bits.append(thin)
        if not bits:
            return None
        # Only when the sweep actually ran short of stars: telling someone whose
        # frames were full of them to expose longer is the same wrong turn as
        # telling them to check a clear sky.
        if clipped:
            # Clipping outranks starvation: on a railed frame "expose longer"
            # is the one move guaranteed to make the next run worse.
            bits.append("Try " + overexposure_levers(exposure_s, gain)
                        + " — more light makes this worse.")
        elif dropped:
            # Points went missing for want of stars, so the levers ARE the fix.
            bits.append("Try " + sweep_levers(exposure_s, binning) + ".")
        elif thin:
            # Points were measured, only thinly. Handing the levers over as an
            # instruction here would contradict an ``extra`` that has just
            # diagnosed a step-size or focuser fault ("raise the step size … or
            # the focuser is not moving as far as it reports. Try a longer
            # exposure…" left the user to pick a half). They are offered as what
            # would make the NEXT run's points firmer, not as a rival fix.
            bits.append("A firmer result would need "
                        + sweep_levers(exposure_s, binning) + ".")
        return " ".join(bits)

    # Failure-recovery invariant (autofocus review): a focuser stranded mid-sweep
    # would make the sequence shoot the whole target defocused (up to
    # steps_each_side*step + step units out of focus). The two explicit failure
    # returns already restore start_pos; wrap the sweep+fit so ANY exception
    # (transient camera/focuser DeviceError, a cancel from /api/focuser/halt)
    # ALSO returns the focuser to where the sweep began before propagating.
    try:
        # Approach from below to take out backlash, then sweep upward.
        await focuser.move_to(max(0, positions[0] - step))

        for pos in positions:
            await focuser.move_to(pos)
            frame = await _expose()
            # numpy star detection is ~0.3-0.5s full-frame; offload it so the
            # sweep never freezes the event loop at every point (matches the
            # offloaded preview-path detection).
            # The floor is the SWEEP's, stated here rather than inherited from the
            # detector's default: what a fit point needs is a property of fitting.
            # focus_size, NOT median_hfr. Measured over tonight's ground-truth
            # sweep (server/tests/fixtures/focus_sweep), median_hfr reads
            # 4.73 4.89 4.69 4.52 4.43 ... 4.56 4.87 4.57 across +/-5000 steps —
            # dead flat at its 15px-box ceiling, which is why every sweep this
            # week fitted noise and died with not_enough_spread. focus_size on
            # the same frames reads 42.7 77.0 76.3 51.4 21.9 4.61 26.7 52.8 81.4:
            # a curve with an actual minimum. Same return shape, same units at
            # focus; it simply keeps rising once the star outgrows a cutout.
            hfr, n_stars, why = await asyncio.to_thread(
                sweep_metric, frame.data, MIN_STARS_PER_POINT)
            if why and metric_note is None:
                metric_note = why
            if hfr is None:
                # Refused, not merely "skipped": a median over one or two
                # detections carries a hot pixel's opinion into the fit with the
                # same weight as a field full of stars. Remember it so the
                # failure can say which positions went dark and how thin they were.
                dropped.append((pos, n_stars))
                # A railed frame's missing stars MERGED; remember the clipping
                # so the advice points at less light, not more.
                sat = saturation_fraction(frame.data)
                clip_note = ""
                if sat >= OVEREXPOSED_FRAC:
                    clipped.append((pos, sat))
                    clip_note = f", {sat:.1%} of pixels at full scale"
                bus.log("warning",
                        f"autofocus: only {n_stars} stars at {pos}{clip_note}, "
                        f"dropping the point (a curve point needs "
                        f"{MIN_STARS_PER_POINT})", "focus")
                continue
            points.append((pos, hfr))
            counts.append(n_stars)
            bus.publish("focus", state="running",
                        points=[{"position": p, "hfr": h} for p, h in points], best=None)

        if len(points) < 4:
            advice = _thin_advice()
            await focuser.move_to(start_pos)
            bus.publish("focus", state="failed", points=[], best=None, advice=advice)
            return AutofocusResult(
                False, start_pos, None, points,
                f"only {len(points)} of {len(positions)} sweep points were "
                f"measurable — a curve needs at least 4", advice=advice)

        xs = np.array([p for p, _ in points], dtype=np.float64)
        ys = np.array([h for _, h in points], dtype=np.float64)
        # Weight each point by how many stars voted for it. np.polyfit's ``w``
        # multiplies the residual (w = 1/σ) and the standard error of a median
        # falls as 1/√n, so a point measured from 5 stars pulls ~10x less than one
        # measured from 500. Unweighted — as this was — a lucky 5-star sample set
        # the vertex as firmly as the richest frame in the sweep.
        ws = np.sqrt(np.array(counts, dtype=np.float64))
        # Sort by position so "neighbours" are sweep-adjacent (the sweep already
        # runs in order, but a skipped point or a refit must not assume that).
        order = np.argsort(xs)
        xs, ys, ws = xs[order], ys[order], ws[order]

        a, b, c = np.polyfit(xs, ys, 2, w=ws)
        if a > 0 and len(points) >= 5:
            # Refine: V-curves are hyperbolic, so a parabola fit over the full
            # (often asymmetric) sweep biases the vertex. Refit a *local* parabola
            # anchored on the measured minimum (the lowest-HFR sample), taking a
            # symmetric window of neighbours around it where a parabola is a good
            # local model. Anchoring on the measured minimum — not the coarse-fit
            # vertex, which can itself be biased by the asymmetric tails — keeps the
            # refit stable (it was the wide-window bias that made the old fit
            # occasionally land hundreds of units off).
            lo = int(np.argmin(ys))
            half = 2  # ±2 → up to 5 local points, centred on the measured minimum
            i0, i1 = max(0, lo - half), min(len(xs), lo + half + 1)
            xl, yl = xs[i0:i1], ys[i0:i1]
            # Only accept the local refit when the minimum is actually BRACKETED —
            # at least one measured point on each side of the anchor. A minimum at a
            # window edge is unbracketed; keep the global fit and let the bracket
            # guard below reject it rather than refitting off a one-sided slope.
            if lo > i0 and lo < i1 - 1 and len(xl) >= 3:
                al, bl, cl = np.polyfit(xl, yl, 2, w=ws[i0:i1])
                if al > 0:
                    a, b, c = al, bl, cl
        # THE MEASUREMENT DECIDES, NOT THE FIT'S ROUNDING. ``a <= 0`` alone
        # rejects a flat curve only when the quadratic's leading coefficient
        # happens to round negative, and on a genuinely flat dataset that sign
        # is floating-point noise: the same nine identical points failed here on
        # Windows and produced a confident minimum of HFR 2.01 — from points
        # that were all exactly 3.40 — on CI's Linux/BLAS (2026-08-08). A curve
        # that does not move has no minimum on any platform, so say so from the
        # spread, which is the evidence the advice below already quotes.
        span = int(xs.max() - xs.min())
        swing = float(ys.max() - ys.min())
        flat = is_flat_sweep(ys)
        if a <= 0 or flat:
            # Say how flat. A V-curve that barely moves over the whole swept
            # range is either a sweep far too narrow to see the V, or a focuser
            # that reported moves it did not make — and the measured spread
            # distinguishes them, where "flat or inverted fit" never could.
            advice = _thin_advice(
                f"HFR changed by only {swing:.2f}px ({swing / max(ys.min(), 1e-6):.0%}) "
                f"across {span} steps — either the sweep is too narrow to reach "
                f"either arm of the V (raise the step size), or the focuser is not "
                f"moving as far as it reports.")
            await focuser.move_to(start_pos)
            bus.publish("focus", state="failed",
                        points=[{"position": p, "hfr": h} for p, h in points],
                        best=None, advice=advice)
            return AutofocusResult(False, start_pos, None, points,
                                   "no V-curve minimum found (flat or inverted fit)",
                                   advice=advice)

        vertex = -b / (2 * a)
        # "Minimum not bracketed" guard (review 4d): if the fitted vertex falls at
        # or outside the measured sweep range, the true focus is beyond the window
        # we explored — clipping to the edge and reporting success would silently
        # park the focuser at a boundary that is NOT in focus. Fail loudly instead
        # so the caller can widen / recentre the sweep.
        lo_edge, hi_edge = float(xs.min()), float(xs.max())
        if not (lo_edge < vertex < hi_edge):
            # Name the direction, and the distance ONLY while the fit is
            # entitled to one. Which way focus lies is knowledge — the sweep
            # watched the stars shrink toward one end and run out of room. How
            # far is not: a parabola whose vertex falls outside the data is
            # extrapolating off a single arm, and out there its curvature is set
            # by the noise on the last few points rather than by the V.
            #
            # Measured on the simulator 2026-08-01, true focus 200-1600 steps
            # outside a 2800-step window: the extrapolated vertex came back
            # 8935, 12540, 16804, 22036 and once 939083 steps out, so the run
            # advised "re-run centred near -921283" — a focuser position that
            # does not exist — for a focus that was 800 steps past the edge.
            # A number that confident and that wrong is worse than no number:
            # it buys a second wasted sweep in the right direction at a
            # fabricated distance.
            span = hi_edge - lo_edge
            above = vertex > hi_edge
            way = "above" if above else "below"
            miss = int(vertex - (hi_edge if above else lo_edge))
            if abs(miss) <= span and 0 <= vertex <= focuser.max_position:
                # Close enough to the data that the arm still constrains it, and
                # a position the focuser can actually reach.
                extra = (f"The fitted minimum sits about {abs(miss)} steps "
                         f"{way} the swept range ({int(lo_edge)}..{int(hi_edge)}). "
                         f"Re-run centred near {int(vertex)}, or raise the step "
                         f"size so ±{steps_each_side} points span it.")
            else:
                low = int(xs[int(np.argmin(ys))])
                extra = (
                    f"Focus is {way} the swept range "
                    f"({int(lo_edge)}..{int(hi_edge)}) — the smallest size the "
                    f"sweep measured, {ys.min():.2f}px, was at {low}. How much "
                    f"further it cannot say: the fitted minimum lies off the end "
                    f"of the measured points. Re-run with the step at "
                    f"{step * 2} (a ±{step * 2 * steps_each_side}-step window), "
                    f"or move the focuser {'up' if above else 'down'} about "
                    f"{int(span)} steps first.")
            advice = _thin_advice(extra)
            await focuser.move_to(start_pos)
            bus.publish("focus", state="failed",
                        points=[{"position": p, "hfr": h} for p, h in points],
                        best=None, advice=advice)
            return AutofocusResult(
                False, start_pos, None, points,
                "minimum not bracketed — true focus is outside the swept range "
                "(widen the sweep or recentre)", advice=advice)

        best = int(np.clip(vertex, lo_edge, hi_edge))
        # Final move from below for backlash consistency
        await focuser.move_to(max(0, best - step))
        await focuser.move_to(best)

        frame = await _expose()
        # The SAME metric the curve was fitted with. Reporting median_hfr here
        # would let a run that landed badly still print a healthy-looking 4.5px,
        # because that number cannot exceed its own measurement box — the exact
        # lie that made every frame this week look identically "FAIR".
        final_hfr, _ = await asyncio.to_thread(focus_size, frame.data)
    except BaseException:
        # Never leave the focuser parked at an arbitrary sweep position. Restore
        # start_pos best-effort (shielded so even a cancel completes the move
        # rather than interrupting it), flag the UI, then re-raise so the caller
        # (engine / _spawn) still sees the failure.
        with contextlib.suppress(Exception):
            await asyncio.shield(focuser.move_to(start_pos))
        bus.publish("focus", state="failed",
                    points=[{"position": p, "hfr": h} for p, h in points],
                    best=None, advice=_thin_advice())
        raise

    # A sweep can succeed on thin evidence. Say so on the WAY OUT too, rather than
    # letting a confident-looking vertex stand on four 5-star samples.
    advice = _thin_advice()
    bus.publish("focus", state="done",
                points=[{"position": p, "hfr": h} for p, h in points],
                best={"position": best, "hfr": final_hfr}, advice=advice)
    bus.log("info", f"autofocus complete: position {best}, HFR {final_hfr:.2f}"
            if final_hfr else f"autofocus complete: position {best}", "focus")
    return AutofocusResult(True, best, final_hfr, points, "ok", advice=advice)


async def _run_native_autofocus(focuser: Focuser) -> AutofocusResult:
    """Delegate to a backend's own autofocus (NINA), rendering its V-curve
    through the same ``focus`` events the UI already consumes."""
    bus.publish("focus", state="running", points=[], best=None)
    bus.log("info", "running native autofocus…", "focus")
    try:
        res = await focuser.native_autofocus()  # type: ignore[attr-defined]
    except Exception as e:
        bus.publish("focus", state="failed", points=[], best=None)
        try:
            pos = await focuser.get_position()
        except Exception:
            pos = 0
        return AutofocusResult(False, pos, None, [], str(e))

    points = [(int(p["position"]), float(p["hfr"])) for p in res.get("points", [])]
    curve = [{"position": p, "hfr": h} for p, h in points]
    if not res.get("success"):
        bus.publish("focus", state="failed", points=curve, best=None)
        return AutofocusResult(False, int(res.get("best_position", 0)), None,
                               points, res.get("message", "autofocus failed"))

    best = int(res["best_position"])
    best_hfr = res.get("best_hfr")
    bus.publish("focus", state="done", points=curve,
                best={"position": best, "hfr": best_hfr})
    bus.log("info", f"native autofocus complete: position {best}"
            + (f", HFR {best_hfr:.2f}" if best_hfr else ""), "focus")
    return AutofocusResult(True, best, best_hfr, points, "ok")
