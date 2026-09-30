"""The recoverable route says why the run stopped, and the card words only
that (#487; the claim-nothing-keeps class: a sentence stating a cause the
data behind it does not carry).

THE DEFECT. ``GET /api/sequence/recoverable`` answered which session RECOVER
would resume and nothing about why it went dormant, and SESSION / NOW's
interrupted-run card (``ui/src/next/hubs/session/now/Interrupted.tsx``)
printed "The server restarted; the frames on disk are intact." after every
ending. After an operator's STOP of a run that had banked two subs, the card
told them the rig had restarted. ``SessionStore.recoverable`` returns the
newest dormant session with frames whatever made it dormant, so every
operator abort of a run with a frame reproduced it.

AS BUILT. The route adds ``end_reason`` (``app._why_dormant``), from the
session's last report (``session.nights[-1]``), read off the loop:

* the report's own word, verbatim, when the engine's finalize stamped one:
  "aborted" for a STOP, "incomplete", "dawn_cutoff", "error" and the rest;
* ``RESTART_END_REASON`` ("restart") for THE EVIDENCE A RESTART LEAVES, which
  is two traces together and nothing else: the last report is on disk but
  records no ending (``engine.start`` writes it at the start, #517, and only
  ``_finalize_report`` stamps an ending, which a process stopped under the
  run never reaches), and the session carries the boot sweep's count of the
  death (``SessionStore.boot_sweep`` turns a session still ``active`` at boot
  dormant and adds one to ``crash_resumes``);
* null otherwise: no report id, a report missing, unreadable or refused by
  the ACL layer (the card still answers), or an unfinalized report on a
  session nobody swept.

The card words "aborted" as "You stopped it", the restart word as "The
server restarted", and states no cause for anything else
(ui/src/next/hubs/session/__tests__/nowDom.test.tsx).

THE HARNESS is test_flows_progress_route.py's ``rig``: the real app on the
test's own loop, the real ``SequenceEngine.start``, ledger write, abort and
finalize, with only ``_run`` (the imaging loop) replaced by a night the test
ends. A process death is what it leaves on disk: the run task is cancelled
without a finalize (the stand-in finalizes only when the test ends its
night), so the session file is still ``active`` and the report records no
ending, and then ``session_store.boot_sweep()`` runs, the call the app's
lifespan makes at boot.

Every mutant was applied in a private copy of ``server/`` (scratchpad
``H4-ROUTES-B-mut``), from a byte backup restored and sha256-checked after
each, never in the shared tree (#254). The observed failure is quoted
verbatim (the first assertion line, long lines wrapped).
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

import astrodeck.api.app as app_module
import astrodeck.sequence.report as report_mod
from astrodeck.api.app import RESTART_END_REASON, _why_dormant
from astrodeck.persist import write_json_atomic
from astrodeck.sequence.models import SequencePlan
from astrodeck.sequence.report import SessionReporter
from astrodeck.sequence.session import Session, SessionFrame, session_store
from astrodeck.windows_acl import PrivateAclError
from test_flows_progress_route import (LR, _bank, _Night,  # noqa: F401
                                       _restore_provider, rig)

INTERRUPTED_TSX = (Path(__file__).resolve().parents[2] / "ui" / "src" / "next"
                   / "hubs" / "session" / "now" / "Interrupted.tsx")


async def _recoverable(api) -> dict:
    r = await api.client.get("/api/sequence/recoverable")
    assert r.status_code == 200, r.text
    return r.json()


async def _run_two_subs(api, engine) -> str:
    """A flow run that has banked two subs (L, L of LR's five): the run
    #487 was seen on, stopped after two. Answers the session id."""
    fid = await api.save_flow(LR)
    r = await api.client.post(f"/api/flows/{fid}/run", json={})
    assert r.status_code == 200, r.text
    _bank(engine, [0, 0])
    return engine._session.id


async def _die(engine) -> None:
    """The process stops under the run: the run task ends with no finalize,
    so nothing on disk records an ending, as after a power cut or a kill."""
    engine._task.cancel()
    try:
        await engine._task
    except asyncio.CancelledError:
        pass
    assert not engine.running, "premise: the run is gone"


class TestTheRouteSaysWhy:
    async def test_an_abort_after_two_subs_says_aborted(self, rig):
        """STOP pressed after two banked subs: the session is dormant with
        two frames, recoverable, and the route says "aborted", the word the
        engine's finalize stamped on its last report. Not the restart word.

        RED under mutant "end_reason not carried" (the route's answer without
        its ``end_reason`` key, the route as it was before #487), observed:

            KeyError: 'end_reason'

        RED under mutant "the first report, not the last" (``s.nights[0]``
        read in place of ``s.nights[-1]``), in the restart case below, whose
        session holds an aborted run before the one the process died under,
        observed:

            AssertionError: a restart after an earlier STOP reads 'aborted'
            assert 'aborted' == 'restart'
        """
        api, engine, _night = rig
        sid = await _run_two_subs(api, engine)
        r = await api.client.post("/api/sequence/abort")
        assert r.status_code == 200, r.text
        s = session_store.load(sid)
        assert (s.status, len(s.frames), s.auto_resume) == (
            "dormant", 2, False), (
            f"premise: STOP left the session dormant, two frames, disarmed: "
            f"{(s.status, len(s.frames), s.auto_resume)}")
        got = await _recoverable(api)
        assert (got["recoverable"], got["session_id"], got["frames_done"]) \
            == (True, sid, 2), f"premise: this session is recoverable: {got}"
        assert got["end_reason"] == "aborted", (
            f"a STOP after two subs reads {got['end_reason']!r}")
        assert got["end_reason"] != RESTART_END_REASON

    async def test_control_a_boot_sweep_restart_says_restart(self, rig):
        """CONTROL: the process dies under the run and the server boots. The
        session file is still ``active``, the last report records no ending,
        and the boot sweep turns the session dormant and counts the death.
        The route says the restart word. The session also holds an earlier
        run that a STOP ended, so the answer is the LAST report's.

        RED under mutant "a restart needs no sweep" (``_why_dormant``
        answering the restart word for any report with no ending, the
        ``crash_resumes`` test dropped), in the control after this one,
        observed:

            AssertionError: an unfinalized report on a session nobody swept
            reads 'restart'
            assert 'restart' is None

        RED under mutant "no ending is no cause" (``_why_dormant`` answering
        None for a report with no ending, whatever the sweep counted),
        observed:

            AssertionError: a restart after an earlier STOP reads None
            assert None == 'restart'
        """
        api, engine, _night = rig
        sid = await _run_two_subs(api, engine)
        r = await api.client.post("/api/sequence/abort")
        assert r.status_code == 200, r.text
        fid = session_store.load(sid).origin_id
        r = await api.client.post(f"/api/flows/{fid}/run", json={})
        assert r.status_code == 200, r.text
        assert engine._session.id == sid, "premise: CONTINUE continued it"
        _bank(engine, [1])
        await _die(engine)
        on_disk = session_store.load(sid)
        assert on_disk.status == "active", (
            f"premise: a dead process leaves the session active: "
            f"{on_disk.status}")
        assert len(on_disk.nights) == 2, "premise: two runs, the STOP first"
        first = SessionReporter.read(on_disk.nights[0]).report
        last = SessionReporter.read(on_disk.nights[-1]).report
        assert (first.end_reason, last is not None and last.end_reason) == (
            "aborted", None), (
            "premise: the first run's report says aborted and the last's, "
            "on disk since its start, records no ending")

        assert session_store.boot_sweep() == 1, "premise: the boot sweep"
        swept = session_store.load(sid)
        assert (swept.status, swept.crash_resumes) == ("dormant", 1), (
            f"premise: swept dormant, the death counted: "
            f"{(swept.status, swept.crash_resumes)}")
        got = await _recoverable(api)
        assert got["session_id"] == sid, f"premise: recoverable: {got}"
        assert got["end_reason"] == RESTART_END_REASON, (
            f"a restart after an earlier STOP reads {got['end_reason']!r}")

    async def test_control_other_endings_verbatim_and_no_evidence_none(
            self, rig):
        """CONTROLS: a night the run ended itself ("incomplete") is carried
        verbatim, not read as a STOP or a restart; and a dormant session
        whose last report records no ending but which no boot sweep counted
        (a final write that failed, a file edited by hand) is no evidence of
        a restart: null. So is a session whose report is missing, and one
        with no run at all.

        RED under mutant "a restart needs no sweep" (see above), observed:

            AssertionError: an unfinalized report on a session nobody swept
            reads 'restart'
            assert 'restart' is None

        RED under mutant "any recorded ending reads as a STOP"
        (``_why_dormant`` answering "aborted" for any report that recorded
        an ending), observed:

            AssertionError: the night's own ending reads 'aborted'
            assert 'aborted' == 'incomplete'
        """
        api, engine, night = rig
        sid = await _run_two_subs(api, engine)
        night.end("incomplete")
        await engine._task
        got = await _recoverable(api)
        assert got["session_id"] == sid, f"premise: recoverable: {got}"
        assert got["end_reason"] == "incomplete", (
            f"the night's own ending reads {got['end_reason']!r}")

        plan = engine.plan
        t = plan.targets[0]
        frame = SessionFrame(night="x", target_id=t.id, step_id=t.steps[0].id,
                             auto_accepted=True)
        rid = "unswept-20260920-210000"
        rep = SessionReporter(SequencePlan(name="unswept"), report_id=rid,
                              started_at=1.0)
        # On a thread with no loop, where the reporter writes inline: the
        # write ``engine.start`` makes at a run's start (#517), and no more.
        await asyncio.to_thread(rep.record_policy, {})
        assert SessionReporter.read(rid).report.end_reason is None, (
            "premise: a report on disk that records no ending")
        cases = {
            "an unfinalized report on a session nobody swept": [rid],
            "a report that is not on disk": ["missing-20260920-210000"],
            "a session with no run": [],
        }
        for i, (what, nights) in enumerate(cases.items()):
            s = Session(name=what, created_ts=10.0 + i,
                        updated_ts=9_000_000_000.0 + i, status="dormant",
                        plan=plan, nights=nights, frames=[frame],
                        crash_resumes=0)
            path = session_store._path(s.id)
            write_json_atomic(path, s.model_dump(), backup=False)
            got = await _recoverable(api)
            assert got["session_id"] == s.id, (
                f"premise: {what} is the newest recoverable: {got}")
            assert got["end_reason"] is None, (
                f"{what} reads {got['end_reason']!r}")

    async def test_control_a_refused_read_still_answers_the_card(
            self, rig, monkeypatch):
        """CONTROL: the ACL layer refuses the report read. ``read_json``
        raises ``PrivateAclError``, which ``SessionReporter.read`` lets
        through rather than call "unreadable" (#370). The route still answers
        the recoverable session, with no cause: until #487 it never read a
        report, so the read it now makes must not become a way for the card,
        and RESUME with it, to vanish. A cause that cannot be read is a cause
        not stated.

        RED under mutant "a refused read fails the card" (the route's
        ``try``/``except`` around the read removed, the call left as it is),
        run in scratchpad ``H4-ROUTES-B-verify-mut``, observed:

            astrodeck.windows_acl.PrivateAclError: refused for the test
        """
        api, engine, _night = rig
        sid = await _run_two_subs(api, engine)
        r = await api.client.post("/api/sequence/abort")
        assert r.status_code == 200, r.text
        assert (await _recoverable(api))["end_reason"] == "aborted", (
            "premise: read, the same report says aborted")
        refused: list[object] = []

        def refuse(path):
            refused.append(path)
            raise PrivateAclError("refused for the test")
        monkeypatch.setattr(report_mod, "read_json", refuse)
        got = await _recoverable(api)
        assert refused, "premise: the route asked for the report and was refused"
        assert (got["recoverable"], got["session_id"], got["frames_done"],
                got["end_reason"]) == (True, sid, 2, None), (
            f"a refused read reads {got}")


class TestOffTheLoop:
    async def test_the_report_is_read_off_the_event_loop(self, rig,
                                                         monkeypatch):
        """``SessionReporter.read`` retries a refused read with sleeps
        (#370), so on the loop it would stall every other request and the
        engine's own ticks (#477). The route reads it on a worker thread.

        RED under mutant "read on the loop" (``SessionReporter.read(...)``
        called directly in place of ``asyncio.to_thread``), observed:

            AssertionError: the report was read on the event loop
            assert [True] == [False]
        """
        api, engine, _night = rig
        await _run_two_subs(api, engine)
        r = await api.client.post("/api/sequence/abort")
        assert r.status_code == 200, r.text
        seen: list[bool] = []
        real = report_mod._read_report_file

        def spy(path):
            try:
                asyncio.get_running_loop()
                seen.append(True)
            except RuntimeError:
                seen.append(False)
            return real(path)
        monkeypatch.setattr(report_mod, "_read_report_file", spy)
        got = await _recoverable(api)
        assert got["end_reason"] == "aborted", f"premise: read: {got}"
        assert seen == [False], "the report was read on the event loop"


class TestTheCardReadsTheseWords:
    def test_the_card_keys_its_sentences_on_the_routes_words(self):
        """The card's two cause words are the route's: "aborted", which the
        engine's finalize stamps on a STOP, and ``RESTART_END_REASON``.
        Read off Interrupted.tsx's exported constants, so a word renamed on
        one side reads here and not as a card that silently stopped saying
        either sentence.

        RED under mutant "the restart word renamed on the server"
        (``RESTART_END_REASON = "server_restart"``), observed:

            AssertionError: the card's restart word is 'restart', the
            route's 'server_restart'
            assert 'restart' == 'server_restart'
        """
        src = INTERRUPTED_TSX.read_text(encoding="utf-8")
        words = dict(re.findall(
            r'export const (END_REASON_[A-Z]+) = "([a-z_]+)";', src))
        assert set(words) == {"END_REASON_ABORTED", "END_REASON_RESTART"}, (
            f"premise: the card exports its two words: {words}")
        assert words["END_REASON_RESTART"] == RESTART_END_REASON, (
            f"the card's restart word is {words['END_REASON_RESTART']!r}, "
            f"the route's {RESTART_END_REASON!r}")
        assert words["END_REASON_ABORTED"] == "aborted", (
            "the card's STOP word is not the finalize's")

    def test_the_rule_by_itself(self):
        """``_why_dormant`` over reports built in memory: the report's word
        wins over the sweep's count (an in-process error also counts one),
        the restart word needs both traces, and no report is no cause.

        RED under mutant "a restart needs no sweep", observed:

            AssertionError: assert 'restart' is None

        under mutant "no ending is no cause", observed:

            AssertionError: assert None == 'restart'

        and under mutant "any recorded ending reads as a STOP", observed:

            AssertionError: assert 'aborted' == 'error'
        """
        swept = Session(crash_resumes=1)
        unswept = Session(crash_resumes=0)
        ended = report_mod.SessionReport(id="r", end_reason="error")
        open_ = report_mod.SessionReport(id="r")
        assert _why_dormant(swept, ended) == "error"
        assert _why_dormant(swept, open_) == RESTART_END_REASON
        assert _why_dormant(unswept, open_) is None
        assert _why_dormant(swept, None) is None
        assert app_module.RESTART_END_REASON == "restart"
