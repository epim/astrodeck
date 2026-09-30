"""The sessions list counts observing nights, as every other surface does
(#430, H4 task H4-SESSIONS; spec 5.9, S7 orchestrator ruling 7: a session's
night is the observing night its run started in).

``GET /api/sessions`` answers ``SessionStore.list``'s rows, and the classic
Sessions panel prints each row's ``nights`` as "N nights"
(``ui/src/components/sequence/SessionsPanel.tsx``). Since S7 the flow's
progress card and CONTINUE's answer count OBSERVING nights
(``Session.observing_nights``: one per distinct ``events.night_key`` of the
runs' start stamps), while the row still sent ``len(s.nights)``, one report
id per ``engine.start``. A session restarted the same night therefore read
"2 nights" in the panel and "night 1" on the flow card. The row now answers
``len(s.observing_nights())``, the one reading every surface uses.

THE STARTS ARE THE ENGINE'S. `rig` (test_flows_continue.py) is the real app
over the real routes and a real ``SequenceEngine`` with only its imaging loop
replaced, so every ``POST /api/flows/{id}/run`` goes through
``engine.start``: it mints the run's report id from the clock
(``_mint_report_id``) and appends it to ``Session.nights``, which is all the
row's count reads. The row is read back through ``GET /api/sessions``.

THE CLOCK AND THE ZONE ARE PINNED. A night key is local time with a noon
rollover, and a report id carries the run's LOCAL start stamp, so a literal
instant names a different wall-clock time on the dev box (Pacific) than on
CI (UTC). The five modules on this path read time through
``_flow_night.ZonedTime``, whose zone is ``_flow_night.ZONE_S`` (UTC-4) on
every machine: the engine and the app (the instant a run starts at), the
report module (the stamp the id carries), the session module (the stamp read
back, and the ``updated_ts`` a save writes) at the test's clock, and
``events`` (``night_key``) at the real clock, since only its zone matters
here. Every instant is built from wall-clock fields in that zone
(`_wall`), so a literal night key can name it.

THE PANEL READS WHAT THE ROUTE SENT. The panel is a pure printer of the row,
so a UI test that grades it against a row typed into it goes on grading an
answer the server no longer gives (#353 item 7). The route's rows are
therefore recorded here, as a file the panel's test READS
(``ui/src/components/sequence/__tests__/sessionsListNights.recorded.json``,
beside that test because this task owns that directory), and this test
rebuilds them and grades the file. Only the two ids the harness mints at
random (the session's and the flow's) are replaced by fixed ones; every
other byte is the route's. To rewrite the file after a deliberate change to
the row:

    cd server && ASTRODECK_REWRITE_H4_SESSIONS_FIXTURE=1 .venv/Scripts/python.exe -m pytest -q -p no:randomly -n0 tests/test_h4_sessions_list_counts_nights.py

then read the diff and run the panel's test
(``ui/src/components/sequence/__tests__/sessionsPanelNightsDom.test.tsx``).
Never hand-edit it.

DISPLAY ONLY. Nothing schedules from the row: the resume arm, the engine and
CONTINUE read ``Session`` itself (``night_at``, ``is_armed``,
``set_aside_on``), and the one reader of ``SessionStore.list`` in the server
is the list route (`TestNothingSchedulesFromTheRow`).

MUTATIONS. Each was written over a byte backup of the file it changes, in a
private copy of ``server/`` and ``ui/`` (scratchpad ``H4-SESSIONS-mut``),
only the named tests were run there, and the file was restored and SHA-256
compared after every mutant. The failures are quoted as observed at each
test.
"""
from __future__ import annotations

import ast
import json
import os
import time
import uuid
from pathlib import Path

import pytest

import astrodeck.api.app as app_module
import astrodeck.events as events_mod
import astrodeck.hub as hub_module
import astrodeck.sequence.engine as engine_module
import astrodeck.sequence.report as report_mod
import astrodeck.sequence.session as session_mod
from _flow_night import ZONE_S, ZonedTime
from astrodeck.sequence.engine import _mint_report_id
from astrodeck.sequence.session import Session, report_night, session_store
from test_flows_continue import LR, rig  # noqa: F401 (fixture)

