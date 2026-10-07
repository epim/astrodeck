# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Tonight's BUDGET counts this flow's own targets (#536, H4 orchestrator
ruling 6).

``GET /api/flows/{id}/tonight`` folds the session ledger into the BUDGET row's
"banked" hours. Since #419 it folded every report in the archive, and
``banked_hours_from_reports`` was called with no ``targets``: every hour of Ha
in ``captures/reports`` filled the Ha bar of whichever flow was open, so an
M31 flow read M16's night as its own progress. The ruling: BUDGET counts only
report rows whose target names are this flow's own, the names its run records
frames under (``tonight.flow_target_names``: the compiled targets, a mosaic's
by panel), and the row says "for these targets" on both Tonight surfaces.

WHAT IS GRADED HERE:

* through the route, with real reports written by the engine's own writer
  (``SessionReporter``, finalized): an M31 flow beside an M16 Ha report
  banks 0 h of Ha, M31's own report counts, and a mosaic counts its live
  panels and neither a bare "M31" nor a skipped panel;
* the names are the ones ``to_sequence_plan`` gives the run's targets, and
  the route folds with exactly them;
* the BUDGET sentence says "for these targets", and the answer both UIs'
  tests read (``tonight_budget_for_these_targets.json``, below) is the
  server's own, kept equal to ``resolve_tonight``'s answer here;
* the route is still ``CAP_VIEW_SITE_DERIVED``.

