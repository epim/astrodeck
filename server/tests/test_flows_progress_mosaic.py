"""A mosaic's progress: its panels, found through its plan group (#189 S3,
spec 1.2, 2.5, 3.3, 5.9, 6.9).

``flows.progress.flow_progress`` answers the card's chip ("4/6 panels done"),
the modal's per-panel bars and Tonight's CAMPAIGN rows. For a mosaic every
number has to land on the right panel of the right block:

* the panels are the plan targets of the block's GROUP, whose id is
  recomputed from the block's ANCHOR with the functions ``to_plan`` minted it
  with. Not the targets whose names look like the block's: two blocks may
  both be called "Veil", and one would take the other's frames;
* one entry per panel the plan shoots, each at its own row and col, with
  banked, owed and total; a SKIPPED panel is listed apart, and what the
  ledger holds on it stays visible (re-enabling it brings those subs back),
  and is not counted as orphaned;
* nothing site-derived: the route is ``CAP_VIEW_STATUS``, so the answer is
  scanned the #19 way, under two synthetic sites, for any number that moves
  when only the site does.

Every test names the mutation of ``flows/progress.py`` it guards and quotes
the failure that mutation produced, run in a private copy of ``server/``
(``scratchpad/s3-t-tonight-q8m4/``; the cases the verifier added in
``scratchpad/s3-t-verify-x4n7/``), never in the shared tree.
"""
from __future__ import annotations

import json

import pytest

from astrodeck.catalog import visibility
from astrodeck.catalog.coords import parse_dec, parse_ra
from astrodeck.flows import identity
from astrodeck.flows.compile import compile_plan
from astrodeck.flows.models import FlowEdge, FlowGraph, FlowNode
from astrodeck.flows.progress import flow_progress
from astrodeck.flows.to_plan import to_sequence_plan
from astrodeck.hub import Hub
from astrodeck.sequence.session import Session, SessionFrame
from _site_tracking import (SITE_A, SITE_B, any_tracking, site,
                            tracking_tokens)

FLOW = "flow-progress-mosaic"
FOV = (2.0, 1.33)

#: Two blocks of one name, a degree and more apart: the Veil's east and west.
VEIL_EAST = ("20h 56m 00s", "+31 43 00")
VEIL_WEST = ("20h 45m 38s", "+30 42 30")
M16 = ("18h 18m 48s", "-13 49 00")


def _n(nid, ntype, x=0.0, y=0.0, **params):
    return FlowNode(id=nid, type=ntype, x=x, y=y, params=params)


def _e(a, ap, b, bp):
    return FlowEdge(**{"from": a, "fromPort": ap, "to": b, "toPort": bp})


def _block(nid, name, where, *, x=100, skip="", anchor="", rows=2, cols=2):
    ra, dec = where
    return _n(nid, "target", x=x, name=name, ra=ra, dec=dec, rotation=30,
              angle="Rotate to PA", rows=rows, cols=cols, overlap=25,
              fovX=FOV[0], fovY=FOV[1], skip=skip, frameAnchor=anchor,
              counts="Accepted subs")


def _capture(nid, x, count=10):
    return _n(nid, "capture", x=x, filter="Ha", exposure=300, gain=100,
              bin="1", count=count, goal=0)


def _one_mosaic(*, skip="", anchor="", where=M16):
    """TARGET M16 (2x2) -> CAPTURE Ha 300 s x 10, one panel at a time."""
    return FlowGraph(nodes=[_block("t", "M16", where, skip=skip,
                                   anchor=anchor),
                            _capture("c", 200)],
                     edges=[_e("t", "target", "c", "run")])


