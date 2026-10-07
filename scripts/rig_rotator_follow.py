# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Does the camera follow the rotator? The repeatable measurement for #594.

Run ON the rig with the install's venv python, next to rig_precheck.py (it
mints its session through that script's loopback path: the admin token never
leaves the box, the cookie stays in memory, and neither is ever printed):

    cd C:\\Users\\James\\AstroDeck; & .\\venv\\Scripts\\python.exe rig_rotator_follow.py

WHEN TO RUN IT. At dusk, with the sky solving, the mount TRACKING, and NO RUN
ARMED. It turns the rotator by up to 103 degrees each way and plate-solves
after every move, so a run, an armed auto-resume, a polar alignment or a live
loop would collide with it; it refuses (exit 3) when it can see one. It cannot
see an armed session, so disarm first. The rotator range must be FULL: on a
limited range the route folds a large move back into the arc and the commanded
change is no longer the planned one.

WHAT IT MEASURES. #594: the CAA's coupling to the camera slips on moves over a
few degrees (a 103 degree move turned the camera 2.8), and has come loose more
than once. The CAA's own mechanical reading always matched the command, so only
a plate solve can say whether the CAMERA turned. Each move below is made
through POST /api/rotator/move with ``direct`` true (the single mechanical
move, with none of the one-sided approach's overshoot that would add 10 degrees
of travel to the very thing being measured), then POST /api/rotator/sync-to-sky
(which solves and moves nothing), and the solved sky angle is read back:

    3 syncs with no move (solve noise), then moves of
    +1 x3, -0.5 x8, +0.5 x4, then +20, -20, +103, -103 degrees mechanical.

For each move it prints one line: the commanded mechanical change, the solved
PA change, and the fraction followed (abs(solved) / abs(commanded), the same
comparison ``rotation.follow_fraction`` makes). The JSONL under
captures/backlash/ keeps the raw angles so a run can be re-graded later with
``--grade FILE``.

THE VERDICT. PASS when every move over 3 degrees is followed 0.9 to 1.1 and
every small step 0.7 to 1.3, and the camera turned the SAME way each time (a
fraction in range on a camera that sometimes turns the wrong way is not
following). The first small step after a reversal is exempt from its range and
reported as the REVERSAL LOSS instead: that is the true backlash (0.1 to 0.2
degrees on the 2026-09-29 data), and it is the number #526 (b) and D-18 need.
Large moves are never exempt: the plan reverses before three of its four, and
exempting them would leave the 103 degree question ungraded.

It leaves the rotator where the last move ended: the plan's net travel is
+1 degree mechanical.

Exit codes: 0 PASS, 1 FAIL (measured, out of tolerance), 2 could not measure
(no solve, a refused move), 3 refused to start (the rig is not idle and ready).

WHAT IS PRINTED. Counts and angles only. Never the mount's coordinates, never
the rest of ``sky_angle`` (the pier side, the camera, the calibration), never
the cookie or the token: the only status keys read are ``rotator.mech_deg``,
``rotator.sky_deg``, ``rotator.sky_sign``, ``sky_angle.pa_deg`` and
``sky_angle.exposed_at``, plus booleans about what is busy.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

#: A move longer than this is a LARGE move, graded 0.9-1.1 with no exemption.
SMALL_STEP_MAX_DEG = 3.0
LARGE_FRACTION = (0.9, 1.1)
SMALL_FRACTION = (0.7, 1.3)

#: Solves with no move between them: their spread is the noise floor.
NOISE_SOLVES = 3

#: The moves, in order, mechanical degrees. The reversals are deliberate: the
#: first -0.5 and the first +0.5 measure the backlash, and the large moves
#: alternate direction so the 103 degree ones are each made after a reversal.
MOVES_DEG: tuple[float, ...] = (
    (1.0,) * 3 + (-0.5,) * 8 + (0.5,) * 4 + (20.0, -20.0, 103.0, -103.0))

#: How long to wait for a move to finish, and for a solve to land.
MOVE_TIMEOUT_S = 240.0
SOLVE_TIMEOUT_S = 150.0


# ----------------------------------------------------------- pure functions


def wrap180(delta_deg: float) -> float:
    """A signed angle difference folded to [-180, 180), the shortest way."""
    return ((delta_deg + 180.0) % 360.0) - 180.0


def follow_fraction(commanded_deg: float, solved_change_deg: float) -> float:
    """What fraction of the commanded mechanical change the solved sky angle
    reached: abs(solved) / abs(commanded). A zero command reads as 1.0 rather
    than dividing by zero, as ``rotation.follow_fraction`` does."""
    if commanded_deg == 0.0:
        return 1.0
    return abs(solved_change_deg) / abs(commanded_deg)


