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
import os
import sys
import urllib.error
import urllib.request

ROOT = os.environ.get("ASTRODECK_INSTALL_ROOT", r"C:\Users\James\AstroDeck")
BASE = "http://127.0.0.1:8800"


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


def _watch_line() -> str:
    """Is anything outside this PC watching it? Counts and booleans only.

    Issue #125: the rig went offline and nothing said so. The product already
    has the watcher - `alerting.py`'s dead-man ping, sent every minute from a
    wall-clock timer, whose ABSENCE is what pages - but on 2026-09-22 this rig
    had no dead-man URL and no alert channel, so it could have been off for a
    week and nothing would have noticed. This line puts that state in front of
    every deploy instead of leaving it to be found by the next outage.

    The dead-man URL carries a per-ping secret in its path, and a sink carries
    a token or a webhook URL, so nothing here can print either: the facts that
    leave are a boolean and two counts.
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
    parts = ["dead-man configured" if deadman
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

    mount = status.get("mount") or {}
    connected = {k: bool(v.get("connected")) for k, v in (status.get("connected") or {}).items()}
    print("connected:", ", ".join(f"{k}={'yes' if v else 'NO'}" for k, v in sorted(connected.items())))
    print(f"mount: slewing={mount.get('slewing')} tracking={mount.get('tracking')} "
          f"parked={mount.get('parked')} ra={mount.get('ra_str')} "
          f"dec={str(mount.get('dec_str')).replace(chr(176), ' deg')}")
    print(f"polar: {polar.get('state')} running={polar.get('running')} ({polar.get('message')})")
    print(f"sequence: {sequence.get('state')} running={sequence.get('running')}")
    lanes = status.get("busy_lanes") or status.get("busy") or {}
    print(f"busy lanes: {lanes if lanes else 'none'}")
    print("site: " + _site_line())
    print("watched: " + _watch_line())
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
