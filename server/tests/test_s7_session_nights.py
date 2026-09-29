"""A session answers the observing nights it has run, not its runs (#430,
S7 orchestrator ruling 7; spec 5.9, Revision 2 ruling 7).

``Session.nights`` is the list of report ids, one per ``engine.start``, and
it stays that: it is the report index. Every reader that showed an operator a
NIGHT read its length, so a restart the same night read as the next night on
the progress card and in CONTINUE's answer. Now:

* EACH RUN IS KEYED AS THE NIGHT LOG KEYS IT. A run's night is
  ``events.night_key`` of the local start stamp its report id carries
  (``SessionReporter._make_id``), the key ``captures/logs/<night>.jsonl`` is
  named by: local noon to local noon. Derived from the id, so every session
  already on disk answers too, with nothing new written.
* ``Session.observing_nights()`` is the distinct keys, so a restart in the
  same night continues that night's entry.
* CONTINUE's ``night`` (``_continue_flow_session``) is that count, plus one
  only when tonight's key is not already counted (``Session.night_at``).
* The progress route's ``session.nights`` is the count of observing nights
  (it counted runs until S7).

THE CLOCK. The dev box runs on Pacific time and CI on UTC, so no instant here
is a literal epoch second: every one is built from LOCAL wall-clock fields
with ``time.mktime`` (``_local``), which is the machine's own zone, and every
case is a wall-clock time relative to local noon, the night key's rollover.
The same instant then reads the same local stamp, and the same night, in any
zone. The route cases pin ``time`` in the engine and the app modules to such
an instant (``_Clock``), so the report ids the engine mints and the tonight
the route asks about are both that wall-clock time.

MUTATIONS. Each was written over a byte backup of the file it changes in a
private copy of ``server/`` (scratchpad ``s7-session-mut``), only the named
tests were run there, and the copy was restored and SHA-256 compared after
every mutant. The failures are quoted as observed.
"""
from __future__ import annotations

import time

import pytest

import astrodeck.api.app as app_module
import astrodeck.sequence.engine as engine_module
from astrodeck import events
from astrodeck.sequence.report import SessionReporter
from astrodeck.sequence.session import Session, report_night, session_store
from test_flows_continue import LR, rig  # noqa: F401 (fixture)


def _local(y: int, mo: int, d: int, h: int, mi: int = 0, s: int = 0) -> float:
    """The epoch second at that LOCAL wall-clock time on this machine. The
    zone is the machine's, so the instant differs between the dev box and CI
    and the wall-clock time, which is what the night key reads, does not."""
    return time.mktime((y, mo, d, h, mi, s, 0, 0, -1))


def _rid(ts: float, name: str = "M31") -> str:
    """The report id the engine mints for a run started at ``ts``."""
    return SessionReporter._make_id(name, ts)


#: Night one: the evening of 2026-09-20 and the small hours after it.
EVENING = _local(2026, 9, 20, 21, 0)
LATE = _local(2026, 9, 20, 23, 0)
SMALL_HOURS = _local(2026, 9, 21, 1, 30)
BEFORE_DAWN = _local(2026, 9, 21, 3, 0)
#: Night two: the next evening.
NEXT_EVENING = _local(2026, 9, 21, 20, 0)
NEXT_LATE = _local(2026, 9, 21, 22, 0)
#: Night three.
THIRD_EVENING = _local(2026, 9, 22, 21, 0)


def _session(*starts: float) -> Session:
    return Session(nights=[_rid(ts) for ts in starts])


# ======================================================= the key of one run

