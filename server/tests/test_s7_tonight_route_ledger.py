# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The Tonight route folds the real reports, not their summaries (#419).

``GET /api/flows/{id}/tonight`` hands ``resolve_tonight`` two ledgers: the
BUDGET row's hours per filter (``banked_hours_from_reports``) and the
CAMPAIGN tab's accepted frames per target and filter
(``frames_by_target_from_reports``). Both folds read each report's
``by_filter`` and ``targets``. The route folded ``list_reports()``, whose
summaries carry eight scalars and neither of those, so both folds answered
``{}`` on every flow: "0 h banked" on every goal and every campaign member at
0, whatever the archive held. The unit tests of the folds hand them dicts of
a shape ``list_reports`` never returns, so nothing graded them on the input
the route really gives them (a test double hiding the code under test).

So this file writes a REAL report through ``SessionReporter``, the engine's
writer (four accepted 900 s Ha frames of M31, finalized), and reads the
answer through the route: the BUDGET row banks 1.0 h of Ha, and the CAMPAIGN
member M31 has four cycles of a one-slot Ha cycle while M33 has none. Each
listed report is loaded (``SessionReporter.load``) inside the worker thread
``resolve_tonight`` runs on, never on the event loop, and once for both
folds.

SINCE H4 (#536, H4 orchestrator ruling 6) the route folds each report's
LEDGER summary, which ``finalize`` writes beside it and which carries the
same ``by_filter`` and ``targets`` numbers the folds read (not the eight
scalars of ``list_reports``, which are still not folded). A report with no
current summary is read in full once and summarised. So the answers below
did not move; the read the last case spies did (see its docstring).

THE HARNESS is ``test_flows_progress_route.py``'s ``api``: the real app over
ASGI, a throwaway config store swept into every module, flow library and
captures directory. The route is ``CAP_VIEW_SITE_DERIVED`` and answers only
for a configured site, so a synthetic one is set (that file's ``SITE_A``).

MUTATIONS. Each was written over a byte backup of ``api/app.py`` in a
private copy of ``server/`` (scratchpad ``s7-session-mut``), only this file
was run there, and the copy was restored and SHA-256 compared after every
mutant. The failures are quoted as observed.
"""
from __future__ import annotations

import asyncio

import astrodeck.sequence.report as report_mod
from astrodeck.config import Site
from astrodeck.sequence.models import SequencePlan
from astrodeck.sequence.report import FrameRecord, SessionReporter
from test_flows_progress_route import (SITE_A, _restore_provider,  # noqa: F401
                                       api)

#: A POOL of M31 and M33 feeding a one-slot FILTER CYCLE of 900 s Ha, one sub
#: a pass and four passes: a member's banked cycles are its Ha subs, and the
#: cycle's BUDGET row banks what the ledger holds in Ha.
POOL_HA = {
    "nodes": [
        {"id": "p", "type": "pool", "x": 0, "y": 0,
         "params": {"members": "M31, M33", "quota": 4, "minAlt": 0,
                    "moonSep": 0, "maxHA": 0}},
        {"id": "y", "type": "cycle", "x": 100, "y": 0,
         "params": {"plan": "Ha 900", "cycles": 4, "perCycle": 1,
                    "gain": 100, "bin": "1"}}],
    "edges": [{"from": "p", "fromPort": "target", "to": "y",
               "toPort": "run"}]}


def _write_report() -> str:
    """Four accepted 900 s Ha subs of M31, through the engine's own report
    writer, finalized; the report's id."""
    rep = SessionReporter(SequencePlan(name="M31 campaign"),
                          started_at=1_790_000_000.0)
    for i in range(4):
        rep.record_frame(FrameRecord(ts=1_790_000_000.0 + 900.0 * i,
                                     target="M31", filter="Ha",
                                     exposure_s=900.0, accepted=True))
    rep.finalize("complete")
    return rep.id


async def _tonight(api, fid: str) -> dict:
    api.store.set_site(Site(name="fixture", latitude=SITE_A[0],
                            longitude=SITE_A[1], elevation_m=10.0,
                            is_default=False))
    r = await api.client.get(f"/api/flows/{fid}/tonight")
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["ok"] is True, f"premise: the site is set: {got['reason']}"
    return got


class TestTheRouteFoldsTheReports:
    async def test_budget_banks_the_hours_and_the_campaign_counts_them(
            self, api):
        """Through the route: the Ha row of BUDGET says 1.0 h banked (four
        900 s subs), and CAMPAIGN counts four cycles for M31 and none for
        M33, so the frames went to the member they were shot of.

        RED under mutant "folds list_reports summaries" (the route's
        ``reports()`` answering ``tuple(SessionReporter.list_reports())``,
        the code before #419), observed:

            assert 0.0 == 1.0

        which is #419 as filed: a ledger that was read (``has_ledger``
        true) and answered nothing.
        """
        rid = _write_report()
        assert SessionReporter.load(rid) is not None, (
            "premise: the report is on disk")
        assert [s["id"] for s in SessionReporter.list_reports()] == [rid]
        fid = await api.save_flow(POOL_HA)

        got = await _tonight(api, fid)

        (row,) = [b for b in got["budget"] if b["filter"] == "Ha"]
        assert row["has_ledger"] is True, "premise: the ledger was read"
        assert row["banked_h"] == 1.0
        members = {m["name"]: m["banked"] for m in got["campaign"]["members"]}
        assert members == {"M31": 4, "M33": 0}
        assert got["campaign"]["has_ledger"] is True

    async def test_each_report_is_loaded_once_off_the_loop(self, api,
                                                           monkeypatch):
        """The ledger is read once for both folds, in a thread with no event
        loop (the worker ``resolve_tonight`` runs on), and a report with no
        summary is read in full once there.

        RE-PINNED FOR H4 (#536, H4 orchestrator ruling 6). This case spied
        ``SessionReporter.load`` and wanted one call per report. The route
        now folds each report's ledger summary (``SessionReporter.summaries``,
        the same ``by_filter`` and ``targets`` numbers, written by
        ``finalize``), reads a report only when it has no current summary,
        and reads it through ``_read_at``, not ``load``; so the spy saw no
        call and the case failed with ``assert [] == [(rid, False)]``. It now
        spies the two things the claim is about: the ledger read
        (``summaries``, once for both folds) and the report read
        (``_read_at``), against a report whose summary is removed, so the
        route has a report to read. test_h4_report_summary_cost.py grades
        the summary itself and the reads it saves.

        Its three mutants as S7 recorded them, observed before H4:

        "loaded on the loop" (the reports loaded in the route handler before
        ``asyncio.to_thread``, and the BUDGET fold handed that tuple):

            AssertionError: assert [('M31_campai...71320', True)] ==
            [('M31_campai...1320', False)]

        "loaded per fold" (the ``functools.cache`` on ``reports`` removed):

            AssertionError: assert [('M31_campai...1320', False)] ==
            [('M31_campai...1320', False)]
              Left contains one more item: ('M31_campaign-20260921-071320',
              False)

        "folds list_reports summaries", which loads nothing:

            AssertionError: assert [] == [('M31_campai...1320', False)]

        And against the H4 route, re-run by the H4 integration in a private
        copy of ``server/`` (scratchpad ``H4-INTEG-mut``):

        "summaries read on the loop" (H4-ROUTES-A's: the route calling
        ``SessionReporter.summaries()`` before ``asyncio.to_thread`` and
        handing both folds the answer), observed:

            AssertionError: the ledger was not read once, off the loop:
            [True]
            assert [True] == [False]

        "summaries read per fold" (the ``functools.cache`` on the route's
        ``summaries`` removed), observed:

            AssertionError: the ledger was not read once, off the loop:
            [False, False]
            assert [False, False] == [False]

        (The report's id carries the dev box's local stamp of its start.)
        """
        # On a worker thread, where no loop runs: every snapshot is written
        # inline, so none is still rewriting the report (and its summary)
        # when the summary is removed below.
        rid = await asyncio.to_thread(_write_report)
        summary = report_mod._summary_path(rid)
        assert summary.is_file(), f"premise: finalize wrote {summary.name}"
        summary.unlink()
        fid = await api.save_flow(POOL_HA)

        def on_loop() -> bool:
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                return False
            return True

        real_summaries = SessionReporter.summaries
        real_read_at = SessionReporter._read_at
        ledgers: list[bool] = []
        reads: list[tuple[str, bool]] = []

        def summaries_spy():
            ledgers.append(on_loop())
            return real_summaries()

        def read_spy(path):
            reads.append((path.stem, on_loop()))
            return real_read_at(path)

        monkeypatch.setattr(SessionReporter, "summaries",
                            staticmethod(summaries_spy))
        monkeypatch.setattr(SessionReporter, "_read_at",
                            staticmethod(read_spy))
        got = await _tonight(api, fid)
        assert ledgers == [False], (
            f"the ledger was not read once, off the loop: {ledgers}")
        assert reads == [(rid, False)], (
            f"the report with no summary was not read once, off the loop: "
            f"{reads}")
        assert {m["name"]: m["banked"]
                for m in got["campaign"]["members"]}["M31"] == 4, (
            "premise: the spied ledger is the one the folds read")

    async def test_control_no_report_banks_nothing_and_says_it_looked(
            self, api):
        """With no report on disk the ledger is still a ledger: 0.0 h and 0
        cycles, ``has_ledger`` true. The fix did not turn an empty archive
        into "no ledger", which is a different sentence. A CONTROL: every
        mutant above leaves it green (1 passed under each), because with
        nothing on disk the summaries and the loaded reports fold alike.

        It can fail: under mutant "an empty archive is no ledger" (the
        route's ``reports()`` raising ``ValueError`` when it loads nothing,
        which both folds read as an unreadable ledger), run by the S7 review
        in a private copy of ``server/`` (scratchpad
        ``S7-REVIEW-tcf-mut``, from a byte backup, sha256 checked after),
        this case alone went red, observed:

            E       assert (False, None) == (True, 0.0)
            E         At index 0 diff: False != True
        """
        fid = await api.save_flow(POOL_HA)
        got = await _tonight(api, fid)
        (row,) = [b for b in got["budget"] if b["filter"] == "Ha"]
        assert (row["has_ledger"], row["banked_h"]) == (True, 0.0)
        assert {m["name"]: m["banked"]
                for m in got["campaign"]["members"]} == {"M31": 0, "M33": 0}
