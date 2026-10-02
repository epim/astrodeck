# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Gate a server restart that is MEANT to land mid-run (deploy 0.3.35).

rig_precheck.py refuses to restart under any running sequence, which is right
for an ordinary deploy. The 0.3.35 deploy restarts mid-run on purpose, to prove
the run auto-resumes, so this gate allows exactly one thing precheck refuses: a
running sequence whose session is armed for auto-resume. It still refuses what
a restart must never interrupt, and it waits for the next saved frame so the
restart lands at a frame boundary and loses the least exposure.

Exit 0: restart now. Exit 3: refused, with the reason printed. Mount state is
printed as booleans only (a parked or reset mount's alt/az is the latitude).
Run with the rig's venv, from C:\\Users\\James\\AstroDeck.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
BASE = "http://127.0.0.1:8800"
WAIT_FOR_FRAME_S = 420.0


def _cookie() -> str:
    cfg = json.load(open(os.path.join(ROOT, "config", "astrodeck.json"), encoding="utf-8"))
    req = urllib.request.Request(
        BASE + "/auth/token",
        data=json.dumps({"token": cfg["auth"]["admin_token"]}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    for name, value in urllib.request.urlopen(req, timeout=15).getheaders():
        if name.lower() == "set-cookie" and value.startswith("ad_session="):
            return value.split(";", 1)[0]
    raise RuntimeError("no session cookie")


def _get(cookie: str, path: str):
    req = urllib.request.Request(BASE + path, headers={"Cookie": cookie})
    return json.loads(urllib.request.urlopen(req, timeout=20).read())


def _refusals(status: dict, polar: dict) -> list[str]:
    mount = status.get("mount") or {}
    out = []
    if mount.get("slewing"):
        out.append("the mount is slewing")
    if polar.get("running") or polar.get("state") not in (None, "idle", "done", "stopped"):
        out.append(f"a polar alignment is {polar.get('state')}")
    for flag in ("looping", "bahtinov_active", "live_stack_active"):
        if status.get(flag):
            out.append(f"{flag} is on")
    return out


def main() -> int:
    cookie = _cookie()
    seq = _get(cookie, "/api/sequence/state")
    rows = _get(cookie, "/api/sessions")
    rows = rows if isinstance(rows, list) else rows.get("sessions", [])
    active = [r for r in rows if r.get("status") == "active"]
    armed = [r for r in active if r.get("auto_resume")]
    print(f"sequence: {seq.get('state')} | active sessions: {len(active)}, armed: {len(armed)}")
    for r in armed:
        print(f"   armed: {str(r.get('id'))[:8]} {r.get('name')}")
    if seq.get("state") in ("running", "holding", "paused") and not armed:
        print("REFUSED: a run is in flight but its session is not armed to resume - "
              "a restart would end it for good")
        return 3
    start_done = (seq.get("progress") or {}).get("frames_done")
    deadline = time.monotonic() + WAIT_FOR_FRAME_S
    while True:
        status = _get(cookie, "/api/status")
        polar = _get(cookie, "/api/polar/state")
        seq = _get(cookie, "/api/sequence/state")
        bad = _refusals(status, polar)
        done = (seq.get("progress") or {}).get("frames_done")
        at_boundary = start_done is None or (done is not None and done != start_done)
        if not bad and (at_boundary or seq.get("state") not in ("running",)):
            m = status.get("mount") or {}
            print(f"GO: frames_done {start_done} -> {done}; mount slewing={bool(m.get('slewing'))} "
                  f"tracking={bool(m.get('tracking'))} parked={bool(m.get('parked'))}")
            return 0
        if time.monotonic() > deadline:
            if bad:
                print("REFUSED: " + "; ".join(bad))
                return 3
            print(f"GO (no frame boundary within {WAIT_FOR_FRAME_S:.0f} s): nothing unsafe in flight")
            return 0
        time.sleep(2)


if __name__ == "__main__":
    raise SystemExit(main())
