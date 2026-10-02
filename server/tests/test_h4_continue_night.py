# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""CONTINUE's night comes from the route that has the clock (#511; spec 5.9,
S7 orchestrator ruling 7; the "two readers of one count that decide it
differently" class).

THE DEFECT. Since S7 the progress route's ``session.nights`` counts the
OBSERVING nights a session has run, and CONTINUE's own answer, the run
route's ``session.night``, is ``Session.night_at(now)``: that count, plus one
only when tonight is not already among them. The RUN button printed
``nights + 1`` because the progress route carried no clock, so on a night
the session had already run (a second CONTINUE in the same evening, or a
manual CONTINUE after a crash) the button read one night more than the run
route answered and the run's log line said.

AS BUILT. The progress route's session carries ``continue_night``, which
``progress.continue_night`` answers with ``Session.night_at`` over the clock
``app.py`` read for the request and handed in: the run route's own rule
over the same kind of clock, so the two cannot disagree. It is carried only
while the session is dormant, the one case ``run_flow`` continues, as a
panel carries ``locked_angle`` only where there is a lock. The button prints
it (``ui/src/components/flows/runCopy.ts``).

THE HARNESS is test_flows_progress_route.py's: the real app on the test's
own loop, the real ``SequenceEngine.start``, ledger write and finalize, with
only ``_run`` (the imaging loop) replaced by a night the test ends. The
engine's and the app's ``time`` are pinned to local wall-clock evenings, so
the night key reads the same nights in any zone (Pacific on the dev box, UTC
in CI).

