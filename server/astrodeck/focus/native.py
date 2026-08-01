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
from .autofocus import (MIN_STARS_PER_POINT, THIN_POINT_STARS, AutofocusResult,
                        dropped_points_phrase, sweep_levers)

# Guarded handle to the Rust wheel. ``NATIVE_AVAILABLE`` (the single source of
# truth) already told us whether the import can succeed; we re-import here only
# to get the module object for the FocusSweep / detect_and_measure calls. When
# the wheel is absent this stays None and ``run_native_autofocus`` refuses with a
# clear DeviceError rather than NameError-ing deep in the loop.
try:  # pragma: no cover - covered both ways via NATIVE_AVAILABLE monkeypatch
    import astrodeck_native as _native
except ImportError:  # pragma: no cover
    _native = None


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
                               step: int = 350, steps_each_side: int = 4,
                               binning: int = 2, expose_guard=None,
                               hfr_method: str | None = None) -> AutofocusResult:
    """Run a V-curve autofocus sweep driven by the native Rust engine.

    ``expose_guard`` mirrors the legacy path: an async context-manager factory
    taking one label, used to serialize the single camera against the live loop /
    single-capture / sequence exposures (hub capture guard). ``hfr_method`` selects
    a detector preset ("autofocus"/"advanced"/"typical") — None uses the shipped
    default.

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

    # Detector params: None selects the shipped Typical preset; an explicit
    # ``hfr_method`` picks a base profile before the engine's per-field defaults.
    params = {"profile": hfr_method} if hfr_method else None

    start_pos = await focuser.get_position()
    # Missing config keys (backlash strategy, max attempts, outlier policy) take
    # the engine's NINA defaults — we only pin the geometry we were asked for.
    config = {
        "step_size": step,
        "offset_steps": steps_each_side,
        "max_position": focuser.max_position,
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

    def _pts() -> list[dict]:
        return [{"position": p, "hfr": h, "sigma": s} for p, h, s in points]

    def _result_pts() -> list[tuple[int, float]]:
        return [(p, h) for p, h, _ in points]

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

    #: (position, star count) for every frame too thin to be a measurement,
    #: positions where the detector found stars but could not size them, and how
    #: many positions we exposed at. These are the facts the advice needs: a run
    #: that dropped five of nine points failed for a reason it can NAME.
    dropped: list[tuple[int, int]] = []
    unsized: list[int] = []
    attempted = 0
    #: Stars at the start position, and the knobs that would change that number.
    #: Set by the probe below; -1 until then so a failure BEFORE the probe (a
    #: camera that will not expose) says nothing about the field rather than
    #: reporting an imaginary zero.
    n0 = -1
    levers = sweep_levers(exposure_s, binning)

    def _advice(*, ok: bool) -> str | None:
        """The specific guidance THIS run earned — the sentence that used to go
        only to the log — or None when the run learned nothing specific.

        None is the honest answer: an absent advice lets the panel fall back to
        its generic copy, whereas an invented one is precisely what sent a user
        under a 2% cloud sky out to "check the sky is clear" on 2026-07-31."""
        bits: list[str] = []
        starved = False   # …of stars, which is what the levers below can fix
        if not ok and 0 <= n0 < SPARSE_FIELD_WARN:
            bits.append(f"Only {n0} stars in the field — too few to keep "
                        f"measuring as the sweep defocuses.")
            starved = True
        if dropped:
            bits.append(dropped_points_phrase(dropped, attempted) + ".")
            starved = True
        if unsized:
            # Not the user's to fix, so it gets no lever — but they should know
            # a point vanished for a reason that is not their sky.
            bits.append(f"{len(unsized)} frame"
                        f"{'' if len(unsized) == 1 else 's'} had stars this "
                        f"detector could not size "
                        f"({', '.join(str(p) for p in unsized[:3])}).")
        weak = sum(1 for c in counts if c < THIN_POINT_STARS)
        if ok and weak and not dropped:
            bits.append(f"{weak} of {len(counts)} points came from fewer than "
                        f"{THIN_POINT_STARS} stars and carry little weight in "
                        f"the fit, so this is thinner evidence than the R² "
                        f"suggests.")
            starved = True
        if not bits:
            return None
        if starved:
            # One "change this" at the end. Every starvation fact above has the
            # same three remedies, and repeating them per fact is how specific
            # advice starts reading as the boilerplate it replaces.
            bits.append(("A firmer result would need " if ok else "Try ")
                        + levers + ".")
        return " ".join(bits)

    try:
        # ONE frame before committing to the whole sweep. The failure this
        # prevents is not a crash: it is five minutes of moving the focuser to
        # reach "not_enough_spread", with nothing on screen saying the field was
        # too sparse to measure before it started.
        probe = await _expose()
        _s, pstats = await asyncio.to_thread(
            _native.detect_and_measure, probe.data, params)
        n0 = int(pstats.get("star_count") or 0)
        bus.log("info", f"autofocus: {n0} stars at the starting position", "focus")
        if n0 < MIN_STARS_TO_SWEEP:
            # message = the diagnosis (what was measured against what is needed);
            # advice = the fix, naming the exposure and binning THIS run used.
            # They are two lines in the panel, so neither repeats the other.
            reason = (f"only {n0} stars at the current focus — a curve needs at "
                      f"least {MIN_STARS_TO_SWEEP} measurable points and "
                      f"defocusing finds fewer, not more")
            # The advice rides on the RESULT and on the event, not only in the
            # log: this same sentence was already going to the log on 2026-07-31
            # while the panel told a user under a clear sky to check the sky.
            advice = _advice(ok=False)
            await focuser.move_to(start_pos)
            bus.publish("focus", state="failed", points=[], best=None,
                        message=reason, advice=advice)
            bus.log("warning",
                    f"autofocus not attempted: {reason}. {advice}", "focus")
            return AutofocusResult(False, start_pos, None, [], reason,
                                   advice=advice)
        if n0 < SPARSE_FIELD_WARN:
            bus.log("warning",
                    f"autofocus: {n0} stars is a sparse field — the sweep may "
                    f"run out of measurable points as it defocuses. If it "
                    f"fails, try {levers}.", "focus")

        sweep = _native.FocusSweep(config, start_pos)

        while True:
            s = sweep.next()
            action = s.get("action")

            if action == "move_to":
                pos = int(s["position"])
                await focuser.move_to(pos)
                frame = await _expose()
                attempted += 1
                # detect_and_measure releases the GIL but is CPU-heavy; offload it
                # so focuser/camera awaits and the event stream stay responsive.
                _stars, stats = await asyncio.to_thread(
                    _native.detect_and_measure, frame.data, params)

                n = int(stats.get("star_count") or 0)
                hfr = stats.get("hfr_median")
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
                    else:
                        unsized.append(pos)
                        why = f"{n} stars but no usable HFR ({hfr!r})"
                    bus.log("warning",
                            f"native autofocus: dropping {pos}: {why} "
                            f"(frame median {float(np.median(px)):.0f}, "
                            f"max {int(px.max())})",
                            "focus")
                    continue

                # σ for the fit is the standard error of THIS median, not the raw
                # population MAD: n is what makes a point believable, and the
                # engine weights by 1/σ². Publish the same σ as the whisker so the
                # chart's confidence and the fit's agree — a five-star point that
                # drew a hairline whisker was the chart lying about its evidence.
                mad = float(stats.get("hfr_mad") or 0.0)
                sigma = point_sigma(float(hfr), mad, n)
                sweep.add_measurement(pos, float(hfr), sigma, n)
                points.append((pos, float(hfr), sigma))
                counts.append(n)
                # The count on a GOOD point is the margin: a sweep that works
                # with 9 stars and one that works with 90 look identical in a
                # log that only mentions failures.
                bus.log("info",
                        f"autofocus: {pos} -> HFR {hfr:.2f} ({n} stars)", "focus")
                bus.publish("focus", state="running", points=_pts(), best=None)

            elif action == "done":
                outcome = s.get("outcome") or {}
                best = int(outcome.get("best_position", start_pos))
                best_hfr = outcome.get("best_value")
                fit = _fit_payload(outcome)
                # Even a success can rest on thin evidence — say so rather than
                # letting a confident R² stand on four five-star samples.
                advice = _advice(ok=True)
                # Settle the focuser on the fitted optimum before reporting done.
                await focuser.move_to(best)
                bus.publish("focus", state="done", points=_pts(),
                            best={"position": best, "hfr": best_hfr}, fit=fit,
                            advice=advice)
                bus.log("info",
                        f"native autofocus complete: position {best}"
                        + (f", HFR {best_hfr:.2f}" if isinstance(best_hfr, (int, float))
                           else "")
                        + f" ({fit.get('method')})", "focus")
                return AutofocusResult(True, best, best_hfr, _result_pts(), "ok",
                                       advice=advice)

            elif action == "failed":
                reason = s.get("reason") or "autofocus failed"
                # The engine's reason names the SHAPE of the failure
                # ("not_enough_spread"); the advice names what THIS run can do
                # about it, from what it measured. Where the run has nothing
                # specific to say it says nothing — see _advice.
                advice = _advice(ok=False)
                # Restore start: never park the focuser at an arbitrary sweep point.
                await focuser.move_to(start_pos)
                bus.publish("focus", state="failed", points=_pts(), best=None,
                            message=reason, advice=advice)
                bus.log("warning", f"native autofocus failed: {reason}"
                        + (f" — {advice}" if advice else ""), "focus")
                return AutofocusResult(False, start_pos, None, _result_pts(),
                                       reason, advice=advice)

            else:  # pragma: no cover - defensive: unknown engine action
                raise DeviceError(f"native autofocus: unexpected step {action!r}")

    except BaseException as e:
        # Any transient DeviceError, a cancel from /api/focuser/halt, or an engine
        # ValueError must still return the focuser to where the sweep began before
        # propagating. Shield the restore so even a cancel completes the move.
        with contextlib.suppress(Exception):
            await asyncio.shield(focuser.move_to(start_pos))
        bus.publish("focus", state="failed", points=_pts(), best=None,
                    message=str(e) or "native autofocus failed",
                    advice=_advice(ok=False))
        # Map engine input rejections to a user-presentable DeviceError.
        if isinstance(e, ValueError):
            raise DeviceError(f"native engine: {e}") from e
        raise
