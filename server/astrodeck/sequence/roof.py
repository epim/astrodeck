"""PRO-4 — the correctness-critical roof/dome close-ordering state machine.

This module holds ONE pure, engine-free async function, ``close_observatory``,
whose invariant physically protects hardware: a roll-off roof must NEVER travel
through an unparked mount. The ordering is:

    park the mount clear (the CALLER's job) → confirm parked → close the roof →
    confirm CLOSED

and if the mount cannot be confirmed parked, the function REFUSES to close (a
wet scope beats a crushed one) and returns ``False`` so the caller can page.

Import-light BY DESIGN: this imports only ``asyncio`` + ``DomeShutterState`` —
NO ``engine``/``hub`` — so it stays cycle-free and unit-testable against sim
doubles directly. Mount MOTION is deliberately NOT performed here: the caller
(``engine._wind_down``) parks the mount under the hub motion fence+lock FIRST;
this only VERIFIES parked before moving the roof.
"""
from __future__ import annotations

import asyncio

from ..devices.base import DomeShutterState

#: A roll-off roof is minutes-slow to travel; the close command gets a generous
#: bound so a real motor run isn't cut short, while a query is snappy.
DOME_CLOSE_TIMEOUT_S: float = 180.0
DOME_QUERY_TIMEOUT_S: float = 30.0


async def close_observatory(
    dome,
    telescope,
    *,
    log,
    close_timeout_s: float = DOME_CLOSE_TIMEOUT_S,
    query_timeout_s: float = DOME_QUERY_TIMEOUT_S,
) -> bool:
    """Close a roll-off roof/dome WITHOUT stranding the mount in its path.

    Returns ``True`` iff the shutter is confirmed CLOSED. Returns ``False``
    (never raises) when it REFUSES to close — the mount could not be confirmed
    parked — or the close failed/timed out, so the caller can page. Mount MOTION
    is NOT performed here: the caller (``_wind_down``) parks the mount,
    fenced+locked, FIRST; this only VERIFIES parked before moving the roof.

    ``log`` is a ``callable(level: str, msg: str, source: str) -> None`` (the
    event bus's ``log``). Every device call is wrapped in a local
    ``asyncio.wait_for`` and its exceptions are swallowed (log + return
    ``False``) — we are tearing down and must never re-raise, matching
    ``_wind_down``'s "log + continue" philosophy.
    """
    # 1. Idempotent: already CLOSED → nothing to do (no park query needed).
    try:
        if await asyncio.wait_for(dome.is_closed(), query_timeout_s):
            return True
    except Exception:
        pass  # fall through and try to close

    # 2. The never-crush-the-mount guard. When the roof travels through the
    #    mount's volume AND a connected telescope exists, the mount MUST be
    #    confirmed parked. Any doubt (query failure/timeout) is treated as
    #    NOT parked (fail-safe) → REFUSE. We never issue the park ourselves.
    if (getattr(dome, "requires_park_before_close", True)
            and telescope is not None
            and getattr(telescope, "connected", False)):
        try:
            parked = await asyncio.wait_for(telescope.is_parked(), query_timeout_s)
        except Exception:
            parked = False
        if not parked:
            log("error", "roof close REFUSED: mount not parked (would collide) "
                         "— roof left OPEN", "safety")
            return False

    # 3. Close, then CONFIRM CLOSED. A failure/timeout anywhere → log + False.
    try:
        await asyncio.wait_for(dome.close_shutter(), close_timeout_s)
    except Exception as e:
        log("error", f"roof close failed: {e}", "safety")
        return False
    try:
        st = await asyncio.wait_for(dome.shutter_state(), query_timeout_s)
    except Exception:
        st = None
    if st is not DomeShutterState.CLOSED:
        log("error", "roof close did not confirm CLOSED — gear may be exposed",
            "safety")
        return False
    log("info", "roof closed", "safety")
    return True
