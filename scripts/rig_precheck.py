# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Is the rig idle enough to restart the server under it?

Run ON the rig with the install's venv python. Mints a session through the
loopback break-glass path (the admin token never leaves the box, the cookie
stays in memory) and refuses when anything is in flight: a sequence, a polar
alignment, a slewing mount, an exposure loop, a busy hub lane. The 0.3.23
deploy checked only the sequence state and read a mount field that does not
exist, and restarted the server sixteen seconds into a polar-alignment point.

Exit codes: 0 idle, 3 busy (the reasons are printed), 2 could not tell.
``--report`` prints the state and always exits 0.
"""
from __future__ import annotations

import json
import math
import os
import sys
import urllib.error
import urllib.request

ROOT = os.environ.get("ASTRODECK_INSTALL_ROOT", r"C:\Users\James\AstroDeck")
BASE = "http://127.0.0.1:8800"
#: How often the server pings the dead-man URL. A COPY of
#: ``astrodeck.alerting.DEADMAN_INTERVAL_S``: this script runs under the rig's
#: venv with nothing of the server on its path, so it cannot import it. A test
#: compares the two, so the copy cannot drift without a red.
DEADMAN_INTERVAL_S = 60.0


def _site_line() -> str:
    """Whether a real observing site is saved, WITHOUT printing any of it.

    Issue #128, and it is a fix to a habit rather than to code. Nothing here
    reported the site, so the way to check it was to read
    `config/astrodeck.json` over ssh - and that file carries the label and the
    coordinates in clear. Doing exactly that on 2026-09-21 put the site's label
    (one of the three privacy needles) into an agent transcript, from a command
    whose actual question was "is is_default false".

    So the question gets an answer of its own. Nothing below can print a
    latitude, a longitude or a name: the only facts that leave here are a
    boolean and an elevation, and the elevation is not a needle.
    """
    try:
        with open(os.path.join(ROOT, "config", "astrodeck.json"),
                  encoding="utf-8") as fh:
            site = (json.load(fh) or {}).get("site") or {}
    except (OSError, ValueError) as exc:
        return f"UNREADABLE ({type(exc).__name__}) - treat as not set"
    if site.get("is_default", True):
        return ("NOT SET - every altitude, meridian flip, dark window and "
                "sun-avoidance decision is computed for latitude 0, longitude 0")
    lat, lon = site.get("latitude"), site.get("longitude")
    if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
        return "saved but its coordinates are not numbers - treat as not set"
    try:
        elevation = f"{float(site.get('elevation_m') or 0.0):.0f} m"
    except (TypeError, ValueError):
        elevation = "unknown elevation"
    return f"configured ({elevation})"


def _deadman_state(health: object) -> str:
    """Whether the configured dead-man has actually been ANSWERED. (#125)

    ``health`` is the ``deadman`` block of ``/api/alerts/health``. Its
    ``healthy`` is "no failure has been warned about", which is also true
    before the first request leaves, so a freshly pasted URL and a typo both
    read healthy; ``last_ok_age_s`` is the seconds since the monitor last
    ACCEPTED a ping, None if it never has. Only that makes the line a fact.

    Total on purpose: a block that is absent, lacks the key, or carries a
    string, a bool, a negative or a non-finite number is "UNKNOWN", never
    "answering". The only things that leave are the words below and one
    integer age; the block's other keys are never read, so nothing in it, a
    url included, can reach the output.
    """
    if not isinstance(health, dict):
        return ("dead-man configured, ping state UNKNOWN (the server's health "
                "was not read)")
    if "last_ok_age_s" not in health:
        return ("dead-man configured, ping state UNKNOWN (this server does not "
                "report accepted pings)")
    age = health["last_ok_age_s"]
    if age is None:
        return "dead-man configured but NO ping has been accepted since start"
    if (isinstance(age, bool) or not isinstance(age, (int, float))
            or not math.isfinite(age) or age < 0):
        return "dead-man configured, ping state UNKNOWN (the server's age was not a number)"
    # Three missed intervals is where an external monitor's grace would have run
    # out and paged; anything fresher is a monitor that is hearing from us.
    if age < 3 * DEADMAN_INTERVAL_S:
        return ("dead-man configured and answering (last ping accepted "
                f"{age:.0f} s ago)")
    return ("dead-man configured but NO ping has been accepted in the last "
            f"{age:.0f} s (one is sent every {DEADMAN_INTERVAL_S:.0f} s)")


def _watch_line(deadman_health: object = None) -> str:
    """Is anything outside this PC watching it? Counts and booleans only.

    Issue #125: the rig went offline and nothing said so. The product already
    has the watcher - `alerting.py`'s dead-man ping, sent every minute from a
    wall-clock timer, whose ABSENCE is what pages - but on 2026-09-22 this rig
    had no dead-man URL and no alert channel, so it could have been off for a
    week and nothing would have noticed. This line puts that state in front of
    every deploy instead of leaving it to be found by the next outage.

    "Configured" is not "watched": a URL nothing has answered is a rig no
    external service has heard from. ``deadman_health`` is the ``deadman``
    block of ``/api/alerts/health`` (None when it could not be read), and what
    it adds is whether the monitor has ACCEPTED a ping (`_deadman_state`).

    The dead-man URL carries a per-ping secret in its path, and a sink carries
    a token or a webhook URL, so nothing here can print either: the facts that
    leave are a boolean, an age and two counts.
    """
    try:
        with open(os.path.join(ROOT, "config", "astrodeck.json"),
                  encoding="utf-8") as fh:
            cfg = json.load(fh) or {}
    except (OSError, ValueError) as exc:
        return f"UNREADABLE ({type(exc).__name__}) - treat as unwatched"
    deadman = bool(str(cfg.get("deadman_url") or "").strip())
    sinks = [s for s in (cfg.get("alerts") or []) if isinstance(s, dict)]
    live = [s for s in sinks if s.get("enabled", True)]
    verified = sum(1 for s in live if s.get("verified"))
    if not deadman and not live:
        return ("UNWATCHED - no dead-man URL and no alert channel: if this PC "
                "stops, nothing outside it will say so (issue #125)")
    parts = [_deadman_state(deadman_health) if deadman
             else "NO dead-man URL - an alert channel cannot report its own PC dying"]
    parts.append(f"{len(live)} alert channel(s), {verified} verified")
    return "; ".join(parts)


def _recovery_line() -> str:
    """Will the engine try to reconnect a dropped or silent device? (#16)

    The reconnect gate - including #16's rule that a camera claiming to be
    connected while producing nothing is dropped - runs only when
    `escalation.reconnect_resume` is on, and it is OFF by default. Checked on
    2026-09-22 it was off on this rig, so that recovery existed and never ran.
    Whether to turn it on is the operator's call; this makes sure it is a
    decision somebody sees rather than a default nobody does.
    """
    try:
        with open(os.path.join(ROOT, "config", "astrodeck.json"),
                  encoding="utf-8") as fh:
            esc = (json.load(fh) or {}).get("escalation") or {}
    except (OSError, ValueError) as exc:
        return f"UNREADABLE ({type(exc).__name__})"
    if esc.get("reconnect_resume") is True:
        return "reconnect-and-resume ON"
    return ("reconnect-and-resume OFF - a dropped or silent device stops the "
            "run instead of being reconnected (issue #16)")


def _mount_line(mount: dict) -> str:
    """The mount's state as booleans only: slewing, tracking, parked and
    whether its position is trusted. No coordinate of any kind. (#140, #883)

    `/api/status` also carries the mount's alt/az, rounded to a tenth of a
    degree (`hub.py`), and its RA/Dec. Every one of them is a site coordinate
    on a mount that is not tracking. A stationary mount holds a fixed hour
    angle and Dec, so its reported RA advances with the local sidereal clock:
    RA plus the wall-clock time of the run is the site longitude, and a mount
    parked at home sits on the pole, where the RA IS that clock and the
    altitude equals the site latitude to that same tenth of a degree. #133 put
    an AM5 into exactly that state (a reset mount believing it is parked at
    home), #140 is an agent printing its altitude there to check tracking, and
    #883 is this line printing the RA beside it. This script runs to check a
    rig that is idle before a deploy, which is when the mount is parked.

    `redact.py` already strips alt/az from every non-admin API response for
    this reason, but rig_precheck authenticates with the admin token, so
    nothing upstream withholds anything here. The withholding has to happen in
    this function, by never reading a coordinate key off ``mount`` - not by
    rounding or formatting one differently after the fact, which is the #19
    key-name-filter failure repeating: a value a caller can compute for itself
    is not made safe by hiding it downstream of where it was read. The precheck
    gates on whether the rig is busy, and no coordinate answers that.
    """
    return (f"mount: slewing={mount.get('slewing')} tracking={mount.get('tracking')} "
            f"parked={mount.get('parked')} position_known={mount.get('position_known')}")


def _session_cookie() -> str:
    with open(os.path.join(ROOT, "config", "astrodeck.json"), encoding="utf-8") as fh:
        cfg = json.load(fh)
    req = urllib.request.Request(
        BASE + "/auth/token",
        data=json.dumps({"token": cfg["auth"]["admin_token"]}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    resp = urllib.request.urlopen(req, timeout=15)
    for name, value in resp.getheaders():
        if name.lower() == "set-cookie" and value.startswith("ad_session="):
            return value.split(";", 1)[0]
    raise RuntimeError("no session cookie from /auth/token")


def _get(cookie: str, path: str) -> dict:
    req = urllib.request.Request(BASE + path, headers={"Cookie": cookie})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read())


def main(argv: list[str]) -> int:
    report_only = "--report" in argv
    try:
        cookie = _session_cookie()
        status = _get(cookie, "/api/status")
        polar = _get(cookie, "/api/polar/state")
        sequence = _get(cookie, "/api/sequence/state")
    except (OSError, urllib.error.HTTPError, RuntimeError, ValueError) as exc:
        print(f"could not read rig state: {type(exc).__name__}: {exc}")
        return 0 if report_only else 2

    # Read on its own, outside the try above: the watched line is information
    # and the deploy gate is the BUSY check below, so a health route that errors
    # must degrade that one line to UNKNOWN rather than turn a readable rig into
    # "could not read rig state" (exit 2).
    try:
        health = _get(cookie, "/api/alerts/health")
    except (OSError, urllib.error.HTTPError, ValueError):
        health = None
    deadman_health = health.get("deadman") if isinstance(health, dict) else None

    mount = status.get("mount") or {}
    connected = {k: bool(v.get("connected")) for k, v in (status.get("connected") or {}).items()}
    print("connected:", ", ".join(f"{k}={'yes' if v else 'NO'}" for k, v in sorted(connected.items())))
    print(_mount_line(mount))
    print(f"polar: {polar.get('state')} running={polar.get('running')} ({polar.get('message')})")
    print(f"sequence: {sequence.get('state')} running={sequence.get('running')}")
    lanes = status.get("busy_lanes") or status.get("busy") or {}
    print(f"busy lanes: {lanes if lanes else 'none'}")
    print("site: " + _site_line())
    print("watched: " + _watch_line(deadman_health))
    print("recovery: " + _recovery_line())
    for flag in ("looping", "bahtinov_active", "live_stack_active"):
        print(f"{flag}: {status.get(flag)}")

    reasons = []
    if mount.get("slewing"):
        reasons.append("the mount is slewing")
    if polar.get("running") or (polar.get("state") not in (None, "idle", "done", "stopped")):
        reasons.append(f"a polar alignment is {polar.get('state')}")
    if sequence.get("running") or sequence.get("state") in ("running", "paused", "holding"):
        reasons.append(f"a sequence is {sequence.get('state')}")
    if lanes:
        reasons.append(f"busy lanes: {lanes}")
    for flag in ("looping", "bahtinov_active", "live_stack_active"):
        if status.get(flag):
            reasons.append(f"{flag} is on")
    if reasons:
        print("BUSY: " + "; ".join(reasons))
        return 0 if report_only else 3
    print("IDLE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
