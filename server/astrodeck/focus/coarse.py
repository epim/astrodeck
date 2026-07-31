"""Coarse focus: get close enough that autofocus can take over.

Autofocus fits a V-curve, so it needs stars at the START — and a badly
defocused rig has none, because the flux is spread until nothing clears the
detection threshold. Measured on the frame from 2026-07-30: the brightest
source in 26 million pixels was 7.7 sigma above background. There was nothing
to measure, autofocus correctly refused, and the only tool offered for getting
to rough focus WAS autofocus. That is the loop this breaks.

This is deliberately not a second autofocus. It fits nothing and finds no
minimum. It walks the travel, counts stars at each stop, and hands over the
moment a position has enough of them — which is the only question that matters
when you cannot see anything at all.
"""
from __future__ import annotations

import asyncio
import contextlib

from ..events import bus
from ..devices.base import Camera, DeviceError, Focuser
from .autofocus import AutofocusResult

#: The bar autofocus itself refuses below (focus/native.py MIN_STARS_TO_SWEEP).
#: Reaching it is this routine's entire job — one more star than this is not
#: better focus, it is just a longer search.
ENOUGH_STARS = 4

#: Stops across the searched range when the caller does not say. Nine keeps a
#: full-travel sweep under a couple of minutes at a short exposure while still
#: landing inside the critical zone of a typical refractor.
DEFAULT_STOPS = 9

#: Wall-clock cap. A search that cannot finish is worse than one that gives up:
#: the mount is tracking and the night is running.
DEFAULT_TIMEOUT_S = 600.0


def plan_positions(current: int, max_position: int,
                   span: int | None = None, stops: int = DEFAULT_STOPS) -> list[int]:
    """The positions to visit, nearest-first.

    Nearest-first matters: focus is usually not far away, and every stop costs
    an exposure. Walking outward from where you are finds the common case in two
    or three frames instead of nine.

    The span defaults to the whole usable travel, because the case this exists
    for is "I have no idea where focus is" — a narrow default search would fail
    exactly when it is needed.
    """
    lo, hi = 0, max(0, int(max_position))
    if hi <= 0:
        return [int(current)]
    if span is not None and span > 0:
        lo = max(lo, int(current) - int(span) // 2)
        hi = min(hi, int(current) + int(span) // 2)
    stops = max(2, int(stops))
    step = (hi - lo) / (stops - 1)
    grid = [int(round(lo + i * step)) for i in range(stops)]
    # De-dupe (a short span can collapse stops onto one another) and order by
    # distance from where the focuser already is.
    seen: set[int] = set()
    ordered: list[int] = []
    for p in sorted(grid, key=lambda p: abs(p - int(current))):
        if p not in seen:
            seen.add(p)
            ordered.append(p)
    return ordered


async def run_coarse_focus(camera: Camera, focuser: Focuser, *,
                           exposure_s: float = 4.0, gain: int = 200,
                           binning: int = 2, span: int | None = None,
                           stops: int = DEFAULT_STOPS,
                           enough: int = ENOUGH_STARS,
                           timeout_s: float = DEFAULT_TIMEOUT_S,
                           expose_guard=None,
                           hfr_method: str | None = None) -> AutofocusResult:
    """Walk the travel until a position has enough stars for autofocus.

    Returns as soon as one does — this hands off, it does not optimise. If none
    does, it goes to the best position seen and says so, which is still progress
    a user can act on.
    """
    from . import native as _n
    if not _n.NATIVE_AVAILABLE or _n._native is None:
        raise DeviceError("native engine not installed")

    start_pos = await focuser.get_position()
    positions = plan_positions(start_pos, focuser.max_position, span, stops)

    async def _expose():
        guard = expose_guard("coarse-focus") if expose_guard is not None \
            else contextlib.nullcontext()
        async with guard:
            return await camera.expose(exposure_s, gain, 30, binning=binning)

    params = {"profile": hfr_method} if hfr_method else None
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(1.0, float(timeout_s))

    bus.publish("focus", state="running", points=[], best=None,
                message="coarse focus: looking for a position with stars")
    bus.log("info",
            f"coarse focus: {len(positions)} stops from {positions[0]} to "
            f"{max(positions)}, {exposure_s:g}s at gain {gain} bin {binning}, "
            f"stopping at {enough} stars", "focus")

    seen: list[tuple[int, int]] = []          # (position, star_count)
    best_pos, best_n = start_pos, -1
    try:
        for pos in positions:
            if loop.time() > deadline:
                bus.log("warning", "coarse focus: out of time", "focus")
                break
            await focuser.move_to(pos)
            frame = await _expose()
            _stars, stats = await asyncio.to_thread(
                _n._native.detect_and_measure, frame.data, params)
            n = int(stats.get("star_count") or 0)
            seen.append((pos, n))
            if n > best_n:
                best_pos, best_n = pos, n
            # Say what happened at every stop. A silent search is
            # indistinguishable from a hung one, and this one moves a focuser
            # for minutes.
            bus.log("info", f"coarse focus: {n} stars at {pos}", "focus")
            bus.publish("focus", state="running", best=None,
                        points=[{"position": p, "hfr": 0.0, "sigma": 0.0}
                                for p, _ in seen],
                        message=f"coarse focus: {n} stars at {pos}")
            if n >= enough:
                msg = (f"found {n} stars at {pos} — enough to autofocus from. "
                       "Run autofocus now.")
                bus.publish("focus", state="idle", points=[], best=pos,
                            message=msg)
                bus.log("info", f"coarse focus: {msg}", "focus")
                return AutofocusResult(True, pos, None, [], msg)

        # Nothing cleared the bar. Park on the richest position seen — it is the
        # best starting point for a manual attempt — and say what was found, so
        # the next decision is informed rather than a shrug.
        await focuser.move_to(best_pos)
        tally = ", ".join(f"{p}:{n}" for p, n in seen) or "nothing measured"
        msg = (f"no position had {enough} stars. Best was {best_n} at "
               f"{best_pos}; moved there. Counts by position — {tally}. "
               "Try a longer exposure, or check the sky and the cover.")
        bus.publish("focus", state="failed", points=[], best=None, message=msg)
        bus.log("warning", f"coarse focus: {msg}", "focus")
        return AutofocusResult(False, best_pos, None, [], msg)
    except asyncio.CancelledError:
        # Never leave the focuser stranded mid-search: the rig would keep
        # shooting from wherever the sweep happened to stop.
        with contextlib.suppress(Exception):
            await focuser.move_to(start_pos)
        bus.publish("focus", state="idle", points=[], best=None,
                    message="coarse focus cancelled")
        raise
    except Exception as e:                      # noqa: BLE001
        with contextlib.suppress(Exception):
            await focuser.move_to(start_pos)
        bus.publish("focus", state="failed", points=[], best=None,
                    message=str(e))
        raise
