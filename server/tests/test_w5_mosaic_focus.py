# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""WHERE A MOSAIC'S DUE SWEEP RUNS, AND WHEN IT RUNS (WP 5.3, #185, spec 5.6
step 6).

A sweep that falls due on a hop used to run on whatever panel the hop landed
on. On a star-poor panel it fails as a sparse field, and under
``af_failure_action`` skip the failure ended the visit with a plain
`StopTarget`, which the group driver defers: a focus sweep cost the panel its
visit. Two changes, both pinned here:

* `_focus_site`: the sweep runs where the hop landed, unless that panel is
  KNOWN to be star-poor (its latest visit counted fewer than the sweep's own
  floor, ``SPARSE_FIELD_WARN``). From a poor panel it goes to the eligible
  member with the most stars that is not itself poor, else to a member not
  seen yet (unknown, not poor), else it waits for the next hop. The mount goes
  there, sweeps, and centres back on the panel the hop was for. A rich or
  unmeasured hop panel never detours: no two slews and a re-centre to go from
  300 stars to 310. A focus failure on that hop never defers the panel.
* `_refocus_left_to_the_hop`: a refocus that falls due mid-visit on a panel
  known to be poor is not taken there; it stays owed for the next hop, which
  places it.
* the AUTOFOCUS node's ``when``: "When stale" (the hop rule as it was),
  "Every panel" (a sweep on each hop) and "First panel only" (one per group per
  run). A flow without the key compiles to the plan it always did.

THE RIG is test_hop_focus.py's: the real sim hub, a virtual clock, the real
`_autofocus`, `_hop_focus_is_owed` and `_setup_target`, with only the sweep's
measurement and the centring stubbed. Added here: a group with its run state,
so the hop is a group member's. The stars a panel counted are fed through
`_note_panel_stars`, the call `_record_session_frame` makes for every frame.
"""
from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest

from _simhub import sim_hub  # noqa: F401 (fixture import)
from test_hop_focus import (HOP_35_MIN_S, SWEEP_S, _noop_gate, _Clock,
                            _Found, _NotFound)

import astrodeck.sequence.engine as engine_mod
from astrodeck.events import bus
from astrodeck.flows.compile import compile_plan
from astrodeck.flows.models import FlowEdge, FlowGraph, FlowNode
from astrodeck.flows.nodes import AUTOFOCUS_WHEN, NODE_DEFS, autofocus_when
from astrodeck.flows.to_plan import to_sequence_plan
from astrodeck.focus.native import SPARSE_FIELD_WARN
from astrodeck.sequence import ExposureStep, SequenceEngine, SequencePlan, Target
from astrodeck.sequence.engine import StopTarget
from astrodeck.sequence.group_rules import GroupRun
from astrodeck.sequence.models import TargetGroup

GID = "m31-mosaic"
RICH, MID, POOR = 300, 80, SPARSE_FIELD_WARN - 1


def _panel(label: str, n: int, *, when: str = "stale") -> Target:
    """A mosaic panel on the hop rule, at its own place in the sky (a made-up
    field: the stub centring only records where it was asked to go)."""
    return Target(name=f"M31 {label}", ra_hours=0.5 + 0.05 * n,
                  dec_deg=40.0 + 0.5 * n, center=True, autofocus_first=True,
                  autofocus_skip_if_fresh=True, mosaic_group=GID,
                  autofocus_when=when,
                  steps=[ExposureStep(filter="L", exposure_s=0.05, count=1)])


class _GroupRig:
    """The sim hub with a virtual clock, a counting sweep that records where
    the mount was, and a group over ``panels``."""

    def __init__(self, sim_hub, monkeypatch, panels, *, outcomes=(),
                 skip_on_failure: bool = False, autofocus_every: int = 0):
        self.clock = _Clock()
        monkeypatch.setattr(engine_mod, "time", self.clock)
        self.hub = sim_hub
        self.panels = list(panels)
        self.gotos: list[str] = []
        self.swept_at: list[str] = []
        self.logs: list[str] = []
        self._outcomes = list(outcomes)
        group = TargetGroup(id=GID, name="M31 mosaic")
        e = SequenceEngine(sim_hub)
        e.plan = SequencePlan(targets=self.panels, groups=[group],
                              meridian_flip=False, guide=False,
                              refocus_on_temp_delta_c=1.0,
                              autofocus_every=autofocus_every)
        e._cfg = (SimpleNamespace(escalation=SimpleNamespace(
            af_failure_action="skip")) if skip_on_failure else None)
        e._safety_gate = _noop_gate
        e._groups = {GID: group}
        self.run = GroupRun({t.id: t.name for t in self.panels},
                            max_failed_visits=3)
        e._group_runs = {GID: self.run}
        self.run.note_let_through(t.id for t in self.panels)
        self.engine = e
        sim_hub.guider = None
        monkeypatch.setattr(sim_hub, "_check_solar", lambda *a, **kw: None)
        by_pos = {(t.ra_hours, t.dec_deg): t.name for t in self.panels}

        async def _goto(ra_h, dec_d, **kw):
            self.gotos.append(by_pos[(ra_h, dec_d)])
            return {"centered": True, "error_arcmin": 0.1}

        async def _run_autofocus(cam, foc, **kw):
            self.clock.advance(SWEEP_S)
            self.swept_at.append(self.gotos[-1] if self.gotos else "")
            ok = self._outcomes.pop(0) if self._outcomes else True
            return _Found() if ok else _NotFound()

        monkeypatch.setattr(sim_hub, "goto_and_center", _goto)
        monkeypatch.setattr(engine_mod, "run_autofocus", _run_autofocus)
        monkeypatch.setattr(bus, "log",
                            lambda lvl, msg, src=None, **kw:
                            self.logs.append(msg))

    def saw(self, panel: Target, stars: int) -> None:
        """``panel``'s latest visit counted ``stars`` stars."""
        self.engine._note_panel_stars(panel, {"stars": stars})

    async def hop(self, target: Target, after_s: float = 0.0) -> None:
        self.clock.advance(after_s)
        await self.engine._setup_target(self.panels.index(target), target)

    def said(self, text: str) -> list[str]:
        return [m for m in self.logs if text in m]


