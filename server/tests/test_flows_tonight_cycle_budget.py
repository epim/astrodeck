# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Tonight's budget has a row for a FILTER CYCLE (S4 orchestrator ruling 5,
#338; spec section 8 S3 item 5, 5.3, 5.5).

S3 made the budget the place a mosaic's panels and hops are added up, but
rows were made only for a step with an integration goal, and only a CAPTURE
carries one. So the mosaic most people build, a lane of FILTER CYCLE, was
counted nowhere: the eighth Example ("M31 3x2, rotating") resolved with a
measured hop of 160 s answered ``budget: []`` for 6 panels x 20 cycles of
L R G B at 120 s (16 h of shutter) and 120 hops (5.33 h). In a lane of a
cycle AND a capture with a goal, the hops were split among all the steps
but drawn only on the capture's row, so the cycle's share was priced on no
row at all.

S4 orchestrator ruling 5: a FILTER CYCLE has a row, its shutter hours
(cycles x filters x exposure, per panel, times the live panels) plus its
share of the measured hops. What this file holds:

* the eighth Example shows its 16 h and its 5.33 h of hops;
* #338's mixed lane loses no hop share: the block's rows add up to its
  whole hop figure, to the hundredth, however the shares round;
* the cycle has no goal, and the story says so in words, never "0 h goal";
* a pool's copies of one cycle are one row, a single target's cycle is a
  row with no panel keys, and the capture rows keep exactly their keys;
* the hops are counted at the visit the brief states (spec 5.3's
  ``max(passes, ceil(minVisit / pass))``), the number the Target modal
  prints, so one Tonight answer does not describe two visits.

