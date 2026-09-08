"""Native (Rust engine) V-curve autofocus.

This is the ``astrodeck`` autofocus provider: it drives the same move → expose →
measure → fit loop as the legacy numpy path, but the *policy* (sweep planning,
backlash, extend-when-unbracketed, final-point selection) and the *measurement*
(true half-flux-radius star detection) live in the Rust engine
(``astrodeck_native``), so a native (Alpaca/sim) rig gets NINA-parity autofocus
with no NINA.

Device I/O stays here in the host (async focuser moves + camera exposures); the
engine is a pure state machine we alternate ``next()`` / ``add_measurement`` on:

    sweep = FocusSweep(config, start_pos)
    while True:
        step = sweep.next()
        move_to(step.position); measure; sweep.add_measurement(...)
        # until step.action is "done" (a FitOutcome) or "failed" (a reason)

Two things the loop below does that this sketch does not (2026-09-08, the
autofocus-efficiency spec). The FIRST ``next()`` happens before the probe frame
is measured, so the probe's full-frame star count — 27 to 67 s on the rig —
overlaps the first point's move and exposure; and every move goes through
``_approach``, which passes an OUTWARD target and returns to it so the drawtube
arrives moving in at every point, at the validation frame and at the vertex the
run settles on.

We publish the SAME ``focus`` bus events the UI already consumes
(``state`` running|done|failed, ``points``, ``best``), PLUS an additive ``fit``
object (method, R², fitted curve, trendlines) so the rebuilt Focus view can draw
the real hyperbola + trendline cross instead of re-fitting a parabola client-side.
"""
from __future__ import annotations

import asyncio
import contextlib
import math

import numpy as np

from ..devices.base import Camera, DeviceError, Focuser
from ..events import bus
from ..providers import NATIVE_AVAILABLE
from ..imaging.stars import OVEREXPOSED_FRAC, focus_size, saturation_fraction
from .autofocus import (MAX_DROPS_PER_POSITION, MIN_STARS_PER_POINT,
                        assert_tracking, confirmed_best,
                        over_swept_advice,
                        AutofocusResult, curve_verdict, dropped_points_phrase,
                        forget_measured_span, overexposure_levers,
                        overexposure_phrase, record_measured_span,
                        resolve_sweep, sweep_levers, thin_points_phrase)
from .pipeline import Prefetch, SweepPredictor
from .span import DEFAULT_STEP
from .window import measure_window, window_frame

# Guarded handle to the Rust wheel. ``NATIVE_AVAILABLE`` (the single source of
# truth) already told us whether the import can succeed; we re-import here only
# to get the module object for the FocusSweep / detect_and_measure calls. When
# the wheel is absent this stays None and ``run_native_autofocus`` refuses with a
# clear DeviceError rather than NameError-ing deep in the loop.
try:  # pragma: no cover - covered both ways via NATIVE_AVAILABLE monkeypatch
    import astrodeck_native as _native
except ImportError:  # pragma: no cover
    _native = None

#: The engine's own defaults, PINNED here rather than left implicit, because a
#: rejected fit has to be able to say what it was measured against. See
#: native/crates/astro-focus/src/config.rs `FocusConfig::default` — these are
#: the same two values, restated so `vcurve_report` can quote the threshold
#: instead of guessing it.
CURVE_FITTING = "hyperbolic"
R_SQUARED_THRESHOLD = 0.7
#: The engine's flat-tip band (native/crates/astro-focus/src/trendline.rs
#: `fit_star_hfr`): a point within 0.1 of the minimum joins NEITHER trendline.
#: When that swallows a whole side, the sweep fails with `not_enough_spread` —
#: so this is the number to print beside a flat curve, and 0.1 px of spread on
#: a metric that ranges 8→110 across a real sweep is the whole diagnosis.
FLAT_TIP_BAND = 0.1

#: The engine failures a sweep that was TOO NARROW produces, as
#: `fail_reason_label` spells them (native/crates/astrodeck-native/src/lib.rs).
#: `not_enough_spread` is the direct one — the flat-tip band swallowed a whole
#: side, so there is no trendline to fit. `fit_unavailable` is the same shortage
#: one notch worse: too few usable points to produce the required fit at all.
#:
#: Deliberately NOT `r_squared_below_threshold` or `out_of_bounds`. Those are
#: what a sweep that is too WIDE or mis-centred produces, and discarding a
#: calibration over them would throw away a good measurement for a fault it did
#: not cause.
FLAT_FAILURES = ("not_enough_spread", "fit_unavailable")


def vcurve_report(points: list[tuple[int, float, float]],
                  counts: list[int]) -> str:
    """The sweep's own per-step data plus the statistic that rejected it, as
    ONE line — what a rejected fit has to leave behind to be diagnosable.

    "V-curve" appeared ZERO times in the rig's entire log history while
    autofocus failed twelve times in thirteen attempts with a one-word reason
    (`not_enough_spread`, `r_squared_below_threshold`). A one-word reason names
    the SHAPE of the failure and nothing about the run that produced it, so
    every diagnosis needed a repeat run on the sky to observe — the same gap
    that cost this project a night on polar alignment on 2026-07-30.

    Two statistics, because there are two failures. `r_squared_below_threshold`
    is answered by re-fitting the collected points and quoting every R² against
    the gate. `not_enough_spread` is answered by the flat-tip band: how much of
    the curve sits within FLAT_TIP_BAND of its minimum, and what that leaves on
    each side of it.

    Never raises. This only ever runs while building the record of a failure
    that has ALREADY happened, so it must not be able to replace that failure
    with one of its own.
    """
    if not points:
        return "V-curve: no measurable points"
    # Pad rather than zip-truncate: a report that silently dropped points would
    # be the very failure this exists to end.
    padded = list(counts) + [0] * max(0, len(points) - len(counts))
    ordered = sorted(zip(points, padded), key=lambda pc: pc[0][0])
    series = " ".join(f"{p}:{h:.2f}/{n}" for (p, h, _s), n in ordered)
    vals = [h for (_p, h, _s), _n in ordered]
    lo, hi = min(vals), max(vals)
    lo_pos = next(p for (p, h, _s), _n in ordered if h == lo)
    bits = [f"V-curve ({len(ordered)} pts, position:size/stars): {series}",
            f"range {lo:.2f}..{hi:.2f} (span {hi - lo:.2f}, min at {lo_pos})"]

    # not_enough_spread, in the engine's own terms. Points inside the band
    # belong to neither trendline; a side left with nothing is the failure.
    band = lo + FLAT_TIP_BAND
    left = sum(1 for (p, h, _s), _n in ordered if h > band and p < lo_pos)
    right = sum(1 for (p, h, _s), _n in ordered if h > band and p > lo_pos)
    flat = len(ordered) - left - right
    bits.append(f"flat tip (within {FLAT_TIP_BAND} of the minimum) holds "
                f"{flat} of {len(ordered)}, leaving {left} left / {right} right "
                f"for the trendlines")

    # r_squared_below_threshold: re-fit what we measured and quote every R².
    # The engine's Failed step carries only the reason label, so the numbers
    # have to be recovered here — from the same points it rejected.
    if _native is not None:
        try:
            fit = _native.fit_focus_curve(
                [(float(p), float(h), float(s)) for p, h, s in points],
                CURVE_FITTING)
            r2s = fit.get("r2s") or {}
            # The GATED fit first — it is the one that decided the run. The
            # others ride along because "hyperbolic 0.68 but quadratic 0.87" is
            # a different night's work from "everything fits nothing".
            order = sorted(r2s, key=lambda k: (k != CURVE_FITTING, k))
            named = ", ".join(
                f"{k} {r2s[k]:.3f}" for k in order
                if isinstance(r2s[k], (int, float)))
            if named:
                bits.append(f"R² {named} (gate: {CURVE_FITTING} needs "
                            f"≥ {R_SQUARED_THRESHOLD:.2f})")
        except Exception as exc:  # noqa: BLE001 - a diagnostic must not throw
            bits.append(f"R² unavailable ({type(exc).__name__}: {exc})")
    return " | ".join(bits)


