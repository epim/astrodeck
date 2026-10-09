# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Read a sync back and say whether the mount took it (#862, the #850 class).

A transport's success signal is a hint, not a measurement. On 2026-10-07 the
AM5 answered its usual acceptance to three centring syncs of 2.2 to 2.8 deg,
took none of them, and the run imaged the wrong field for hours (#850). The
AM5 driver now reads its position back after every sync. The Alpaca (which
also serves ASCOM-local / COM-host mounts), NINA and ASIAIR drivers trusted an
HTTP 200, an ``ErrorNumber`` of 0, a ``Success: true`` or a libasi return in
the same way, so a sync the mount silently declined read as success.

This module is the ONE copy of the read-back those three drivers share:

* ``verify_sync`` reads the position up to ``1 + SYNC_READBACK_RETRIES``
  times, ``SYNC_READBACK_RETRY_S`` apart, each read bounded by
  ``SYNC_READ_TIMEOUT_S`` (or the caller's own bound), and returns as soon as
  one lands within ``SYNC_VERIFY_DEG`` of the synced coordinates. Otherwise
  the LAST read decides: it answered and still disagrees, ``SyncRefused``
  with the residual; it failed, ``SyncUnverified``. A read that does not
  answer within its bound ends the read-back at once (``SyncUnverified``):
  the retries exist to wait out a report that moves late, which assumes the
  reads answer.
* ``refused_residual_deg`` is the one best-effort read for a refusal the
  transport already reported, so the refusal can say how far the mount's
  opinion is from the sky (the resume ladder weighs ``residual_deg``).
* ``jnow_alternates`` lets a driver whose reporting frame is not known (NINA,
  the ASIAIR) accept a read-back in either J2000 or JNOW.

Every constant is a module global read at CALL time, never a default
argument, so a test can monkeypatch it.

NO COORDINATES IN ANY TEXT. At the home position the read-back is the pole,
and its RA follows local sidereal time, so a coordinate in a message is a site
oracle (#140, #166). Messages carry fixed words, the driver's name and the
separation; ``reason`` carries fixed words only (the #618 contract). No
transport error's own words are interpolated, and every raise here is made
outside an ``except`` block, so neither ``__cause__`` nor ``__context__``
carries them.
"""
from __future__ import annotations

import asyncio
import math
from typing import Awaitable, Callable

from ..catalog import coords
from .base import SyncRefused, SyncUnverified

#: The read-back agrees with the sync within this separation (deg). The worst
#: honest disagreement, summed linearly: LX200-class low-precision rounding
#: beneath a driver, one full unit per axis, sqrt(0.025^2 + 0.0167^2) = 0.030;
#: tracking off for the 5 s window, 5 x 0.004178 = 0.0209; one concurrent 1 s
#: guide pulse at 1x sidereal, 0.0042. Total 0.0551; 0.1 is a 1.8x margin.
#: Wider than the AM5's 0.05 because nothing about these drivers' underlying
#: mounts is known. HARDWARE-PENDING H1/H2/H3 measure it.
SYNC_VERIFY_DEG = 0.1
#: Six reads over 5 s: 2x NINA's inferred ~2 s mount-info refresh plus the
#: read cadence. Costs nothing on a sync that reads back promptly (the first
#: matching read ends it).
SYNC_READBACK_RETRIES = 5
SYNC_READBACK_RETRY_S = 1.0
#: Each read's own bound (s): 2x the hub's 2.0 s status period. Without it a
#: read is bounded only by the transport, up to 60 s for an Alpaca
#: get_position (two GETs at a 30 s client timeout) and 120 s for NINA.
SYNC_READ_TIMEOUT_S = 4.0
#: Bound on the one J2000 -> JNOW transform per sync (s): a cold call measured
#: 0.44 to 0.48 s on the dev box, 10x for the slowest supported host = 4.8 s,
#: plus astropy's 10 s remote_timeout for one IERS fetch = 14.8 s; 20 s is a
#: 1.35x margin. A timeout drops the alternate (only the target counts).
SYNC_ALTERNATE_TIMEOUT_S = 20.0

#: The ``code`` a raise carries; quotable under ``base.quotable_sync_reply``.
#: "OK": the transport reported success (the read-back disproved it).
SYNC_REPLY_OK = "OK"
#: The driver, NINA or the ASIAIR answered with a positively known refusal.
SYNC_REPLY_ERROR = "error"
#: The ASIAIR is busy with another task.
SYNC_REPLY_BUSY = "busy"

# Fixed words for a person (no digits, no codes, no raw replies; #618). Each
# carries its own action, and none advises a slew, a goto or homing (the
# safety rule: the mount's position may not be known when one is shown).
SYNC_NOT_MOVED_REASON = ("the mount answered OK but its position did not "
                         "move; check the driver's sync settings")
SYNC_REFUSED_BY_DRIVER_REASON = ("the mount's driver refused the sync; its "
                                 "own log says why")
SYNC_PARKED_REASON = "the mount is parked; unpark it first"
SYNC_NOT_SUPPORTED_REASON = ("this mount does not support sync, so centring "
                             "cannot correct its pointing")
# Same words as zwo_am5's three SYNC_UNVERIFIED_* constants, character for
# character, so one cause reads as one reason across drivers (D-03). Kept here
# rather than imported: a vendor driver is not a dependency of this module.
SYNC_UNVERIFIED_READBACK = ("the mount did not answer the position read after "
                            "the sync, so whether it took is unknown")
SYNC_UNVERIFIED_LINK_DURING = ("the link failed during the sync, so whether "
                               "the mount took it is unknown")
SYNC_UNVERIFIED_LINK_BEFORE = "the link failed before the sync was sent"
# No AM5 counterpart: the sync call came back with an answer that is not a
# known refusal (HTTP 5xx, a body that is not JSON, an unrecognised libasi
# error). Not "the link failed": something answered.
SYNC_UNVERIFIED_UNCLEAR = ("the sync failed without a clear refusal, so "
                           "whether the mount took it is unknown")

ReadPosition = Callable[[], Awaitable[tuple[float, float]]]
Alternates = Callable[[float, float], Awaitable[list[tuple[float, float]]]]


def refused_message(name: str, reason: str, residual: float | None) -> str:
    """``sync refused: <reason> (<name>; 2.50 deg off)``. The reason, which
    carries the action, comes FIRST and the driver-supplied name last, so the
    action's place does not depend on the name's length (NINA and Alpaca
    names come from the driver and have no bound). The route's
    ``sync not taken: {e}`` line (``api/app.py``) puts 16 characters in front
    of it, and the UI cuts a line at 137."""
    where = "" if residual is None else f"; {residual:.2f} deg off"
    return f"sync refused: {reason} ({name}{where})"


def unverified_message(name: str, reason: str,
                       detail: str | None = None) -> str:
    """``sync not confirmed: <reason> (<name>; the last read got <detail>)``,
    in the same order and for the same reason as ``refused_message``.
    ``detail`` is fixed words, never an exception's text."""
    tail = "" if detail is None else f"; the last read got {detail}"
    return f"sync not confirmed: {reason} ({name}{tail})"


async def jnow_alternates(ra_hours: float, dec_deg: float
                          ) -> list[tuple[float, float]]:
    """``[JNOW(target)]``, or ``[]`` when the transform fails or runs past
    ``SYNC_ALTERNATE_TIMEOUT_S``. Never raises (except CancelledError).

    NINA transforms a sync to the mount's epoch (usually JNOW) and reports in
    it, and the ASIAIR's frame is unknown, so their read-backs are accepted in
    either frame (#862). The transform is the hub's ``precess_j2000_to_jnow``,
    the one copy, looked up on the hub module at CALL time: imported lazily
    because the hub imports this package, and looked up late so a test stub
    reaches it. Run in a thread: astropy is slow on its first call, and an
    abandoned precession thread is pure computation."""
    from .. import hub as hub_module
    try:
        alt = await asyncio.wait_for(
            asyncio.to_thread(hub_module.precess_j2000_to_jnow,
                              ra_hours, dec_deg),
            SYNC_ALTERNATE_TIMEOUT_S)
        return [(float(alt[0]), float(alt[1]))]
    except Exception:  # noqa: BLE001 - the timeout too; only T counts then
        return []


def _parse(pos) -> tuple[float, float] | str:
    """The read as two finite floats, or the fixed words for why not."""
    try:
        ra, dec = float(pos[0]), float(pos[1])
    except (TypeError, ValueError, IndexError):
        return "an unreadable answer"
    if not (math.isfinite(ra) and math.isfinite(dec)):
        return "a position that was not finite"
    return ra, dec


async def _residual(ra_hours: float, dec_deg: float, ra: float, dec: float,
                    alternates: Alternates | None,
                    alt_box: list) -> float:
    """Separation of the read from the target, or from the closest
    alternate when the target misses and alternates are given. ``alt_box``
    holds the alternates once computed (at most once per sync)."""
    residual = coords.angular_sep_deg(ra_hours, dec_deg, ra, dec)
    if residual > SYNC_VERIFY_DEG and alternates is not None:
        if not alt_box:
            alt_box.append(await alternates(ra_hours, dec_deg))
        residual = min([residual] + [
            coords.angular_sep_deg(a_ra, a_dec, ra, dec)
            for a_ra, a_dec in alt_box[0]])
    return residual


async def verify_sync(name: str, read_position: ReadPosition,
                      ra_hours: float, dec_deg: float, *,
                      alternates: Alternates | None = None,
                      not_moved_reason: str = SYNC_NOT_MOVED_REASON,
                      read_timeout_s: float | None = None) -> float:
    """Return the residual (deg) of the read that proved the sync, or raise
    ``SyncRefused`` / ``SyncUnverified``. Raises nothing else;
    CancelledError propagates. ``read_timeout_s`` None means
    ``SYNC_READ_TIMEOUT_S``, read at call time (the ASIAIR passes its own).

    A read that FAILS quickly (an HTTP 500, a missing field) counts as one
    failed read and the loop goes on; a read that does not answer within its
    bound ends the read-back."""
    bound = SYNC_READ_TIMEOUT_S if read_timeout_s is None else read_timeout_s
    alt_box: list = []
    residual: float | None = None
    # Why the LAST read failed, in fixed words, or None when it answered.
    last_failed: str | None = None
    for attempt in range(SYNC_READBACK_RETRIES + 1):
        if attempt:
            await asyncio.sleep(SYNC_READBACK_RETRY_S)
        try:
            pos = await asyncio.wait_for(read_position(), bound)
        except asyncio.TimeoutError:
            # Caught BEFORE Exception: on 3.11+ it is the builtin
            # TimeoutError, an OSError. A link that gave no answer within 2x
            # a status period will not answer the next read either.
            last_failed = "no answer in time"
            break
        except Exception:  # noqa: BLE001 - its text is never kept
            last_failed = "no usable answer"
            continue
        parsed = _parse(pos)
        if isinstance(parsed, str):
            last_failed = parsed
            continue
        last_failed = None
        residual = await _residual(ra_hours, dec_deg, parsed[0], parsed[1],
                                   alternates, alt_box)
        if residual <= SYNC_VERIFY_DEG:
            return residual
    if last_failed is not None:
        raise SyncUnverified(
            unverified_message(name, SYNC_UNVERIFIED_READBACK, last_failed),
            code=SYNC_REPLY_OK, reason=SYNC_UNVERIFIED_READBACK)
    raise SyncRefused(
        refused_message(name, not_moved_reason, residual),
        code=SYNC_REPLY_OK, reason=not_moved_reason, residual_deg=residual)


async def refused_residual_deg(read_position: ReadPosition, ra_hours: float,
                               dec_deg: float, *,
                               alternates: Alternates | None = None,
                               read_timeout_s: float | None = None
                               ) -> float | None:
    """One best-effort read for a refusal the transport already reported:
    the residual, or None when the read fails or does not answer within the
    read bound. Never raises (except CancelledError). No retries: the mount
    already said no."""
    bound = SYNC_READ_TIMEOUT_S if read_timeout_s is None else read_timeout_s
    try:
        pos = await asyncio.wait_for(read_position(), bound)
    except Exception:  # noqa: BLE001 - the timeout too
        return None
    parsed = _parse(pos)
    if isinstance(parsed, str):
        return None
    try:
        return await _residual(ra_hours, dec_deg, parsed[0], parsed[1],
                               alternates, [])
    except Exception:  # noqa: BLE001 - best effort
        return None