REPO = Path(__file__).resolve().parents[2]
FIXTURE = (REPO / "ui" / "src" / "components" / "sequence" / "__tests__"
           / "sessionsListNights.recorded.json")
REWRITE = os.environ.get("ASTRODECK_REWRITE_H4_SESSIONS_FIXTURE") == "1"

#: The flow's name, which the engine gives the plan and the session.
NAME = "M42 LR"
#: The ids the recording carries in place of the two the harness mints at
#: random, in the shape a real one has (uuid4 hex), so a reader that expects
#: one is not handed a sentinel.
SESSION_ID = uuid.uuid5(uuid.NAMESPACE_URL, "astrodeck:h4-sessions:session").hex
FLOW_ID = uuid.uuid5(uuid.NAMESPACE_URL, "astrodeck:h4-sessions:flow").hex


def _wall(y: int, mo: int, d: int, h: int, mi: int = 0) -> float:
    """The instant a wall clock in the pinned zone reads as that time."""
    return ZonedTime(time.time, ZONE_S).mktime((y, mo, d, h, mi, 0, 0, 0, -1))


#: Night one, 2026-09-20: a start in the evening and a restart in the small
#: hours after it, on the far side of the calendar midnight.
EVENING = _wall(2026, 9, 20, 21, 0)
SMALL_HOURS = _wall(2026, 9, 21, 1, 30)
#: Night two: the next evening.
NEXT_EVENING = _wall(2026, 9, 21, 20, 0)
NIGHT_ONE, NIGHT_TWO = "2026-09-20", "2026-09-21"


class _Clock:
    """The instant ``time()`` answers in the modules `clock` pins."""

    def __init__(self, t: float) -> None:
        self.t = t

    def now(self) -> float:
        return self.t


@pytest.fixture
def clock(monkeypatch) -> _Clock:
    """Pin the run's clock and every local-time reading on the path to the
    zone (see the module docstring)."""
    c = _Clock(EVENING)
    zoned = ZonedTime(c.now, ZONE_S)
    for mod in (engine_module, app_module, report_mod, session_mod):
        monkeypatch.setattr(mod, "time", zoned)
    monkeypatch.setattr(events_mod, "time", ZonedTime(time.time, ZONE_S))
    return c


async def _start(rig, clock: _Clock, fid: str, at: float) -> dict:
    """One run of the flow started at ``at`` through ``/run``: one frame
    banked, then the night ends incomplete, leaving the session dormant."""
    clock.t = at
    r = await rig.run(fid)
    assert r.status_code == 200, r.text
    rig.bank([0])
    await rig.end_night("incomplete")
    return r.json()["session"]


async def _row(rig, sid: str) -> dict:
    """The session's row, as ``GET /api/sessions`` serves it."""
    r = await rig.client.get("/api/sessions")
    assert r.status_code == 200, r.text
    (row,) = [x for x in r.json()["sessions"] if x["id"] == sid]
    return row


async def _card_nights(rig, fid: str) -> int:
    """The flow card's night count, the progress route's ``session.nights``."""
    r = await rig.client.get(f"/api/flows/{fid}/progress")
    assert r.status_code == 200, r.text
    return r.json()["session"]["nights"]


async def _one_night_twice(rig, clock: _Clock) -> tuple[str, str]:
    """A fresh run at 21:00 and a CONTINUE at 01:30: two starts, one night.
    Returns the flow's id and the session's."""
    fid = await rig.save_flow(LR, name=NAME)
    first = await _start(rig, clock, fid, EVENING)
    again = await _start(rig, clock, fid, SMALL_HOURS)
    assert (first["continued"], again["continued"]) == (False, True)
    assert again["id"] == first["id"], "premise: the restart continued it"
    s = session_store.load(first["id"])
    assert [report_night(rid) for rid in s.nights] == [NIGHT_ONE, NIGHT_ONE], (
        f"premise: two starts inside one night key: {s.nights}")
    return fid, first["id"]


# ============================================================= the row counts