def _two_veils():
    """Two 2x2 blocks, both named "Veil", each owning its own CAPTURE: the
    east block, then (what its lane's "all done" runs next) the west."""
    return FlowGraph(
        nodes=[_block("t1", "Veil", VEIL_EAST, x=100), _capture("c1", 200),
               _block("t2", "Veil", VEIL_WEST, x=300), _capture("c2", 400)],
        edges=[_e("t1", "target", "c1", "run"),
               _e("c1", "complete", "t2", "arm"),
               _e("t2", "target", "c2", "run")])


def _compile(graph):
    compiled = compile_plan(graph, "n")
    plan, _ = to_sequence_plan(compiled, graph, flow_id=FLOW)
    return compiled, plan


def _session(plan, frames, *, count_mode="accepted"):
    return Session(id="session-1", status="dormant", nights=["n1"],
                   plan=plan.model_copy(update={"count_mode": count_mode}),
                   frames=frames, origin="flow", origin_id=FLOW)


def _on(target, n, *, accepted=True):
    """``n`` frames on ``target``'s first step."""
    return [SessionFrame(target_id=target.id, step_id=target.steps[0].id,
                         auto_accepted=accepted) for _ in range(n)]


def _panel(plan, group_id, row, col):
    return next(t for t in plan.targets if t.mosaic_group == group_id
                and (t.panel_row, t.panel_col) == (row, col))


def _geometry_group(node_id, where):
    """The group id spec 3.3 gives a typed 2x2 block with no anchor: its
    current geometry, keyed with its grid."""
    return identity.group_id(FLOW, node_id, identity.geometry_key(
        parse_ra(where[0]), parse_dec(where[1]), 30.0, rows=2, cols=2,
        overlap=0.25, fov_x=FOV[0], fov_y=FOV[1]))


def _shape(block):
    """``[(name, row, col, banked, owed, total)]`` of a block's panels."""
    return [(p["name"], p["row"], p["col"], p["banked"], p["owed"],
             p["total"]) for p in block["panels"]]


# --------------------------------------------------------- found by its group