THE UI FIXTURE. ``ui/src/next/hubs/session/flows/tonight/__tests__/
tonight_budget_for_these_targets.json`` holds the parts of a Tonight answer
that do not depend on the sky (``ok``, ``brief``, ``budget`` and the BUDGET
story rows) for the flow ``_fixture_graph`` builds, with the ledger
``_fixture_ledger`` holds, and ``tonightPanelDom.test.tsx`` (the classic
panel) and ``budgetForTheseTargets.test.tsx`` (the #/next sheet) mount it. So
the words they look for are the server's, not a copy that could drift from
them (#353 item 7's lesson). To rewrite it after a deliberate change:

    cd server && ASTRODECK_REWRITE_TONIGHT_BUDGET_FIXTURE=1 .venv/Scripts/python.exe -m pytest -q -p no:randomly -n0 tests/test_h4_budget_for_these_targets.py

then look at the diff and run both UI tests. Never hand-edit it.

THE HARNESS is ``test_flows_progress_route.py``'s ``api``: the real app over
ASGI, a throwaway config store swept into every module, flow library and
captures directory, with a synthetic site (that file's ``SITE_A``, not the
observatory's) set so the route answers.

MUTATIONS. Each named mutant was written over a byte backup in a private copy
of ``server/`` (the session scratchpad's ``H4-ROUTES-A-mut``), only this file
was run there, and the copy was restored and SHA-256 compared after each.
The failures are quoted as observed.
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import json
import os
from pathlib import Path

import astrodeck.api.app as app_module
from astrodeck.auth import principal_for_role, set_active_provider
from astrodeck.auth.capabilities import CAP_VIEW_SITE_DERIVED
from astrodeck.auth.rbac import CAP_ATTR
from astrodeck.config import Site
from astrodeck.flows.compile import compile_plan
from astrodeck.flows.models import FlowGraph
from astrodeck.flows.to_plan import to_sequence_plan
from astrodeck.flows.tonight import (banked_hours_from_reports,
                                     flow_target_names, resolve_tonight)
from astrodeck.sequence.models import SequencePlan
from astrodeck.sequence.report import (FrameRecord, SessionReport,
                                       SessionReporter, report_summary)
from test_flows_progress_route import (SITE_A, _Fixed,  # noqa: F401
                                       _restore_provider, api)

#: The UI fixture both Tonight surfaces' tests read.
UI_FIXTURE = (Path(__file__).resolve().parents[2] / "ui" / "src" / "next"
              / "hubs" / "session" / "flows" / "tonight" / "__tests__"
              / "tonight_budget_for_these_targets.json")
REWRITE = os.environ.get("ASTRODECK_REWRITE_TONIGHT_BUDGET_FIXTURE") == "1"

#: 13:00 local solar time at the fixture's synthetic site, 2026-06-15, so the
#: night is ahead (``test_flows_tonight``'s JUNE).
JUNE = _dt.datetime(2026, 6, 15, 20, 0, tzinfo=_dt.timezone.utc).timestamp()

#: M31 and M16 at their catalogue positions, typed as the TARGET types them.
M31 = {"name": "M31", "ra": "00h 42m 44s", "dec": "+41 16 09"}
M16 = {"name": "M16", "ra": "18h 18m 48s", "dec": "-13 49 00"}


# ------------------------------------------------------------------ builders

def _wire(a: str, ap: str, b: str, bp: str) -> dict:
    return {"from": a, "fromPort": ap, "to": b, "toPort": bp}


def _ha(node_id: str = "c", *, goal: float = 2.0) -> dict:
    """A CAPTURE LOOP of Ha 900 s with an integration goal, which is what
    gives it a BUDGET row."""
    return {"id": node_id, "type": "capture", "x": 100, "y": 0,
            "params": {"filter": "Ha", "exposure": 900, "gain": 100,
                       "bin": "1", "count": 8, "goal": goal}}


def _single(target: dict = M31) -> dict:
    """One TARGET feeding one Ha CAPTURE with a 2 h goal."""
    return {"nodes": [{"id": "t", "type": "target", "x": 0, "y": 0,
                       "params": dict(target)}, _ha()],
            "edges": [_wire("t", "target", "c", "run")]}


def _mosaic(skip: str = "2-2") -> dict:
    """M31 as a framed 2x2 at 25% overlap, 2.0 x 1.33 deg panels, a fixed
    camera at PA 30, panel 2-2 skipped, feeding a Ha CAPTURE with a 1 h goal
    per panel."""
    return {"nodes": [{"id": "t", "type": "target", "x": 0, "y": 0,
                       "params": {**M31, "rows": 2, "cols": 2,
                                  "overlap": 25, "fovX": 2.0, "fovY": 1.33,
                                  "angle": "Camera fixed at PA",
                                  "rotation": 30, "skip": skip}},
                      _ha(goal=1.0)],
            "edges": [_wire("t", "target", "c", "run")]}


def _written(plan: str, target: str, frames: list[tuple[str, float]], *,
             started: float) -> str:
    """Accepted frames of ``target`` through the engine's own report writer,
    finalized, so its ledger summary is written as a night's is. The id."""
    rep = SessionReporter(SequencePlan(name=plan), started_at=started)
    for i, (filt, exposure) in enumerate(frames):
        rep.record_frame(FrameRecord(ts=started + i, target=target,
                                     filter=filt, exposure_s=exposure,
                                     accepted=True))
    rep.finalize("complete")
    return rep.id


async def _report(plan: str, target: str, frames: list[tuple[str, float]],
                  *, started: float) -> str:
    """``_written`` on a worker thread, where no loop runs, so each snapshot
    is written inline and every write is on disk when this returns. On the
    test's own loop each snapshot would be a task still writing the report
    while the route reads it."""
    return await asyncio.to_thread(_written, plan, target, frames,
                                   started=started)


async def _four_ha(target: str, started: float) -> str:
    """Four accepted 900 s Ha subs of ``target``: one hour."""
    return await _report(f"{target} night", target, [("Ha", 900.0)] * 4,
                         started=started)


async def _tonight(api, fid: str) -> dict:
    api.store.set_site(Site(name="fixture", latitude=SITE_A[0],
                            longitude=SITE_A[1], elevation_m=10.0,
                            is_default=False))
    r = await api.client.get(f"/api/flows/{fid}/tonight")
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["ok"] is True, f"premise: the site is set: {got['reason']}"
    return got


def _ha_row(got: dict) -> dict:
    (row,) = [b for b in got["budget"] if b["filter"] == "Ha"]
    assert row["has_ledger"] is True, "premise: the ledger was read"
    return row


def _budget_lines(got: dict) -> list[str]:
    return [s["msg"] for s in got["story"] if s["label"] == 'TIME']


# ================================================================ the route

class TestTheRouteCountsThisFlowsTargets:
    async def test_an_m31_flow_beside_an_m16_ha_report_banks_no_ha(self,
                                                                   api):
        """An hour of M16 Ha is on disk and the flow is M31's: its Ha row
        banks 0 h, and says so for these targets. With M31's own hour beside
        it, the row banks that hour and not M16's too.

        RED under mutant "targets=None" (the route's BUDGET lambda calling
        ``banked_hours_from_reports(summaries())``, the archive-wide reading
        #536 filed), observed:

            AssertionError: M16's hour filled M31's bar
            assert 1.0 == 0.0
        """
        await _four_ha("M16", 1_790_000_000.0)
        fid = await api.save_flow(_single())
        got = await _tonight(api, fid)
        assert _ha_row(got)["banked_h"] == 0.0, "M16's hour filled M31's bar"
        (line,) = _budget_lines(got)
        assert "Ha: 0 h captured for these targets / 2 h goal" in line, line
        await _four_ha("M31", 1_790_100_000.0)
        got = await _tonight(api, fid)
        assert _ha_row(got)["banked_h"] == 1.0, (
            "M16's hour was added to M31's")

    async def test_control_m31s_own_report_counts(self, api):
        """The CONTROL: the same flow with M31's own hour alone on disk
        banks exactly that hour, under the fix and under "targets=None"
        alike (1 passed under that mutant: an archive of M31's hour is the
        same hour read either way).

        It can fail: RED under mutant "no names at all"
        (``flow_target_names`` answering ``[]``), observed:

            AssertionError: M31's own hour did not count
            assert 0.0 == 1.0
        """
        await _four_ha("M31", 1_790_000_000.0)
        fid = await api.save_flow(_single())
        got = await _tonight(api, fid)
        assert _ha_row(got)["banked_h"] == 1.0, "M31's own hour did not count"

    async def test_a_mosaic_counts_its_live_panels_by_their_names(self, api):
        """A 2x2 of M31 with 2-2 skipped. Its run records frames as "M31
        1-1" .. "M31 2-1"; half an hour on "M31 1-2" counts. An hour on a
        bare "M31" (a single-target flow's) does not, and neither does an
        hour on the skipped "M31 2-2", whose goal the row does not carry
        (the row's goal is three panels' 3 h).

        RED under mutant "the block name for a mosaic" (``flow_target_names``
        naming a mosaic entry by its label alone, as a single target),
        observed:

            AssertionError: the mosaic's banked hours: 1.0
            assert 1.0 == 0.5

        RED under mutant "skips ignored" (every panel of the grid named),
        observed:

            AssertionError: the mosaic's banked hours: 1.5
            assert 1.5 == 0.5

        and under "targets=None", every hour on disk:

            AssertionError: the mosaic's banked hours: 2.5
            assert 2.5 == 0.5
        """
        await _report("M31 mosaic night", "M31 1-2", [("Ha", 900.0)] * 2,
                      started=1_790_000_000.0)
        await _four_ha("M31", 1_790_100_000.0)
        await _four_ha("M31 2-2", 1_790_200_000.0)
        fid = await api.save_flow(_mosaic())
        got = await _tonight(api, fid)
        row = _ha_row(got)
        assert (row["panels"], row["goal_h"]) == (3, 3.0), (
            "premise: three live panels at 1 h each")
        assert row["banked_h"] == 0.5, (
            f"the mosaic's banked hours: {row['banked_h']}")

    async def test_the_route_folds_with_the_flows_own_names(self, api,
                                                            monkeypatch):
        """The route hands ``banked_hours_from_reports`` a ``targets``, and
        it is ``flow_target_names`` of the stored flow.

        RED under mutant "targets=None", observed:

            AssertionError: the BUDGET fold was handed targets=None
            assert None == ['M31']
        """
        seen: list = []
        real = app_module.banked_hours_from_reports

        def spy(reports, targets=None):
            seen.append(None if targets is None else list(targets))
            return real(reports, targets)
        monkeypatch.setattr(app_module, "banked_hours_from_reports", spy)
        fid = await api.save_flow(_single())
        await _tonight(api, fid)
        assert seen, "premise: the BUDGET fold ran"
        assert seen[-1] == ["M31"], (
            f"the BUDGET fold was handed targets={seen[-1]!r}")

    async def test_the_route_stays_view_site_derived(self, api):
        """Every value in the answer is f(latitude, longitude), so the route
        keeps ``CAP_VIEW_SITE_DERIVED``: its declaration says so, and a
        viewer, who does not hold it, is refused.

        RED under mutant "gated on view.status" (``CAP_VIEW_SITE_DERIVED``
        made ``CAP_VIEW_STATUS`` in the route's dependency and
        declaration), observed:

            AssertionError: the route declares frozenset({'view.status'})
        """
        route = next(r for r in app_module.create_app().routes
                     if getattr(r, "path", "") ==
                     "/api/flows/{flow_id}/tonight")
        caps = getattr(route.endpoint, CAP_ATTR, None)
        assert caps == frozenset({CAP_VIEW_SITE_DERIVED}), (
            f"the route declares {caps}")
        fid = await api.save_flow(_single())
        api.store.set_site(Site(name="fixture", latitude=SITE_A[0],
                                longitude=SITE_A[1], elevation_m=10.0,
                                is_default=False))
        set_active_provider(_Fixed(principal_for_role("viewer")))
        r = await api.client.get(f"/api/flows/{fid}/tonight")
        assert r.status_code == 403, r.text


# ================================================================ the names

class TestTheNamesAreTheRunsNames:
    def test_they_are_the_names_to_sequence_plan_gives_the_targets(self):
        """A mosaic with a skipped panel, a single target and a pool: the
        names ``flow_target_names`` counts are exactly the names the plan
        the run executes gives its targets, which are the names a report's
        rows carry (``FrameRecord.target = target.name``).

        RED under mutant "the block name for a mosaic", observed:

            AssertionError: assert ['M31', 'M33', 'M13', 'M92'] == ['M31
            1-1', '... 'M13', 'M92']

        RED under mutant "skips ignored", observed:

            AssertionError: assert ['M31 1-1', '...', 'M13', ...] == ['M31
            1-1', '... 'M13', 'M92']
              At index 3 diff: 'M31 2-2' != 'M33'
        """
        g = FlowGraph.model_validate({
            "nodes": [
                *_mosaic()["nodes"],
                {"id": "u", "type": "target", "x": 300, "y": 0,
                 "params": {"name": "M33", "ra": "01h 33m 51s",
                            "dec": "+30 39 37"}},
                {"id": "k", "type": "capture", "x": 400, "y": 0,
                 "params": {"filter": "L", "exposure": 60, "gain": 100,
                            "bin": "1", "count": 5, "goal": 0}},
                {"id": "p", "type": "pool", "x": 500, "y": 0,
                 "params": {"members": "M13, M92"}},
                {"id": "q", "type": "capture", "x": 600, "y": 0,
                 "params": {"filter": "L", "exposure": 60, "gain": 100,
                            "bin": "1", "count": 5, "goal": 0}}],
            "edges": [_wire("t", "target", "c", "run"),
                      _wire("c", "complete", "u", "arm"),
                      _wire("u", "target", "k", "run"),
                      _wire("k", "complete", "p", "arm"),
                      _wire("p", "target", "q", "run")]})
        plan, _ = to_sequence_plan(compile_plan(g, "names"), g, flow_id="f1")
        assert flow_target_names(g, "names") == [t.name for t in plan.targets]
        assert "M31 2-2" not in flow_target_names(g, "names"), (
            "premise: the skipped panel is not in the plan")

    def test_a_compiled_plan_is_read_as_the_graph_is(self):
        """``flow_target_names`` of the compile is the graph's: the route
        hands it the graph, ``resolve_tonight`` compiles the same one.

        RED under mutant "the block name for a mosaic", observed:

            AssertionError: assert ['M31'] == ['M31 1-1', '...1', 'M31
            2-2']
        """
        g = FlowGraph.model_validate(_mosaic(skip=""))
        assert (flow_target_names(compile_plan(g, "m"))
                == flow_target_names(g, "m")
                == ["M31 1-1", "M31 1-2", "M31 2-1", "M31 2-2"])


# ============================================================ the sentence

def _fixture_graph() -> FlowGraph:
    """M31 feeding a Ha CAPTURE LOOP with a 2 h goal, then an L, R FILTER
    CYCLE: one capture row and one cycle row in BUDGET."""
    return FlowGraph.model_validate({
        "nodes": [{"id": "t", "type": "target", "x": 0, "y": 0,
                   "params": dict(M31)},
                  _ha(),
                  {"id": "y", "type": "cycle", "x": 200, "y": 0,
                   "params": {"plan": "L 60, R 60", "cycles": 10,
                              "perCycle": 1, "gain": 100, "bin": "1"}}],
        "edges": [_wire("t", "target", "c", "run"),
                  _wire("c", "complete", "y", "run")]})


def _fixture_ledger() -> list[dict]:
    """Two nights' summaries, as ``SessionReporter.summaries`` hands them
    over: M31 with 4 x 900 s Ha and 12 x 60 s L, and M16 with 4 x 900 s Ha
    and 30 x 60 s L, which must not count."""
    def summary(target: str, rows: list[tuple[str, int, float]]) -> dict:
        from astrodeck.sequence.report import (FilterBreakdown,
                                               TargetBreakdown)
        by_filter = [FilterBreakdown(filter=f, frames=n,
                                     integration_s=n * exp)
                     for f, n, exp in rows]
        rep = SessionReport(id=f"{target}-night", by_filter=by_filter,
                            targets=[TargetBreakdown(name=target,
                                                     by_filter=by_filter)])
        return report_summary(rep, {"size": 1, "mtime_ns": 1})
    return [summary("M31", [("Ha", 4, 900.0), ("L", 12, 60.0)]),
            summary("M16", [("Ha", 4, 900.0), ("L", 30, 60.0)])]


def _fixture_answer() -> dict:
    """The sky-free parts of ``resolve_tonight``'s answer for the fixture's
    flow and ledger, folded the way the route folds (its own names), at the
    synthetic site ``test_flows_tonight`` uses (40 N 105 W)."""
    g = _fixture_graph()
    ledger = _fixture_ledger()
    out = resolve_tonight(
        g, {"latitude": 40.0, "longitude": -105.0, "elevation_m": 1600.0,
            "is_default": False},
        name="budget for these targets", now=JUNE,
        twilight_deg=-12.0,
        banked=lambda: banked_hours_from_reports(
            ledger, targets=flow_target_names(g, "budget for these targets")))
    out = json.loads(json.dumps(out))
    return {"ok": out["ok"], "brief": out["brief"], "budget": out["budget"],
            "story": [s for s in out["story"] if s["label"] == 'TIME']}


ABOUT = ("The sky-free parts (ok, brief, budget, the BUDGET story rows) of "
         "flows/tonight.py resolve_tonight's answer for the flow and ledger "
         "server/tests/test_h4_budget_for_these_targets.py builds, which "
         "keeps this file equal to that answer. Read by tonightPanelDom."
         "test.tsx and budgetForTheseTargets.test.tsx (#536). Never "
         "hand-edit it.")


class TestTheRowSaysForTheseTargets:
    def test_both_rows_say_whose_hours_they_hold(self):
        """The capture row and the cycle row each say their banked hours
        are for these targets, and hold M31's alone: 1 h of Ha, and 0.2 h of
        L, R (twelve L subs; M16's thirty are not counted).

        RED under mutant "the words dropped" (``_FOR_THESE`` left out of
        both sentences in ``_story``), observed:

            AssertionError: a BUDGET row that does not say whose hours:
            'Ha: 1 h banked / 2 h goal — tonight adds ≈2 h; the session
            ledger resumes the remainder next clear night'

        RED under mutant "no names at all", observed:

            AssertionError: {'Ha': 0.0, 'L, R': 0.0}
            assert {'Ha': 0.0, 'L, R': 0.0} == {'Ha': 1.0, 'L, R': 0.2}
        """
        got = _fixture_answer()
        banked = {b["filter"]: b["banked_h"] for b in got["budget"]}
        assert banked == {"Ha": 1.0, "L, R": 0.2}, banked
        lines = [s["msg"] for s in got["story"]]
        assert len(lines) == 2, lines
        for line in lines:
            assert "for these targets" in line, (
                f"a BUDGET row that does not say whose hours: {line!r}")
        assert lines[0].startswith("Ha: 1 h captured for these targets / 2 h "
                                   "goal"), lines[0]
        assert "0.2 h captured in its filters for these targets" in lines[1], (
            lines[1])

    def test_the_ui_fixture_is_this_answer(self):
        """The file both UIs' tests mount is ``_fixture_answer()``, exactly:
        every string, every number, every key. Rewrite it with the command
        in the module docstring after a deliberate change.

        RED under mutant "the words dropped", with the fixture as committed,
        observed:

            AssertionError: the UI fixture is not the server's answer; rewrite
            it (module docstring)
        """
        want = _fixture_answer()
        if REWRITE:
            UI_FIXTURE.write_bytes((json.dumps(
                {"about": ABOUT, "response": want}, indent=1,
                ensure_ascii=True) + "\n").encode("utf-8"))
        assert UI_FIXTURE.is_file(), f"premise: {UI_FIXTURE.name} exists"
        got = json.loads(UI_FIXTURE.read_bytes().decode("utf-8"))
        assert got["about"] == ABOUT
        assert got["response"] == want, (
            "the UI fixture is not the server's answer; rewrite it (module "
            "docstring)")
