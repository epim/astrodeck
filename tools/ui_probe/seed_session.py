"""seed_session.py -- move a flow's dormant session onto an earlier observing
night, and arm it, for the UI probe's CONTINUE walk (#189 S7 item 1, the third
of its four simulator scenarios: "a CONTINUE on a second simulated night").

Run with the SERVER's venv python, never the system one: it writes the session
in the store's own shape by the store's own code (``SessionStore.load`` and
``save``, the report store's reader and ``write_json_atomic``), so what the
private server reads back is exactly what it would have written itself. It is
started by probe.py's ``seed_session`` seed op, against the private server's
own directories, after that server has run the flow for real and aborted it:

    server/.venv/Scripts/python.exe tools/ui_probe/seed_session.py \\
        --config-dir <private cfg> --capture-dir <private captures> \\
        --flow <flow id> --nights-ago 1 --arm

WHAT IT MOVES. Every run of the session is re-stamped ``--nights-ago`` local
calendar days earlier, at the same wall-clock time: the stamp in each report
id (``<slug>-YYYYMMDD-HHMMSS``, the one ``report_night`` reads, so the
session's ``observing_nights`` and the night CONTINUE says move with it),
each ledger frame's ``night`` and ``ts``, the session's ``created_ts``, the
night keys of its set-aside and pier records, the times of its locked angles,
and each run's report file, renamed to its new id with its own times moved.
The frames, their step ids and the plan are left exactly as the run banked
them, so the progress route's counts are the ledger's.

WHAT IT LEAVES. ``plan_saved_ts``, the saved time of the flow version the
session froze (#473, S7 orchestrator ruling 1). That is a fact about the flow
record, not about when the session ran; moving it earlier than the record's
own ``updated_ts`` would make the editor claim edits nobody made, and the walk
grades that the replay notice appears only after its own edit and save.

``--arm`` turns ``auto_resume`` on, and off on every other session, the
singleton ``engine.start`` and the sessions PATCH keep, so the progress route
answers ``armed: true`` (the replay notice needs it). The walk keeps the armed
session from being resumed under it by the site probe.py saves first, where
the sun is up (``daylight_site``): auto-resume opens no window in daylight.

REFUSALS, each in words and before anything is written: a directory that is
not given, or that lies inside the repository's ``server/`` or ``captures/``
(where the server keeps the developer's real config and captures when no
directory is set); no session for the flow; a session that is not
dormant (a run owns it, and would write over the change); a run id with no
stamp; and a move that leaves a run on tonight's observing night, since
CONTINUE would then not be a second night at all.

Prints one JSON object on stdout: the session id, the report ids before and
after, the observing nights, the frame count, whether it is armed, and
``plan_saved_ts``. It carries nothing about the site.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


class SeedRefused(RuntimeError):
    pass


#: Where a server run from this checkout keeps the developer's own state when
#: no directory is set: the config in ``server/config`` (config.py
#: ``CONFIG_DIR``, under ``server/``) and the captures, sessions and reports
#: in the repository's ``captures/`` (hub.py ``CAPTURE_DIR``, which is NOT
#: under ``server/``). Both are refused, and everything under them.
_DEVELOPERS_OWN = ((REPO_ROOT / "server", "server/"),
                   (REPO_ROOT / "captures", "captures/"))


def _private(raw: str | None, flag: str) -> Path:
    """A directory the caller named, which is not the developer's own
    (``_DEVELOPERS_OWN``): this script edits session files, and there it would
    edit the developer's real sessions."""
    if not raw:
        raise SeedRefused(f"{flag} is required: this script writes only a private "
                          f"server's directories, never a default")
    path = Path(raw).resolve()
    for own_dir, name in _DEVELOPERS_OWN:
        own = own_dir.resolve()
        if path == own or own in path.parents:
            raise SeedRefused(f"{flag} {path} is inside the repository's {name} "
                              f"directory, which holds the developer's own config "
                              f"or captures: pass the private server's directory")
    if not path.is_dir():
        raise SeedRefused(f"{flag} {path} is not a directory")
    return path


def _moved_id(report_id: str, days: int, stamp_re) -> tuple[str, float]:
    """``report_id`` with its stamp ``days`` local calendar days earlier, at
    the same wall-clock time, and the seconds that moved it (a day is not
    always 86400 s of wall clock: the clocks change)."""
    m = stamp_re.search(report_id or "")
    if m is None:
        raise SeedRefused(f"the run {report_id!r} carries no start stamp, so there is "
                          f"no night to move it from")
    was = _dt.datetime.strptime(m.group(1), "%Y%m%d-%H%M%S")
    now = was - _dt.timedelta(days=days)
    moved = report_id[:m.start(1)] + now.strftime("%Y%m%d-%H%M%S") + report_id[m.end(1):]
    delta = time.mktime(was.timetuple()) - time.mktime(now.timetuple())
    return moved, delta


