"""Tonight's mosaic branch (#189 U-10, spec 1.2, 3.2, S3 item 5, 5.5, 6.9).

A multi-panel TARGET compiles to ONE entry, and Tonight has to read it as
one block, not as one target and not as N:

* ONE ``compute_night`` PER BLOCK, on its centre: one curve, one window, one
  flip. A night per panel would be N astropy passes for N curves the
  timeline cannot tell apart at its scale;
* each shot panel's PEAK ALTITUDE, stamped by ``framing._stamp_transit_alt``
  on the panel centres ``compute_mosaic`` lays out (the run's own panels),
  and the BAND the timeline draws from the worst and the best of them;
* a BUDGET that counts every live panel: each panel owes the step, so the
  block owes live panels times the per-panel figure, plus the hops between
  them once a hop has been measured. Deduped like a pool's copies, a 2x2
  would promise one panel's hours for four panels' work;
* CAMPAIGN rows per panel from the injected progress answer, with no pool.

Single targets and pools must read exactly as they did: the controls at the
bottom pin that the new arguments never reach them.

The site is pinned twice, to the same synthetic place (40 N 105 W, the one
``test_flows_tonight.py`` uses, NOT the observatory's): once as the
``site`` argument and once as the hub's site, because
``framing._stamp_transit_alt`` reads the hub's (see ``_mosaic_night``). No
test here reads the configured site.

Every test names the mutation of ``flows/tonight.py`` it guards and quotes
the failure that mutation produced, run in a private copy of ``server/``
(``scratchpad/s3-t-tonight-q8m4/``; the cases the verifier added in
``scratchpad/s3-t-verify-x4n7/``), never in the shared tree.
"""
from __future__ import annotations

import copy
import datetime as _dt
import math

import pytest

from astrodeck.catalog import framing, visibility
from astrodeck.flows.compile import compile_plan
from astrodeck.flows.models import FlowEdge, FlowGraph, FlowNode
from astrodeck.flows.progress import flow_progress
from astrodeck.flows.to_plan import to_sequence_plan
from astrodeck.flows.tonight import _night_date, resolve_tonight
from astrodeck.hub import Hub
from astrodeck.sequence.session import Session, SessionFrame

SITE = {"latitude": 40.0, "longitude": -105.0, "elevation_m": 1600.0,
        "is_default": False}
TWILIGHT = -12.0
#: Late afternoon at the site (13:00 local solar), so "tonight" is ahead.
JUNE = _dt.datetime(2026, 6, 15, 20, 0, tzinfo=_dt.timezone.utc).timestamp()
FLOW = "flow-tonight-mosaic"

#: M16's position, typed the way an operator types it, and its hours.
M16_RA, M16_DEC = "18h 18m 48s", "-13 49 00"
M16_RA_H, M16_DEC_D = 18.0 + 18 / 60 + 48 / 3600, -(13 + 49 / 60)
FOV = (2.0, 1.33)


@pytest.fixture(autouse=True)
def hub_site(monkeypatch):
    """The hub's site is the synthetic one ``resolve_tonight`` is handed, so
    the panel altitudes ``_stamp_transit_alt`` computes are for the same
    place as the curves. Patched on the property, as ``test_framing.py``
    does, and put back by monkeypatch. Needed because the stamp takes no
    site of its own (#336)."""
    monkeypatch.setattr(Hub, "site", property(lambda self: {
        "name": "synthetic", **SITE, "horizon_min_deg": 0.0}))


def _n(nid, ntype, x=0.0, y=0.0, **params):
    return FlowNode(id=nid, type=ntype, x=x, y=y, params=params)


def _e(a, ap, b, bp):
    return FlowEdge(**{"from": a, "fromPort": ap, "to": b, "toPort": bp})


def _mosaic_flow(*, rows=2, cols=2, skip="", loop=True, passes=1,
                 angle="Rotate to PA", rotation=30, fov=FOV, count=10,
                 exposure=300, goal=5, oiii=False, single=False):
    """dusk -> TARGET M16 (a grid) -> CAPTURE Ha [-> CAPTURE OIII]
    [-> TARGET M31, a single target, with its own CAPTURE].

    ``loop`` draws the pass wire from the lane's tail into the block's
    `next`, so the panels rotate every pass; without it they run one at a
    time. ``oiii`` adds a second goal step to the lane; ``single`` puts a
    plain target after the mosaic, owning its own capture."""
    nodes = [_n("d", "dusk", offset=-30, stop="Dawn", minAlt=30),
             _n("t", "target", x=100, name="M16", ra=M16_RA, dec=M16_DEC,
                rotation=rotation, angle=angle, rows=rows, cols=cols,
                overlap=25, fovX=fov[0], fovY=fov[1], skip=skip,
                passes=passes, counts="Accepted subs"),
             _n("c", "capture", x=200, filter="Ha", exposure=exposure,
                gain=100, bin="1", count=count, goal=goal)]
    edges = [_e("d", "window", "t", "arm"), _e("t", "target", "c", "run")]
    tail = "c"
    if oiii:
        nodes.append(_n("o", "capture", x=300, filter="OIII",
                        exposure=exposure, gain=100, bin="1", count=count,
                        goal=goal))
        edges.append(_e("c", "complete", "o", "run"))
        tail = "o"
    if loop:
        edges.append(_e(tail, "pass", "t", "next"))
    if single:
        nodes += [_n("u", "target", x=400, name="M31", ra="00h 42m 44s",
                     dec="+41 16 09", rotation=-1, counts="Accepted subs"),
                  _n("k", "capture", x=500, filter="L", exposure=60, gain=100,
                     bin="1", count=5, goal=0)]
        edges += [_e(tail, "complete", "u", "arm"),
                  _e("u", "target", "k", "run")]
    return FlowGraph(nodes=nodes, edges=edges)