def _fit_payload(outcome: dict) -> dict:
    """Shape the engine's ``FitOutcome`` into the additive ``fit`` bus object.

    The UI's existing ``focus`` schema (points/best) is untouched; ``fit`` is new
    and optional, carrying everything needed to render the real curve: the chosen
    ``method`` and its R², every fit's R² (so the UI can show a method chip), the
    sampled ``curve`` (``[[position, hfr], …]``) and the left/right ``trendlines``
    with their intersection (the trend cross)."""
    r2s = outcome.get("r2s") or {}
    method = outcome.get("method")
    return {
        "method": method,
        # Primary R² is the chosen method's; fall back to None so the UI can hide
        # the chip rather than render a wrong number.
        "r2": r2s.get(method),
        "r2s": r2s,
        "curve": outcome.get("curve") or [],
        "trendlines": outcome.get("trendlines"),
    }


def native_sweep_metric(frame) -> tuple[float | None, int, object | None]:
    """The size a native sweep point contributes:
    ``(px | None, sources, the SourceSize | None)``.

    THE seam the native sweep measures through, deliberately one named function
    — it is what tests substitute, and it is where the choice of metric lives so
    that choice cannot silently differ from the legacy path's ``sweep_metric``.

    Naming it matters for a second reason. When the measurement moved here, four
    tests that stubbed ``_native.detect_and_measure`` went on passing while no
    longer intercepting anything the fit used. A stub that has stopped
    intercepting is worse than no stub: the test still reports success, while
    testing something else entirely.

    Takes a FRAME, not an array, for the reason ``sweep_metric`` does: the next
    point is already exposing while this one is measured, so the focuser's live
    position no longer identifies the frame in hand and a substitute has to be
    able to read the position off the frame it was handed.

    It is NOT necessarily the whole frame any more (2026-09-08): the sweep hands
    it a centred window sized once from the probe's star count — see
    ``focus.window`` for the rig measurements. The seam does not choose the
    window; it measures whatever pixels it is given, so a substitute that only
    cares about the size still works unchanged.

    THE THIRD VALUE is the ``SourceSize``, carrying the scatter that sets this
    point's σ and the resolved count that "thin"/"rich" are judged on. The loop
    reads it defensively — a substitute returning the old ``(size, n)`` pair
    still works and takes the documented 5% scatter fallback — because half the
    sweep tests in this suite return exactly that pair.
    """
    return focus_size(frame.data)


def _read_metric(measured) -> tuple[float | None, int, object | None]:
    """Unpack whatever ``native_sweep_metric`` answered with.

    The real seam returns ``(size, sources, SourceSize)``. Substitutes in this
    suite — and any harness that stubs the metric to replay a modelled V-curve —
    return the older ``(size, sources)`` pair, and must go on working: a stub
    that stops intercepting is worse than no stub, and a stub that CRASHES the
    run it is measuring is worse still. A missing third value means no measured
    scatter and no resolved count, both of which have documented fallbacks at
    the call site.
    """
    size, n = measured[0], int(measured[1])
    detail = measured[2] if len(measured) > 2 else None
    return size, n, detail


def point_sigma(hfr: float, mad: float, n_stars: int) -> float:
    """Uncertainty of THIS point's median HFR: what the fit should weight by
    (engine weight = 1/σ²) and what the whisker should draw.

    Raw MAD is the scatter of the star population, which does not shrink as more
    stars are measured — passing it straight through is how a 3-star sample whose
    stars happened to agree became the most confident point in the sweep on
    2026-07-31, outvoting frames with hundreds of stars. The median of n samples
    has standard error ≈ 1.858·MAD/√n (1.4826 to turn MAD into σ, ×1.2533 for the
    median), so √n is the term that separates a measurement from a rumour.

    The 2% floor replaces the old ``mad or 0.001`` hack: a freak zero-MAD sample
    is not infinitely precise, and no HFR is known to better than a few percent.
    """
    scatter = max(float(mad), 0.02 * float(hfr))
    return 1.858 * scatter / math.sqrt(max(1, n_stars))