def seed(flow_id: str, days: int, arm: bool) -> dict:
    """The move itself, inside a process whose astrodeck reads the private
    directories (``main`` sets them before importing it)."""
    from astrodeck.events import night_key
    from astrodeck.persist import write_json_atomic
    from astrodeck.sequence import report as report_mod
    from astrodeck.sequence.session import (_REPORT_STAMP, report_night,
                                            session_store)

    if days < 1:
        raise SeedRefused(f"--nights-ago {days}: a second night is at least one "
                          f"night after the first")
    s = session_store.current_for_flow(flow_id)
    if s is None:
        raise SeedRefused(f"no session for the flow {flow_id!r}: run it on this server "
                          f"first (probe.py's run_flow seed op)")
    if s.status != "dormant":
        raise SeedRefused(f"the session {s.id} of {flow_id!r} is {s.status!r}, not "
                          f"dormant: a run owns it and would write over this")
    if not s.nights:
        raise SeedRefused(f"the session {s.id} has no runs to move")

    ids_before = list(s.nights)
    moved: dict[str, str] = {}
    deltas: dict[str, float] = {}
    for rid in ids_before:
        moved[rid], deltas[rid] = _moved_id(rid, days, _REPORT_STAMP)
    tonight = night_key(time.time())
    keys_after = [report_night(moved[rid]) for rid in ids_before]
    if tonight in keys_after:
        raise SeedRefused(f"moved {days} night(s) back, a run of {s.id} still falls on "
                          f"tonight's observing night ({tonight})")
    night_map = {report_night(rid): report_night(moved[rid]) for rid in ids_before}
    first = deltas[ids_before[0]]

    s.nights = [moved[rid] for rid in ids_before]
    for f in s.frames:
        if f.night in moved:
            f.ts -= deltas[f.night]
            f.night = moved[f.night]
    s.created_ts -= first
    for rec in s.set_aside:
        if isinstance(rec, dict) and rec.get("night") in night_map:
            rec["night"] = night_map[rec["night"]]
    for rec in s.group_pier.values():
        if isinstance(rec, dict) and rec.get("night") in night_map:
            rec["night"] = night_map[rec["night"]]
    for lock in s.locked_angles.values():
        if isinstance(lock, dict):
            for key in ("solved_at", "exposed_at"):
                if isinstance(lock.get(key), (int, float)):
                    lock[key] -= first

    # Each run's report under its new id, its times moved with it, through
    # the report store's own reader and writer; the old file goes once the
    # new one is written. A run whose report was never written (a start
    # refused before its first snapshot) has nothing to move.
    reports: list[dict] = []
    for rid in ids_before:
        got = report_mod.SessionReporter.read(rid)
        if got.report is None:
            reports.append({"id": rid, "moved": False, "why": got.reason})
            continue
        d = deltas[rid]
        rep = got.report.model_copy(deep=True)
        rep.id = moved[rid]
        rep.started_at -= d
        if rep.ended_at is not None:
            rep.ended_at -= d
        for fr in rep.frames:
            fr.ts -= d
        for ev in rep.safety_events:
            if isinstance(ev, dict) and isinstance(ev.get("ts"), (int, float)):
                ev["ts"] -= d
        new_path = report_mod._reports_dir() / f"{report_mod._slug(rep.id)}.json"
        write_json_atomic(new_path, rep.model_dump())
        if got.path != new_path:
            got.path.unlink(missing_ok=True)
        reports.append({"id": rid, "moved": True, "to": rep.id})

    if arm:
        # The singleton, as engine.start keeps it: arming one disarms the rest.
        for other in session_store.load_all():
            if other.id != s.id and other.auto_resume:
                other.auto_resume = False
                session_store.save(other)
        s.auto_resume = True
    session_store.save(s)

    back = session_store.load(s.id)
    return {"session": back.id, "flow": flow_id, "status": back.status,
            "runs_before": ids_before, "runs_after": list(back.nights),
            "observing_nights": back.observing_nights(), "tonight": tonight,
            "frames": len(back.frames), "armed": back.is_armed(),
            "plan_saved_ts": back.plan_saved_ts, "reports": reports}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="seed_session")
    ap.add_argument("--config-dir", required=True,
                    help="the private server's ASTRODECK_CONFIG_DIR")
    ap.add_argument("--capture-dir", required=True,
                    help="the private server's ASTRODECK_CAPTURE_DIR (sessions and "
                        "reports live here)")
    ap.add_argument("--flow", required=True, help="the flow whose session to move")
    ap.add_argument("--nights-ago", type=int, default=1,
                    help="local calendar days to move every run back (default 1)")
    ap.add_argument("--arm", action="store_true",
                    help="arm the session for auto-resume, disarming every other")
    args = ap.parse_args(argv)
    try:
        cfg = _private(args.config_dir, "--config-dir")
        cap = _private(args.capture_dir, "--capture-dir")
    except SeedRefused as exc:
        print(f"seed_session: REFUSED: {exc}", file=sys.stderr)
        return 2
    # Before astrodeck is imported: both are read at import (config.py,
    # hub.py), so the store this process opens is the private server's.
    os.environ["ASTRODECK_CONFIG_DIR"] = str(cfg)
    os.environ["ASTRODECK_CAPTURE_DIR"] = str(cap)
    try:
        out = seed(args.flow, args.nights_ago, args.arm)
    except SeedRefused as exc:
        print(f"seed_session: REFUSED: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