def _tonight(graph, **kw):
    kw.setdefault("twilight_deg", TWILIGHT)
    return resolve_tonight(graph, SITE, now=JUNE, **kw)


def _block(out, node_name="M16"):
    return next(t for t in out["targets"] if t["label"] == node_name)


def _stub_stamp(alts):
    """A stand-in for ``framing._stamp_transit_alt`` that stamps each panel
    with ``alts[(row, col)]``, or with an error where that is None, and runs
    no ephemeris, so a count of ``compute_night`` calls is the resolver's
    own."""
    async def stamp(panels, date):
        for p in panels:
            alt = alts((p["row"], p["col"])) if callable(alts) \
                else alts[(p["row"], p["col"])]
            if alt is None:
                p["transit_alt_error"] = "stub: no altitude"
            else:
                p["transit_alt"] = alt
    return stamp


# ------------------------------------------------------- one night per block

class TestOneNightPerBlock:
    def test_a_multi_panel_target_is_one_compute_night(self, monkeypatch):
        """A 2x2 is one block: ONE ``compute_night``, on the block's centre.
        With a single target after it, two: one per block, never one per
        panel.

        The stamp is stood in for here, so that its own per-panel passes
        (``transit_alt_for`` runs ``compute_night`` too) are not counted:
        what is spied is the resolver's.

        Mutant "one per panel" (a ``compute_night`` for every live panel of
        a mosaic, the per-target loop the spec retires) failed:
            E   AssertionError: [(18.313333333333333, -13.816666666666666),
                (18.313333333333333, -13.816666666666666), (18.313333333333333,
                -13.816666666666666), (18.313333333333333, -13.816666666666666)]
            E   assert 4 == 1
        """
        calls = []
        real = visibility.compute_night

        def spy(ra, dec, **kw):
            calls.append((ra, dec))
            return real(ra, dec, **kw)
        monkeypatch.setattr(visibility, "compute_night", spy)
        monkeypatch.setattr(framing, "_stamp_transit_alt",
                            _stub_stamp(lambda rc: 40.0 + rc[0] + rc[1]))

        _tonight(_mosaic_flow())
        assert len(calls) == 1, calls
        assert calls[0] == (pytest.approx(M16_RA_H, abs=1e-6),
                            pytest.approx(M16_DEC_D, abs=1e-6)), \
            "the one night is the block's centre"

        calls.clear()
        _tonight(_mosaic_flow(single=True))
        assert len(calls) == 2, calls


# ------------------------------------------------------- panel peak altitudes

