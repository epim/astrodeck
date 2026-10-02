# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""WP-50 (a), #562: BUDGET is computed per block, not per flow.

``_budget`` used to fold the ledger into ONE ``{filter: hours}`` mapping for
the whole flow (``banked_hours_from_reports``) and gave every row the hours of
its own filters from THAT mapping. In a flow of several blocks that shoot the
same filter, every row then banked the flow's total rather than its own
block's: an M16 Ha row and an M31 Ha row in the same flow both read
M16's-plus-M31's Ha, so a block that had banked nothing still read as
progressing on the other block's frames.

The fix (``banked_hours_by_target_from_reports``, ``_entry_target_names``):
each row is given the names ITS OWN compiled entry stands for - a single
target or pool member its own name, a mosaic its live panels, a pool row (one
per deduped step signature) the union of the members that share it - and
reads only those names out of a ``{target: {filter: hours}}`` ledger.

A made-up site (not the observatory's), the made-up targets test_flows_tonight
already uses elsewhere (M16) plus a second (M31), both fixed catalogue
objects so no network/ephemeris lookup is needed beyond what ``compute_night``
already does for every Tonight test in this suite.
"""
from __future__ import annotations

import datetime as _dt

from astrodeck.flows.models import FlowEdge, FlowGraph, FlowNode
from astrodeck.flows.tonight import resolve_tonight

SITE = {"latitude": 40.0, "longitude": -105.0, "elevation_m": 1600.0,
        "is_default": False}
TWILIGHT = -12.0
JUNE = _dt.datetime(2026, 6, 15, 20, 0, tzinfo=_dt.timezone.utc).timestamp()

M16 = {"name": "M16", "ra": "18h 18m 48s", "dec": "-13 49 00"}
M31 = {"name": "M31", "ra": "00h 42m 44s", "dec": "+41 16 09"}


def _n(nid, ntype, x=0.0, y=0.0, **params):
    return FlowNode(id=nid, type=ntype, x=x, y=y, params=params)


def _e(a, ap, b, bp):
    return FlowEdge(**{"from": a, "fromPort": ap, "to": b, "toPort": bp})


def _ha(node_id, *, goal, count=8, exposure=900.0):
    return _n(node_id, "capture", filter="Ha", exposure=exposure, gain=100,
             bin="1", count=count, goal=goal)


def _two_block_flow() -> FlowGraph:
    """A DUSK WINDOW arming M16 (Ha, 2 h goal), which on completion arms M31
    (Ha, 3 h goal): two blocks, one shared filter, so an un-fixed fold cannot
    tell them apart."""
    return FlowGraph(nodes=[
        _n("d", "dusk", offset=-30, stop="Dawn", minAlt=30),
        _n("t1", "target", x=100, **M16),
        _ha("c1", goal=2.0),
        _n("t2", "target", x=300, **M31),
        _ha("c2", goal=3.0),
    ], edges=[
        _e("d", "window", "t1", "arm"),
        _e("t1", "target", "c1", "run"),
        _e("c1", "complete", "t2", "arm"),
        _e("t2", "target", "c2", "run"),
    ])


def _pool_flow() -> FlowGraph:
    """A POOL of M16 and M31 feeding one Ha capture (2 h goal): "best
    available" shoots exactly one of them, so the row is one row, and (per
    the suggested fix) its names are the UNION of the members that carry the
    deduped step's signature."""
    return FlowGraph(nodes=[
        _n("p", "pool", members="M16, M31", quota=0, minAlt=0, moonSep=0,
          maxHA=0),
        _ha("c", goal=2.0),
    ], edges=[_e("p", "target", "c", "run")])


def _budget_row(out: dict, filt: str = "Ha") -> dict:
    (row,) = [b for b in out["budget"] if b["filter"] == filt]
    return row