class TestTheRowCountsObservingNights:
    async def test_two_starts_inside_one_night_read_one_night(self, rig,
                                                              clock):
        """Two starts of one session inside one night key: the row says one
        night, the count the flow card gives the same session.

        RED under mutant "row counts runs" (``SessionStore.list``'s row
        answering ``"nights": len(s.nights)``, the code before H4), observed:

            AssertionError: a restart the same night was counted as another
            night on the sessions list
            assert 2 == 1
        """
        fid, sid = await _one_night_twice(rig, clock)
        row = await _row(rig, sid)
        assert row["nights"] == 1, (
            "a restart the same night was counted as another night on the "
            "sessions list")
        assert row["nights"] == await _card_nights(rig, fid), (
            "the list and the flow card count one session's nights apart")

    async def test_control_a_start_the_next_evening_reads_two(self, rig,
                                                             clock):
        """The same session started again the next evening has run on two
        nights, and the row says 2 while it holds three runs.

        Not a control that cannot fail. RED under mutant "row says one night
        once it ran" (the row answering ``"nights": 1 if s.nights else 0``,
        which passes the case above), observed:

            AssertionError: the next evening's start was not counted as a
            second night
            assert 1 == 2

        and RED under mutant "row counts runs", observed:

            AssertionError: the next evening's start was not counted as a
            second night
            assert 3 == 2
        """
        fid, sid = await _one_night_twice(rig, clock)
        await _start(rig, clock, fid, NEXT_EVENING)
        s = session_store.load(sid)
        assert len(s.nights) == 3, "premise: three runs"
        assert report_night(s.nights[-1]) == NIGHT_TWO, "premise: next night"
        row = await _row(rig, sid)
        assert row["nights"] == 2, (
            "the next evening's start was not counted as a second night")
        assert row["nights"] == await _card_nights(rig, fid)

    def test_control_undated_runs_and_a_session_that_never_ran(
            self, tmp_path, monkeypatch, clock):
        """A run whose report id carries no stamp cannot be placed on a
        night, so it counts once and is never merged into another
        (``observing_nights``, as every run counted before #430): a
        hand-written or legacy session reads the count it always read. A
        session that never ran reads 0.

        RED under mutant "row counts dated nights only" (the row counting
        the distinct ``report_night`` of its ids, ``len({report_night(r) for
        r in s.nights} - {None})``, which passes both cases above), observed:

            AssertionError: the rows' nights, legacy / mixed / never run
            assert [0, 1, 0] == [2, 2, 0]
              At index 0 diff: 0 != 2

        RED under mutant "row counts runs", observed:

            AssertionError: the rows' nights, legacy / mixed / never run
            assert [2, 3, 0] == [2, 2, 0]
              At index 1 diff: 3 != 2

        and RED under mutant "row says one night once it ran", observed:

            AssertionError: the rows' nights, legacy / mixed / never run
            assert [1, 1, 0] == [2, 2, 0]
              At index 0 diff: 1 != 2
        """
        monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path / "captures")
        dated = [_mint_report_id(NAME, EVENING, []),
                 _mint_report_id(NAME, SMALL_HOURS, [])]
        assert [report_night(r) for r in dated] == [NIGHT_ONE, NIGHT_ONE], (
            "premise: the engine's ids for two starts in one night")
        legacy = Session(name="legacy", nights=["night-1", "night-2"])
        mixed = Session(name="mixed", nights=["night-1", *dated])
        never = Session(name="never")
        for s in (legacy, mixed, never):
            session_store.save(s)
        by_id = {r["id"]: r for r in session_store.list()}
        got = [by_id[s.id]["nights"] for s in (legacy, mixed, never)]
        assert got == [2, 2, 0], (
            "the rows' nights, legacy / mixed / never run")


# ============================================================ the recording

def _text(body: dict) -> str:
    """The file's bytes: one-space indented, ASCII, keys in the order the
    answer has them (``test_s5_recorded_state._text``'s format)."""
    return json.dumps(body, indent=1, ensure_ascii=True) + "\n"


def _on_disk(path: Path) -> str:
    """The recorded file as text with its line ends read as ``\\n``: the
    repository runs with ``core.autocrlf=true``, and only the whitespace
    between JSON tokens can differ by it (#445). A missing file fails."""
    return path.read_bytes().decode("utf-8").replace("\r\n", "\n")


