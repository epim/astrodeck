# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The tracking-truth probe's caller side (#1071).

``Telescope.probe_ra_drift`` is the measurement: the mount's own reported RA
over an interval with nothing moving it, returned as a rate. This module is
the three places that ask for it and the one line each writes.

WHY A PROBE AT ALL. ``:GAT#`` says the tracking flag is on. On pier east after
a flip the guider behaves as if the RA drive were slow or stopped while that
flag still reads on, so the flag is a claim and the reported RA's rate is the
measurement.

NEVER A COORDINATE. The line carries rates, flags, the pier side and the
minutes since the last flip. The RA readings stay in the driver (at home or
park they are the local sidereal time, #140). A line that carries the minutes
since a flip is flagged ``site_derived``: the flip happens at the target's
computed crossing, so that figure and the line's own timestamp solve for the
longitude, the same reason the engine's flip-complete line is flagged.

FAILURE IS LOGGED AND IGNORED. A probe that raised into the guide loop or the
frame loop would turn a diagnostic into an outage. A cancel is not a failure
and passes through.
"""
from __future__ import annotations

import asyncio

from .events import bus

#: Seconds a probe watches the RA, at every trigger.
PROBE_SECONDS = 10.0


def probe_line(trigger: str, r: dict, since_flip_min: float | None) -> str:
    """The one log line for a probe result. Numbers: two rates, the
    resolution, the elapsed seconds and the minutes since the last flip."""
    flag = ("on" if r.get("tracking_flag_before") else "off")
    after = ("on" if r.get("tracking_flag_after") else "off")
    flags = (f"tracking flag {flag}" if flag == after
             else f"tracking flag {flag} then {after}")
    notes = []
    if r.get("disturbed"):
        notes.append("the mount was slewing, so the rate is not valid")
    if r.get("cut_short"):
        notes.append("cut short for a park")
    when = (f", {since_flip_min:.0f} min since the last flip"
            if since_flip_min is not None else "")
    return (f"tracking probe ({trigger}): RA drift "
            f"{r['ra_drift_arcsec_s']:+.2f} arcsec/s = "
            f"{r['sidereal_fraction']:+.2f} sidereal over "
            f"{r['seconds']:.1f} s (resolution "
            f"{r['resolution_arcsec_s']:.2f} arcsec/s), {flags}, pier side "
            f"{r['pier_side']}{when}"
            + (f" ({'; '.join(notes)})" if notes else ""))


async def run_probe(tel, trigger: str, *, seconds: float = PROBE_SECONDS,
                    since_flip_min: float | None = None) -> dict | None:
    """Probe ``tel`` and log one line. Returns the result, or None when the
    mount cannot probe or the probe failed. Never raises (a cancel excepted).
    """
    if tel is None or not getattr(tel, "can_probe_ra_drift", False):
        return None
    try:
        r = await tel.probe_ra_drift(seconds)
    except asyncio.CancelledError:
        raise
    except Exception as e:                      # noqa: BLE001 - never stops a run
        try:
            bus.log("warning",
                    f"tracking probe ({trigger}) failed: {type(e).__name__}"
                    f": {str(e)[:120]}", "mount")
        except Exception:                       # noqa: BLE001
            pass
        return None
    if not isinstance(r, dict):
        return None
    try:
        flag = {"site_derived": True} if since_flip_min is not None else {}
        bus.log("info", probe_line(trigger, r, since_flip_min), "mount",
                **flag)
    except Exception as e:                      # noqa: BLE001
        bus.log("warning", f"tracking probe ({trigger}) result could not be "
                           f"written: {type(e).__name__}", "mount")
    return r