# ------------------------------------------- the sweep runs on the richest


class TestADueSweepRunsOnTheRichestPanel:
    async def test_a_sweep_due_as_the_next_panel_is_star_poor_runs_on_the_richest(
            self, sim_hub, monkeypatch):
        """THE ISSUE'S CASE. The group's first sweep is owed on the hop to
        panel 1-3, which counted 14 stars last visit. 1-2 counted 300 and 1-1
        counted 80. The sweep runs where the mount is on 1-2, the mount goes
        back to 1-3 after it, and 1-3 is not deferred.

        MUTATION "sweep on the star-poor panel" (`_focus_site` returns
        ``target``, the richest-panel choice dropped). Observed:
            AssertionError: the sweep ran on ['M31 1-3'], the panel the hop
            landed on, which counted 14 stars (under the sweep's floor of 15);
            it belongs on M31 1-2, the richest eligible panel
        """
        a, b, c = _panel("1-1", 1), _panel("1-2", 2), _panel("1-3", 3)
        rig = _GroupRig(sim_hub, monkeypatch, [a, b, c])
        rig.saw(a, MID), rig.saw(b, RICH), rig.saw(c, POOR)
        await rig.hop(c)
        assert rig.swept_at == [b.name], (
            f"the sweep ran on {rig.swept_at}, the panel the hop landed on, "
            f"which counted {POOR} stars (under the sweep's floor of "
            f"{SPARSE_FIELD_WARN}); it belongs on {b.name}, the richest "
            f"eligible panel")
        assert rig.gotos == [c.name, b.name, c.name], (
            "the mount goes to the hop's panel, to the richest for the sweep "
            f"and back: {rig.gotos}")
        assert rig.said("the focus sweep ran on M31 1-2")

    async def test_the_panel_the_hop_was_for_is_not_deferred_by_a_failed_sweep(
            self, sim_hub, monkeypatch):
        """`af_failure_action` skip raises a plain `StopTarget` from a failed
        sweep, which the group driver turns into a deferral and, three passes
        on, a panel set aside. On the hop rule it is said and the visit goes
        on at the focus the run has.

        MUTATION "the failure still stops the panel" (`_autofocus_for_hop`
        re-raises). Observed: `StopTarget: autofocus failed: not_enough_spread`
        out of `_setup_target`."""
        a, b, c = _panel("1-1", 1), _panel("1-2", 2), _panel("1-3", 3)
        rig = _GroupRig(sim_hub, monkeypatch, [a, b, c], outcomes=[False],
                        skip_on_failure=True)
        rig.saw(a, MID), rig.saw(b, RICH), rig.saw(c, POOR)
        try:
            await rig.hop(c)
        except StopTarget as e:
            pytest.fail(f"a failed focus sweep stopped the panel the hop was "
                        f"for, which the group driver defers: {e}")
        assert rig.swept_at == [b.name]
        assert rig.said("a focus failure does not defer a panel")
        assert rig.gotos[-1] == c.name, "the visit goes on at its own panel"

    async def test_a_target_outside_a_group_still_stops_on_a_failed_sweep(
            self, sim_hub, monkeypatch):
        """The control: a single target under skip is dropped for a failed
        sweep, as it always was. Only a mosaic panel is spared."""
        solo = Target(name="M33", ra_hours=1.5, dec_deg=30.0, center=True,
                      autofocus_first=True,
                      steps=[ExposureStep(filter="L", exposure_s=0.05,
                                          count=1)])
        rig = _GroupRig(sim_hub, monkeypatch, [solo], outcomes=[False],
                        skip_on_failure=True)
        rig.engine._groups = {}
        rig.engine._group_runs = {}
        with pytest.raises(StopTarget):
            await rig.hop(solo)

    async def test_the_hop_panel_sweeps_where_it_stands_when_it_is_richest(
            self, sim_hub, monkeypatch):
        """No detour when the panel the hop landed on is the richest one: one
        centring, the sweep there."""
        a, b, c = _panel("1-1", 1), _panel("1-2", 2), _panel("1-3", 3)
        rig = _GroupRig(sim_hub, monkeypatch, [a, b, c])
        rig.saw(a, MID), rig.saw(b, POOR), rig.saw(c, RICH)
        await rig.hop(c)
        assert rig.swept_at == [c.name]
        assert rig.gotos == [c.name, c.name], (
            "the hop's centring and the one after an unguided sweep, "
            f"nothing else: {rig.gotos}")

    async def test_a_rich_hop_panel_sweeps_in_place_whatever_its_neighbour_has(
            self, sim_hub, monkeypatch):
        """The hop panel counted 300 and its neighbour 310: no detour for 10
        stars. Nor for a hop panel that is merely the thinnest of the rich.

        MUTATION "the literal most-stars rule" (the ``_panel_is_poor`` test
        dropped from `_focus_site`'s first line). Observed:
            AssertionError: assert ['M31 1-1'] == ['M31 1-3']"""
        a, c = _panel("1-1", 1), _panel("1-3", 3)
        rig = _GroupRig(sim_hub, monkeypatch, [a, c])
        rig.saw(a, RICH + 10), rig.saw(c, RICH)
        await rig.hop(c)
        assert rig.swept_at == [c.name]
        assert rig.gotos == [c.name, c.name], (
            f"the mount went somewhere for the sweep: {rig.gotos}")

    async def test_an_unmeasured_panel_is_not_poor_the_hop_panel_keeps_the_sweep(
            self, sim_hub, monkeypatch):
        """The first hop of a run: nothing has counted any stars yet. The
        sweep runs where the mount is, as it always did."""
        a, b = _panel("1-1", 1), _panel("1-2", 2)
        rig = _GroupRig(sim_hub, monkeypatch, [a, b])
        await rig.hop(a)
        assert rig.swept_at == [a.name]

    async def test_a_poor_hop_panel_goes_to_an_unmeasured_one_over_none(
            self, sim_hub, monkeypatch):
        """Poor panel, nobody known rich: an unmeasured panel is not poor, so
        the sweep goes there rather than stay on the poor field."""
        a, b = _panel("1-1", 1), _panel("1-2", 2)
        rig = _GroupRig(sim_hub, monkeypatch, [a, b])
        rig.saw(b, POOR)
        await rig.hop(b)
        assert rig.swept_at == [a.name]

    async def test_with_every_eligible_panel_poor_the_sweep_waits_for_the_next_hop(
            self, sim_hub, monkeypatch):
        """No member qualifies: nothing sweeps, the visit goes on, the group's
        first acquisition is given back so the NEXT hop still owes the sweep,
        and that hop, onto a rich panel, runs it.

        MUTATION "sweep anyway" (`_focus_site` falls back to ``target``).
        Observed: AssertionError: a sweep ran on a poor field: ['M31 1-2']"""
        a, b = _panel("1-1", 1), _panel("1-2", 2)
        rig = _GroupRig(sim_hub, monkeypatch, [a, b])
        rig.saw(a, POOR), rig.saw(b, POOR)
        await rig.hop(b)
        assert rig.swept_at == [], (
            f"a sweep ran on a poor field: {rig.swept_at}")
        assert rig.gotos == [b.name], "the visit's own centring only"
        assert rig.said("a focus sweep is owed, but no panel")
        rig.saw(a, RICH)
        await rig.hop(a, after_s=60.0)
        assert rig.swept_at == [a.name], "the owed sweep runs at the next hop"

    async def test_a_member_this_pass_cannot_visit_is_not_a_place_to_sweep(
            self, sim_hub, monkeypatch):
        """Eligible now: a member set aside, or one no selection of the pass
        let through (held by its limit, the meridian rule or its window), is
        not somewhere the mount can go."""
        a, b, c, d = (_panel("1-1", 1), _panel("1-2", 2), _panel("1-3", 3),
                      _panel("1-4", 4))
        rig = _GroupRig(sim_hub, monkeypatch, [a, b, c, d])
        rig.run.let_through.discard(b.id)
        rig.run.set_aside_panel(a.id, "centring failed")
        rig.saw(a, RICH + 100), rig.saw(b, RICH + 100)
        rig.saw(c, POOR), rig.saw(d, MID)
        await rig.hop(c)
        assert rig.swept_at == [d.name], rig.swept_at

    async def test_a_panel_the_plan_does_not_centre_sweeps_where_it_stands(
            self, sim_hub, monkeypatch):
        """The mount comes back from a detour by centring, so a panel that
        opts out of centring never leaves for another."""
        a, c = _panel("1-1", 1), _panel("1-3", 3)
        c.center = False
        rig = _GroupRig(sim_hub, monkeypatch, [a, c])
        rig.saw(a, RICH), rig.saw(c, POOR)
        await rig.hop(c)
        assert len(rig.swept_at) == 1 and rig.gotos == [], (
            f"the mount left the panel that is not centred: {rig.gotos}")

    async def test_a_sweep_is_not_moved_when_the_detour_cannot_centre(
            self, sim_hub, monkeypatch):
        """The richest panel does not centre: no sweep on a field nobody
        found, the mount goes back to the hop's panel, and the sweep stays
        owed."""
        a, c = _panel("1-1", 1), _panel("1-3", 3)
        rig = _GroupRig(sim_hub, monkeypatch, [a, c])
        rig.saw(a, RICH), rig.saw(c, POOR)

        async def _goto(ra_h, dec_d, **kw):
            name = {(t.ra_hours, t.dec_deg): t.name for t in (a, c)}[
                (ra_h, dec_d)]
            rig.gotos.append(name)
            return {"centered": name != a.name, "error_arcmin": 0.1}

        monkeypatch.setattr(sim_hub, "goto_and_center", _goto)
        await rig.hop(c)
        assert rig.swept_at == []
        assert rig.gotos[-1] == c.name
        assert rig.said("not centred for the focus sweep")


