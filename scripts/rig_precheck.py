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