async def run_native_autofocus(camera: Camera, focuser: Focuser, *,
                               exposure_s: float = 2.0, gain: int = 120,
                               step: int | None = None, steps_each_side: int = 4,
                               binning: int = 2, expose_guard=None,
                               hfr_method: str | None = None,
                               tracking_check=None,
                               approach_overshoot_steps: int | None = None
                               ) -> AutofocusResult:
    """Run a V-curve autofocus sweep driven by the native Rust engine.

    ``step`` None — the default — sizes the sweep from this focuser's MEASURED
    defocus slope (see ``focus.span``), falling back to the shipped 350 until a
    sweep has measured one. An explicit value is used verbatim: the Advanced
    panel exists so an operator can overrule us.

    ``expose_guard`` mirrors the legacy path: an async context-manager factory
    taking one label, used to serialize the single camera against the live loop /
    single-capture / sequence exposures (hub capture guard). ``hfr_method`` selects
    a detector preset ("autofocus"/"advanced"/"typical") — None uses the shipped
    default.

    ``tracking_check`` mirrors the legacy path too, and this is the path the rig
    actually runs: a tri-state async probe of the mount's tracking state, asked
    before the first frame and again before every point. See
    ``focus.autofocus.assert_tracking`` and the 2026-08-21 sweep that returned
    HFR 8.40 px measured entirely on a stopped mount.

    ``approach_overshoot_steps`` None — the default — reads
    ``config.focus.approach_overshoot_steps``, which is how far past an OUTWARD
    target every move travels before returning to it so the drawtube always
    arrives moving IN (see ``config.FocusConfig`` for the backlash this exists
    for). An explicit value overrides the config and is what the tests use; 0
    disables the overshoot entirely.

    Raises ``DeviceError`` (user-presentable) when the wheel is absent or the
    engine rejects an input; ALWAYS restores the focuser to its start position on
    failure/cancel so a stranded sweep never leaves the rig shooting defocused.
    """
    if not NATIVE_AVAILABLE or _native is None:
        raise DeviceError("native engine not installed")

    async def _expose():
        # Same guarded exposure discipline as the legacy path: two capture paths
        # must never expose the one camera at once (interleaved imageready polls
        # download each other's frames / raise InvalidOperation).
        guard = expose_guard("autofocus") if expose_guard is not None \
            else contextlib.nullcontext()
        async with guard:
            return await camera.expose(exposure_s, gain, 30, binning=binning)

    #: How far past an OUTWARD target to travel before returning to it, so the
    #: last leg of every move is inward. Read ONCE, here, for the same reason
    #: the measurement window is: a value that changed mid-sweep would put a
    #: mechanical step into the middle of the curve.
    if approach_overshoot_steps is not None:
        overshoot = max(0, int(approach_overshoot_steps))
    else:
        try:
            from ..config import config_store
            overshoot = max(
                0, int(config_store.cfg().focus.approach_overshoot_steps))
        except Exception:      # noqa: BLE001 - see below
            # An unreadable config is not a reason to refuse to focus, and the
            # overshoot is an improvement rather than a precondition: without it
            # the sweep behaves exactly as it did before 2026-09-08.
            overshoot = 0

    async def _approach(pos: int) -> None:
        """Move to ``pos``, ARRIVING FROM ABOVE whenever the move is outward.

        EVERY focuser move this run makes goes through here — the swept points,
        the validation frame, the final settle and every restore-to-start —
        because the point is not that the final position is approached inward
        but that ALL of them are, including the frames the curve is fitted from.
        A sweep whose points were measured one way and whose vertex was reached
        the other is measuring one focus and settling on a different one.

        The current position is TRACKED rather than read back per move. A read
        would cost a device round trip per point, and on the EAF it would
        answer with the commanded count anyway — the same number tracked here —
        so it would buy nothing and could not see the slack this is about.
        """
        nonlocal current_pos
        pos = int(pos)
        if overshoot > 0 and pos > current_pos:
            ceiling = getattr(focuser, "max_position", None)
            over = pos + overshoot
            if ceiling is not None:
                over = min(over, int(ceiling))
            # Clamped away entirely at the top of the focuser's travel: there is
            # no room to overshoot into, so this move arrives outward and the
            # slack stays where it is. Better than refusing the move.
            if over > pos:
                await focuser.move_to(over)
                current_pos = over
        await focuser.move_to(pos)
        current_pos = pos

    async def _move_and_expose(pos: int):
        """One point's device work, as a unit — so it can run as a speculative
        task while the previous frame is being measured (see `focus.pipeline`)."""
        await _approach(pos)
        return await _expose()

    # Detector params: None selects the shipped Typical preset; an explicit
    # ``hfr_method`` picks a base profile before the engine's per-field defaults.
    params = {"profile": hfr_method} if hfr_method else None

    start_pos = await focuser.get_position()
    #: Where the focuser is, as far as this run knows — see `_approach`, the
    #: only thing that moves it and therefore the only thing that updates this.
    current_pos = int(start_pos)
    geometry = resolve_sweep(focuser, step, steps_each_side)
    step = geometry.step
    # Missing config keys (backlash strategy, max attempts, outlier policy) take
    # the engine's NINA defaults — we only pin the geometry we were asked for.
    config = {
        "step_size": step,
        "offset_steps": steps_each_side,
        # `max_step` IS THE KEY THE ENGINE READS (build_focus_config in
        # native/crates/astrodeck-native/src/lib.rs). This said "max_position"
        # until 2026-09-08, which the engine has never looked at, so the
        # focuser's travel ceiling was never reaching it at all.
        "max_step": focuser.max_position,
        # Both were already the engine's defaults; stated here so the failure
        # record can quote the gate a rejected fit was measured against rather
        # than assuming it (see CURVE_FITTING / R_SQUARED_THRESHOLD).
        "curve_fitting": CURVE_FITTING,
        "r_squared_threshold": R_SQUARED_THRESHOLD,
    }

    # (position, hfr, sigma) — sigma is the standard error of that point's median
    # HFR (see point_sigma), published so the rebuilt Focus view can draw an error
    # whisker that means the same thing as the fit's weight (additive; the legacy
    # {position,hfr} contract is preserved, `sigma` rides alongside).
    # AutofocusResult still gets plain (position,hfr) tuples.
    points: list[tuple[int, float, float]] = []
    #: Stars behind each accepted point, parallel to ``points`` — how much the
    #: fit is entitled to believe each one.
    counts: list[int] = []
    bus.publish("focus", state="running", points=[], best=None)
    # Say what we are about to do, in the terms that determine whether it can
    # work. A sweep that fails is otherwise indistinguishable in the log from a
    # sweep that was never going to: 2026-07-30 spent forty minutes on a rig
    # reporting "only 0 stars" at every point, with nothing recorded about the
    # exposure, the binning, or the frame it actually got.
    bus.log("info",
            f"autofocus sweep: {exposure_s:g}s at gain {gain}, bin {binning}, "
            f"{2 * steps_each_side + 1} points of {step} steps around {start_pos}",
            "focus")
    # WHERE THE GEOMETRY CAME FROM, beside the geometry itself. The sweep width
    # is no longer a constant in a signature — it is sized from what the last
    # successful sweep measured — so a log that printed the number without the
    # reason would leave "why is it sweeping ±300 tonight" unanswerable from the
    # morning's log alone.
    bus.log("info", f"autofocus span: {geometry.basis}", "focus")
    # WHICH WAY THE TUBE ARRIVES, said once. A curve measured one way and a
    # vertex reached the other differ by the focuser's backlash, and nothing in
    # a night log distinguishes the two — so a run that is compensating says so,
    # and a run that is not (overshoot 0) says nothing rather than claiming it.
    if overshoot > 0:
        bus.log("info",
                f"autofocus: every outward move overshoots by {overshoot} "
                f"steps and returns, so each point is reached moving in",
                "focus")

    def _pts() -> list[dict]:
        return [{"position": p, "hfr": h, "sigma": s} for p, h, s in points]

    def _result_pts() -> list[tuple[int, float]]:
        return [(p, h) for p, h, _ in points]

    #: The REQUESTED point count — the engine may extend past it to bracket a
    #: minimum, so the narration says "point 5 of ~9" honestly as an estimate.
    points_planned = 2 * steps_each_side + 1

    def _activity(activity: str | None, *, index: int | None = None) -> None:
        """Narrate what the sweep is doing THIS second (mirrors the polar
        driver's `activity`, 2026-08-07). Each point is exposure_s of shutter
        plus 2-4 s of measurement, and a chart that grows every ~8 s reads as
        hung in between — the same dead air the Align screen had."""
        bus.publish("focus", state="running", points=_pts(), best=None,
                    activity=activity, exposure_s=exposure_s,
                    point_index=index, points_planned=points_planned)

    #: The engine needs at least four measurable points to fit a curve, so a
    #: field with fewer stars than that AT BEST FOCUS certainly cannot produce
    #: them — defocusing spreads each star over more pixels and only ever finds
    #: fewer. This is the "certainly hopeless" line, and it refuses.
    MIN_STARS_TO_SWEEP = 4
    #: Below this it is doubtful rather than hopeless, so it WARNS and proceeds.
    #: Deliberately not a refusal: a synthetic field of 11 stars converges
    #: perfectly well, and blocking a sweep that would have worked is worse than
    #: attempting one that might not — the user can halt, and now knows why if
    #: it fails. (Measured on a real rig: 24 stars at bin 1 became 8 at bin 2
    #: and 0 a few thousand steps out, which is the case this warns about.)
    SPARSE_FIELD_WARN = 15

    #: How far past the REQUESTED window the search may roam, as a multiple of
    #: its half-span. The engine legitimately extends beyond the requested
    #: points to bracket a minimum; it must not leave the neighbourhood. 2x is
    #: deliberately generous so this fires only on a search that has lost the
    #: plot — on 2026-08-01 one walked 2600 steps past the floor of a +/-1000
    #: window and was still going when it was halted by hand.
    LEASH_FACTOR = 2

    #: (position, star count) for every frame too thin to be a measurement,
    #: positions where the detector found stars but could not size them, and how
    #: many positions we exposed at. These are the facts the advice needs: a run
    #: that dropped five of nine points failed for a reason it can NAME.
    dropped: list[tuple[int, int]] = []
    unsized: list[int] = []
    #: (position, saturated fraction) for the subset of ``dropped`` frames that
    #: were CLIPPED — the fact that decides which direction the advice points.
    #: "0 stars" off a railed frame means the stars merged, not that they were
    #: missing, and the fix runs the opposite way from every other shortage.
    clipped: list[tuple[int, float]] = []
    attempted = 0
    #: The position the last drop happened at, and how many times in a row.
    #: The sweep engine advances only when a measurement is ADDED, so a dropped
    #: point leaves it asking for the same position again — see
    #: ``MAX_DROPS_PER_POSITION`` for the rig run this spun on forever.
    drop_pos: int | None = None
    drops_here = 0
    #: The speculative move+expose running ahead of the measurement, if any, and
    #: the model that decides where to aim it. See `focus.pipeline`: a frame is
    #: only ever used for the position the engine actually asks for, so this
    #: cannot change what is measured — only when.
    predictor = SweepPredictor()
    prefetch: Prefetch | None = None

    async def _settle(*, cancel: bool = False) -> None:
        """Let any in-flight speculative exposure finish before we touch the
        devices ourselves. EVERY path out of the loop goes through this: the
        task owns the focuser and the camera while it runs, and a restore-to-
        start racing a speculative move is two moves on one focuser."""
        nonlocal prefetch
        if prefetch is not None:
            spent, prefetch = prefetch, None
            await spent.settle(cancel=cancel)

    #: Stars at the start position, and the knobs that would change that number.
    #: Set by the probe below; -1 until then so a failure BEFORE the probe (a
    #: camera that will not expose) says nothing about the field rather than
    #: reporting an imaginary zero.
    n0 = -1
    levers = sweep_levers(exposure_s, binning)

    def _advice(*, ok: bool) -> str | None:
        """The specific guidance THIS run earned — the sentence that used to go
        only to the log — or None when the run learned nothing specific.

        Every clause below is keyed to something this run MEASURED: points it
        had to drop, frames it could not size, how thin the fitted points were.
        What is deliberately absent is a cause inferred from a proxy. The
        start-position star count NEVER speaks on its own here, because the
        panel ranks this advice above the engine's own failure token said in
        words — so a guess made from ``n0`` does not merely sit beside the
        accurate explanation, it replaces it. A flat curve measured from 12
        stars at every point, nothing dropped, is a step-size or focuser fault;
        captioning it "too few stars" is the 2026-07-31 mistake with a fresh
        number in it, and worst on exactly the rigs that filed the bug (24 stars
        at bin 1 become 8 at bin 2, so EVERY failure there would be blamed on
        the field while the same fault on a rich rig got diagnosed correctly).

        None is the honest answer when nothing specific was learned: an absent
        advice lets the panel fall back to the engine token in words, whereas an
        invented one is precisely what sent a user under a 2% cloud sky out to
        "check the sky is clear"."""
        bits: list[str] = []
        if dropped:
            bits.append(dropped_points_phrase(dropped, attempted) + ".")
            if clipped:
                # The pixels outrank any inference from the counts: a railed
                # frame's "0 stars" is merged stars, not missing ones, and the
                # sparse-field context below would blame the sky for it.
                bits.append(overexposure_phrase(clipped) + ".")
            # The start count is context FOR THE DROPS, never a finding on its
            # own: a field already thin at best focus had no margin to lose,
            # which is the 24-at-bin-1 → 8-at-bin-2 → 0-further-out shape this
            # warns about. With drops on the record it is measured, not guessed.
            elif 0 <= n0 < SPARSE_FIELD_WARN:
                bits.append(f"The field had only {n0} stars at the start "
                            f"position, so it had nothing to spare as it "
                            f"defocused.")
        if unsized:
            # Not the user's to fix, so it gets no lever — but they should know
            # a point vanished for a reason that is not their sky.
            bits.append(f"{len(unsized)} frame"
                        f"{'' if len(unsized) == 1 else 's'} had stars this "
                        f"detector could not size "
                        f"({', '.join(str(p) for p in unsized[:3])}).")
        # Thin-but-measured points qualify a RESULT; they are not a cause of a
        # failure. On a failed run the engine's reason is the finding, and
        # burying it under a star-count caveat is the same suppression the n0
        # clause used to commit. (The per-point counts still go to the log.)
        thin = thin_points_phrase(counts) if ok and not dropped else None
        if thin:
            bits.append(thin)
        if not bits:
            return None
        # One "change this" at the end. Every starvation fact above has the same
        # three remedies, and repeating them per fact is how specific advice
        # starts reading as the boilerplate it replaces.
        if clipped:
            # Clipping outranks starvation: on a railed frame "expose longer"
            # is the one move guaranteed to make the next run worse.
            bits.append("Try " + overexposure_levers(exposure_s, gain)
                        + " — more light makes this worse.")
        elif dropped:
            # Points went missing for want of stars, so the levers ARE the fix.
            bits.append("Try " + levers + ".")
        elif thin:
            bits.append("A firmer result would need " + levers + ".")
        return " ".join(bits)

    try:
        # Before the probe frame, so a stopped mount costs one mount read and
        # not five minutes of exposures.
        await assert_tracking(tracking_check, "before the sweep")

        # How far the search may roam. The requested window is
        # start +/- step*steps_each_side; the leash is twice that half-span,
        # clamped to the focuser's real travel, so bracketing has room and a
        # runaway does not. See the move_to guard below for what this is for.
        #
        # Computed BEFORE the probe because the first speculative move happens
        # before the probe is measured, and a speculative move is exactly the
        # kind this leash exists to bound.
        half_span = step * steps_each_side
        leash_lo = max(0, start_pos - LEASH_FACTOR * half_span)
        leash_hi = min(getattr(focuser, "max_position", start_pos + half_span),
                       start_pos + LEASH_FACTOR * half_span)

        # THE ENGINE IS ASKED FOR ITS FIRST MOVE BEFORE THE PROBE IS MEASURED.
        # It can answer: the first step of a sweep is geometry, not a response
        # to anything measured. That is what lets the probe's own measurement
        # — a full-frame Rust pass, 27 s on the rig's sparsest field and 60+ on
        # a rich one — overlap the first point's move and exposure instead of
        # holding the camera shut through it.
        sweep = _native.FocusSweep(config, start_pos)
        first = sweep.next()

        # ONE frame before committing to the whole sweep. The failure this
        # prevents is not a crash: it is five minutes of moving the focuser to
        # reach "not_enough_spread", with nothing on screen saying the field was
        # too sparse to measure before it started.
        _activity("exposing")
        probe = await _expose()
        # AFTER the shutter closes, never before: the probe's star count is the
        # count AT THE START POSITION, and it sizes the measurement window for
        # the whole run. Starting the first move under the probe's EXPOSURE
        # would make it a count of somewhere else.
        if (first.get("action") == "move_to"
                and leash_lo <= int(first["position"]) <= leash_hi):
            nxt = int(first["position"])
            # Seeded, not guessed: the accounting must not read the run's one
            # certain move as a miss and turn speculation off before the first
            # point (see `SweepPredictor.seed`).
            predictor.seed(nxt)
            prefetch = Prefetch(
                nxt, asyncio.create_task(_move_and_expose(nxt)))
        _activity("measuring")
        _s, pstats = await asyncio.to_thread(
            _native.detect_and_measure, probe.data, params)
        n0 = int(pstats.get("star_count") or 0)
        probe_sat = saturation_fraction(probe.data)
        bus.log("info", f"autofocus: {n0} stars at the starting position", "focus")
        if n0 < MIN_STARS_TO_SWEEP:
            # Both of these land in the panel — ``reason`` as the verdict chip,
            # ``advice`` as the detail line — so between them they get to carry
            # two facts, not one fact twice. The chip states what was measured
            # against what a fit needs; the detail states what to CHANGE, and
            # therefore does not repeat the star count it is a response to.
            if probe_sat >= OVEREXPOSED_FRAC:
                # The pixels say why the stars are missing, and it is not the
                # sky: on 2026-08-06 this exact frame shape (3s/gain 200 over a
                # field the operator could SEE 200 stars in) was captioned
                # "lack of stars" with advice to expose longer — the one change
                # guaranteed to merge the blobs further.
                reason = (f"only {n0} measurable stars because the frame is "
                          f"overexposed — {probe_sat:.1%} of its pixels sit at "
                          f"full scale, so stars merge into saturated blobs")
                advice = ("Try " + overexposure_levers(exposure_s, gain)
                          + " — more light makes this worse.")
            else:
                reason = (f"only {n0} stars at the current focus — a curve "
                          f"needs at least {MIN_STARS_TO_SWEEP} measurable "
                          f"points and defocusing finds fewer, not more")
                # The fix rides on the RESULT and on the event, not only in the
                # log: this sentence was already going to the log on 2026-07-31
                # while the panel told a user under a clear sky to check the
                # sky. Built here rather than by _advice, which speaks only
                # from what a SWEEP measured and this run has not swept.
                advice = "Try " + levers + "."
            # THE SPECULATIVE MOVE IS ALREADY IN FLIGHT. It was started under
            # the probe's measurement, and it owns the focuser until it
            # finishes, so restoring the start position without settling first
            # is two moves on one focuser — on the sim, two loops chasing each
            # other's target for ever.
            await _settle()
            await _approach(start_pos)
            bus.publish("focus", state="failed", points=[], best=None,
                        message=reason, advice=advice)
            bus.log("warning",
                    f"autofocus not attempted: {reason}. {advice}", "focus")
            return AutofocusResult(False, start_pos, None, [], reason,
                                   advice=advice)
        if probe_sat >= OVEREXPOSED_FRAC:
            # Measurable, but on borrowed time: clipped cores read fat and
            # flat, so the curve's tip is distorted even when the fit succeeds.
            bus.log("warning",
                    f"autofocus: {probe_sat:.1%} of the starting frame is at "
                    f"full scale — star sizes will read fat and the curve tip "
                    f"may be distorted. Consider "
                    f"{overexposure_levers(exposure_s, gain)}.", "focus")
        elif n0 < SPARSE_FIELD_WARN:
            bus.log("warning",
                    f"autofocus: {n0} stars is a sparse field — the sweep may "
                    f"run out of measurable points as it defocuses. If it "
                    f"fails, try {levers}.", "focus")

        # HOW MUCH OF EACH FRAME THE SWEEP WILL MEASURE, decided ONCE, here,
        # from the only full-frame star count this run will make. See
        # `focus.window` for the rig numbers: on 26 MP frames the two per-point
        # measurements cost 27 s + 5-20 s against a 6 s exposure, and the
        # central 0.4 of each axis measures the same size for a sixth of that.
        #
        # ONCE and not per point, because the window is not merely a cheaper
        # sample: corner stars are bigger on this optic (the rig's Ha light read
        # 3.27 px windowed against 3.98 whole-frame), so a window that changed
        # mid-sweep would put a centre-versus-corner step into the curve itself.
        frac = measure_window(n0)
        if frac < 1.0:
            bus.log("info",
                    f"autofocus: measuring the central {frac:.0%} of each axis "
                    f"(about {max(1, int(round(n0 * frac * frac)))} of {n0} "
                    f"stars): the centre's focus, for a measurement "
                    f"{1 / (frac * frac):.1f}x cheaper", "focus")
        else:
            bus.log("info",
                    f"autofocus: measuring the whole frame — {n0} stars is too "
                    f"few to crop", "focus")

        #: The step the engine gave us before the probe, waiting to be handled
        #: by the first turn of the loop. ``next()`` IS NOT IDEMPOTENT — it
        #: advances the machine — so asking again here would skip the very
        #: point already being exposed for. Every later turn asks the engine.
        pending_step = first

        while True:
            if pending_step is not None:
                s, pending_step = pending_step, None
            else:
                s = sweep.next()
            action = s.get("action")

            if action == "move_to":
                pos = int(s["position"])
                # CLAIM THE SPECULATIVE FRAME FIRST, whatever happens next. This
                # both hands us the frame when the guess was right and — because
                # it awaits either way — gives the devices back before any of the
                # branches below move the focuser.
                frame = None
                if prefetch is not None:
                    spent, prefetch = prefetch, None
                    frame = await spent.take(pos)
                predictor.emitted(pos)
                # AND AGAIN AT EVERY POINT. A sweep is minutes long; a mount can
                # reach its meridian limit in the middle of one and every point
                # after that measures the drift. Asked here, with the devices
                # released and nothing in flight, so the teardown below can put
                # the focuser back cleanly.
                await assert_tracking(
                    tracking_check, f"at sweep point {attempted + 1}")
                if not (leash_lo <= pos <= leash_hi):
                    # A BOUNDED SWEEP MUST NOT BECOME AN UNBOUNDED WALK.
                    #
                    # 2026-08-01, on the sky: a run requested step 200 with 5
                    # points each side — a window of 9500..11500 — and marched
                    # to 6900, 2600 steps below the floor and still descending
                    # when it was halted by hand. Its size metric was inverted
                    # on that sparse field, so every step away from focus read
                    # "better" and the search believed it.
                    #
                    # The engine extending PAST the requested points to bracket
                    # a minimum is legitimate; leaving the neighbourhood
                    # entirely is not. Unattended this drives the drawtube at a
                    # mechanical stop that the EAF's firmware cannot report
                    # (3.3.8 answers NOT_SUPPORTED to EAFGetErrorCode — see
                    # vendor/zwo/EAF_focuser.h), so nothing downstream would
                    # notice either.
                    #
                    # The leash is deliberately generous — twice the requested
                    # half-span — so this can only ever fire on a search that
                    # has genuinely lost the plot, never on honest bracketing.
                    await _approach(start_pos)
                    reason = (f"the sweep tried to move to {pos}, outside the "
                              f"window this run asked for ({leash_lo}..{leash_hi}) "
                              f"— stopping rather than walking the focuser away")
                    advice = (
                        "The size metric was almost certainly reading smaller "
                        "the further out it went, which makes every wrong step "
                        "look like an improvement. Check the field is rich "
                        f"enough to measure while defocused, or try {levers}.")
                    bus.log("warning", f"autofocus: {reason}. {advice}", "focus")
                    # A search that walked off is the case where the per-step
                    # series matters most: it shows the metric that inverted.
                    bus.log("warning", vcurve_report(points, counts), "focus")
                    # _pts()/_result_pts(), NOT the raw tuples. Every other exit
                    # from this function publishes {position,hfr,sigma} dicts,
                    # and the UI's VCurve reads p.position/p.hfr off them
                    # (ui/src/components/graphs.tsx:78) — publishing 3-tuples
                    # here made every coordinate NaN, so the one failure whose
                    # whole story IS the shape of the curve was the one that
                    # drew an empty chart. Likewise AutofocusResult.points is
                    # (position, hfr) everywhere else.
                    bus.publish("focus", state="failed", points=_pts(),
                                best=None, message=reason, advice=advice)
                    return AutofocusResult(False, start_pos, None,
                                           _result_pts(), reason, advice=advice)
                if frame is None:
                    await _approach(pos)
                    _activity("exposing", index=attempted)
                    frame = await _expose()
                attempted += 1
                # ARM THE NEXT POINT BEFORE MEASURING THIS ONE. That overlap is
                # the whole speedup: the camera spends the star detection
                # exposing instead of waiting for it. The frame it produces is
                # used only if the engine asks for this exact position next —
                # see `focus.pipeline` for the rule that bounds a wrong guess.
                #
                # THE ENGINE IS ASKED, not a rule of thumb: `peek_next` runs the
                # hypothetical on a clone of the state machine, so the guess
                # survives the sweep's turn-round and the validation move
                # arrives LABELLED rather than counted to. `pos` is the point
                # whose frame is about to be measured; `points`/`counts` are
                # what has been measured so far, which is what the expected
                # measurement is extrapolated from.
                nxt = predictor.predict(sweep, pos, points, counts)
                if nxt is not None and leash_lo <= nxt <= leash_hi:
                    prefetch = Prefetch(
                        nxt, asyncio.create_task(_move_and_expose(nxt)))
                _activity("measuring", index=attempted - 1)
                # ONE MEASUREMENT PER POINT, AND ONLY THE CENTRE OF IT.
                #
                # The Rust `detect_and_measure` used to run here too, on the
                # whole frame: 27 s of a 40 s point on the rig's 26 MP frames
                # (60-67 s on a rich light), for a star count and a MAD the size
                # metric can supply itself. It is gone from the loop and lives
                # only at the probe, whose count sized the window above.
                #
                # THE SIZE COMES FROM focus_size, NOT FROM THE DETECTOR'S HFR.
                #
                # The engine's hfr_median is measured inside a fixed cutout, so
                # it cannot grow past that box — and once the star outgrows it,
                # the number stops describing the star and starts describing the
                # box. On 2026-08-01 that made a sweep read SMALLER the further
                # it went from focus, so every wrong step looked like an
                # improvement and the search walked the drawtube 3600 steps
                # before it was halted by hand.
                #
                # Measured on that same sky (a moonlit, sparse field — the
                # failing case, not the easy one), off the saved frames:
                #     offset  -1500  -900  -300  -150    +0  +150  +300  +900  +1500
                #     size     94.5  54.6  32.2  20.3   8.6   5.4  16.2  63.4  111.6
                # a clean V with its minimum at focus, on the very field where
                # the detector's HFR was inverted.
                #
                # Offloaded because it is numpy-heavy and would otherwise stall
                # the event stream the UI is drawing from.
                size, n, detail = _read_metric(
                    await asyncio.to_thread(native_sweep_metric,
                                            window_frame(frame, frac)))
                if frac < 1.0 and (size is None or n < MIN_STARS_PER_POINT):
                    # THE WINDOW RAN OUT OF SOURCES — measure this ONE point
                    # whole rather than drop it. A point measured from two
                    # donuts is worse than a slow point measured from twenty.
                    #
                    # This is allowed to change the pixels mid-sweep, and the
                    # window above is not, because of WHERE it can fire. A
                    # window that held 40 stars at the probe still holds them
                    # near focus, so this only ever fires at the wings — and out
                    # there defocus dominates the size, so the centre-versus-
                    # corner offset (1.5 px of a 20 px blob on the rig's worst
                    # filter) adds in quadrature: sqrt(20² + 1.5²) = 20.06. Near
                    # focus the same offset WOULD be a visible step in the
                    # curve, which is why this must not be turned into a
                    # per-point choice about which window is better.
                    whole = _read_metric(
                        await asyncio.to_thread(native_sweep_metric, frame))
                    bus.log("info",
                            f"autofocus: the central {frac:.0%} of {pos} held "
                            f"only {n} measurable source"
                            f"{'' if n == 1 else 's'} — measuring the whole "
                            f"frame for this point", "focus")
                    size, n, detail = whole
                hfr = size
                # WHICH COUNT IS "n", now that the Rust detector no longer
                # supplies one. `focus_size` reports two different numbers and
                # they are not interchangeable:
                #
                #   n (n_sources) — the sources that VOTED for the median, 5..25
                #     by construction (`_bright_population` lets the top flux
                #     quartile vote, capped). This is the sample size of the
                #     median, so it is the honest √n for `point_sigma` and the
                #     honest thing to compare against MIN_STARS_PER_POINT, whose
                #     docstring is about medians over one or two detections. The
                #     old Rust count over-stated it: √(1000/25) = 6x.
                #
                #   found (n_found) — the sources RESOLVED at all, the count
                #     that means "how rich was this frame". This is what
                #     THIN_POINT_STARS (10), RICH_FIELD_STARS (50) and
                #     `curve_verdict`'s tip gate were all measured against —
                #     NGC 5907, 2026-08-08: 460 stars at the best point, 2 at
                #     the eleventh — so `counts` carries this one. Feeding them
                #     the voter count instead would quietly quadruple the "thin"
                #     threshold (10 voters needs 40 detections) and make
                #     RICH_FIELD_STARS unreachable, which is the #114 wrong turn
                #     coming back in through the arithmetic.
                #
                # A substitute that returns only (size, n) has no n_found, so
                # the voter count stands in — the same number the legacy path's
                # `sweep_metric` has always reported.
                found = getattr(detail, "n_found", None) or n
                # REFUSE unmeasurable and near-empty frames — do not merely skip
                # the starless ones. A median over one or two detections is a hot
                # pixel's opinion, and this sweep used to record such a point with
                # exactly the authority of a 900-star frame. Not adding a
                # measurement lets the engine fail cleanly (it cannot bracket a
                # minimum) if EVERY frame is that thin — which restores start below.
                if (n < MIN_STARS_PER_POINT or hfr is None
                        or not math.isfinite(hfr) or hfr <= 0):
                    # Include what the frame actually looked like. "0 stars" on
                    # its own cannot distinguish a starless field from a black
                    # frame from a detector that rejected everything — and that
                    # ambiguity is what made this undiagnosable the first time.
                    # Sampled every 8th pixel: representative, and ~64x cheaper
                    # than a full-frame median inside the sweep loop.
                    px = frame.data[::8, ::8]
                    # Two different faults, kept apart: too few stars to have a
                    # median (a field/exposure problem the user can fix), versus
                    # stars the detector could not size (ours). Filing the second
                    # under the first would tell someone with a rich frame to
                    # expose longer — the class of wrong turn this change exists
                    # to stop.
                    if n < MIN_STARS_PER_POINT:
                        dropped.append((pos, n))
                        why = (f"only {n} stars — a fit point needs "
                               f"{MIN_STARS_PER_POINT}")
                        # A railed frame's missing stars MERGED; remember the
                        # clipping so the advice points at less light, not more.
                        sat = saturation_fraction(frame.data)
                        if sat >= OVEREXPOSED_FRAC:
                            clipped.append((pos, sat))
                            why += f", {sat:.1%} of pixels at full scale"
                    else:
                        unsized.append(pos)
                        why = f"{n} stars but no usable size ({hfr!r})"
                    bus.log("warning",
                            f"native autofocus: dropping {pos}: {why} "
                            f"(frame median {float(np.median(px)):.0f}, "
                            f"max {int(px.max())})",
                            "focus")
                    drops_here = drops_here + 1 if pos == drop_pos else 1
                    drop_pos = pos
                    if drops_here >= MAX_DROPS_PER_POSITION:
                        # The engine will keep asking for this position forever:
                        # it only advances on a measurement, and this one cannot
                        # be measured. Stop asking -- but do NOT throw away the
                        # points that WERE measured.
                        #
                        # ASK THE CURVE BEFORE GIVING UP. Measured on NGC 5907
                        # 2026-08-08 22:04: ten points, a textbook symmetric V,
                        # HFR 1.90 from 460 STARS at 11173, wings rising to
                        # 55.91 and 65.97, hyperbolic R-squared 0.996 against a
                        # 0.70 gate -- and the whole run was discarded because
                        # the ELEVENTH point, five half-steps out, showed 2
                        # detectable stars where 3 are needed. The stars there
                        # are so bloated that finding them is the thing that
                        # fails; that is a fact about the end of the sweep, not
                        # about the focus.
                        #
                        # `curve_verdict` was written for exactly this shape and
                        # was only consulted on the engine's own `failed` path,
                        # so this branch returned before ever reaching it.
                        salvage = curve_verdict(_result_pts(), counts)
                        await _settle()
                        if salvage.accepted and salvage.best_position is not None:
                            best = int(round(salvage.best_position))
                            best_hfr = min(h for _p, h, _s in points)
                            advice = _advice(ok=True)
                            await record_measured_span(
                                focuser, _result_pts(), best, binning)
                            await _approach(best)
                            bus.publish("focus", state="done", points=_pts(),
                                        best={"position": best,
                                              "hfr": best_hfr},
                                        advice=advice)
                            bus.log("info",
                                    f"native autofocus: {pos} could not be "
                                    f"measured ({why}), but the {len(points)} "
                                    f"points that were locate focus at {best} "
                                    f"— {salvage.reason}", "focus")
                            bus.log("info", vcurve_report(points, counts),
                                    "focus")
                            return AutofocusResult(
                                True, best, best_hfr, _result_pts(),
                                f"accepted on curve shape after the sweep ran "
                                f"out of measurable range at {pos}: "
                                f"{salvage.reason}", advice=advice)

                        await _approach(start_pos)
                        reason = (
                            f"the sweep could not measure {pos} on "
                            f"{drops_here} tries in a row ({why}), and the "
                            f"search cannot move on without it")
                        # Two different failures wore one message. If the BEST
                        # point of the sweep is rich, the field is fine and the
                        # sweep simply reached past what it can measure -- say
                        # that, and name the range that worked. Blaming the
                        # filter there is the #114 wrong turn: it sends someone
                        # to lengthen an exposure that was never the problem.
                        best_n = max((n for n in counts), default=0)
                        advice = over_swept_advice(
                            best_n, [p for p, _h, _s in points], pos, levers)
                        bus.log("warning",
                                f"autofocus: {reason}. {advice}", "focus")
                        bus.log("warning", vcurve_report(points, counts), "focus")
                        bus.publish("focus", state="failed", points=_pts(),
                                    best=None, message=reason, advice=advice)
                        return AutofocusResult(False, start_pos, None,
                                               _result_pts(), reason,
                                               advice=advice)
                    continue

                # σ for the fit is the standard error of THIS median, not the raw
                # population MAD: n is what makes a point believable, and the
                # engine weights by 1/σ². Publish the same σ as the whisker so the
                # chart's confidence and the fit's agree — a five-star point that
                # drew a hairline whisker was the chart lying about its evidence.
                #
                # The scatter is now the SIZE METRIC'S OWN, over the sources that
                # voted for this median (`SourceSize.mad`). It used to be the
                # Rust detector's MAD for the Rust detector's estimator, carried
                # across as a RATIO because the two measured different things;
                # measuring the scatter of the population we actually fit
                # removes the translation and the second full-frame pass with
                # it. Still expressed relatively so the 2% floor keeps its
                # meaning. No scatter (a single donut, or a substitute that does
                # not model one) falls back to 5%: no size is known better than
                # that, and point_sigma's √n term is what actually separates a
                # measurement from a rumour.
                rel = 0.05
                mad = getattr(detail, "mad", None)
                if mad is not None and float(hfr) > 0:
                    rel = max(0.02, float(mad) / float(hfr))
                sigma = point_sigma(float(hfr), rel * float(hfr), n)
                # The engine's own star_count argument is the fit's weight input
                # beside σ, so it gets the same voter count σ was computed from.
                sweep.add_measurement(pos, float(hfr), sigma, n)
                points.append((pos, float(hfr), sigma))
                counts.append(found)
                # The count on a GOOD point is the margin: a sweep that works
                # with 9 stars and one that works with 90 look identical in a
                # log that only mentions failures. BOTH numbers, because they
                # now differ and the morning log is the only forensic record:
                # how rich the frame was, and how many of those actually voted.
                bus.log("info",
                        f"autofocus: {pos} -> HFR {hfr:.2f} ({found} stars, "
                        f"{n} sized)", "focus")
                bus.publish("focus", state="running", points=_pts(), best=None,
                            activity=None, point_index=attempted - 1,
                            points_planned=points_planned)

            elif action == "done":
                await _settle()
                outcome = s.get("outcome") or {}
                best = int(outcome.get("best_position", start_pos))
                model_hfr = outcome.get("best_value")
                fit = _fit_payload(outcome)
                # THE SWEEP MEASURED A FRAME AT THE FITTED POSITION. Use it.
                # Every sweep on 2026-08-19 moved to a vertex whose own
                # confirming frame read worse than a sample already in hand,
                # and the frames that followed the worst of them were 10-20%
                # softer across all six filters.
                verdict = confirmed_best(_result_pts(), best)
                if verdict.overridden:
                    bus.log("warning", f"autofocus: {verdict.reason}", "focus")
                    best = verdict.position
                # What was MEASURED at the position we are settling on. The
                # completion line used to quote the fit's y0, which is a model
                # number the very next exposure contradicted (2.94 logged
                # against 3.78 measured).
                best_hfr = (verdict.measured_hfr
                            if verdict.measured_hfr is not None else model_hfr)
                # Even a success can rest on thin evidence — say so rather than
                # letting a confident R² stand on four five-star samples.
                advice = _advice(ok=True)
                # WHAT THIS SWEEP TAUGHT THE NEXT ONE. A curve the engine
                # accepted is the only kind worth learning a defocus slope from
                # — a rejected one describes something that is not a V.
                await record_measured_span(focuser, _result_pts(), best, binning)
                # Settle the focuser on the position we CHOSE — the fitted vertex,
                # or the best measured sample when the confirming frame refused it.
                await _approach(best)
                bus.publish("focus", state="done", points=_pts(),
                            best={"position": best, "hfr": best_hfr,
                                  "model_hfr": model_hfr,
                                  "confirmed": not verdict.overridden}, fit=fit,
                            advice=advice)
                measured_note = (f", HFR {best_hfr:.2f} measured"
                                 if verdict.measured_hfr is not None
                                 else (f", HFR {best_hfr:.2f}"
                                       if isinstance(best_hfr, (int, float)) else ""))
                model_note = (f" (fit {model_hfr:.2f}, {fit.get('method')})"
                              if isinstance(model_hfr, (int, float))
                              and verdict.measured_hfr is not None
                              else f" ({fit.get('method')})")
                bus.log("info",
                        f"native autofocus complete: position {best}"
                        + measured_note + model_note, "focus")
                return AutofocusResult(True, best, best_hfr, _result_pts(), "ok",
                                       advice=advice)

            elif action == "failed":
                reason = s.get("reason") or "autofocus failed"

                # THE ENGINE'S GATE IS A STATISTIC; THE CURVE IS THE EVIDENCE.
                #
                # `r_squared_below_threshold` is scored on how faithfully the
                # model tracks the WINGS, which are the part of a V-curve
                # nobody wants a number from — see the block over
                # `curve_verdict` for the measured table where a perfect fit at
                # exactly the right position scores anywhere from 0.996 to
                # −0.611 depending only on how far the wings saturate. The rig
                # threw away a 1.67 px minimum at 11173 from 321 stars on
                # 2026-08-08 for that reason, and autofocus succeeds about one
                # run in thirteen here.
                #
                # So ask the curve directly before accepting the refusal. The
                # criterion is strict enough that a genuinely bad sweep still
                # fails it (a flat one has no interior minimum; a starved one
                # has no depth against its own scatter), which is why this can
                # sit under EVERY engine failure rather than only the one
                # reason: the shape decides, not the label.
                salvage = curve_verdict(_result_pts(), counts)
                await _settle()
                if salvage.accepted and salvage.best_position is not None:
                    best = int(round(salvage.best_position))
                    # The smallest size the sweep MEASURED, not a fitted value
                    # and not a re-measurement: the fitted tip of a curve whose
                    # wings saturate is an extrapolation, and re-exposing here
                    # would spend a frame on a number nothing acts on. The
                    # engine's own `done` path reports its fit's `best_value`,
                    # which is the same kind of claim; this one is at least a
                    # reading. (`points` is non-empty — the criterion needs
                    # five of them.)
                    best_hfr = min(h for _p, h, _s in points)
                    advice = _advice(ok=True)
                    await record_measured_span(focuser, _result_pts(), best, binning)
                    await _approach(best)
                    bus.publish("focus", state="done", points=_pts(),
                                best={"position": best, "hfr": best_hfr},
                                advice=advice)
                    bus.log("info",
                            f"native autofocus: the engine refused this sweep "
                            f"({reason}), but the curve locates focus at "
                            f"{best} — {salvage.reason}", "focus")
                    bus.log("info", vcurve_report(points, counts), "focus")
                    return AutofocusResult(
                        True, best, best_hfr, _result_pts(),
                        f"accepted on curve shape after the fit gate refused it "
                        f"({reason}): {salvage.reason}", advice=advice)

                # The engine's reason names the SHAPE of the failure
                # ("not_enough_spread"); the advice names what THIS run can do
                # about it, from what it measured. Where the run has nothing
                # specific to say it says nothing — see _advice.
                advice = _advice(ok=False)
                # A FLAT CURVE IS THE ONE FAILURE A TOO-NARROW SPAN PRODUCES, so
                # a span WE sized is the first thing to doubt. Forgetting it
                # sends the next attempt back to the shipped 350 rather than
                # letting one bad calibration narrow every sweep of the night
                # into the same failure. An operator's explicit step is not
                # ours to forget, hence `geometry.measured`.
                if geometry.measured and reason in FLAT_FAILURES:
                    forget_measured_span(focuser)
                    bus.log("warning",
                            f"autofocus: this sweep was sized from a measured "
                            f"defocus slope and came back {reason} — discarding "
                            f"that calibration so the next run uses the "
                            f"{DEFAULT_STEP}-step default", "focus")
                # Restore start: never park the focuser at an arbitrary sweep point.
                await _approach(start_pos)
                bus.publish("focus", state="failed", points=_pts(), best=None,
                            message=reason, advice=advice)
                bus.log("warning", f"native autofocus failed: {reason}"
                        + (f" — {advice}" if advice else ""), "focus")
                # The data behind the one-word reason, so the NEXT failure is
                # diagnosable from the log alone. Second line on purpose: the
                # verdict stays short and scannable, the evidence sits under it.
                bus.log("warning", vcurve_report(points, counts), "focus")
                # And why the CURVE did not rescue it either — the second
                # opinion, in shape terms. It goes to the LOG only. Not into
                # ``reason``: that string is a token the UI maps to human copy
                # (ui/src/lib/autofocus.ts readFocusFailure) and the sequence
                # engine records verbatim, so decorating it turns a recognised
                # failure into an unrecognised one. Not into ``advice`` either:
                # that field is what THIS RUN can change, and a run that
                # measured everything cleanly and still failed is required to
                # keep quiet there rather than crowd out the engine's reason
                # (test_autofocus_advice.py).
                bus.log("warning", f"curve check agrees: {salvage.reason}", "focus")
                return AutofocusResult(False, start_pos, None, _result_pts(),
                                       reason, advice=advice)

            else:  # pragma: no cover - defensive: unknown engine action
                raise DeviceError(f"native autofocus: unexpected step {action!r}")

    except BaseException as e:
        # Any transient DeviceError, a cancel from /api/focuser/halt, or an engine
        # ValueError must still return the focuser to where the sweep began before
        # propagating. Shield the restore so even a cancel completes the move.
        #
        # CANCELLED, not awaited, and before the restore. Everywhere else a
        # speculative exposure is allowed to finish; here the sweep itself is
        # already being torn down, so waiting out a 30 s narrowband frame would
        # only delay the halt the user asked for — and the restore below must
        # not race a speculative move on the same focuser.
        with contextlib.suppress(Exception):
            await _settle(cancel=True)
        with contextlib.suppress(Exception):
            await asyncio.shield(_approach(start_pos))
        bus.publish("focus", state="failed", points=_pts(), best=None,
                    message=str(e) or "native autofocus failed",
                    advice=_advice(ok=False))
        # A sweep killed part-way (a focuser that refused a move, a halt from
        # the UI) still measured something, and those points are the only
        # record of it — the publish above reaches a browser that may not be
        # open, the log reaches the morning.
        if points:
            bus.log("warning", vcurve_report(points, counts), "focus")
        # Map engine input rejections to a user-presentable DeviceError.
        if isinstance(e, ValueError):
            raise DeviceError(f"native engine: {e}") from e
        raise