class TestPanelAltitudes:
    def test_each_shot_panel_is_stamped_at_its_centre(self, monkeypatch):
        """Each shot panel's peak altitude is ``framing._stamp_transit_alt``'s,
        run once on the panel centres ``compute_mosaic`` lays out from the
        block (the panels ``to_plan`` gives the run), for the night the
        resolver pinned, and the value on the row is what the stamp wrote.

        The stamp runs for real, so each value is also checked against the
        visibility module asked directly for that centre at the site the
        night was resolved for: the two agree only because the hub's site is
        that site (``hub_site``).

        Mutant "the centre's altitude on every panel" (every panel handed to
        the stamp at the block's centre, so each gets the centre's peak)
        failed:
            E   assert 18.313333333333333 == 18.251641860806885 ± 1.0e-09
            E     comparison failed
            E     Obtained: 18.313333333333333
            E     Expected: 18.251641860806885 ± 1.0e-09
        """
        seen = []
        real = framing._stamp_transit_alt

        async def spy(panels, date):
            seen.append((copy.deepcopy(panels), date))
            await real(panels, date)
        monkeypatch.setattr(framing, "_stamp_transit_alt", spy)

        out = _tonight(_mosaic_flow())
        mosaic = _block(out)["mosaic"]

        layout = framing.compute_mosaic({
            "ra_hours": M16_RA_H, "dec_deg": M16_DEC_D, "rows": 2, "cols": 2,
            "overlap": 0.25, "rotation_deg": 30.0, "fov_x_deg": FOV[0],
            "fov_y_deg": FOV[1]})["panels"]
        want = sorted((p["row"], p["col"], p["ra_hours"], p["dec_deg"])
                      for p in layout)
        assert len(seen) == 1, "one stamp for the block"
        panels, date = seen[0]
        got = sorted((p["row"], p["col"], p["ra_hours"], p["dec_deg"])
                     for p in panels)
        assert [g[:2] for g in got] == [w[:2] for w in want]
        for g, w in zip(got, want):
            assert g[2] == pytest.approx(w[2], abs=1e-9)
            assert g[3] == pytest.approx(w[3], abs=1e-9)
        assert date == _night_date(JUNE, SITE["longitude"]), \
            "the stamp answers for the night the resolver pinned"

        alts = {(p["row"], p["col"]): p["transit_alt"]
                for p in mosaic["panels"]}
        assert len(set(alts.values())) > 1, \
            "premise: the panels peak at different altitudes"
        for row, col, ra, dec in got:
            assert alts[(row, col)] == visibility.transit_alt_for(
                ra, dec, date=date, site=SITE)
        assert [p["panel"] for p in mosaic["panels"]] == \
            ["1-1", "1-2", "2-1", "2-2"], "grid order, 1-based labels"

    def test_a_skipped_panel_is_listed_and_not_stamped(self, monkeypatch):
        """A skipped panel is not shot, so it is not stamped: it is named in
        ``skipped``, and ``live`` counts the three that are.

        Mutant "skipped panels stamped" (the skip filter dropped from the
        panels handed to the stamp) failed:
            E   assert [(0, 0), (0, ...1, 0), (1, 1)] == [(0, 0), (0, 1), (1, 1)]
            E     At index 2 diff: (1, 0) != (1, 1)
            E     Left contains one more item: (1, 1)
        """
        stamped = []

        async def stamp(panels, date):
            stamped.extend((p["row"], p["col"]) for p in panels)
            for p in panels:
                p["transit_alt"] = 45.0
        monkeypatch.setattr(framing, "_stamp_transit_alt", stamp)

        mosaic = _block(_tonight(_mosaic_flow(skip="2-1")))["mosaic"]
        assert sorted(stamped) == [(0, 0), (0, 1), (1, 1)]
        assert [p["panel"] for p in mosaic["panels"]] == ["1-1", "1-2", "2-2"]
        assert mosaic["skipped"] == ["2-1"]
        assert (mosaic["rows"], mosaic["cols"], mosaic["live"]) == (2, 2, 3)

    def test_a_block_that_cannot_be_laid_out_says_why(self, monkeypatch):
        """No camera field (M1): the panels cannot be placed, so none is
        stamped and there is no band, and ``reason`` says why. The block's
        centre still gets its night, so Tonight still draws it.

        Control, not a mutant's guard: it pins that a block the run would
        refuse is drawn without guessed panels."""
        monkeypatch.setattr(framing, "_stamp_transit_alt",
                            _stub_stamp(lambda rc: 45.0))
        row = _block(_tonight(_mosaic_flow(fov=(0, 0))))
        assert row["mosaic"]["panels"] == [] and row["mosaic"]["band"] is None
        assert "camera field" in row["mosaic"]["reason"]
        assert row["resolved"] is True and row["curve"], \
            "the centre's night is still drawn"

    def test_a_block_at_no_angle_or_no_position_says_why(self, monkeypatch):
        """A grid at "Any angle" (M2) and a grid whose name places nothing are
        drawn without panels and say why, rather than taking the Tonight
        panel down: ``compute_mosaic`` cannot lay out panels at no angle
        (its spec holds a float), and there is no centre to lay them round.

        Mutant "no angle guard" (the layout-angle check skipped, so the
        panels are laid out at None) failed:
            E   pydantic_core._pydantic_core.ValidationError: 1 validation
                error for MosaicSpecIn
            E   rotation_deg
            E     Input should be a valid number [type=float_type,
                input_value=None, input_type=NoneType]
        Mutant "no coordinates, no reason" (the no-coordinates sentence
        dropped) failed:
            E   KeyError: 'reason'
        """
        monkeypatch.setattr(framing, "_stamp_transit_alt",
                            _stub_stamp(lambda rc: 45.0))
        row = _block(_tonight(_mosaic_flow(angle="Any angle", rotation=-1)))
        assert row["mosaic"]["panels"] == [] and row["mosaic"]["band"] is None
        assert "no camera angle" in row["mosaic"]["reason"]
        assert row["resolved"] is True and row["curve"], \
            "the centre's night is still drawn"

        nowhere = _mosaic_flow()
        nowhere.nodes[1].params.update(name="Nowhere", ra="", dec="")
        row = _block(_tonight(nowhere, resolve_name=lambda name: None),
                     "Nowhere")
        assert row["resolved"] is False
        assert row["mosaic"]["panels"] == [] and row["mosaic"]["live"] == 4
        assert "no coordinates" in row["mosaic"]["reason"]