class TestEachBlockReadsItsOwnBank:
    def test_an_m16_row_does_not_bank_m31s_hours(self):
        """Two blocks, both Ha. Only M31 has banked hours; M16's row must
        read 0, not M31's 1.5 h - the direction #562 was filed over.

        RED under mutant "one mapping for every row" (``_budget``'s
        ``banked_hours`` made to always use ``flat_bank`` even when
        ``by_target_bank`` is given, i.e. the pre-fix fold - here read as the
        empty flat mapping, since this caller supplies no ``banked``),
        observed:
            AssertionError: M16's row (2 h goal) must read 0, M31's (3 h
            goal) 1.5: {2.0: 0.0, 3.0: 0.0}
            assert {2.0: 0.0, 3.0: 0.0} == {2.0: 0.0, 3.0: 1.5}
        """
        out = resolve_tonight(
            _two_block_flow(), SITE, now=JUNE, twilight_deg=TWILIGHT,
            banked_by_target=lambda: {"M31": {"Ha": 1.5}})
        rows = [b for b in out["budget"] if b["filter"] == "Ha"]
        assert len(rows) == 2, (
            f"premise: two distinct Ha rows, one per block: {rows}")
        by_goal = {r["goal_h"]: r["banked_h"] for r in rows}
        assert by_goal == {2.0: 0.0, 3.0: 1.5}, (
            f"M16's row (2 h goal) must read 0, M31's (3 h goal) 1.5: "
            f"{by_goal}")

    def test_each_blocks_own_hours_count_toward_its_own_row(self):
        """The control: each block's OWN hours reach its OWN row, not just
        "not the other's" - a fold that zeroed everything would pass the test
        above too."""
        out = resolve_tonight(
            _two_block_flow(), SITE, now=JUNE, twilight_deg=TWILIGHT,
            banked_by_target=lambda: {"M16": {"Ha": 1.0},
                                      "M31": {"Ha": 2.5}})
        rows = [b for b in out["budget"] if b["filter"] == "Ha"]
        by_goal = {r["goal_h"]: r["banked_h"] for r in rows}
        assert by_goal == {2.0: 1.0, 3.0: 2.5}, by_goal


class TestAPoolRowIsTheUnionOfItsMembers:
    def test_either_members_hours_reach_the_deduped_row(self):
        """A pool's copies of one step are deduped to ONE row (#562's
        suggested fix): its names are the union of the members that carry
        it, since "best available" means either could be the one shot."""
        out = resolve_tonight(
            _pool_flow(), SITE, now=JUNE, twilight_deg=TWILIGHT,
            banked_by_target=lambda: {"M31": {"Ha": 0.75}})
        row = _budget_row(out)
        assert row["banked_h"] == 0.75, (
            f"the deduped pool row must read its M31 member's hours: {row}")

    def test_the_union_sums_rather_than_picks_one(self):
        """Both members have banked hours in the shared filter: the row
        reads their SUM, since the archive does not know yet which one
        tonight will pick."""
        out = resolve_tonight(
            _pool_flow(), SITE, now=JUNE, twilight_deg=TWILIGHT,
            banked_by_target=lambda: {"M16": {"Ha": 0.5},
                                      "M31": {"Ha": 0.75}})
        row = _budget_row(out)
        assert row["banked_h"] == 1.25, row


class TestAFlatCallerIsUnchanged:
    def test_no_banked_by_target_falls_back_to_the_flow_wide_mapping(self):
        """A caller that still hands only ``banked`` (no per-target ledger)
        keeps exactly the old behaviour: the same flat figure for every row,
        which is correct for the common single-block flow and the only
        thing an older caller can supply.

        RED under mutant "have_by_target always True" (``_budget``'s
        ``have_by_target = banked_by_target is not None`` made
        ``have_by_target = True``, so a caller that never gave
        ``banked_by_target`` still reads the empty ``by_target_bank`` instead
        of the flat figure it did supply), observed:
            AssertionError: every row must read the one flat figure:
            [{'filter': 'Ha', 'goal_h': 2.0, 'banked_h': 0.0, ...},
             {'filter': 'Ha', 'goal_h': 3.0, 'banked_h': 0.0, ...}]
            assert {0.0} == {4.2}
        """
        out = resolve_tonight(
            _two_block_flow(), SITE, now=JUNE, twilight_deg=TWILIGHT,
            banked=lambda: {"Ha": 4.2})
        rows = [b for b in out["budget"] if b["filter"] == "Ha"]
        assert {r["banked_h"] for r in rows} == {4.2}, (
            f"every row must read the one flat figure: {rows}")