class TestPanelsAreFoundThroughTheGroup:
    def test_a_2x2_with_frames_on_two_panels(self):
        """Two blocks called "Veil". The east one holds 3 accepted subs on
        1-1 (and one rejected there, which accepted mode does not count) and
        2 on 2-2; the west one holds none. Each block reads its own four
        panels, in the plan's order, at their own rows and cols, and the
        west one reads nothing banked.

        Mutant "match panels by name" (a block's panels taken as the plan
        targets named "<block name> r-c", not its group's members) failed:
            E   AssertionError: assert [('Veil 1-1',... 10, 10), ...] ==
                [('Veil 1-1',...0, 0, 10, 10)]
            E     Left contains 4 more items, first extra item: ('Veil 1-1',
                0, 0, 0, 10, 10)
        """
        compiled, plan = _compile(_two_veils())
        east = _geometry_group("t1", VEIL_EAST)
        west = _geometry_group("t2", VEIL_WEST)
        assert {g.id for g in plan.groups} == {east, west}, \
            "premise: each block's group is keyed on its geometry (spec 3.3)"
        frames = (_on(_panel(plan, east, 0, 0), 3)
                  + _on(_panel(plan, east, 0, 0), 1, accepted=False)
                  + _on(_panel(plan, east, 1, 1), 2))

        got = flow_progress(compiled, plan, _session(plan, frames),
                            flow_id=FLOW)

        e, w = got["blocks"]
        assert (e["node_id"], w["node_id"]) == ("t1", "t2")
        assert _shape(e) == [("Veil 1-1", 0, 0, 3, 7, 10),
                             ("Veil 1-2", 0, 1, 0, 10, 10),
                             ("Veil 2-2", 1, 1, 2, 8, 10),
                             ("Veil 2-1", 1, 0, 0, 10, 10)]
        assert [p["target_id"] for p in e["panels"]] == [
            _panel(plan, east, r, c).id for r, c in
            ((0, 0), (0, 1), (1, 1), (1, 0))]
        assert (e["banked"], e["owed"], e["total"]) == (5, 35, 40)
        assert _shape(w) == [("Veil 1-1", 0, 0, 0, 10, 10),
                             ("Veil 1-2", 0, 1, 0, 10, 10),
                             ("Veil 2-2", 1, 1, 0, 10, 10),
                             ("Veil 2-1", 1, 0, 0, 10, 10)]
        assert {p["target_id"] for p in w["panels"]} == {
            t.id for t in plan.targets if t.mosaic_group == west}
        assert (e["grid"], e["skipped"]) == ({"rows": 2, "cols": 2}, [])
        assert got["orphaned"] == {"frames": 0, "steps": 0}

    def test_an_anchored_block_is_found_through_its_anchor(self):
        """The block was saved at M16's position and later nudged 1' south,
        which the save carried: its anchor is the old geometry, so its group
        (and every panel id) is keyed on the anchor, not on where the block
        is drawn now (spec 3.3, ruling 3). The frames banked before the
        nudge are found.

        Mutant "key on the current geometry" (``_group`` passes no anchor)
        failed:
            E   ValueError: 4 plan target(s) match no block of this compile
                (M16 1-1, M16 1-2, M16 2-2, M16 2-1): the plan was not
                compiled from it with flow_id='flow-progress-mosaic', so every
                count read against it would be wrong
        """
        ra0, dec0 = parse_ra(M16[0]), parse_dec(M16[1])
        anchor = identity.anchor_for(
            {"name": "M16", "ra": M16[0], "dec": M16[1]}, ra0, dec0, 30.0,
            canonical=None, rows=2, cols=2, overlap=0.25, fov_x=FOV[0],
            fov_y=FOV[1])
        nudged = (M16[0], "-13 50 00")
        compiled, plan = _compile(_one_mosaic(anchor=anchor, where=nudged))
        group = identity.group_id(FLOW, "t", identity.anchor_key(anchor))
        assert [g.id for g in plan.groups] == [group], \
            "premise: to_plan keyed the group on the anchor"
        assert group != _geometry_group("t", nudged), \
            "premise: the current geometry keys another group"

        frames = _on(_panel(plan, group, 1, 0), 4)
        got = flow_progress(compiled, plan, _session(plan, frames),
                            flow_id=FLOW)
        (block,) = got["blocks"]
        assert [p["banked"] for p in block["panels"]] == [0, 0, 0, 4]
        assert block["panels"][3]["name"] == "M16 2-1"


# ------------------------------------------------------------ skipped panels