def is_reversal(previous_commanded_deg: float | None,
                commanded_deg: float) -> bool:
    """Whether this move turns the opposite way to the one before it. The
    first move of a run has nothing before it and is not a reversal."""
    if previous_commanded_deg is None:
        return False
    return (previous_commanded_deg > 0.0) != (commanded_deg > 0.0)


def _in(fraction: float, band: tuple[float, float]) -> bool:
    return band[0] <= fraction <= band[1]


def grade(records: list[dict]) -> dict:
    """Grade a run from its raw records (the JSONL, one dict per line).

    ``noise`` records carry ``pa_deg``; ``move`` records carry
    ``commanded_deg``, ``pa_before_deg`` and ``pa_after_deg`` (None when the
    solve after the move never landed). Everything derived (the solved change,
    the fraction, the direction, the verdict) is computed here from those, so a
    saved file can be re-graded and a hand-edited number cannot disagree with
    the grade.

    The verdict is PASS only when there is at least one graded move, every
    move was solved, every large move is followed within LARGE_FRACTION, every
    small step within SMALL_FRACTION except the first one after a reversal
    (reported as the reversal loss), and every graded move turned the camera
    the same way. The reversal exemption applies to SMALL steps only."""
    noise_pas = [float(r["pa_deg"]) for r in records
                 if r.get("kind") == "noise" and r.get("pa_deg") is not None]
    noise = None
    if len(noise_pas) >= 2:
        rel = [wrap180(p - noise_pas[0]) for p in noise_pas]
        noise = max(rel) - min(rel)

    rows: list[dict] = []
    failures: list[str] = []
    previous: float | None = None
    for r in records:
        if r.get("kind") != "move":
            continue
        commanded = float(r["commanded_deg"])
        before, after = r.get("pa_before_deg"), r.get("pa_after_deg")
        small = abs(commanded) <= SMALL_STEP_MAX_DEG
        reversal = is_reversal(previous, commanded)
        previous = commanded
        row = {"commanded_deg": commanded,
               "kind": "small" if small else "large",
               "reversal": reversal, "exempt": False, "solved_deg": None,
               "fraction": None, "direction": None, "ok": False,
               "reversal_loss_deg": None}
        rows.append(row)
        if before is None or after is None:
            failures.append(f"move {len(rows)} ({commanded:+g} deg): no "
                            f"solve landed, so nothing was measured")
            continue
        solved = wrap180(float(after) - float(before))
        fraction = follow_fraction(commanded, solved)
        row["solved_deg"] = solved
        row["fraction"] = fraction
        row["direction"] = 0 if solved == 0.0 else (
            1 if (solved > 0.0) == (commanded > 0.0) else -1)
        if reversal:
            # The loss is reported for every reversal; only a small step's
            # grade is waived by it.
            row["reversal_loss_deg"] = abs(commanded) - abs(solved)
        if small and reversal:
            row["exempt"] = True
            row["ok"] = True
            continue
        band = SMALL_FRACTION if small else LARGE_FRACTION
        row["ok"] = _in(fraction, band)
        if not row["ok"]:
            failures.append(
                f"move {len(rows)} ({commanded:+g} deg): followed "
                f"{fraction:.2f}, outside {band[0]:g}-{band[1]:g}")

    graded = [x for x in rows if x["solved_deg"] is not None]
    directions = {x["direction"] for x in graded if not x["exempt"]}
    if {1, -1} <= directions:
        failures.append("the camera turned both ways for the same sign of "
                        "command: it does not reliably follow the rotator")
    if not graded:
        failures.append("no move was measured")
    sign = next(iter(directions)) if directions in ({1}, {-1}) else None
    return {"verdict": "FAIL" if failures else "PASS", "moves": rows,
            "failures": failures, "noise_deg": noise, "sign": sign,
            "reversal_losses_deg": [x["reversal_loss_deg"] for x in rows
                                    if x["reversal_loss_deg"] is not None]}


def format_move_line(index: int, total: int, row: dict) -> str:
    """One line per move: the commanded change, the solved change, the
    fraction followed. Angles only."""
    head = f"move {index:2d}/{total}  commanded {row['commanded_deg']:+7.2f}"
    if row["solved_deg"] is None:
        return head + "  no solve landed"
    tail = (f"  solved {row['solved_deg']:+7.2f}  "
            f"followed {row['fraction']:.2f}")
    if row["reversal"]:
        tail += f"  reversal, loss {row['reversal_loss_deg']:+.2f}"
        if row["exempt"]:
            tail += " (exempt)"
    elif not row["ok"]:
        tail += "  OUT OF RANGE"
    return head + tail