class TestEachRunIsKeyedAsTheNightLogKeysIt:
    def test_the_key_is_events_night_key_of_the_start_on_both_sides_of_noon(
            self):
        """Across the rollover, one second either side of local noon, and in
        the evening and the small hours: the night a report id says is the
        night ``events.night_key`` gives its start instant, which is the
        night log's file name.

        RED under mutant "calendar date without the noon rollover"
        (``report_night`` answering the stamp's own calendar date,
        ``time.strftime("%Y-%m-%d", parsed)``, with no noon rollover),
        observed:

            AssertionError: assert ['2026-09-20'... '2026-09-21'] ==
            ['2026-09-19'... '2026-09-20']
              At index 0 diff: '2026-09-20' != '2026-09-19'
        """
        instants = [_local(2026, 9, 20, 11, 59, 59), _local(2026, 9, 20, 12),
                    _local(2026, 9, 20, 12, 0, 1), EVENING, SMALL_HOURS]
        got = [report_night(_rid(ts)) for ts in instants]
        assert got == [events.night_key(ts) for ts in instants]
        assert got == ["2026-09-19", "2026-09-20", "2026-09-20",
                       "2026-09-20", "2026-09-20"], (
            "premise: noon is the rollover, and the small hours belong to "
            "the evening before")

    def test_control_a_restarts_suffix_and_a_name_of_digits_parse(self):
        """``_mint_report_id`` suffixes ``-2`` for a second start in the same
        second, and a plan name may end in digits that look like a stamp:
        the stamp read is always the id's own, the last one.

        A control of the parse, not of the rollover, but its first case is
        a start in the small hours, so mutant "calendar date without the
        noon rollover" turns it red as well, observed:

            AssertionError: assert '2026-09-21' == '2026-09-20'
        """
        assert report_night(_rid(SMALL_HOURS) + "-2") == "2026-09-20"
        assert report_night(_rid(NEXT_EVENING, name="20260101-000000")) == \
            "2026-09-21"
        assert report_night(_rid(NEXT_EVENING, name="x 12345678")) == \
            "2026-09-21"

    def test_control_an_id_with_no_stamp_is_undated(self):
        """An id the engine did not mint (a hand-written test session, a
        legacy file) carries no start to read, so it has no night: None,
        never a guessed date."""
        assert report_night("night-1") is None
        assert report_night("") is None
        assert report_night("m31-20261399-250000") is None, (
            "a stamp that is no date is no stamp")


# ====================================================== the nights a session ran

class TestASessionCountsObservingNights:
    def test_two_runs_inside_one_night_are_one_night(self):
        """A run in the evening and a restart in the small hours after it are
        one observing night, and a CONTINUE later that same night is still
        night 1: the night the run log is already writing.

        RED under mutant "night is the run count" (``observing_nights``
        keying each run by its report id, ``key = rid``, one entry per run),
        observed:

            AssertionError: assert ['M31-2026092...60921-013000'] ==
            ['2026-09-20']
              At index 0 diff: 'M31-20260920-210000' != '2026-09-20'
              Left contains one more item: 'M31-20260921-013000'

        RED under mutant "calendar date without the noon rollover" (see
        above), observed:

            AssertionError: assert ['2026-09-20', '2026-09-21'] ==
            ['2026-09-20']
              Left contains one more item: '2026-09-21'

        RED under mutant "tonight always adds one" (see below), observed:

            AssertionError: assert 2 == 1
             +  where 2 = night_at(1789984800.0)

        (the epoch second is the dev box's, Pacific; it names 03:00 local.)
        """
        s = _session(EVENING, SMALL_HOURS)
        assert len(s.nights) == 2, "premise: two runs"
        assert s.observing_nights() == ["2026-09-20"]
        assert s.night_at(BEFORE_DAWN) == 1

    def test_a_run_the_next_evening_is_night_two(self):
        """The next evening's run is a second night. A CONTINUE later that
        evening is night 2, the one it is on, and one the evening after is
        night 3.

        RED under mutant "night is the run count", observed:

            AssertionError: assert ['M31-2026092...60921-200000'] ==
            ['2026-09-20', '2026-09-21']
              At index 0 diff: 'M31-20260920-210000' != '2026-09-20'
              Left contains one more item: 'M31-20260921-200000'

        RED under mutant "tonight always adds one", observed:

            AssertionError: assert 3 == 2
             +  where 3 = night_at(1790053200.0)

        RED under mutant "tonight is always counted" (``night_at``
        answering ``len(nights)``, never adding tonight), observed:

            AssertionError: assert 2 == 3
             +  where 2 = night_at(1790136000.0)
        """
        s = _session(EVENING, SMALL_HOURS, NEXT_EVENING)
        assert s.observing_nights() == ["2026-09-20", "2026-09-21"]
        assert s.night_at(NEXT_LATE) == 2
        assert s.night_at(THIRD_EVENING) == 3

    def test_continue_adds_a_night_only_when_tonight_is_not_counted(self):
        """The plus one of CONTINUE's night, on its own: a session with one
        night says 1 while that night lasts and 2 after the next noon.

        RED under mutant "tonight always adds one" (``night_at`` answering
        ``len(nights) + 1``, the rule CONTINUE had), observed:

            AssertionError: assert 2 == 1
             +  where 2 = night_at(1789970400.0)

        RED under mutant "tonight is always counted", observed:

            AssertionError: assert 1 == 2
             +  where 1 = night_at(1790046000.0)

        Mutant "night is the run count" fails here too, at the first line:
        ``assert 2 == 1``, ``where 2 = night_at(1789970400.0)``.
        """
        s = _session(EVENING)
        assert s.night_at(LATE) == 1
        assert s.night_at(NEXT_EVENING) == 2

    def test_control_undated_runs_count_as_they_always_did(self):
        """A run whose id carries no stamp cannot be placed on a night, so it
        is never merged into another: each counts once, as every run did
        before #430. Hand-written sessions in older tests (``"night-1"``,
        ``"night-2"``) therefore read the count they always read, and a
        session that never ran is night 1 on its first run.

        Not a control that cannot fail: mutant "night is the run count"
        reads the mixed session as three nights, observed ``AssertionError:
        assert 3 == 2``, ``where 3 = len(['night-1', 'M31-20260920-210000',
        'M31-20260920-230000'])``; and mutant "tonight is always counted"
        makes a first run night 0, observed ``AssertionError: assert 0 ==
        1``."""
        assert Session(nights=["night-1", "night-2"]).observing_nights() == [
            "night-1", "night-2"]
        mixed = Session(nights=["night-1", _rid(EVENING), _rid(LATE)])
        assert len(mixed.observing_nights()) == 2
        assert Session().observing_nights() == []
        assert Session().night_at(EVENING) == 1