class TestASkippedPanelKeepsItsCount:
    def _skipped_after_shooting(self, count_mode="accepted"):
        """2-1 and 1-1 were shot on a night when nothing was skipped (the
        session's plan), and then 2-1 was skipped (the compile)."""
        compiled, plan = _compile(_one_mosaic(skip="2-1"))
        _, before = _compile(_one_mosaic())
        (group,) = before.groups
        frames = (_on(_panel(before, group.id, 1, 0), 4)
                  + _on(_panel(before, group.id, 1, 0), 1, accepted=False)
                  + _on(_panel(before, group.id, 0, 0), 2))
        session = _session(before, frames, count_mode=count_mode)
        return compiled, plan, before, group, session

    def test_a_skipped_panels_banked_count_stays_visible(self):
        """The skipped 2-1 is listed apart, at its own row and col, holding
        its 4 accepted subs; it owes nothing, so it is in none of the block's
        sums, and its frames are not orphaned. The three panels shot read as
        before.

        Mutant "skipped panels not listed" (``_skipped`` answers []) failed,
        and in four more tests, Tonight's campaign rows among them:
            E   AssertionError: assert [] == [{'banked': 4...row': 1, ...}]
            E     Right contains one more item: {'banked': 4, 'col': 0,
                'name': 'M16 2-1', 'row': 1, ...}
        Mutant "a skipped panel's frames are orphaned" (``_orphaned`` no
        longer passes over frames on a skipped panel) failed:
            E   AssertionError: assert {'frames': 5, 'steps': 1} ==
                {'frames': 0, 'steps': 0}
        """
        compiled, plan, before, group, session = \
            self._skipped_after_shooting()
        (now,) = plan.groups
        tid = _panel(before, group.id, 1, 0).id
        assert now.skipped_ids == [tid], \
            "premise: skipping keeps the panel's id (spec 3.3: skip is not " \
            "in the key)"

        got = flow_progress(compiled, plan, session, flow_id=FLOW)
        (block,) = got["blocks"]
        assert block["skipped"] == [{"target_id": tid, "name": "M16 2-1",
                                     "row": 1, "col": 0, "banked": 4}]
        assert _shape(block) == [("M16 1-1", 0, 0, 2, 8, 10),
                                 ("M16 1-2", 0, 1, 0, 10, 10),
                                 ("M16 2-2", 1, 1, 0, 10, 10)]
        assert (block["banked"], block["owed"], block["total"]) == (2, 28, 30)
        assert got["orphaned"] == {"frames": 0, "steps": 0}

    def test_skipped_panels_are_listed_in_grid_order(self):
        """The group lists its skipped ids in the run's snaking order (2-2
        before 2-1 on a 2x2); the card lists them in grid order, the order
        the modal's grid reads.

        Mutant "skipped panels in the group's order" (the sort dropped)
        failed:
            E   AssertionError: assert ['M16 2-2', 'M16 2-1'] == ['M16 2-1',
                'M16 2-2']
            E     At index 0 diff: 'M16 2-2' != 'M16 2-1'
        """
        compiled, plan = _compile(_one_mosaic(skip="2-1, 2-2"))
        (group,) = plan.groups
        cell = {identity.target_id(group.id, r, c): (r, c)
                for r in range(2) for c in range(2)}
        assert [cell[t] for t in group.skipped_ids] == [(1, 1), (1, 0)], \
            "premise: the group holds them in snaking order"
        got = flow_progress(compiled, plan, _session(plan, []), flow_id=FLOW)
        assert [s["name"] for s in got["blocks"][0]["skipped"]] == [
            "M16 2-1", "M16 2-2"]

    def test_a_skipped_panel_counts_the_way_the_session_counts(self):
        """Accepted mode counts the 4 accepted subs and not the rejected one;
        attempts mode counts all 5: the session's frozen mode, one rule with
        every other count in the answer.

        Mutant "every frame on a skipped panel counted" (the count mode
        ignored) failed:
            E   assert 5 == 4
        """
        compiled, plan, *_, session = self._skipped_after_shooting()
        got = flow_progress(compiled, plan, session, flow_id=FLOW)
        assert got["blocks"][0]["skipped"][0]["banked"] == 4
        compiled, plan, *_, session = self._skipped_after_shooting("attempts")
        got = flow_progress(compiled, plan, session, flow_id=FLOW)
        assert got["blocks"][0]["skipped"][0]["banked"] == 5

    def test_a_block_whose_every_panel_is_skipped_still_lists_them(self):
        """Skip all four panels and ``to_plan`` leaves the block out: no
        group, no member. Its panels are still the operator's, and 1-2 holds
        3 subs, so they are listed, with ids minted as ``to_plan`` mints a
        panel's, and none is orphaned. A single target keeps the plan
        runnable.

        CHANGED IN S4 (#335): the panels are the ids the PLAN names in its
        own ``skipped_ids``, the list CONTINUE now reads too
        (``continuation.plan_skipped_ids``), where S3 read the compile's
        skip list, which CONTINUE could not see. The card and CONTINUE then
        name one set of frames (``test_flows_continue_all_skipped.py``).

        Mutant "only the group's skipped ids" (no fallback when the plan
        holds no group) failed:
            E   AssertionError: assert [] == [('M16 1-1', ...'M16 2-2', 0)]
            E     Right contains 4 more items, first extra item: ('M16 1-1', 0)

        RED under mutant "no skip trace for a dropped block" (``to_plan``'s
        all-skipped branch keeps its ids to itself, as S3 built it),
        observed on the premise:

            AssertionError: premise: the plan names the four panels
            assert [] == ['636d9496d83...70e492dc3a05']
              Right contains 4 more items, first extra item:
              '636d9496d830549ebddf1fc8be2f7f4f'

        RED under mutant "CONTINUE reads the groups alone"
        (``continuation.plan_skipped_ids`` without the plan's own list,
        which ``_orphaned`` asks too), observed:

            AssertionError: assert {'frames': 3, 'steps': 1} == {'frames':
            0, 'steps': 0}
        """
        def graph(skip):
            return FlowGraph(
                nodes=[_block("t", "M16", M16, skip=skip),
                       _capture("c", 200),
                       _n("u", "target", x=300, name="M31",
                          ra="00h 42m 44s", dec="+41 16 09", rotation=-1),
                       _capture("k", 400, count=5)],
                edges=[_e("t", "target", "c", "run"),
                       _e("c", "complete", "u", "arm"),
                       _e("u", "target", "k", "run")])
        _, before = _compile(graph(""))
        (group,) = before.groups
        frames = _on(_panel(before, group.id, 0, 1), 3)
        compiled, plan = _compile(graph("1-1, 1-2, 2-1, 2-2"))
        assert plan.groups == [], "premise: to_plan leaves the block out"
        cell = {r * 2 + c: identity.target_id(group.id, r, c)
                for r in range(2) for c in range(2)}
        assert sorted(plan.skipped_ids) == sorted(cell.values()), (
            "premise: the plan names the four panels")

        got = flow_progress(compiled, plan, _session(before, frames),
                            flow_id=FLOW)
        block = got["blocks"][0]
        assert block["panels"] == []
        assert [(s["name"], s["banked"]) for s in block["skipped"]] == [
            ("M16 1-1", 0), ("M16 1-2", 3), ("M16 2-1", 0), ("M16 2-2", 0)]
        assert block["skipped"][1]["target_id"] == \
            _panel(before, group.id, 0, 1).id
        assert got["orphaned"] == {"frames": 0, "steps": 0}