def summary_lines(result: dict) -> list[str]:
    """The run's summary: the evidence for #594, #526 (b) and D-18."""
    out = []
    if result["noise_deg"] is not None:
        out.append(f"solve noise: {result['noise_deg']:.2f} deg spread over "
                   f"{NOISE_SOLVES} solves with no move")
    if result["sign"] is not None:
        word = "together" if result["sign"] == 1 else "OPPOSITE"
        out.append(f"direction: the solved PA ran {word} to the mechanical "
                   f"angle on every graded move (sign {result['sign']:+d})")
    losses = result["reversal_losses_deg"]
    if losses:
        out.append("reversal loss (first step after a direction change), "
                   "deg: " + ", ".join(f"{x:+.2f}" for x in losses))
    for f in result["failures"]:
        out.append("FAIL: " + f)
    out.append(result["verdict"])
    return out


def read_jsonl(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


# ------------------------------------------------------------------ the rig


def _rig_precheck():
    """The sibling script that already knows how to mint a loopback session.
    Imported by path, so this works from the install root or from scripts/."""
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    try:
        import rig_precheck
    except ImportError as exc:
        raise SystemExit(
            f"rig_precheck.py must sit next to this script ({exc}); it mints "
            f"the loopback session")
    return rig_precheck


class Unmeasured(RuntimeError):
    """A step produced no measurement (a solve that failed, a timeout)."""


class Rig:
    """The few routes the measurement uses, over the loopback session."""

    def __init__(self) -> None:
        self._pre = _rig_precheck()
        self.root = self._pre.ROOT
        self._cookie = self._pre._session_cookie()
        self._base = self._pre.BASE

    def get(self, path: str) -> dict:
        req = urllib.request.Request(self._base + path,
                                     headers={"Cookie": self._cookie})
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read())

    def post(self, path: str, body: dict | None = None) -> dict:
        req = urllib.request.Request(
            self._base + path, data=json.dumps(body or {}).encode(),
            headers={"Cookie": self._cookie,
                     "Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read() or b"{}")

    def halt(self) -> None:
        try:
            self.post("/api/rotator/halt")
        except (OSError, urllib.error.HTTPError, ValueError):
            pass


def _refusals(status: dict, sequence: dict, cfg: dict) -> list[str]:
    """Why the measurement must not start. Booleans and a range name only."""
    out = []
    if not isinstance(status.get("rotator"), dict):
        out.append("no rotator is connected")
    if not ((status.get("connected") or {}).get("camera") or {}).get("connected"):
        out.append("no camera is connected")
    mount = status.get("mount") or {}
    if not mount.get("tracking"):
        out.append("the mount is not tracking")
    if mount.get("slewing"):
        out.append("the mount is slewing")
    if mount.get("parked"):
        out.append("the mount is parked")
    lanes = status.get("busy_lanes") or []
    if lanes:
        out.append(f"busy lanes: {lanes}")
    for flag in ("looping", "bahtinov_active", "live_stack_active"):
        if status.get(flag):
            out.append(f"{flag} is on")
    if sequence.get("running") or sequence.get("state") in (
            "running", "paused", "holding"):
        out.append(f"a sequence is {sequence.get('state')}")
    range_type = (cfg.get("rotator") or {}).get("range_type")
    if range_type != "full":
        out.append(f"the rotator range is {range_type!r}, not 'full'; a "
                   f"limited range folds the large moves back into its arc")
    return out


def _wait_idle(rig: Rig, what: str) -> dict:
    """Wait until the rotator lanes are free and the rotator reports at rest,
    twice running, and return that status. Raises TimeoutError."""
    deadline = time.monotonic() + MOVE_TIMEOUT_S
    quiet = 0
    while time.monotonic() < deadline:
        status = rig.get("/api/status")
        lanes = status.get("busy_lanes") or []
        moving = (status.get("rotator") or {}).get("moving")
        if "rotator" not in lanes and "rotate_to_pa" not in lanes and not moving:
            quiet += 1
            if quiet >= 2:
                return status
        else:
            quiet = 0
        time.sleep(1.0)
    raise TimeoutError(f"{what} did not finish in {MOVE_TIMEOUT_S:.0f} s")


def _solve(rig: Rig) -> tuple[float, float]:
    """One plate solve with the rotator at rest: (solved sky PA, mechanical
    angle read after it). Posts sync-to-sky (which moves nothing) and waits
    for a sky-angle record whose exposure began after the post."""
    since = time.time()
    rig.post("/api/rotator/sync-to-sky", {})
    deadline = time.monotonic() + SOLVE_TIMEOUT_S
    while time.monotonic() < deadline:
        time.sleep(1.5)
        status = rig.get("/api/status")
        rec = status.get("sky_angle") or {}
        exposed = rec.get("exposed_at")
        busy = "rotate_to_pa" in (status.get("busy_lanes") or [])
        if (isinstance(exposed, (int, float)) and exposed >= since
                and rec.get("pa_deg") is not None and not busy):
            mech = (status.get("rotator") or {}).get("mech_deg")
            return float(rec["pa_deg"]), float(mech)
        if not busy and time.time() - since > 15.0:
            # The lane ran and ended with no new sky angle: the solve failed.
            # Waiting out the whole timeout would only hide that.
            raise Unmeasured("the sync-to-sky solve finished without a "
                             "position angle (the night log says why)")
    raise TimeoutError(f"no solve landed within {SOLVE_TIMEOUT_S:.0f} s")


def run(out_path: str) -> int:
    rig = Rig()
    status = rig.get("/api/status")
    why = _refusals(status, rig.get("/api/sequence/state"),
                    rig.get("/api/config"))
    if why:
        print("REFUSED: " + "; ".join(why))
        return 3
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    print(f"{len(MOVES_DEG)} moves, {NOISE_SOLVES} noise solves; "
          f"raw angles to {out_path}")
    records: list[dict] = []

    def keep(rec: dict) -> None:
        records.append(rec)
        with open(out_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")

    try:
        pa, mech = _solve(rig)
        keep({"kind": "noise", "pa_deg": pa, "mech_deg": mech})
        for _ in range(NOISE_SOLVES - 1):
            pa, mech = _solve(rig)
            keep({"kind": "noise", "pa_deg": pa, "mech_deg": mech})
        before_pa, before_mech = pa, mech
        for i, commanded in enumerate(MOVES_DEG, start=1):
            rot = rig.get("/api/status").get("rotator") or {}
            sign = rot.get("sky_sign") or 1      # the route's own default
            target = (float(rot["sky_deg"]) + sign * commanded) % 360.0
            reply = rig.post("/api/rotator/move",
                             {"position_deg": target, "direct": True})
            if reply.get("adjusted"):
                print("REFUSED mid-run: the route folded the move into the "
                      "rotator's range; the commanded change is not the "
                      "planned one")
                rig.halt()
                return 2
            _wait_idle(rig, "the move")
            after_pa, after_mech = _solve(rig)
            keep({"kind": "move", "index": i, "commanded_deg": commanded,
                  "pa_before_deg": before_pa, "pa_after_deg": after_pa,
                  "mech_before_deg": before_mech,
                  "mech_after_deg": after_mech})
            partial = grade(records)
            print(format_move_line(i, len(MOVES_DEG), partial["moves"][-1]))
            before_pa, before_mech = after_pa, after_mech
    except KeyboardInterrupt:
        rig.halt()
        print("interrupted: the rotator was halted")
        return 2
    except (Unmeasured, TimeoutError, OSError, urllib.error.HTTPError,
            ValueError, KeyError) as exc:
        rig.halt()
        print(f"could not measure: {type(exc).__name__}: {exc}")
        return 2
    result = grade(records)
    for line in summary_lines(result):
        print(line)
    return 0 if result["verdict"] == "PASS" else 1


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--grade", metavar="FILE",
                    help="re-grade a saved JSONL run; contacts nothing")
    ap.add_argument("--plan", action="store_true",
                    help="print the moves and exit; contacts nothing")
    args = ap.parse_args(argv)
    if args.plan:
        print(f"{NOISE_SOLVES} solves with no move, then mechanical moves of "
              + ", ".join(f"{m:+g}" for m in MOVES_DEG) + " degrees")
        return 0
    if args.grade:
        result = grade(read_jsonl(args.grade))
        moves = result["moves"]
        for i, row in enumerate(moves, start=1):
            print(format_move_line(i, len(moves), row))
        for line in summary_lines(result):
            print(line)
        return 0 if result["verdict"] == "PASS" else 1
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    root = os.environ.get("ASTRODECK_INSTALL_ROOT",
                          r"C:\Users\James\AstroDeck")
    return run(os.path.join(root, "captures", "backlash",
                            f"rotator_follow_{stamp}.jsonl"))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