class TestResolvedInsideARunningLoop:
    async def test_a_caller_inside_a_loop_still_gets_the_panels(
            self, monkeypatch):
        """The route runs the resolver on a worker thread, where no loop
        runs; a caller already inside a running loop (this test is one) must
        still get its panels stamped, on a loop of the stamp's own, not a
        RuntimeError out of the Tonight panel (``_run_sync``).

        Mutant "no loop of its own" (``_run_sync`` always calls
        ``asyncio.run``) failed:
            E   RuntimeError: asyncio.run() cannot be called from a running
                event loop
        """
        monkeypatch.setattr(framing, "_stamp_transit_alt",
                            _stub_stamp(lambda rc: 40.0 + rc[0]))
        mosaic = _block(_tonight(_mosaic_flow()))["mosaic"]
        assert [(p["panel"], p["transit_alt"]) for p in mosaic["panels"]] == [
            ("1-1", 40.0), ("1-2", 40.0), ("2-1", 41.0), ("2-2", 41.0)]


class TestTheBand:
    def test_the_band_runs_from_the_worst_panel_to_the_best(self, monkeypatch):
        """The band is the spread the centre's one curve cannot show: the
        lowest peak and the highest, each named. On a tie the panel first in
        grid order is named (1-2 before 2-2 here). A panel the stamp could
        not answer carries its reason and stays out of the band.

        Mutant "worst and best swapped" (``_band`` returns the highest peak
        as the worst and the lowest as the best) failed:
            E   AssertionError: assert {'best': {'co...t_alt': 70.0}} ==
                {'best': {'co...t_alt': 30.0}}
            E     Differing items:
            E     {'worst': {'col': 0, 'panel': '2-1', 'row': 1, 'transit_alt':
                70.0}} != {'worst': {'col': 1, 'panel': '1-2', 'row': 0,
                'transit_alt': 30.0}}
        """
        monkeypatch.setattr(framing, "_stamp_transit_alt", _stub_stamp({
            (0, 0): 50.0, (0, 1): 30.0, (1, 0): 70.0, (1, 1): 30.0}))
        band = _block(_tonight(_mosaic_flow()))["mosaic"]["band"]
        assert band == {
            "worst": {"panel": "1-2", "row": 0, "col": 1, "transit_alt": 30.0},
            "best": {"panel": "2-1", "row": 1, "col": 0, "transit_alt": 70.0}}

        monkeypatch.setattr(framing, "_stamp_transit_alt", _stub_stamp({
            (0, 0): None, (0, 1): 33.0, (1, 0): None, (1, 1): None}))
        mosaic = _block(_tonight(_mosaic_flow()))["mosaic"]
        assert mosaic["band"]["worst"]["panel"] == "1-2" == \
            mosaic["band"]["best"]["panel"]
        assert mosaic["panels"][0] == {"panel": "1-1", "row": 0, "col": 0,
                                       "transit_alt_error": "stub: no altitude"}

    def test_no_panel_answered_is_no_band(self, monkeypatch):
        monkeypatch.setattr(framing, "_stamp_transit_alt",
                            _stub_stamp(lambda rc: None))
        assert _block(_tonight(_mosaic_flow()))["mosaic"]["band"] is None


# ------------------------------------------------------------------- budget