# ================================================================ the routes

class _Clock:
    """``time`` as the engine and the app read it, with ``time()`` pinned to
    ``t`` and every other name the real module's. The engine mints its
    report id (and the session's ``created_ts``) from it, and CONTINUE asks
    it for tonight."""

    def __init__(self, t: float) -> None:
        self.t = t

    def time(self) -> float:
        return self.t

    def __getattr__(self, name: str):
        return getattr(time, name)


async def _nights(rig, fid: str) -> int:
    r = await rig.client.get(f"/api/flows/{fid}/progress")
    assert r.status_code == 200, r.text
    return r.json()["session"]["nights"]


class TestTheRoutesSayObservingNights:
    async def test_restarts_in_one_night_are_night_one_and_the_next_evening_two(
            self, rig, monkeypatch):
        """Through ``/run`` and the progress route, on the engine's own start
        and finalize: a fresh run at 21:00, a CONTINUE at 23:00 and another
        at 01:30 are all night 1, the card reads one night after three runs,
        and the CONTINUE the next evening is night 2, which the card then
        reads.

        RED under mutant "route night is the run count"
        (``_continue_flow_session`` answering ``night = len(s.nights) + 1``,
        the code before S7), observed:

            AssertionError: a restart the same night was called another
            assert [2, 3] == [1, 1]
              At index 0 diff: 2 != 1

        (mutant "night is the run count" in ``session.py`` fails at the
        same line with the same numbers, and "tonight always adds one" with
        ``assert [2, 2] == [1, 1]``.)

        RED under mutant "the card counts runs" (``flows/progress.py``'s
        ``_summary`` answering ``len(session.nights)``), observed:

            assert 3 == 1

        RED under mutant "calendar date without the noon rollover" (the
        01:30 run keyed to the next calendar date), observed:

            assert 2 == 1

        RED under mutant "tonight is always counted", at the next
        evening's CONTINUE, observed:

            assert 1 == 2
        """
        clock = _Clock(EVENING)
        monkeypatch.setattr(engine_module, "time", clock)
        monkeypatch.setattr(app_module, "time", clock)
        fid = await rig.save_flow(LR)

        r = await rig.run(fid)
        assert r.status_code == 200, r.text
        assert r.json()["session"]["continued"] is False
        sid = r.json()["session"]["id"]
        rig.bank([0])
        await rig.end_night("incomplete")

        said = []
        for t in (LATE, SMALL_HOURS):
            clock.t = t
            r = await rig.run(fid)
            assert r.status_code == 200, r.text
            out = r.json()["session"]
            assert (out["id"], out["continued"]) == (sid, True), out
            said.append(out["night"])
            rig.bank([0])
            await rig.end_night("incomplete")
        assert len(session_store.load(sid).nights) == 3, "premise: three runs"
        assert said == [1, 1], "a restart the same night was called another"
        assert await _nights(rig, fid) == 1

        clock.t = NEXT_EVENING
        r = await rig.run(fid)
        assert r.status_code == 200, r.text
        assert r.json()["session"]["night"] == 2
        assert await _nights(rig, fid) == 2