class TestTheStarsAPanelCounted:
    async def test_the_visits_best_frame_counts_not_its_last(
            self, sim_hub, monkeypatch):
        """A visit ending on a narrowband sub must not read as a poor field:
        the most of the visit's frames stands. The first frame of the NEXT
        visit starts the count again."""
        a, b = _panel("1-1", 1), _panel("1-2", 2)
        rig = _GroupRig(sim_hub, monkeypatch, [a, b])
        rig.saw(a, 400), rig.saw(a, 3)
        assert rig.engine._panel_stars[a.id] == 400
        rig.engine._begin_panel_visit(a)
        assert rig.engine._panel_stars[a.id] == 400, (
            "the last visit's count stands until a frame of the new one")
        rig.saw(a, 3)
        assert rig.engine._panel_stars[a.id] == 3

    async def test_a_calibration_frame_and_a_loose_target_count_nothing(
            self, sim_hub, monkeypatch):
        a = _panel("1-1", 1)
        rig = _GroupRig(sim_hub, monkeypatch, [a])
        dark = Target(name="darks", ra_hours=0, dec_deg=0, calibration=True,
                      mosaic_group=GID, steps=[])
        loose = Target(name="M33", ra_hours=1, dec_deg=1, steps=[])
        for t in (dark, loose):
            rig.engine._note_panel_stars(t, {"stars": 500})
        rig.engine._note_panel_stars(a, {"stars": None})
        rig.engine._note_panel_stars(a, {})
        assert rig.engine._panel_stars == {}

    async def test_a_frame_the_ledger_records_feeds_the_count(
            self, sim_hub, monkeypatch):
        """The wire: `_record_session_frame`, which every frame passes
        through, hands its info to `_note_panel_stars`."""
        a = _panel("1-1", 1)
        rig = _GroupRig(sim_hub, monkeypatch, [a])
        rig.engine._record_session_frame(
            a, a.steps[0], {"stars": 123}, auto_accepted=True)
        assert rig.engine._panel_stars == {a.id: 123}


