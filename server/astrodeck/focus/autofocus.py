"""V-curve autofocus.

Sweep the focuser through a window around the current position, measure
median HFR at each step, fit a parabola to the V-curve, move to the minimum,
and verify. Publishes progress on the event bus so the UI can draw the curve
live.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..devices.base import Camera, Focuser
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
                        binning: int = 2) -> AutofocusResult:
    start_pos = await focuser.get_position()
    positions = [start_pos + step * i
                 for i in range(-steps_each_side, steps_each_side + 1)]
    positions = [max(0, min(focuser.max_position, p)) for p in positions]

    points: list[tuple[int, float]] = []
    bus.publish("focus", state="running", points=[], best=None)

    # Approach from below to take out backlash, then sweep upward.
    await focuser.move_to(max(0, positions[0] - step))

    for pos in positions:
        await focuser.move_to(pos)
        frame = await camera.expose(exposure_s, gain, 30, binning=binning)
        hfr, n_stars = median_hfr(frame.data)
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
    a, b, c = np.polyfit(xs, ys, 2)
    if a > 0 and len(points) >= 6:
        # Refine: V-curves are hyperbolic, so a parabola fit over the full
        # (often asymmetric) sweep biases the vertex. Refit on the points
        # nearest the coarse minimum, where a parabola is a good local model.
        vertex = -b / (2 * a)
        nearest = np.argsort(np.abs(xs - vertex))[:5]
        a, b, c = np.polyfit(xs[nearest], ys[nearest], 2)
    if a <= 0:
        await focuser.move_to(start_pos)
        bus.publish("focus", state="failed",
                    points=[{"position": p, "hfr": h} for p, h in points], best=None)
        return AutofocusResult(False, start_pos, None, points,
                               "no V-curve minimum found (flat or inverted fit)")

    best = int(np.clip(-b / (2 * a), xs.min(), xs.max()))
    # Final move from below for backlash consistency
    await focuser.move_to(max(0, best - step))
    await focuser.move_to(best)

    frame = await camera.expose(exposure_s, gain, 30, binning=binning)
    final_hfr, _ = median_hfr(frame.data)
    bus.publish("focus", state="done",
                points=[{"position": p, "hfr": h} for p, h in points],
                best={"position": best, "hfr": final_hfr})
    bus.log("info", f"autofocus complete: position {best}, HFR {final_hfr:.2f}"
            if final_hfr else f"autofocus complete: position {best}", "focus")
    return AutofocusResult(True, best, final_hfr, points, "ok")