# --------------------------------------------------- nothing from the site

class TestNothingInItMovesWithTheSite:
    """The route is ``CAP_VIEW_STATUS``, so a viewer reads this answer (spec
    6.9). A key-name filter cannot withhold a value a route computes and
    names itself (#19), so the answer is scanned the way the #19 scanner
    scans: the same flow and session under two synthetic sites
    (``_site_tracking``), and a number counts as site-derived only if it
    moves when only the site moves. The keys are held to an allow-list as
    well, so a new one is added in a diff someone reads."""

    ALLOWED = {
        "top": {"flow_id", "session", "blocks", "orphaned"},
        "session": {"id", "status", "nights", "count_mode"},
        "block": {"node_id", "name", "kind", "banked", "owed", "total",
                  "panels", "grid", "skipped"},
        "grid": {"rows", "cols"},
        "panel": {"target_id", "name", "row", "col", "banked", "owed",
                  "total", "steps"},
        "skipped": {"target_id", "name", "row", "col", "banked"},
        "step": {"step_id", "filter", "frame_type", "exposure_s", "count",
                 "banked", "owed"},
        "orphaned": {"frames", "steps"},
    }

    @staticmethod
    def _at(monkeypatch, pair):
        """The whole route's pipeline, compile to answer, with the hub's site
        and the config's site both at ``pair``. A typed mosaic with a panel
        skipped and a mosaic known only by its NAME (the catalogue is the one
        outside answer this path reads), and frames on both."""
        from astrodeck.config import config_store
        cfg = config_store.cfg()
        monkeypatch.setattr(cfg, "site", cfg.site.model_copy(update={
            "latitude": pair[0], "longitude": pair[1], "elevation_m": 10.0,
            "is_default": False}))
        monkeypatch.setattr(Hub, "site", property(lambda self: site(pair)))
        graph = FlowGraph(
            nodes=[_block("t", "M16", M16, skip="2-1"), _capture("c", 200),
                   # Blank, not missing: a missing ra/dec reads as M31's
                   # typed coordinates (the missing-key default).
                   _n("m", "target", x=300, name="M31", ra="", dec="",
                      rotation=30, angle="Rotate to PA", rows=1, cols=2,
                      overlap=25, fovX=FOV[0], fovY=FOV[1],
                      counts="Accepted subs"),
                   _capture("k", 400)],
            edges=[_e("t", "target", "c", "run"),
                   _e("c", "complete", "m", "arm"),
                   _e("m", "target", "k", "run")])
        compiled, plan = _compile(graph)
        assert not identity.typed_coordinates(compiled["targets"][1]), \
            "premise: the second mosaic is known only by its name"
        frames = [f for t in plan.targets for f in _on(t, 2)]
        return flow_progress(compiled, plan, _session(plan, frames),
                             flow_id=FLOW)

    def test_no_number_moves_when_the_site_does(self, monkeypatch):
        """Mutant "a peak altitude on each panel" (each panel gains the
        ``transit_alt`` the visibility module gives its target at the hub's
        site) failed (the sites are ``_site_tracking``'s synthetic pair):
            E   AssertionError: numbers that move with the site: under SITE_A
                ['30.6', '31.1', '31.7', '88.5', '88.9'], under SITE_B
                ['57.6', '58.6', '59.0', '60.5', '61.3']
        The allow-list test below failed on the same mutant, on its key.
        """
        a = self._at(monkeypatch, SITE_A)
        b = self._at(monkeypatch, SITE_B)
        assert [len(blk["panels"]) for blk in a["blocks"]] == [3, 2], \
            "premise: both mosaics were read, the named one too"
        found = tracking_tokens([json.dumps(a, sort_keys=True)],
                                [json.dumps(b, sort_keys=True)])
        assert not any_tracking(found), \
            f"numbers that move with the site: under SITE_A {found['a']}, " \
            f"under SITE_B {found['b']}"

    def test_the_scan_can_see_a_site_derived_number(self, monkeypatch):
        """THE KNOWN POSITIVE (a scan that finds nothing may be a scan that
        cannot see, #19): under the same two sites, a panel's peak altitude,
        the number the modal's gated column shows, does move, and the scan
        finds it."""
        def alt(pair):
            monkeypatch.setattr(Hub, "site",
                                property(lambda self: site(pair)))
            return visibility.transit_alt_for(parse_ra(M16[0]),
                                              parse_dec(M16[1]))
        found = tracking_tokens([json.dumps({"transit_alt": alt(SITE_A)})],
                                [json.dumps({"transit_alt": alt(SITE_B)})])
        assert any_tracking(found)

    def test_every_key_is_in_the_allow_list(self, monkeypatch):
        got = self._at(monkeypatch, SITE_A)
        seen = {level: set() for level in self.ALLOWED}
        seen["top"] |= set(got)
        seen["session"] |= set(got["session"])
        seen["orphaned"] |= set(got["orphaned"])
        for block in got["blocks"]:
            seen["block"] |= set(block)
            seen["grid"] |= set(block["grid"])
            for s in block["skipped"]:
                seen["skipped"] |= set(s)
            for panel in block["panels"]:
                seen["panel"] |= set(panel)
                for step in panel["steps"]:
                    seen["step"] |= set(step)
        for level, keys in seen.items():
            assert keys <= self.ALLOWED[level], \
                f"{level} carries keys outside the allow-list"
        assert all(seen.values()), "premise: every level was walked"
        assert json.loads(json.dumps(got, allow_nan=False)) == got