ABOUT = (
    "GET /api/sessions rows for one session of the flow 'M42 LR', recorded "
    "by server/tests/test_h4_sessions_list_counts_nights.py (#430, H4 task "
    "H4-SESSIONS). 'one_night': after a start at 21:00 and a restart at "
    "01:30, one observing night. 'next_evening': after a third start at "
    "20:00 the next evening. 'runs' is the session's run count, "
    "len(Session.nights), which the row does not carry. The session's and "
    "the flow's ids are fixed stand-ins; every other value is the route's. "
    "Rewrite with ASTRODECK_REWRITE_H4_SESSIONS_FIXTURE=1; never hand-edit.")


class TestThePanelReadsTheRowsAsRecorded:
    async def test_the_recorded_rows_are_the_routes(self, rig, clock):
        """The rows the panel's test renders are the ones the route serves
        for one night of two starts and for a third start the next evening.

        RED under mutant "row counts runs", observed:

            AssertionError: the file is not the route's answer; rewrite it
            (see the module docstring)
            assert '{\\n "about":...n  }\\n }\\n}\\n' ==
            '{\\n "about":...n  }\\n }\\n}\\n'
              Skipping 753 identical leading characters in diff, use -v to
              show
              Skipping 145 identical trailing characters in diff, use -v to
              show
              - "nights": 2,
              ?           ^
              + "nights": 1,

        and, with the file rewritten under that mutant (its ``one_night``
        row then says ``"nights": 2`` and its ``next_evening`` row 3), the
        panel's test goes red too, 1/3 passed; its test quotes the failure.

        RED under mutant "row says one night once it ran" (see the control
        above), at the ``next_evening`` row, observed:

            - "nights": 1,
            ?           ^
            + "nights": 2,
        """
        fid, sid = await _one_night_twice(rig, clock)
        one_night = await _row(rig, sid)
        runs_one = len(session_store.load(sid).nights)
        await _start(rig, clock, fid, NEXT_EVENING)
        next_evening = await _row(rig, sid)
        runs_next = len(session_store.load(sid).nights)
        for row in (one_night, next_evening):
            assert (row["origin"], row["origin_id"]) == ("flow", fid), (
                "premise: the flow's id is the only other random value")

        def fixed(row: dict) -> dict:
            return {**row, "id": SESSION_ID, "origin_id": FLOW_ID}

        body = {"about": ABOUT,
                "one_night": {"runs": runs_one, "row": fixed(one_night)},
                "next_evening": {"runs": runs_next,
                                 "row": fixed(next_evening)}}
        text = _text(body)
        if REWRITE:
            FIXTURE.write_bytes(text.encode("utf-8"))
        assert _on_disk(FIXTURE) == text, (
            "the file is not the route's answer; rewrite it (see the module "
            "docstring)")


# ====================================================== display only

class TestNothingSchedulesFromTheRow:
    def test_the_list_route_is_the_only_reader_of_the_rows(self):
        """The row is display only: nothing that decides what runs reads it.
        The resume arm, the engine and CONTINUE read ``Session`` (its
        ``night_at``, ``is_armed``, ``set_aside_on``), never a list row, so a
        change to how the row counts cannot move a night. Held here as the
        set of places in ``astrodeck/`` that touch ``session_store.list``:
        the list route and nothing else.

        RED under mutant "a scheduler reads the row" (``ResumeArm.tick``
        given a first line ``rows = session_store.list()``), observed:

            AssertionError: SessionStore.list is read outside the list
            route; its rows are display only (#430)
            assert [('api/app.py....py', 'tick')] ==
            [('api/app.py...st_sessions')]
              Left contains one more item: ('sequence/resume_arm.py', 'tick')
        """
        root = Path(hub_module.__file__).resolve().parent
        found = []
        for path in sorted(root.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            parents = {child: node for node in ast.walk(tree)
                       for child in ast.iter_child_nodes(node)}
            for node in ast.walk(tree):
                if (isinstance(node, ast.Attribute) and node.attr == "list"
                        and isinstance(node.value, ast.Name)
                        and node.value.id == "session_store"):
                    fn = node
                    while fn is not None and not isinstance(
                            fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        fn = parents.get(fn)
                    found.append((path.relative_to(root).as_posix(),
                                  fn.name if fn is not None else "<module>"))
        assert found == [("api/app.py", "list_sessions")], (
            "SessionStore.list is read outside the list route; its rows are "
            "display only (#430)")
