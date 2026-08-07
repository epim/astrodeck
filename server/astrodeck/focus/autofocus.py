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

#: Measured, but from few enough stars to be worth naming in the advice: a run
#: that "succeeded" on a handful of these is thinner evidence than its R²
#: suggests. What it means for the FIT depends on the company the point keeps —
#: see ``thin_points_phrase``.
THIN_POINT_STARS = 10


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
        if a <= 0:
            # Say how flat. A V-curve that barely moves over the whole swept
            # range is either a sweep far too narrow to see the V, or a focuser
            # that reported moves it did not make — and the measured spread
            # distinguishes them, where "flat or inverted fit" never could.
            span = int(xs.max() - xs.min())
            swing = float(ys.max() - ys.min())
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
