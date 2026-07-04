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
from ..imaging.stars import median_hfr


@dataclass
class AutofocusResult:
    success: bool
    best_position: int
    best_hfr: float | None
    points: list[tuple[int, float]] = field(default_factory=list)
    message: str = ""


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
    bus.publish("focus", state="running", points=[], best=None)

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
            hfr, n_stars = await asyncio.to_thread(median_hfr, frame.data)
            if hfr is None:
                bus.log("warning", f"autofocus: only {n_stars} stars at {pos}, skipping", "focus")
                continue
            points.append((pos, hfr))
            bus.publish("focus", state="running",
                        points=[{"position": p, "hfr": h} for p, h in points], best=None)

        if len(points) < 4:
            await focuser.move_to(start_pos)
            bus.publish("focus", state="failed", points=[], best=None)
            return AutofocusResult(False, start_pos, None, points,
                                   "not enough measurable points (need stars in frame)")

        xs = np.array([p for p, _ in points], dtype=np.float64)
        ys = np.array([h for _, h in points], dtype=np.float64)
        # Sort by position so "neighbours" are sweep-adjacent (the sweep already
        # runs in order, but a skipped point or a refit must not assume that).
        order = np.argsort(xs)
        xs, ys = xs[order], ys[order]

        a, b, c = np.polyfit(xs, ys, 2)
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
                al, bl, cl = np.polyfit(xl, yl, 2)
                if al > 0:
                    a, b, c = al, bl, cl
        if a <= 0:
            await focuser.move_to(start_pos)
            bus.publish("focus", state="failed",
                        points=[{"position": p, "hfr": h} for p, h in points], best=None)
            return AutofocusResult(False, start_pos, None, points,
                                   "no V-curve minimum found (flat or inverted fit)")

        vertex = -b / (2 * a)
        # "Minimum not bracketed" guard (review 4d): if the fitted vertex falls at
        # or outside the measured sweep range, the true focus is beyond the window
        # we explored — clipping to the edge and reporting success would silently
        # park the focuser at a boundary that is NOT in focus. Fail loudly instead
        # so the caller can widen / recentre the sweep.
        lo_edge, hi_edge = float(xs.min()), float(xs.max())
        if not (lo_edge < vertex < hi_edge):
            await focuser.move_to(start_pos)
            bus.publish("focus", state="failed",
                        points=[{"position": p, "hfr": h} for p, h in points], best=None)
            return AutofocusResult(
                False, start_pos, None, points,
                "minimum not bracketed — true focus is outside the swept range "
                "(widen the sweep or recentre)")

        best = int(np.clip(vertex, lo_edge, hi_edge))
        # Final move from below for backlash consistency
        await focuser.move_to(max(0, best - step))
        await focuser.move_to(best)

        frame = await _expose()
        final_hfr, _ = await asyncio.to_thread(median_hfr, frame.data)
    except BaseException:
        # Never leave the focuser parked at an arbitrary sweep position. Restore
        # start_pos best-effort (shielded so even a cancel completes the move
        # rather than interrupting it), flag the UI, then re-raise so the caller
        # (engine / _spawn) still sees the failure.
        with contextlib.suppress(Exception):
            await asyncio.shield(focuser.move_to(start_pos))
        bus.publish("focus", state="failed",
                    points=[{"position": p, "hfr": h} for p, h in points], best=None)
        raise

    bus.publish("focus", state="done",
                points=[{"position": p, "hfr": h} for p, h in points],
                best={"position": best, "hfr": final_hfr})
    bus.log("info", f"autofocus complete: position {best}, HFR {final_hfr:.2f}"
            if final_hfr else f"autofocus complete: position {best}", "focus")
    return AutofocusResult(True, best, final_hfr, points, "ok")


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
