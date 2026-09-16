"""Verify 0.3.32 actually carries what it claims, on the rig.

Derived from verify_0331.py. 0.3.32 = 0.3.30 + the flows compiler notes, the
flows inspector, the atlas default with targets, skydome tracks, and the rig
session's F3/F4/F6 (flip-owed hold, unattended-guiding stop, pier side
published and the slew guard armed).

A deploy report is a claim; this checks the running server and the deployed
source. Three things, because a release can be right about its version number
and still be wrong about its contents:

  1. the release pointer and the version the server answers with;
  2. the CONFIG the server is really using -- including
     safety.enforce_pier_limits, which was set by hand at 09:52 and could have
     been overwritten by a deploy that carried an older config forward;
  3. that the deployed MODULES contain the new code, not just that the
     version string changed. `_relock_limit_exceeded` either exists in the file
     the server imported or the guider cannot stop itself, whatever the tag
     says.
"""
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, r"C:\Users\James\AstroDeck")
from flow_ngc7331 import _cookie, call  # noqa: E402

ROOT = r"C:\Users\James\AstroDeck"

print("== 1. release pointer ==")
for name in ("current", "previous"):
    p = os.path.join(ROOT, name)
    if os.path.isfile(p):
        with open(p, encoding="utf-8") as fh:
            print(f"  {name}: {fh.read().strip()}")
    elif os.path.isdir(p):
        print(f"  {name}: -> {os.path.realpath(p)}")
    else:
        print(f"  {name}: not found")

cookie = _cookie()

print("== 2. config the server is actually using ==")
code, cfg = call(cookie, "GET", "/api/config")
cfg = cfg if isinstance(cfg, dict) else {}
guide = cfg.get("guide") or {}
safety = cfg.get("safety") or {}
want_guide = {"dither_settle_fail_limit": 2,
              "relock_jump_arcsec": 120.0,
              "relock_arcsec_limit": 300.0,
              "relock_limit": 3,
              "relock_window_min": 10.0}
ok = True
for k, want in want_guide.items():
    got = guide.get(k, "MISSING")
    flag = "ok " if got == want else "!! "
    if got != want:
        ok = False
    print(f"  {flag}guide.{k} = {got!r} (expected {want!r})")
for k, want in (("flip_owed_hold_min", 20.0), ("unattended_guide_min", 10.0)):
    got = safety.get(k, "MISSING")
    flag = "ok " if got == want else "!! "
    if got != want:
        ok = False
    print(f"  {flag}safety.{k} = {got!r} (expected {want!r}; this key ARMS F3/F4 - "
          f"a config carried forward without it deploys the fix switched off)")
pier = safety.get("enforce_pier_limits", "MISSING")
print(f"  {'ok ' if pier is True else '!! '}safety.enforce_pier_limits = "
      f"{pier!r} (set by hand 09:52; a deploy must not have reverted it)")
if pier is not True:
    ok = False

print("== 3. the deployed source really contains the new code ==")
# Resolve what the server imported, not what a directory name suggests.
checks = [
    ("astrodeck/guide/native.py", ["_relock_limit_exceeded",
                                   "_relock_arcsec_in_window",
                                   "_relock_stop_reason"]),
    ("astrodeck/dawn_park.py", ['if not getattr(eng, "paused", False):',
                                "A PAUSED RUN IS NOT SOMEBODY STANDING"]),
    ("astrodeck/sequence/engine.py", ["_note_dither_failure",
                                      "_maybe_hold_for_dither_failures",
                                      "_hold_recentre_recalibrate"]),
    ("astrodeck/config.py", ["relock_arcsec_limit", "relock_jump_arcsec",
                             "dither_settle_fail_limit",
                             "flip_owed_hold_min", "unattended_guide_min"]),
    ("astrodeck/sequence/engine.py", ["_enforce_flip_owed", "_hold_for_owed_flip",
                                      "flip_owed"]),
    ("astrodeck/dawn_park.py", ["_check_unattended_guiding", "_somebody_is_driving"]),
    ("astrodeck/catalog/coords.py", ["pier_side_for_hour_angle"]),
    ("astrodeck/devices/backends/zwo_am5.py", ["destination_pier_side",
                                               "reports_destination_pier_side = True"]),
    ("astrodeck/hub.py", ["_note_pier_side", "PIER_SIDE_STALE_S"]),
    ("astrodeck/flows/to_plan.py", ['"carried"', '"ignored"', '"source"']),
]
# `current` is a FILE holding the version string, not a junction: the package
# lives at releases/<version>/server. The first version of this script treated
# it as a directory and reported every check as unreadable, which reads
# identically to a bad deploy -- so resolve it properly and say which path was
# used.
base = ROOT
cur = os.path.join(ROOT, "current")
version = ""
if os.path.isfile(cur):
    with open(cur, encoding="utf-8") as fh:
        version = fh.read().strip()
for candidate in (os.path.join(ROOT, "releases", version, "server"),
                  os.path.realpath(cur) if os.path.isdir(cur) else "",
                  os.path.join(ROOT, "server")):
    if candidate and os.path.isdir(os.path.join(candidate, "astrodeck")):
        base = candidate
        break
print(f"  package root: {base}")
for rel, needles in checks:
    path = os.path.join(base, rel)
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            src = fh.read()
    except OSError as e:
        print(f"  !! {rel}: unreadable ({e})")
        ok = False
        continue
    for needle in needles:
        present = needle in src
        if not present:
            ok = False
        print(f"  {'ok ' if present else '!! '}{rel}: {needle[:46]}")

code, st = call(cookie, "GET", "/api/status")
st = st if isinstance(st, dict) else {}
print("== 3b. the running server answers 0.3.32 and its meridian block carries the new fields ==")
code, hz = call(cookie, "GET", "/healthz")
hz = hz if isinstance(hz, dict) else {}
v_ok = hz.get("version") == "0.3.32"
if not v_ok:
    ok = False
print(f"  {'ok ' if v_ok else '!! '}healthz version = {hz.get('version')!r}")
mer = st_mer = None
code, st0 = call(cookie, "GET", "/api/status")
st0 = st0 if isinstance(st0, dict) else {}
mer = st0.get("meridian")
if isinstance(mer, dict):
    for k in ("pier_side", "pier_side_source", "pier_side_age_s", "flip_owed"):
        present = k in mer
        if not present:
            ok = False
        print(f"  {'ok ' if present else '!! '}status.meridian.{k} = {mer.get(k, 'MISSING')!r}")
else:
    ok = False
    print("  !! status.meridian is ABSENT (the block should degrade to unknown, never vanish)")
print("== 4. rig state ==")
m = st.get("mount") or {}
cam = st.get("camera") or {}
print(f"  mount parked={m.get('parked')} tracking={m.get('tracking')}")
print(f"  camera temp={cam.get('temperature_c')} cooler_on={cam.get('cooler_on')}")
g = st.get("guider") or {}
print(f"  guider guiding={g.get('guiding')} phase={g.get('phase')}")

print("VERDICT:", "0.3.32 CARRIES THE FIXES" if ok else "SOMETHING IS MISSING")
print("VERIFY_DONE")
