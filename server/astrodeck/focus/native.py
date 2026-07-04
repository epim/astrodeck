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

from ..devices.base import Camera, DeviceError, Focuser
from ..events import bus
from ..providers import NATIVE_AVAILABLE
from .autofocus import AutofocusResult

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

    # (position, hfr, sigma) — sigma is the per-point HFR MAD, published so the
    # rebuilt Focus view can draw a real error whisker on each measured point
    # (additive; the legacy {position,hfr} contract is preserved, `sigma` rides
    # alongside). AutofocusResult still gets plain (position,hfr) tuples.
    points: list[tuple[int, float, float]] = []
    bus.publish("focus", state="running", points=[], best=None)

    def _pts() -> list[dict]:
        return [{"position": p, "hfr": h, "sigma": s} for p, h, s in points]

    def _result_pts() -> list[tuple[int, float]]:
        return [(p, h) for p, h, _ in points]

    try:
        sweep = _native.FocusSweep(config, start_pos)

        while True:
            s = sweep.next()
            action = s.get("action")

            if action == "move_to":
                pos = int(s["position"])
                await focuser.move_to(pos)
                frame = await _expose()
                # detect_and_measure releases the GIL but is CPU-heavy; offload it
                # so focuser/camera awaits and the event stream stay responsive.
                _stars, stats = await asyncio.to_thread(
                    _native.detect_and_measure, frame.data, params)

                n = int(stats.get("star_count") or 0)
                hfr = stats.get("hfr_median")
                # Skip unmeasurable frames (no stars / degenerate HFR) exactly like
                # the legacy path: feeding a 0.0 HFR would poison the fit. Not adding
                # a measurement lets the engine fail cleanly (can't bracket a
                # minimum) if EVERY frame is starless — which restores start below.
                if n <= 0 or hfr is None or not math.isfinite(hfr) or hfr <= 0:
                    bus.log("warning",
                            f"native autofocus: only {n} stars at {pos}, skipping",
                            "focus")
                    continue

                # HFR MAD is the per-point σ; floor the FIT weight so a perfectly
                # flat (zero-MAD) point doesn't become an infinite-weight anchor,
                # but publish the RAW mad as the whisker half-length (0 ⇒ no whisker).
                mad = float(stats.get("hfr_mad") or 0.0)
                weight = mad or 0.001
                sweep.add_measurement(pos, float(hfr), weight, n)
                points.append((pos, float(hfr), mad))
                bus.publish("focus", state="running", points=_pts(), best=None)

            elif action == "done":
                outcome = s.get("outcome") or {}
                best = int(outcome.get("best_position", start_pos))
                best_hfr = outcome.get("best_value")
                fit = _fit_payload(outcome)
                # Settle the focuser on the fitted optimum before reporting done.
                await focuser.move_to(best)
                bus.publish("focus", state="done", points=_pts(),
                            best={"position": best, "hfr": best_hfr}, fit=fit)
                bus.log("info",
                        f"native autofocus complete: position {best}"
                        + (f", HFR {best_hfr:.2f}" if isinstance(best_hfr, (int, float))
                           else "")
                        + f" ({fit.get('method')})", "focus")
                return AutofocusResult(True, best, best_hfr, _result_pts(), "ok")

            elif action == "failed":
                reason = s.get("reason") or "autofocus failed"
                # Restore start: never park the focuser at an arbitrary sweep point.
                await focuser.move_to(start_pos)
                bus.publish("focus", state="failed", points=_pts(), best=None,
                            message=reason)
                bus.log("warning", f"native autofocus failed: {reason}", "focus")
                return AutofocusResult(False, start_pos, None, _result_pts(), reason)

            else:  # pragma: no cover - defensive: unknown engine action
                raise DeviceError(f"native autofocus: unexpected step {action!r}")

    except BaseException as e:
        # Any transient DeviceError, a cancel from /api/focuser/halt, or an engine
        # ValueError must still return the focuser to where the sweep began before
        # propagating. Shield the restore so even a cancel completes the move.
        with contextlib.suppress(Exception):
            await asyncio.shield(focuser.move_to(start_pos))
        bus.publish("focus", state="failed", points=_pts(), best=None,
                    message=str(e) or "native autofocus failed")
        # Map engine input rejections to a user-presentable DeviceError.
        if isinstance(e, ValueError):
            raise DeviceError(f"native engine: {e}") from e
        raise