# ----------------------------------------------- the AUTOFOCUS "when" policy


class TestFirstPanelOnly:
    async def test_one_sweep_per_group_per_run_whatever_comes_due(
            self, sim_hub, monkeypatch):
        """Past the first panel nothing sweeps on a hop: not a due refocus
        (``autofocus_every`` reached), not a temperature drift, not a failed
        first sweep.

        MUTATION "first reads as stale" (the ``when == "first"`` branch of
        `_hop_focus_is_owed` removed). Observed:
            AssertionError: second hop swept: ['M31 1-1', 'M31 1-2']"""
        a = _panel("1-1", 1, when="first")
        b = _panel("1-2", 2, when="first")
        rig = _GroupRig(sim_hub, monkeypatch, [a, b], autofocus_every=2)
        await rig.hop(a)
        assert rig.swept_at == [a.name]
        rig.engine._frames_since_focus = 5          # a refocus is due
        await rig.hop(b, after_s=HOP_35_MIN_S)
        assert rig.swept_at == [a.name], (
            f"second hop swept: {rig.swept_at}")
        assert rig.said("sweeps on the first panel only")

    async def test_a_sweep_that_failed_is_not_retried_at_the_next_hop(
            self, sim_hub, monkeypatch):
        a = _panel("1-1", 1, when="first")
        b = _panel("1-2", 2, when="first")
        rig = _GroupRig(sim_hub, monkeypatch, [a, b], outcomes=[False])
        await rig.hop(a)
        await rig.hop(b)
        assert rig.swept_at == [a.name]

    async def test_a_first_sweep_that_waited_is_still_owed_at_the_next_hop(
            self, sim_hub, monkeypatch):
        """The first acquisition is given back when no panel could take the
        sweep, so "first panel only" still gets its one sweep."""
        a = _panel("1-1", 1, when="first")
        b = _panel("1-2", 2, when="first")
        rig = _GroupRig(sim_hub, monkeypatch, [a, b])
        rig.saw(a, POOR), rig.saw(b, POOR)
        await rig.hop(a)
        assert rig.swept_at == []
        rig.saw(b, RICH)
        await rig.hop(b)
        assert rig.swept_at == [b.name]

    async def test_a_second_group_is_its_own_first(self, sim_hub, monkeypatch):
        a = _panel("1-1", 1, when="first")
        other = Target(name="M33 1-1", ra_hours=2.0, dec_deg=30.0,
                       center=True, autofocus_first=True,
                       autofocus_skip_if_fresh=True, mosaic_group="m33",
                       autofocus_when="first",
                       steps=[ExposureStep(filter="L", exposure_s=0.05,
                                           count=1)])
        rig = _GroupRig(sim_hub, monkeypatch, [a, other])
        rig.engine._groups["m33"] = TargetGroup(id="m33", name="M33")
        rig.engine._group_runs["m33"] = GroupRun({other.id: other.name},
                                                 max_failed_visits=3)
        by_pos = {(t.ra_hours, t.dec_deg): t.name for t in (a, other)}

        async def _goto(ra_h, dec_d, **kw):
            rig.gotos.append(by_pos[(ra_h, dec_d)])
            return {"centered": True, "error_arcmin": 0.1}

        monkeypatch.setattr(sim_hub, "goto_and_center", _goto)
        await rig.hop(a)
        await rig.hop(other)
        assert rig.swept_at == [a.name, other.name]