class TestTheBudgetCountsEveryPanel:
    @pytest.fixture(autouse=True)
    def _no_ephemeris(self, monkeypatch):
        monkeypatch.setattr(framing, "_stamp_transit_alt",
                            _stub_stamp(lambda rc: 45.0))

    def test_live_panels_times_the_per_panel_figure(self):
        """A 2x2 with one panel skipped shoots three, and each owes Ha 300 s x
        10 with a 5 h goal: the row is 3 x 5 = 15 h of goal and 3 x 0.83 h =
        2.5 h tonight, and says it counted three panels.

        Mutant "dedupe panels" (the mosaic's row counted once, as a pool's
        copies are) failed:
            E     At index 0 diff: {'filter': 'Ha', 'goal_h': 5.0, 'banked_h':
                None, 'tonight_h': 0.83, 'has_ledger': False, 'panels': 3,
                'hop_h': None} != {'filter': 'Ha', 'goal_h': 15.0, 'banked_h':
                None, 'tonight_h': 2.5, 'has_ledger': False, 'panel
        Mutant "skips counted as shot" (live = rows x cols) failed:
            E     At index 0 diff: {'filter': 'Ha', 'goal_h': 20.0, 'banked_h':
                None, 'tonight_h': 3.33, 'has_ledger': False, 'panels': 4,
                'hop_h': None} != {'filter': 'Ha', 'goal_h': 15.0, 'banked_h':
                None, 'tonight_h': 2.5, 'has_ledger': False, 'pane
        """
        budget = _tonight(_mosaic_flow(skip="2-1"))["budget"]
        assert budget == [{"filter": "Ha", "goal_h": 15.0, "banked_h": None,
                           "tonight_h": 2.5, "has_ledger": False,
                           "panels": 3, "hop_h": None}]

    def test_hops_are_added_once_a_hop_is_measured(self):
        """With no measured hop the row says nothing about hops (None). With
        a measured 160 s hop, a rotating 2x2 of Ha x 10 at one pass a visit
        makes 10 visits a panel, 40 hops in all: 40 x 160 s = 1.78 h. Run
        one panel at a time (no loop wire) it is 4 hops, 0.18 h; at two
        passes a visit, 20 hops, 0.89 h.

        Mutant "hops ignored" (``hop_h`` left None when a cost is passed)
        failed:
            E   assert None == 1.78
            E    +  where 1.78 = round(((40 * 160) / 3600), 2)
        """
        assert _tonight(_mosaic_flow())["budget"][0]["hop_h"] is None
        rot = _tonight(_mosaic_flow(), hop_cost_s=160.0)["budget"][0]
        assert rot["hop_h"] == round(40 * 160 / 3600, 2) == 1.78
        assert rot["tonight_h"] == round(4 * 3000 / 3600, 2), \
            "tonight_h stays shutter time: hops are their own figure"
        seq = _tonight(_mosaic_flow(loop=False), hop_cost_s=160.0)["budget"][0]
        assert seq["hop_h"] == round(4 * 160 / 3600, 2)
        two = _tonight(_mosaic_flow(passes=2), hop_cost_s=160.0)["budget"][0]
        assert two["hop_h"] == round(20 * 160 / 3600, 2)

    def test_a_blocks_hops_are_shared_by_its_rows_not_repeated(self):
        """Ha and OIII in one lane share every visit, so they share its hops:
        each row carries half of the block's 40 hops, and the two add up to
        the block's 1.78 h, not twice it.

        Mutant "each row carries every hop" (the shutter share dropped)
        failed:
            E   assert [1.78, 1.78] == [0.89, 0.89]
            E     At index 0 diff: 1.78 != 0.89
        """
        budget = _tonight(_mosaic_flow(oiii=True), hop_cost_s=160.0)["budget"]
        assert [b["filter"] for b in budget] == ["Ha", "OIII"]
        assert [b["hop_h"] for b in budget] == [0.89, 0.89]
        assert sum(b["hop_h"] for b in budget) == pytest.approx(
            40 * 160 / 3600, abs=0.01)

    def test_a_mosaics_rows_are_its_own(self):
        """Two mosaics and a single target, all with the same Ha recipe and
        goal: three rows. A pool's copies of one step are deduped, but each
        mosaic's step is owed by its own panels (spec 1.5 gives a block's
        stages to it alone), so a mosaic's row is neither dropped as a copy
        of another's nor used to drop the single target's.

        Mutant "mosaic rows dedupe too" (the recipe dedupe applied to a
        mosaic's rows as to every other) failed:
            E   assert [(20.0, 3.33, 4)] == [(20.0, 3.33,..., 0.83, None)]
            E     Right contains 2 more items, first extra item: (20.0, 3.33,
                4)
        """
        ha = dict(filter="Ha", exposure=300, gain=100, bin="1", count=10,
                  goal=5)
        grid = dict(rotation=30, angle="Rotate to PA", rows=2, cols=2,
                    overlap=25, fovX=FOV[0], fovY=FOV[1],
                    counts="Accepted subs")
        graph = FlowGraph(
            nodes=[_n("d", "dusk", offset=-30, stop="Dawn", minAlt=30),
                   _n("t1", "target", x=100, name="M16", ra=M16_RA,
                      dec=M16_DEC, **grid),
                   _n("c1", "capture", x=200, **ha),
                   _n("t2", "target", x=300, name="M17", ra="18h 20m 48s",
                      dec="-16 10 00", **grid),
                   _n("c2", "capture", x=400, **ha),
                   _n("u", "target", x=500, name="M8", ra="18h 03m 37s",
                      dec="-24 23 12", rotation=-1),
                   _n("k", "capture", x=600, **ha)],
            edges=[_e("d", "window", "t1", "arm"),
                   _e("t1", "target", "c1", "run"),
                   _e("c1", "complete", "t2", "arm"),
                   _e("t2", "target", "c2", "run"),
                   _e("c2", "complete", "u", "arm"),
                   _e("u", "target", "k", "run")])
        budget = _tonight(graph)["budget"]
        assert [(b["goal_h"], b["tonight_h"], b.get("panels"))
                for b in budget] == [(20.0, 3.33, 4), (20.0, 3.33, 4),
                                     (5.0, 0.83, None)]

    def test_a_cycle_owes_a_visit_per_round(self):
        """A rotating lane of a FILTER CYCLE (6 cycles of Ha, one sub a pass)
        and a CAPTURE of OIII x 2 with a goal: a panel owes as many rounds as
        its most-served step, the cycle's 6, so 6 visits a panel and 24 hops
        for the 2x2 (the engine's ``_visits_owed`` with nothing banked, read
        here off the plan the run would be handed). Only OIII has a goal, so
        it is the one row, and it carries its shutter share of the hops,
        600 s of the panel's 2400 s: a quarter of 24 x 160 s.

        Mutant "cycle visits are one" (a cycle step owes one round) failed:
            E   assert 0.09 == 0.27
            E    +  where 0.27 = round(((((4 * 6) * 160) / 3600) * 0.25), 2)
        """
        graph = FlowGraph(
            nodes=[_n("d", "dusk", offset=-30, stop="Dawn", minAlt=30),
                   _n("t", "target", x=100, name="M16", ra=M16_RA,
                      dec=M16_DEC, rotation=30, angle="Rotate to PA", rows=2,
                      cols=2, overlap=25, fovX=FOV[0], fovY=FOV[1],
                      passes=1, counts="Accepted subs"),
                   _n("y", "cycle", x=200, plan="Ha 300", cycles=6,
                      perCycle=1, gain=100, bin="1"),
                   _n("o", "capture", x=300, filter="OIII", exposure=300,
                      gain=100, bin="1", count=2, goal=1)],
            edges=[_e("d", "window", "t", "arm"),
                   _e("t", "target", "y", "run"),
                   _e("y", "complete", "o", "run"),
                   _e("o", "pass", "t", "next")])
        plan, _ = to_sequence_plan(compile_plan(graph, "n"), graph,
                                   flow_id=FLOW)
        (group,) = plan.groups
        assert group.mode == "rotate", "premise: the pass wire rotates"
        visits = {max(-(-s.count // max(1, int(s.per_visit or 1)))
                      for s in t.steps) for t in plan.targets}
        assert visits == {6}, "premise: the run owes 6 visits a panel"

        (row,) = _tonight(graph, hop_cost_s=160.0)["budget"]
        assert (row["filter"], row["goal_h"], row["panels"]) == ("OIII", 4.0,
                                                                  4)
        assert row["hop_h"] == round(4 * 6 * 160 / 3600 * 0.25, 2)

    @pytest.mark.parametrize("cost", [0, -5.0, math.nan, math.inf, "160",
                                      True])
    def test_a_cost_that_is_no_measurement_prices_no_hop(self, cost):
        """0, a negative, NaN, infinity, text and a bool are not a measured
        hop: the row says "not measured" (None), never a free hop."""
        assert _tonight(_mosaic_flow(), hop_cost_s=cost)["budget"][0][
            "hop_h"] is None

    def test_the_story_says_the_figures_are_every_panels(self):
        """The BUDGET sentence names the panels and the hops, measured or
        not, so the multiplied figure is not read as one panel's."""
        def line(**kw):
            return next(s["msg"] for s in _tonight(_mosaic_flow(), **kw)[
                "story"] if s["label"] == "BUDGET")
        assert "20 h goal across 4 panels" in line()
        assert "no hop measured on this rig" in line()
        assert "plus ≈1.78 h moving between panels" in line(hop_cost_s=160)

    def test_every_panel_skipped_is_no_row(self):
        """Every panel skipped: ``to_plan`` leaves the block out, so there is
        no row, rather than a "0 h goal" that reads as unmeetable."""
        assert _tonight(_mosaic_flow(skip="1-1, 1-2, 2-1, 2-2"))[
            "budget"] == []


# ----------------------------------------------------------------- campaign

def _progress_for(graph, frames_on):
    """The flow's progress answer, made the way the route makes it: the
    graph compiled with the flow's id, a dormant session of that plan in
    accepted mode, and ``frames_on[(row, col)]`` accepted frames on that
    panel's one step."""
    compiled = compile_plan(graph, "n")
    plan, _ = to_sequence_plan(compiled, graph, flow_id=FLOW)
    frames = []
    for t in plan.targets:
        n = frames_on.get((t.panel_row, t.panel_col), 0)
        frames += [SessionFrame(target_id=t.id, step_id=t.steps[0].id)
                   for _ in range(n)]
    session = Session(id="s-1", status="dormant", nights=["n1"],
                      plan=plan.model_copy(update={"count_mode": "accepted"}),
                      frames=frames, origin="flow", origin_id=FLOW)
    return flow_progress(compiled, plan, session, flow_id=FLOW)


class TestCampaignRowsPerPanel:
    @pytest.fixture(autouse=True)
    def _no_ephemeris(self, monkeypatch):
        monkeypatch.setattr(framing, "_stamp_transit_alt",
                            _stub_stamp(lambda rc: 45.0))

    def test_rows_come_from_the_progress_with_no_pool(self):
        """A mosaic flow has no pool, and its CAMPAIGN tab still has a row per
        panel, straight off the progress answer: 1-1 done at 10 of 10, 1-2
        at 4 of 10, 2-2 untouched, and the skipped 2-1 with the 3 subs it
        held before it was skipped.

        Mutant "require a pool" (the no-pool answer returned for every flow
        without a pool, as before S3: ``has_pool`` False and nothing else)
        failed, and in five more tests of this class:
            E   KeyError: 'has_progress'
        """
        graph = _mosaic_flow(skip="2-1")
        unskipped = _mosaic_flow()
        # The frames on 2-1 were banked before the skip: the session's plan
        # is the unskipped one, the compile is the skipped one.
        compiled = compile_plan(graph, "n")
        plan, _ = to_sequence_plan(compiled, graph, flow_id=FLOW)
        old, _ = to_sequence_plan(compile_plan(unskipped, "n"), unskipped,
                                  flow_id=FLOW)
        on = {(0, 0): 10, (0, 1): 4, (1, 0): 3}
        frames = [SessionFrame(target_id=t.id, step_id=t.steps[0].id)
                  for t in old.targets
                  for _ in range(on.get((t.panel_row, t.panel_col), 0))]
        session = Session(id="s-1", status="dormant", nights=["n1"],
                          plan=old.model_copy(update={"count_mode": "accepted"}),
                          frames=frames, origin="flow", origin_id=FLOW)
        answer = flow_progress(compiled, plan, session, flow_id=FLOW)

        c = _tonight(graph, progress=lambda: answer)["campaign"]
        assert c["has_pool"] is False and c["has_progress"] is True
        assert c["members"] == [], "no pool, no pool members"
        assert c["panels"] == [
            {"block": "t", "name": "M16 1-1", "row": 0, "col": 0,
             "banked": 10, "owed": 0, "total": 10, "done": True, "pct": 100,
             "skipped": False},
            {"block": "t", "name": "M16 1-2", "row": 0, "col": 1,
             "banked": 4, "owed": 6, "total": 10, "done": False, "pct": 40,
             "skipped": False},
            {"block": "t", "name": "M16 2-2", "row": 1, "col": 1,
             "banked": 0, "owed": 10, "total": 10, "done": False, "pct": 0,
             "skipped": False},
            {"block": "t", "name": "M16 2-1", "row": 1, "col": 0,
             "banked": 3, "owed": 0, "total": 0, "done": False, "pct": None,
             "skipped": True}]
        assert c["note"].startswith(
            "1 of 3 mosaic panels done, 14 of 30 subs banked; 1 skipped "
            "panel holds 3 more, which come back when re-enabled."), c["note"]
        assert "not forecast" in c["note"] and "~" not in c["note"]

    def test_a_pool_and_a_mosaic_answer_both(self):
        """A mosaic lane and then a pool: the pool's answer (its members, its
        quota) with the mosaic's panel rows beside it and one sentence about
        them after the pool's note.

        Mutant "pool and mosaic, pool only" (the panel rows left off a flow
        that has a pool) failed:
            E   KeyError: 'has_progress'
        """
        graph = FlowGraph(
            nodes=[_n("d", "dusk", offset=-30, stop="Dawn", minAlt=30,
                      repeat="Nightly until pool complete"),
                   _n("t", "target", x=100, name="M16", ra=M16_RA,
                      dec=M16_DEC, rotation=30, angle="Rotate to PA", rows=2,
                      cols=2, overlap=25, fovX=FOV[0], fovY=FOV[1],
                      counts="Accepted subs"),
                   _n("c", "capture", x=200, filter="Ha", exposure=300,
                      gain=100, bin="1", count=10, goal=5),
                   _n("p", "pool", x=300, members="M13, M92",
                      counts="Accepted subs"),
                   _n("k", "capture", x=400, filter="L", exposure=60,
                      gain=100, bin="1", count=5, goal=0)],
            edges=[_e("d", "window", "t", "arm"),
                   _e("t", "target", "c", "run"),
                   _e("c", "complete", "p", "arm"),
                   _e("p", "target", "k", "run")])
        answer = _progress_for(graph, {(0, 0): 10, (1, 1): 2})

        c = _tonight(graph, progress=lambda: answer)["campaign"]
        assert (c["has_pool"], c["is_campaign"], c["has_progress"]) == \
            (True, True, True)
        assert len(c["members"]) == 2, "the pool's own rows are kept"
        assert [(r["name"], r["banked"], r["done"]) for r in c["panels"]] == [
            ("M16 1-1", 10, True), ("M16 1-2", 0, False),
            ("M16 2-2", 2, False), ("M16 2-1", 0, False)]
        assert c["note"].endswith(
            " 1 of 4 mosaic panels done, 12 of 40 subs banked."), c["note"]

    def test_a_repeating_mosaic_is_a_campaign(self):
        """A mosaic whose DUSK WINDOW repeats is a campaign with no pool; one
        that shoots a single night is not.

        Mutant "a mosaic is never a campaign" (``is_campaign`` False on the
        no-pool answer) failed:
            E   assert False is True
        """
        once = _tonight(_mosaic_flow(), progress=None)["campaign"]
        assert once["is_campaign"] is False
        nightly = _mosaic_flow()
        nightly.nodes[0].params["repeat"] = "Nightly until pool complete"
        again = _tonight(nightly, progress=None)["campaign"]
        assert again["is_campaign"] is True

    def test_a_mapping_serves_as_well_as_a_callable(self):
        answer = _progress_for(_mosaic_flow(), {(0, 0): 2})
        by_call = _tonight(_mosaic_flow(), progress=lambda: answer)["campaign"]
        by_map = _tonight(_mosaic_flow(), progress=answer)["campaign"]
        assert by_call == by_map and by_call["panels"][0]["banked"] == 2

    @pytest.mark.parametrize("how", ["none", "raises", "not a mapping"])
    def test_no_progress_claims_no_count(self, how):
        """No progress, a read that raises and a read that answers something
        else are all "no progress": no rows, and the note says no count is
        claimed, rather than a tab of zeros that says nothing was shot."""
        def boom():
            raise ValueError("1 plan target(s) match no block")
        progress = {"none": None, "raises": boom,
                    "not a mapping": lambda: ["blocks"]}[how]
        c = _tonight(_mosaic_flow(), progress=progress)["campaign"]
        assert c["panels"] == [] and c["has_progress"] is False
        assert "no panel's count is claimed" in c["note"]

    def test_a_block_that_is_not_this_graphs_mosaic_puts_no_row(self):
        """Rows are read only for blocks that are a multi-panel TARGET in this
        graph: another flow's answer, or a single target's block, adds none.
        """
        answer = _progress_for(_mosaic_flow(), {(0, 0): 2})
        other = {**answer, "blocks": [{**b, "node_id": "zz"}
                                      for b in answer["blocks"]]}
        c = _tonight(_mosaic_flow(), progress=other)["campaign"]
        assert c["panels"] == [] and c["has_progress"] is True
        single = _progress_for(_mosaic_flow(single=True), {})
        c = _tonight(_mosaic_flow(single=True), progress=single)["campaign"]
        assert {r["block"] for r in c["panels"]} == {"t"}


# ----------------------------------------------------------------- controls

def _single_flow():
    return FlowGraph(
        nodes=[_n("d", "dusk", offset=-30, stop="Dawn", minAlt=30),
               _n("t", "target", x=100, name="M16", ra=M16_RA, dec=M16_DEC,
                  rotation=-1),
               _n("c", "capture", x=200, filter="Ha", exposure=300, gain=100,
                  bin="1", count=10, goal=5)],
        edges=[_e("d", "window", "t", "arm"), _e("t", "target", "c", "run")])


def _pool_flow():
    return FlowGraph(
        nodes=[_n("d", "dusk", x=0, repeat="Nightly until pool complete"),
               _n("p", "pool", x=100, members="M16, M17"),
               _n("c", "capture", x=200, filter="Ha", exposure=180, gain=100,
                  bin="1", count=20, goal=12)],
        edges=[_e("d", "window", "p", "arm"), _e("p", "target", "c", "run")])


class TestSinglesAndPoolsAreUntouched:
    """The new arguments reach a mosaic only. A single target and a pool
    answer exactly as they did: their rows carry no new key, and handing
    them a hop cost and a progress answer changes nothing, not a byte."""

    @pytest.mark.parametrize("make", [_single_flow, _pool_flow],
                             ids=["single", "pool"])
    def test_the_new_arguments_change_nothing(self, make):
        """Mutant "progress read for every flow" (``_campaign`` reads the
        progress answer before asking whether the flow has a mosaic) failed,
        for the single target and the pool alike:
            E   AssertionError: a flow with no mosaic never reads its progress
            E   assert [1] == []
            E     Left contains one more item: 1
        """
        asked = []

        def progress():
            asked.append(1)
            return {"blocks": []}
        placed = lambda name: (18.3, -13.8)                    # noqa: E731
        plain = _tonight(make(), resolve_name=placed)
        given = _tonight(make(), resolve_name=placed, hop_cost_s=160.0,
                         progress=progress)
        assert given == plain
        assert asked == [], "a flow with no mosaic never reads its progress"

    @pytest.mark.parametrize("make", [_single_flow, _pool_flow],
                             ids=["single", "pool"])
    def test_no_new_key_reaches_them(self, make):
        """Mutant "every row is a panel row" (``panels`` and ``hop_h`` put on
        every budget row) failed, for the single target and the pool alike:
            E   assert False
            E    +  where False = all(<generator object TestSinglesAndPoolsAre
                Untouched.test_no_new_key_reaches_them.<locals>.<genexpr> at
                0x0000023C464569B0>)
        """
        out = _tonight(make(), resolve_name=lambda name: (18.3, -13.8),
                       hop_cost_s=160.0)
        assert all("mosaic" not in t for t in out["targets"])
        assert all(set(b) == {"filter", "goal_h", "banked_h", "tonight_h",
                              "has_ledger"} for b in out["budget"])
        assert set(out["campaign"]) == {"is_campaign", "has_pool",
                                        "has_ledger", "quota", "members",
                                        "note"}
        assert out["budget"], "premise: there is a budget row to look at"
