# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""PRO-4 — the correctness-critical roof/dome close-ordering state machine.

This module holds ONE pure, engine-free async function, ``close_observatory``,
whose invariant physically protects hardware: a roll-off roof must NEVER travel
through an unparked mount. The ordering is:

    park the mount clear (the CALLER's job) → confirm parked → close the roof →
    confirm CLOSED

and if the mount cannot be confirmed parked, the function REFUSES to close (a
wet scope beats a crushed one) and returns ``False`` so the caller can page.
That holds whatever state the mount's link is in (owner ruling D-B, #893): a
dropped link is reopened once to read the park state, and a mount that still
cannot be read keeps the roof open.

The other half of the envelope lives here too, for the same reason (one module
owns the roof's ordering): ``open_for_the_night`` opens the roof and the dust
cover and confirms both before the first light, and ``bind_dome`` puts a dome's
azimuth under the mount (#601, #602). Both take the devices and return a reason
that names the device that did not confirm, or None.

Import-light BY DESIGN: this imports only ``asyncio``, ``time`` and the device
roles in ``devices.base``, and NO ``engine``/``hub``, so it stays cycle-free and unit-testable against sim
doubles directly. Mount MOTION is deliberately NOT performed here: the caller
(``engine._wind_down``) parks the mount under the hub motion fence+lock FIRST;
this only VERIFIES parked before moving the roof.
"""
from __future__ import annotations

import asyncio
import time

from ..devices.base import (
    SHUTTER_POLL_S, CoverState, DomePolicy, DomeShutterState, is_present)

#: A roll-off roof is minutes-slow to travel; the close command gets a generous
#: bound so a real motor run isn't cut short, while a query is snappy.
DOME_CLOSE_TIMEOUT_S: float = 180.0
DOME_QUERY_TIMEOUT_S: float = 30.0
#: How long a close waits for a mount whose link is down to answer a reopen
#: (#893). One attempt, bounded: the roof is what waits on it.
MOUNT_REOPEN_TIMEOUT_S: float = 30.0
#: A motorized dust cover is seconds to tens of seconds of travel. Read through
#: the module global on every wait, so a test can shorten it.
COVER_OPEN_TIMEOUT_S: float = 60.0


async def _park_not_confirmed(telescope, log, query_timeout_s: float,
                              reopen_timeout_s: float) -> str | None:
    """None when the mount is confirmed parked, else why it is not (words for
    the page).

    A mount in doubt (`is_present`, #989: it lost its link a moment ago and has
    not been heard from since) is asked as it stands, and a failed read is a
    refusal. A mount that is not there to ask (its link is down) has its link
    reopened ONCE, bounded; the mount is then asked only if the reopen brought
    it back, and not if the driver says it does not know where it points: an
    AM5 that lost power reads its home pole on reconnect, and "parked" read off
    that is no proof about the tube."""
    reopened = False
    if not is_present(telescope):
        log("warning", "roof close: the mount's link is down, reopening it "
                       "once to read the park state", "safety")
        try:
            await asyncio.wait_for(telescope.connect(), reopen_timeout_s)
        except Exception:      # noqa: BLE001 - includes the timeout
            return "mount link down, park not confirmed"
        if not is_present(telescope):
            return "mount link down, park not confirmed"
        reopened = True
    if reopened and getattr(telescope, "position_known", True) is False:
        return ("mount link was down and its position is unknown, park "
                "not confirmed")
    try:
        parked = await asyncio.wait_for(telescope.is_parked(), query_timeout_s)
    except Exception:      # noqa: BLE001 - a failed read is not a confirmation
        parked = False
    return None if parked else "mount not parked (would collide)"


async def close_observatory(
    dome,
    telescope,
    *,
    log,
    close_timeout_s: float = DOME_CLOSE_TIMEOUT_S,
    query_timeout_s: float = DOME_QUERY_TIMEOUT_S,
    reopen_timeout_s: float = MOUNT_REOPEN_TIMEOUT_S,
) -> bool:
    """Close a roll-off roof/dome WITHOUT stranding the mount in its path.

    Returns ``True`` iff the shutter is confirmed CLOSED. Returns ``False``
    (never raises) when it REFUSES to close — the mount could not be confirmed
    parked, with its link up or down - or the close failed/timed out, so the
    caller can page. Mount MOTION is NOT performed here: the caller
    (``_wind_down``) parks the mount, fenced+locked, FIRST; this only VERIFIES
    parked before moving the roof. The one thing it does to the mount is to
    reopen a dropped link, once, to be able to read it (#893).

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
    #    mount's volume AND the rig has a telescope, the mount MUST be
    #    confirmed parked, whatever state its link is in (#893, owner ruling
    #    D-B). Any doubt (query failure/timeout) is treated as NOT parked
    #    (fail-safe) -> REFUSE. We never issue the park ourselves.
    #
    #    A mount whose link is down is not skipped: the check used to ride the
    #    link ("connected"), and the hazard does not. The link is reopened
    #    once and the park read; if that cannot be done the roof stays open and
    #    the refusal pages. A collision costs more than rain.
    if (getattr(dome, "requires_park_before_close", True)
            and telescope is not None):
        gap = await _park_not_confirmed(
            telescope, log, query_timeout_s, reopen_timeout_s)
        if gap:
            log("error", f"roof close REFUSED: {gap} - roof left OPEN",
                "safety")
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


# ----------------------------------------------------------------------------
# The other half of the envelope (#601, #602, #192): open before the first
# light, and bind the dome to the mount. Same shape as the close: pure, takes
# the devices, bounds every call, and never raises for a device that fails. A
# refusal is a sentence that names the device, which the caller gives to the
# operator as the reason the night did not start.
# ----------------------------------------------------------------------------

def _named(device, detail: str) -> str:
    """``detail`` with the device's name in it, once."""
    return detail if device.name in detail else f"{device.name}: {detail}"


async def open_for_the_night(
    dome,
    cover,
    *,
    log,
    policy: DomePolicy | None = None,
    before_roof=None,
    cover_timeout_s: float | None = None,
    query_timeout_s: float = DOME_QUERY_TIMEOUT_S,
) -> str | None:
    """Open the roof and the dust cover, and confirm both OPEN, before any
    light frame is planned: the nights that nothing opened them for are the
    ones that solved, and shot, through a shut cover (#192, #601).

    Returns None when the observatory is open, or when the rig has nothing to
    open (no roof, no cover, a panel with no cover: such a rig is never asked
    anything). Otherwise returns the reason, a sentence that names the device
    that did not confirm; the roof is opened first and the cover is left alone
    once the roof has refused. Never raises for a device that fails.

    A device already open is not commanded again: some drivers send an open
    roof through another full travel on a repeat command, and the second call
    of a night (a resume after a run) must cost nothing.

    ``before_roof`` is awaited only when the roof is about to travel, and what
    it raises is the caller's to handle, not a refusal: the engine passes its
    safety gate, so a roof is not opened into rain. Opening a cover is no
    weather risk and is not gated by it."""
    policy = policy or DomePolicy()
    if dome is not None and is_present(dome):
        try:
            state = await asyncio.wait_for(dome.shutter_state(),
                                           query_timeout_s)
        except Exception:      # noqa: BLE001 - unreadable: command, then confirm
            state = None
        if state is not DomeShutterState.OPEN:
            if before_roof is not None:
                await before_roof()
            try:
                await asyncio.wait_for(
                    policy.open_and_confirm(dome),
                    policy.shutter_timeout_s + query_timeout_s)
            except Exception as e:      # noqa: BLE001 - includes the timeout
                reason = ("the roof did not open: "
                          + _named(dome, str(e) or type(e).__name__))
                log("error", reason, "safety")
                return reason
            log("info", "roof open", "sequence")
    if (cover is not None and getattr(cover, "has_cover", False)
            and is_present(cover)):
        return await _open_cover(
            cover, cover_timeout_s if cover_timeout_s is not None
            else COVER_OPEN_TIMEOUT_S, query_timeout_s, log)
    return None


async def _open_cover(cover, timeout_s: float, query_timeout_s: float,
                      log) -> str | None:
    """Open the dust cover and wait for it to read OPEN. None, or the reason."""
    try:
        state = await asyncio.wait_for(cover.get_cover_state(), query_timeout_s)
        if state is CoverState.OPEN:
            return None
        await asyncio.wait_for(cover.open_cover(), query_timeout_s)
        deadline = time.monotonic() + timeout_s
        while True:
            state = await asyncio.wait_for(cover.get_cover_state(),
                                           query_timeout_s)
            if state is CoverState.OPEN:
                break
            if state is CoverState.ERROR:
                raise RuntimeError("the cover reports a fault")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError(
                    f"the cover did not confirm open within {timeout_s:g}s "
                    f"(last state: {getattr(state, 'value', state)})")
            await asyncio.sleep(min(SHUTTER_POLL_S, remaining))
    except Exception as e:      # noqa: BLE001 - includes the timeout
        reason = ("the dust cover did not open: "
                  + _named(cover, str(e) or type(e).__name__))
        log("error", reason, "safety")
        return reason
    log("info", "dust cover open", "sequence")
    return None


async def bind_dome(
    dome,
    *,
    log,
    policy: DomePolicy | None = None,
    query_timeout_s: float = DOME_QUERY_TIMEOUT_S,
) -> str | None:
    """Put a dome's azimuth under the mount (#602), through
    ``DomePolicy.apply_binding``, and say what that did.

    None when it did, or when there is nothing to bind: a rig with no dome is
    not asked, and a roll-off roof has no azimuth. With a ``policy`` (the
    plan carried a DOME CONTROL node) that roof's line says the node's
    "Slave to mount" has no effect; with none (no node, so nobody chose it)
    it is silent, and a dome that can bind is still bound by the defaults. A dome that
    claims it can bind and then refuses is the reason this returns, naming it:
    an OTA that spends the night looking at the dome wall while every readout
    says bound is the failure a refusal is for. Never raises for a device that
    fails."""
    if dome is None or not is_present(dome):
        return None
    if policy is None and not getattr(dome, "can_bind", False):
        return None
    try:
        line = await asyncio.wait_for(
            (policy or DomePolicy()).apply_binding(dome), query_timeout_s)
    except Exception as e:      # noqa: BLE001 - includes the timeout
        reason = ("the dome would not bind to the mount: "
                  + _named(dome, str(e) or type(e).__name__))
        log("error", reason, "safety")
        return reason
    log("info", line, "sequence")
    return None