class TestEveryPanel:
    async def test_a_sweep_on_each_hop_placed_by_the_richest_rule(
            self, sim_hub, monkeypatch):
        """Two hops 35 minutes apart with no trigger due sweep both times
        (the stale rule sweeps neither the second), the second one on the
        richest panel.

        MUTATION "every reads as stale" (the ``when == "every"`` branch of
        `_hop_focus_is_owed` removed). Observed:
            AssertionError: the second hop did not sweep: ['M31 1-1']"""
        a = _panel("1-1", 1, when="every")
        b = _panel("1-2", 2, when="every")
        c = _panel("1-3", 3, when="every")
        rig = _GroupRig(sim_hub, monkeypatch, [a, b, c])
        await rig.hop(a)
        rig.saw(a, MID), rig.saw(c, RICH)
        rig.saw(b, POOR)
        await rig.hop(b, after_s=60.0)
        assert rig.swept_at == [a.name, c.name], (
            f"the second hop did not sweep on the richest panel: "
            f"{rig.swept_at}")

    async def test_the_default_reuses_a_good_focus_at_the_same_hop(
            self, sim_hub, monkeypatch):
        """The control for the test above: "When stale" is today's rule."""
        a, b = _panel("1-1", 1), _panel("1-2", 2)
        rig = _GroupRig(sim_hub, monkeypatch, [a, b])
        await rig.hop(a)
        await rig.hop(b, after_s=60.0)
        assert rig.swept_at == [a.name]
        assert rig.said("focus reused")