Every mutant was applied in a private copy of ``server/`` (scratchpad
``H4-ROUTES-B-mut``), from a byte backup restored and sha256-checked after
each, never in the shared tree (#254). The observed failure is quoted
verbatim (the first assertion line, long lines wrapped).
"""
from __future__ import annotations

import astrodeck.api.app as app_module
import astrodeck.sequence.engine as engine_module
from astrodeck.flows.progress import continue_night
from astrodeck.sequence.models import SequencePlan
from astrodeck.sequence.session import Session
from test_flows_progress_route import (LR, _bank, _Clock, _local,  # noqa: F401
                                       _Night, _restore_provider, rig)


async def _run(api, fid: str) -> dict:
    """Press RUN (CONTINUE when the flow's session is dormant) and answer
    the run route's ``session``."""
    r = await api.client.post(f"/api/flows/{fid}/run", json={})
    assert r.status_code == 200, r.text
    return r.json()["session"]


async def _end(engine, night, reason: str = "incomplete") -> None:
    night.end(reason)
    await engine._task


class TestContinueNightIsTheRunRoutes:
    async def test_the_same_evening_reads_the_route_and_the_next_one_more(
            self, rig, monkeypatch):
        """Night one is run at 21:00 and stopped. At 23:00 the same evening
        the progress route says CONTINUE would start night 1, and CONTINUE,
        pressed then, answers night 1. That run is stopped too, and the next
        evening the progress route says night 2, and CONTINUE answers night
        2. At every read ``continue_night`` IS the number the run route then
        answers, and on the same evening it is not ``nights + 1``.

        RED under mutant "continue_night = nights + 1"
        (``progress.continue_night`` answering
        ``len(session.observing_nights()) + 1``), observed:

            AssertionError: the same evening: the progress route says
            CONTINUE starts night 2
            assert 2 == 1

        RED under mutant "tonight is never among them"
        (``progress.continue_night`` answering
        ``len(session.observing_nights())``, the other half of the rule
        dropped), at the next evening, observed:

            AssertionError: the next evening: the progress route says
            CONTINUE starts night 1
            assert 1 == 2

        RED under mutant "progress reads its own clock"
        (``progress.continue_night`` asking ``night_at(time.time())``, the
        clock ``app.py`` hands in ignored), observed:

            AssertionError: the same evening: the progress route says
            CONTINUE starts night 2
            assert 2 == 1

        (the real clock was 2026-09-29, a night the session had not run, so
        the pinned evening was never asked about).

        RED under mutant "the route hands in yesterday's clock"
        (``get_flow_progress`` handing ``time.time() - 86400.0`` in place of
        ``time.time()``), observed:

            AssertionError: the same evening: the progress route says
            CONTINUE starts night 2
            assert 2 == 1

        RED under mutant "no CONTINUE night on the route" (``app.py``'s
        ``continue_night`` line removed), observed:

            KeyError: 'continue_night'
        """
        api, engine, night = rig
        fid = await api.save_flow(LR)
        clock = _Clock(_local(2026, 9, 20, 21, 0))
        monkeypatch.setattr(engine_module, "time", clock)
        monkeypatch.setattr(app_module, "time", clock)

        first = await _run(api, fid)
        assert (first["continued"], first["night"]) == (False, 1), (
            f"premise: a fresh session's first run is night 1: {first}")
        _bank(engine, [0])
        await _end(engine, night)

        clock.t = _local(2026, 9, 20, 23, 0)
        same = (await api.ok(fid))["session"]
        assert (same["status"], same["nights"]) == ("dormant", 1), (
            f"premise: dormant after one observing night: {same}")
        assert same["continue_night"] == 1, (
            f"the same evening: the progress route says CONTINUE starts "
            f"night {same['continue_night']}")
        pressed = await _run(api, fid)
        assert pressed["continued"] is True, (
            f"premise: the press continued the session: {pressed}")
        assert pressed["night"] == same["continue_night"], (
            f"the same evening: the run route answered night "
            f"{pressed['night']} where the progress route said "
            f"{same['continue_night']}")
        assert pressed["night"] != same["nights"] + 1, (
            "premise: on the same evening nights + 1 is not the run route's "
            "night, so this case can tell the two rules apart")
        await _end(engine, night)

        clock.t = _local(2026, 9, 21, 21, 0)
        nxt = (await api.ok(fid))["session"]
        assert (nxt["status"], nxt["nights"]) == ("dormant", 1), (
            f"premise: two runs on one evening are one observing night: "
            f"{nxt}")
        assert nxt["continue_night"] == 2, (
            f"the next evening: the progress route says CONTINUE starts "
            f"night {nxt['continue_night']}")
        pressed = await _run(api, fid)
        assert pressed["night"] == nxt["continue_night"], (
            f"the next evening: the run route answered night "
            f"{pressed['night']} where the progress route said "
            f"{nxt['continue_night']}")
        await _end(engine, night)

    async def test_control_no_night_for_a_session_continue_would_not_reach(
            self, rig, monkeypatch):
        """CONTROLS, the cases in which CONTINUE is not what RUN does: while
        the run is live the session is active and carries no key (RUN stops
        it); once the run completes the session is complete and carries none
        (RUN starts a fresh session); a flow that never ran has no session
        at all. The dormant read between them carries a number, so this is
        not a key that is never sent.

        RED under mutant "a night for any session"
        (``progress.continue_night``'s dormant test removed), observed:

            AssertionError: live: no CONTINUE night while the run is the
            rig's
            assert 'continue_night' not in {'armed': False,
            'continue_night': 1, 'count_mode': 'accepted', 'id':
            '22cb98d9a38745e1ae49eb441ef07109', ...}
        """
        api, engine, night = rig
        never = await api.save_flow(LR)
        assert (await api.ok(never))["session"] is None, (
            "a flow that never ran has no session, and so no night")

        fid = await api.save_flow(LR)
        clock = _Clock(_local(2026, 9, 20, 21, 0))
        monkeypatch.setattr(engine_module, "time", clock)
        monkeypatch.setattr(app_module, "time", clock)
        await _run(api, fid)
        live = (await api.ok(fid))["session"]
        assert live["status"] == "active", f"premise: live: {live}"
        assert "continue_night" not in live, (
            "live: no CONTINUE night while the run is the rig's")
        await _end(engine, night)
        dormant = (await api.ok(fid))["session"]
        assert dormant["continue_night"] == 1, (
            f"premise: the dormant read carries a night: {dormant}")

        clock.t = _local(2026, 9, 21, 21, 0)
        await _run(api, fid)
        _bank(engine, [0, 0, 0, 1, 1])
        await _end(engine, night, "complete")
        done = (await api.ok(fid))["session"]
        assert done["status"] == "complete", f"premise: complete: {done}"
        assert "continue_night" not in done, (
            "complete: RUN starts a fresh session, so there is no CONTINUE "
            "night to state")


class TestTheRule:
    def test_it_is_night_at_over_the_clock_it_is_handed(self):
        """The pure half, over report ids stamped the way the engine mints
        them: two runs on the night of 2026-09-28 (22:15, and 01:30 after
        midnight), so one observing night. Asked at 23:00 that night it is
        night 1; asked the next evening, night 2. The clock is the argument,
        so the same session answers both.

        RED under mutant "continue_night = nights + 1", observed:

            AssertionError: the same night
            assert 2 == 1

        and under mutant "tonight is never among them", observed:

            AssertionError: the next evening
            assert 1 == 2
        """
        s = Session(status="dormant", plan=SequencePlan(name="p", targets=[]),
                    nights=["m31-20260928-221500", "m31-20260929-013000"])
        assert len(s.observing_nights()) == 1, "premise: one observing night"
        assert continue_night(s, _local(2026, 9, 28, 23, 0)) == 1, (
            "the same night")
        assert continue_night(s, _local(2026, 9, 29, 21, 0)) == 2, (
            "the next evening")
        assert continue_night(s, _local(2026, 9, 28, 23, 0)) \
            == s.night_at(_local(2026, 9, 28, 23, 0)), (
                "the run route's rule, asked of the same clock")