The site is synthetic (40 N 105 W, the place ``test_flows_tonight_mosaic``
uses, NOT the observatory's), the hub is pinned to the same place so no
path in this file can reach the configured site, and the panel stamp is
stood in for: nothing here is about altitude.

Every test names the mutation of ``flows/tonight.py`` it guards and quotes
the failure it produced, each run in a private copy of ``server/``
(scratchpad ``s4-tonight-mut``, from byte backups), never in the shared tree.
"""
from __future__ import annotations

import datetime as _dt

import pytest

from astrodeck.catalog import framing
from astrodeck.flows.compile import compile_plan
from astrodeck.flows.examples import examples
from astrodeck.flows.models import FlowEdge, FlowGraph, FlowNode
from astrodeck.flows.readouts import readouts
from astrodeck.flows.rig import RigFacts
from astrodeck.flows.to_plan import to_sequence_plan
from astrodeck.flows.tonight import _budget, resolve_tonight
from astrodeck.hub import Hub

SITE = {"latitude": 40.0, "longitude": -105.0, "elevation_m": 1600.0,
        "is_default": False}
TWILIGHT = -12.0
#: Late afternoon at the site (13:00 local solar), so "tonight" is ahead.
JUNE = _dt.datetime(2026, 6, 15, 20, 0, tzinfo=_dt.timezone.utc).timestamp()
FLOW = "flow-tonight-cycle-budget"
M16_RA, M16_DEC = "18h 18m 48s", "-13 49 00"
FOV = (2.0, 1.33)


@pytest.fixture(autouse=True)
def synthetic_hub_and_no_ephemeris(monkeypatch):
    """The hub on the synthetic site, and the panel stamp stood in for with
    a constant: a budget is shutter time and hops, and computing panel
    altitudes here would only cost time."""
    monkeypatch.setattr(Hub, "site", property(lambda self: {
        "name": "synthetic", **SITE, "horizon_min_deg": 0.0}))

    async def stamp(panels, date, *, site=None):
        for p in panels:
            p["transit_alt"] = 45.0
    monkeypatch.setattr(framing, "_stamp_transit_alt", stamp)


def _n(nid, ntype, x=0.0, **params):
    return FlowNode(id=nid, type=ntype, x=x, y=0.0, params=params)


def _e(a, ap, b, bp):
    return FlowEdge(**{"from": a, "fromPort": ap, "to": b, "toPort": bp})


def _tonight(graph, **kw):
    kw.setdefault("twilight_deg", TWILIGHT)
    return resolve_tonight(graph, SITE, now=JUNE, **kw)


def _eighth():
    ex = examples()[-1]
    assert ex.id == "example-m31-mosaic", "premise: the eighth Example"
    return ex


def _mosaic(lane, *, rows=2, cols=2, passes=1, min_visit=0, loop=True):
    """dusk -> TARGET M16 (a framed grid at Rotate to PA 30) -> ``lane``, a
    list of ``(id, type, params)`` wired one after another. With ``loop``
    the last stage's pass wire goes back to the block's `next`, so the
    panels rotate every pass; without it they run one at a time."""
    nodes = [_n("d", "dusk", offset=-30, stop="Dawn", minAlt=30),
             _n("t", "target", x=100, name="M16", ra=M16_RA, dec=M16_DEC,
                rotation=30, angle="Rotate to PA", rows=rows, cols=cols,
                overlap=25, fovX=FOV[0], fovY=FOV[1], passes=passes,
                minVisit=min_visit, counts="Accepted subs")]
    edges = [_e("d", "window", "t", "arm")]
    prev, port = "t", "target"
    for i, (nid, ntype, params) in enumerate(lane):
        nodes.append(_n(nid, ntype, x=200 + 100 * i, **params))
        edges.append(_e(prev, port, nid, "run"))
        prev, port = nid, "complete"
    if loop:
        edges.append(_e(prev, "pass", "t", "next"))
    return FlowGraph(nodes=nodes, edges=edges)


def _budget_lines(out):
    return [s["msg"] for s in out["story"] if s["label"] == 'TIME']


class TestTheEighthExampleShowsItsHoursAndHops:
    def test_its_16_h_of_shutter_and_5_33_h_of_hops(self):
        """6 panels x 20 cycles of L R G B at 120 s, one sub a pass: 9600 s a
        panel, 16 h in all; one pass a visit, so 20 visits a panel and 120
        hops, at the measured 160 s 5.33 h. One row, the cycle's, with no
        goal (a FILTER CYCLE sets none) and no ledger read.

        RED under mutant "cycle steps get no row" (``_drawn`` answers False
        for a cycle, the rule S3 shipped), observed:

            AssertionError: assert [] == [{'banked_h':...': None, ...}]
              Right contains one more item: {'banked_h': None, 'cycles': 20,
              'filter': 'L, R, G, B', 'goal_h': None, ...}

        and in nine more tests of this file and ``test_flows_tonight_mosaic``
        ("ValueError: not enough values to unpack (expected 1, got 0)" where
        a test takes the one row).
        """
        out = _tonight(_eighth().graph, hop_cost_s=160.0)
        assert out["budget"] == [{
            "filter": "L, R, G, B", "goal_h": None, "banked_h": None,
            "tonight_h": 16.0, "has_ledger": False, "strategy": "cycle",
            "cycles": 20, "panels": 6, "hop_h": 5.33}]
        assert 16.0 == round(6 * 4 * 120 * 20 / 3600, 2)
        assert 5.33 == round(6 * 20 * 160 / 3600, 2)

    def test_the_story_states_the_goal_in_words(self):
        """The row has no goal, and its sentence says so; it never prints
        "0 h goal", which reads as a goal nobody can miss. Its hours are
        what the cycles owe, not "tonight adds": 16 h is more than any
        night holds.

        RED under mutant "the goal as zero" (the cycle row's ``goal_h`` 0.0
        and its sentence left to the capture branch), observed:

            AssertionError: L, R, G, B: 0 h goal across 6 panels — tonight
            adds ≈16 h, plus ≈5.33 h moving between panels. No session ledger
            was read, so nothing here is counted as already banked
            assert 'no integration goal' in 'L, R, G, B: 0 h goal across 6
            panels — tonight adds ≈16 h, plus ≈5.33 h moving between panels.
            No session ledger was read, so nothing here is counted as already
            banked'

        RED under mutant "hops split over capture rows only" (below), at the
        hop clause: the cycle's line read "... its 20 cycles owe ≈16 h of
        shutter across 6 panels, before the moves between panels, which no
        hop measured on this rig can price yet", with a hop measured.
        """
        (line,) = _budget_lines(_tonight(_eighth().graph, hop_cost_s=160.0))
        assert "no integration goal" in line, line
        assert "its 20 cycles take ≈16 h of exposure across 6 panels" in line, \
            line
        assert "plus ≈5.33 h moving between panels" in line, line
        assert "0 h goal" not in line and "tonight adds" not in line, line
        (unmeasured,) = _budget_lines(_tonight(_eighth().graph))
        assert "whose duration has not been measured on this rig yet" in \
            unmeasured, unmeasured

    def test_a_ledger_reads_the_hours_banked_in_its_filters(self):
        """With a ledger, the row's banked figure is what the ledger holds in
        the cycle's own filters, and Ha, which it does not shoot, is not in
        it.

        RED under mutant "the goal as zero", observed:

            AssertionError: L, R, G, B: 2 h banked / 0 h goal across 6 panels
            — tonight adds ≈16 h, plus ≈5.33 h moving between panels; the
            session ledger resumes the remainder next clear night
        """
        out = _tonight(_eighth().graph, hop_cost_s=160.0,
                       banked=lambda: {"L": 1.5, "R": 0.5, "Ha": 9.0})
        (row,) = out["budget"]
        assert (row["banked_h"], row["has_ledger"]) == (2.0, True)
        (line,) = _budget_lines(out)
        assert "2 h captured in its filters" in line, line


class TestNoHopShareIsLost:
    def test_338s_mixed_lane(self):
        """#338's own case: a rotating 2x2 of a FILTER CYCLE (6 cycles of Ha
        300 s) and a CAPTURE of OIII 300 s x 2 with a 1 h goal. 6 visits a
        panel, 24 hops, 1.07 h at 160 s, shared by shutter time, 1800 s to
        600 s a panel: the cycle's row 0.80 h and OIII's 0.27 h, which add
        up to the block's 1.07 h.

        RED under mutant "hops split over capture rows only" (a cycle row's
        ``hop_h`` None, the capture keeping its share of every step's
        shutter, as S3 split it), observed:

            AssertionError: rows carry [None, 0.27] h of hops; 0.80 h of the
            block's 1.07 h is missing
            assert [None, 0.27] == [0.8, 0.27]
              At index 0 diff: None != 0.8

        RED under mutant "cycle steps get no row", observed:

            AssertionError: assert [('OIII', None, 0.67)] == [('Ha',
            'cycl..., None, 0.67)]
              At index 0 diff: ('OIII', None, 0.67) != ('Ha', 'cycle', 2.0)
        """
        graph = _mosaic([
            ("y", "cycle", dict(plan="Ha 300", cycles=6, perCycle=1,
                                gain=100, bin="1")),
            ("o", "capture", dict(filter="OIII", exposure=300, gain=100,
                                  bin="1", count=2, goal=1))])
        plan, _ = to_sequence_plan(compile_plan(graph, "n"), graph,
                                   flow_id=FLOW)
        visits = {max(-(-s.count // max(1, int(s.per_visit or 1)))
                      for s in t.steps) for t in plan.targets}
        assert (len(plan.targets), visits) == (4, {6}), \
            "premise: four panels, each owing six visits"
        budget = _tonight(graph, hop_cost_s=160.0)["budget"]
        assert [(b["filter"], b.get("strategy"), b["tonight_h"])
                for b in budget] == [("Ha", "cycle", 2.0), ("OIII", None,
                                                            0.67)]
        hops = [b["hop_h"] for b in budget]
        assert hops == [0.8, 0.27], (
            f"rows carry {hops} h of hops; 0.80 h of the block's 1.07 h is "
            f"missing")
        assert round(sum(hops), 2) == round(24 * 160 / 3600, 2) == 1.07

    def test_the_rows_add_up_to_the_hundredth(self):
        """Three rows of equal shutter share a block's 1.00 h of hops (a 2x2,
        6 visits a panel, 24 hops at 150 s). Each third rounded alone is
        0.33, and three of them are 0.99, a hundredth of an hour priced on
        no row. Split as the whole is printed, they are 0.34, 0.33, 0.33:
        the first row takes the odd hundredth.

        RED under mutant "each row rounded alone" (``_shares`` rounds every
        part on its own, as S3 rounded each row), observed:

            AssertionError: [0.33, 0.33, 0.33] add up to 0.99, not the
            block's 1.0
            assert 0.99 == 1.0
             +  where 0.99 = round(0.99, 2)
             +    where 0.99 = sum([0.33, 0.33, 0.33])
        """
        graph = _mosaic([
            ("y", "cycle", dict(plan="Ha 300", cycles=6, perCycle=1,
                                gain=100, bin="1")),
            ("o", "capture", dict(filter="OIII", exposure=300, gain=100,
                                  bin="1", count=6, goal=1)),
            ("s", "capture", dict(filter="SII", exposure=300, gain=100,
                                  bin="1", count=6, goal=1))])
        hops = [b["hop_h"] for b in
                _tonight(graph, hop_cost_s=150.0)["budget"]]
        total = round(4 * 6 * 150 / 3600, 2)
        assert round(sum(hops), 2) == total, (
            f"{hops} add up to {round(sum(hops), 2)}, not the block's {total}")
        assert hops == [0.34, 0.33, 0.33]

    def test_a_step_with_no_row_takes_no_share(self):
        """A capture with no goal has no row, so it takes no share of the
        hops: the one drawn row carries the block's whole 0.18 h (a 2x2 at
        one visit a panel, the lane run panel-first, 4 hops at 160 s).

        RED under mutant "rowless steps keep a share" (the hops split over
        every step's shutter, drawn or not), observed:

            AssertionError: the drawn rows carry [0.09] h; the block's hops
            are 0.18 h
            assert [0.09] == [0.18]
              At index 0 diff: 0.09 != 0.18
        """
        graph = _mosaic([
            ("o", "capture", dict(filter="OIII", exposure=300, gain=100,
                                  bin="1", count=2, goal=1)),
            ("l", "capture", dict(filter="L", exposure=300, gain=100,
                                  bin="1", count=2, goal=0))], loop=False)
        hops = [b["hop_h"] for b in
                _tonight(graph, hop_cost_s=160.0)["budget"]]
        assert hops == [round(4 * 160 / 3600, 2)], (
            f"the drawn rows carry {hops} h; the block's hops are "
            f"{round(4 * 160 / 3600, 2)} h")


class TestTheHopsAreCountedAtTheVisitTheBriefStates:
    def test_a_minimum_visit_makes_fewer_visits(self):
        """The default cycle (L R G B at 60 s, Ha OIII SII at 180 s, 45
        cycles: a 13-minute pass) at one pass a visit and a 30 min minimum:
        a visit lasts three passes, so a panel takes 15 visits, not 45, and
        the 2x2 makes 60 hops, 2.67 h at 160 s. That is the Target modal's
        figure, ``readouts``' ``visits_total``, for the same plan.

        RED under mutant "hops priced at passes alone" (``_visits_per_panel``
        divides the rounds by ``passes``, leaving the minimum out, as S3
        did), observed:

            AssertionError: the budget prices 8.0 h of hops; the modal's 60
            visits at 160 s are 2.67 h
            assert 8.0 == 2.67
        """
        graph = _mosaic([("y", "cycle", {})], min_visit=30)
        compiled = compile_plan(graph, "n")
        plan, _ = to_sequence_plan(compiled, graph, flow_id=FLOW)
        (block,) = readouts(compiled, plan,
                            RigFacts(hop_cost_s=160.0, hop_samples=3)).values()
        assert (block["pass_s"], block["visit_passes"],
                block["visits_total"]) == (780.0, 3, 60), \
            "premise: a 13 minute pass, three a visit, 60 visits"
        (row,) = _tonight(graph, hop_cost_s=160.0)["budget"]
        want = round(block["visits_total"] * 160 / 3600, 2)
        assert row["hop_h"] == want, (
            f"the budget prices {row['hop_h']} h of hops; the modal's "
            f"{block['visits_total']} visits at 160 s are {want} h")

    def test_control_no_minimum_counts_as_before(self):
        """Control: with no minimum the visit is ``passes`` rounds, as
        before, so the 2x2 of the default cycle at one pass a visit makes 45
        visits a panel, 180 hops, 8.0 h at 160 s. Green on the code and
        under the mutant above."""
        (row,) = _tonight(_mosaic([("y", "cycle", {})]),
                          hop_cost_s=160.0)["budget"]
        assert row["hop_h"] == round(4 * 45 * 160 / 3600, 2) == 8.0


class TestPoolsSinglesAndCaptures:
    def test_a_pools_copies_of_one_cycle_are_one_row(self):
        """The campaign Example: a POOL of four candidates and one FILTER
        CYCLE (the default, 9.75 h a target). Each candidate carries a copy
        of the cycle, and one is shot a night, so the budget has ONE row of
        9.75 h, with no panel keys: a pool is not a mosaic.

        RED under mutant "cycle rows not deduped" (the cycle row appended
        without its signature check), observed:

            AssertionError: assert [('L, R, G, B..., SII', 9.75)] == [('L, R,
            G, B..., SII', 9.75)]
              Left contains 3 more items, first extra item: ('L, R, G, B,
              Ha, OIII, SII', 9.75)
        """
        ex = next(e for e in examples() if e.id == "example-campaign")
        budget = _budget(compile_plan(ex.graph, ex.name), None, 160.0)
        assert [(b["filter"], b["tonight_h"]) for b in budget] == [
            ("L, R, G, B, Ha, OIII, SII", 9.75)]
        (row,) = budget
        assert set(row) == {"filter", "goal_h", "banked_h", "tonight_h",
                            "has_ledger", "strategy", "cycles"}
        assert (row["goal_h"], row["cycles"]) == (None, 45)

    def test_a_single_targets_cycle_is_a_row_without_panels(self):
        """The M33 cycle Example: one target, the default cycle, 9.75 h, and
        no ``panels`` or ``hop_h``: a single target makes no hop.

        RED under mutant "cycle steps get no row", observed:

            ValueError: not enough values to unpack (expected 1, got 0)
        """
        ex = next(e for e in examples() if e.id == "example-cycle")
        (row,) = _budget(compile_plan(ex.graph, ex.name), None, 160.0)
        assert (row["tonight_h"], row["strategy"], "panels" in row,
                "hop_h" in row) == (9.75, "cycle", False, False)

    def test_control_capture_rows_keep_their_keys(self):
        """Control: a capture row, single or mosaic, has exactly the keys it
        had, and no cycle key reaches it. Green on the code and under every
        mutant in this file."""
        graph = _mosaic([("o", "capture", dict(filter="OIII", exposure=300,
                                               gain=100, bin="1", count=2,
                                               goal=1))])
        (row,) = _tonight(graph, hop_cost_s=160.0)["budget"]
        assert set(row) == {"filter", "goal_h", "banked_h", "tonight_h",
                            "has_ledger", "panels", "hop_h"}
        assert (row["goal_h"], row["tonight_h"]) == (4.0, 0.67)

    def test_a_cycle_with_no_filter_has_no_row(self):
        """A FILTER CYCLE with no slot ticked shoots nothing (``to_plan``
        refuses it), so it has no row: a "0 h" row would describe a stage
        the run will not start.

        RED under mutant "empty cycle drawn" (``_drawn`` answers True for
        every cycle), observed:

            AssertionError: assert [{'banked_h':...': None, ...}] == []
              Left contains one more item: {'banked_h': None, 'cycles': 6,
              'filter': '—', 'goal_h': None, ...}
        """
        graph = _mosaic([("y", "cycle", dict(plan="", cycles=6))])
        assert _tonight(graph, hop_cost_s=160.0)["budget"] == []

    def test_a_filter_shot_twice_in_a_cycle_is_named_and_banked_once(self):
        """A cycle may shoot one filter in two slots ("L 60, L 300, R 60":
        ``parse_cycle_plan`` keeps both). The row names each filter once,
        "L, R", and its banked figure is the ledger's L and R once each,
        1.0 + 0.5 = 1.5 h: counting L's hours twice would promise the
        operator an hour of L nobody shot.

        RED under mutant "no slot dedupe" (``_slot_filters`` keeps every
        slot's filter), observed (S4-TONIGHT verifier, scratchpad
        ``s4-tonight-verify-mut``):

            AssertionError: assert ('L, L, R', 2.5) == ('L, R', 1.5)
              At index 0 diff: 'L, L, R' != 'L, R'
        """
        graph = _mosaic([("y", "cycle", dict(plan="L 60, L 300, R 60",
                                             cycles=6, perCycle=1, gain=100,
                                             bin="1"))])
        (row,) = _tonight(graph, hop_cost_s=160.0,
                          banked=lambda: {"L": 1.0, "R": 0.5})["budget"]
        assert (row["filter"], row["banked_h"]) == ("L, R", 1.5)
        assert row["tonight_h"] == round(4 * 6 * 420 / 3600, 2), \
            "premise: every slot's shutter is counted, L's two included"


class TestAPassCountThatIsNoNumber:
    @pytest.mark.parametrize("passes", ["inf", "nan"])
    def test_tonight_still_answers_and_prices_one_pass_a_visit(self, passes):
        """A TARGET's ``passes`` is read through the compile's ``_num``,
        which hands an infinity or a NaN on for "inf" and "nan" (only the
        counts are read finite-only, #328). Since S4 the brief asks every
        rotating block for its visit (``_visit_passes``), so an ``int()``
        of that value would take the whole Tonight answer down, the route
        a 500 (#362's class). ``_count`` reads it as 1 pass: 6 visits a
        panel, 24 hops, 1.07 h at 160 s, what passes 1 prices.

        RED under mutant "no infinity guard" (``_count`` without its
        ``isfinite`` check), observed (S4-TONIGHT verifier, scratchpad
        ``s4-tonight-verify-mut``):

            [inf] OverflowError: cannot convert float infinity to integer
            [nan] ValueError: cannot convert float NaN to integer
        """
        graph = _mosaic([("y", "cycle", dict(plan="Ha 300", cycles=6,
                                             perCycle=1, gain=100, bin="1"))],
                        passes=passes)
        out = _tonight(graph, hop_cost_s=160.0)
        assert out["ok"], out["reason"]
        (row,) = out["budget"]
        assert row["hop_h"] == round(4 * 6 * 160 / 3600, 2) == 1.07
        assert "M16 is a 2x2 mosaic of 4 panels" in out["brief"], out["brief"]


class TestTheStorySaysOneCycleInTheSingular:
    def test_one_cycle_owes(self):
        """A cycle of 1 reads "its 1 cycle owes", not "its 1 cycle owe".

        RED under mutant "one cycle owe" (the verb left plural whatever the
        count, as S4-TONIGHT first wrote it), observed (S4-TONIGHT
        verifier, scratchpad ``s4-tonight-verify-mut``):

            AssertionError: Ha cycle: no integration goal (a FILTER CYCLE
            sets none) — its 1 cycle owe ≈0.33 h of shutter across 4
            panels, plus ≈0.18 h moving between panels. ...

        The eighth Example's "its 20 cycles owe" (above) is the control:
        green on the code and under the mutant.
        """
        graph = _mosaic([("y", "cycle", dict(plan="Ha 300", cycles=1,
                                             perCycle=1, gain=100, bin="1"))])
        (line,) = _budget_lines(_tonight(graph, hop_cost_s=160.0))
        assert "its 1 cycle takes ≈0.33 h of exposure across 4 panels" in \
            line, line