# ------------------------------------------------ the node, the compile, the plan


def _graph(af_params: dict | None, *, rows=2, cols=2) -> FlowGraph:
    """DUSK -> TARGET (a framed grid) -> AUTOFOCUS -> CAPTURE LOOP, the loop
    wire back to the TARGET."""
    target = {"name": "M31", "ra": "00h 42m 44s", "dec": "+41 16 09",
              "fovX": 2.0, "fovY": 1.33, "rotation": 30,
              "angle": "Rotate to PA", "rows": rows, "cols": cols}

    def node(nid, ntype, x, **params):
        return FlowNode(id=nid, type=ntype, x=x, y=0.0, params=params)

    def edge(a, ap, b, bp):
        return FlowEdge(**{"from": a, "fromPort": ap, "to": b, "toPort": bp})

    return FlowGraph(
        nodes=[node("d", "dusk", 0), node("t", "target", 100, **target),
               node("af", "autofocus", 200, **(af_params or {})),
               node("c", "capture", 300, exposure=60, count=5, goal=0)],
        edges=[edge("d", "window", "t", "arm"), edge("t", "target", "af", "run"),
               edge("af", "focused", "c", "run"), edge("c", "pass", "t", "next")])


def _plan(graph: FlowGraph):
    return to_sequence_plan(compile_plan(graph, "n"), graph,
                            flow_id="flow-s3cp")


def _sha(obj) -> str:
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True).encode()).hexdigest()


#: Taken on the tree BEFORE this change (origin/main d4549f22): the plan and
#: the loss list of a 2x2 mosaic with an AUTOFOCUS stage and no ``when`` key,
#: looped and not, and with the stage's three old params set to their values.
PIN_LOOPED = ("224245e0a84502e2441764a4bb0d200c9131584b4af337c65db656371b49845b",
              "5ad9c4e4f127cf52012de28a4c5c7bd0a3d745af3d21a87d3bb4d82054bf8d0f")
PIN_PARAMS = ("224245e0a84502e2441764a4bb0d200c9131584b4af337c65db656371b49845b",
              "8b2ddc8d40189ab1c8e110a8f94d0505c0830af9a1df93afa17db84ddb41f415")


def _pin(graph: FlowGraph) -> tuple[str, str]:
    plan, unmapped = _plan(graph)
    return (_sha(plan.model_dump(mode="json")), _sha(unmapped))


class TestTheNodeAndTheCompile:
    def test_a_flow_without_the_choice_compiles_to_the_plan_it_always_did(self):
        """RULE 14: the plan hash and the loss list are the base tree's, for a
        flow with no ``when`` key, one with the old params, and one that
        names the default outright."""
        assert _pin(_graph(None)) == PIN_LOOPED
        assert _pin(_graph({"method": "V-curve sweep", "step": 12,
                            "samples": 9})) == PIN_PARAMS
        assert _pin(_graph({"when": "When stale"}))[0] == PIN_LOOPED[0]

    def test_the_default_is_absent_from_the_plan_dump(self):
        plan, _ = _plan(_graph(None))
        assert all("autofocus_when" not in t for t in
                   plan.model_dump(mode="json")["targets"])
        assert all(t.autofocus_when == "stale" for t in plan.targets)

    @pytest.mark.parametrize("label,word", [("Every panel", "every"),
                                            ("First panel only", "first")])
    def test_a_choice_reaches_every_panel(self, label, word):
        """MUTATION "the compile drops the choice" (`compile_plan`'s
        autofocus branch removed). Observed:
            AssertionError: assert {'stale'} == {'every'}"""
        plan, _ = _plan(_graph({"when": label}))
        assert len(plan.targets) == 4
        assert {t.autofocus_when for t in plan.targets} == {word}
        assert {t["autofocus_when"] for t in
                plan.model_dump(mode="json")["targets"]} == {word}

    def test_a_choice_survives_the_plan_round_trip(self):
        plan, _ = _plan(_graph({"when": "First panel only"}))
        again = SequencePlan.model_validate(plan.model_dump(mode="json"))
        assert {t.autofocus_when for t in again.targets} == {"first"}

    def test_a_single_target_ignores_the_choice(self):
        """On one target the plan is the one a flow without the choice makes."""
        plain, _ = _plan(_graph(None, rows=1, cols=1))
        chosen, _ = _plan(_graph({"when": "Every panel"}, rows=1, cols=1))
        assert (_sha(chosen.model_dump(mode="json"))
                == _sha(plain.model_dump(mode="json")))

    def test_the_node_is_created_with_the_default_and_names_every_option(self):
        node = NODE_DEFS["autofocus"]
        assert node.params["when"] == AUTOFOCUS_WHEN[0][0] == "When stale"
        assert [label for label, _ in AUTOFOCUS_WHEN] == [
            "When stale", "Every panel", "First panel only"]
        assert autofocus_when(None) == autofocus_when("") == "stale"
        assert autofocus_when(" every PANEL ") == "every"
        assert autofocus_when("nonsense") == "stale"
