# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A POOL compiles to a pool group, and the pool examples run (WP 5.1b2, #183).

THE CLAIM. POOL was reported as "not something the engine can do yet": the
candidates compiled as plain targets in rank order and the note counted as a
loss the operator had to accept. A POOL now compiles to WP 5.1a's pool group
with the node's strategy: "Best available" and "Priority order" run each pick
to completion (``sequential``), "Round robin" re-picks every visit
(``rotate``). The note is gone, the members keep the ids and the constraints
they had, and Best-of-four and the Campaign Example complete a night on the
simulator.

Every night runs the REAL scheduler on the clocked simulator at the fixed
instant ``T0`` (tests/_group_harness.py), so none depends on the wall clock,
the hour or the PC's time zone. The compiled candidates are moved onto
made-up fields at chosen hour angles, because ``T0`` is a fixed instant and
the catalogue's own positions are not where the test needs them.
"""
from __future__ import annotations

import pytest

from _group_harness import (LAT, LON, T0, Night, group_hub,  # noqa: F401
                            group_store, ra_at)
from astrodeck.flows.compile import compile_plan
from astrodeck.flows.examples import examples
from astrodeck.flows.models import FlowEdge, FlowGraph, FlowNode
from astrodeck.flows.nodes import NODE_DEFS, create_params
from astrodeck.flows.to_plan import blocking_reasons, losses, to_sequence_plan
from astrodeck.sequence.group_rules import pool_sky_scores
from astrodeck.sequence.models import plan_identity_errors
from astrodeck.sequence.session import session_store

#: Any fixed instant: the members are placed by the catalogue at it.
WHEN = 1788313689.0

BEST = NODE_DEFS["pool"].params["strategy"]

#: The member ids and step ids of the two pool Examples as the tree BEFORE this
#: change compiled them (origin/main d4549f22, flow id the Example's own):
#: (name, target id, step id of its first step).
BASE_POOL_IDS = [
    ("M16", "3440f55312c0554b8be0d0c793717072",
     "a5a58052659f59cb8299efd2767c5b7b"),
    ("M17", "bcb126c5d534556e8b294022d17243f2",
     "9fffc0bd6dc05245aeb08f722a5554c4"),
    ("M8", "f3c5af31b3ad531998780d706d40d446",
     "1cc547a1ca01520cbf0da45aa6ae6d2d"),
    ("NGC 6946", "5ee0d4c0c769503f804c66cdad0ada97",
     "7df6db343558594bb1a81b60bb35ac18"),
]
BASE_CAMPAIGN_IDS = [
    ("M33", "7b8b96e60f4a529ebad05164343a83f4",
     "b83e48c1cfeb595da2c93516d790f6c3"),
    ("NGC 7331", "5e7bbf2ca0b55aeeac73ed597732d416",
     "100437a978e55c86819d0aa405646d3b"),
    ("IC 1396", "8dd7c8ff5cbe5ae8868ab3fdf15a72ec",
     "6d64fe1b50ac5294ac886b7108828d6d"),
    ("M45", "4b63f5bce3ae55a0a2c2f41afaf73397",
     "2eee78b4844d55429875d4960d64ec92"),
]

#: (name, hour angle at T0 in hours, dec): the plan's rank is this order. The
#: first is the lowest in the sky and the last is nearer the meridian, so rank
#: and score disagree.
SKY = (("Low", 3.0, 20.0), ("High", -0.5, 20.0), ("Mid", 1.0, 20.0))

#: Four candidates all east of the meridian or near it, so each stays inside
#: the Examples' 4 h hour-angle limit for the whole night.
FOUR = (("A", -3.0, 20.0), ("B", -0.5, 20.0), ("C", -2.0, 20.0),
        ("D", -1.0, 20.0))


def pool_flow(strategy: str = BEST, members: str = "M31, M33, M13",
              **over) -> FlowGraph:
    nodes = [("n20", "pool", 30, 100), ("n7", "capture", 300, 100)]
    params = {"n20": {"members": members, "strategy": strategy, **over},
              "n7": {"filter": "L", "exposure": 60, "count": 2}}
    return FlowGraph(
        nodes=[FlowNode(id=i, type=t, x=float(x), y=float(y),
                        params={**create_params(t), **params.get(i, {})})
               for i, t, x, y in nodes],
        edges=[FlowEdge(**{"from": "n20", "fromPort": "target",
                           "to": "n7", "toPort": "run"})])


def compiled(g: FlowGraph, flow_id: str = "f-pool"):
    return to_sequence_plan(compile_plan(g, "x"), g, flow_id=flow_id,
                            when=WHEN)


def _example(ex_id: str):
    return next(e for e in examples() if e.id == ex_id)


# ---------------------------------------------------------------- the compile

@pytest.mark.parametrize("strategy, word, mode", [
    (BEST, "best", "sequential"),
    ("Priority order", "priority", "sequential"),
    ("Round robin", "round_robin", "rotate"),
])
def test_a_pool_compiles_to_a_pool_group_with_its_strategy(
        strategy, word, mode):
    plan, _ = compiled(pool_flow(strategy))
    assert len(plan.groups) == 1
    group = plan.groups[0]
    assert (group.kind, group.pool_strategy, group.mode) == (
        "pool", word, mode)
    assert [t.name for t in plan.targets] == ["M31", "M33", "M13"]
    assert {t.mosaic_group for t in plan.targets} == {group.id}
    assert plan_identity_errors(plan) == []


def test_the_not_something_the_engine_can_do_yet_loss_is_gone():
    _plan, unmapped = compiled(pool_flow())
    assert not [u for u in unmapped if "pool_rank" in u["key"]], unmapped
    assert not [u for u in losses(unmapped) if "pool" in u["key"]], unmapped


def test_a_pool_member_waits_its_turn_where_it_used_to_be_passed_over():
    """``on_missed`` "skip" was the pre-group approximation of "best of
    several": once the first pick had run past the grace, every other
    candidate was a missed window and was skipped for the night. The group
    picks one at a time and the rest wait."""
    plan, _ = compiled(pool_flow())
    assert all(t.schedule.on_missed == "wait" for t in plan.targets)


def test_the_pools_constraints_still_reach_every_member():
    plan, _ = compiled(pool_flow(
        minAlt=33, moonSep=44, maxHA=5,
        onFloor="Keep imaging (not recommended)"))
    for t in plan.targets:
        assert (t.schedule.min_altitude_deg, t.schedule.min_moon_sep_deg,
                t.schedule.max_hour_angle_h, t.schedule.on_floor) == (
            33, 44, 5, "keep"), t.name


def test_a_strategy_this_build_does_not_offer_reads_as_best_available():
    plan, _ = compiled(pool_flow("Something a newer build offers"))
    assert plan.groups[0].pool_strategy == "best"


def test_a_pool_without_a_flow_id_still_compiles_its_group():
    g = pool_flow()
    plan, _ = to_sequence_plan(compile_plan(g, "x"), g, when=WHEN)
    assert plan.groups[0].kind == "pool"
    assert plan_identity_errors(plan) == []


def test_a_candidate_named_twice_leaves_its_pool_as_it_always_compiled():
    """A plan with groups tells targets apart by name, so a pool that repeats
    one (or shares one with a TARGET) would refuse a start that has always
    worked. It compiles as before, in rank order with each member passed over
    when its window is missed, and the loss says why."""
    plan, unmapped = compiled(pool_flow(members="M31, M33, M31"))
    assert plan.groups == [], plan.groups
    assert [t.mosaic_group for t in plan.targets] == [None] * 3
    assert all(t.schedule.on_missed == "skip" for t in plan.targets)
    rows = [u for u in losses(unmapped) if u["key"] == "targets[*].pool_rank"]
    assert len(rows) == 1 and "'M31'" in rows[0]["detail"], unmapped
    assert plan_identity_errors(plan) == []


def test_a_candidate_sharing_a_target_blocks_name_does_the_same():
    g = pool_flow(members="M31, M33")
    g.nodes.append(FlowNode(id="n2", type="target", x=30.0, y=300.0,
                            params={**create_params("target"),
                                    "name": "M33", "ra": "01h 33m 51s",
                                    "dec": "+30 39 37"}))
    plan, unmapped = compiled(g)
    assert [grp.kind for grp in plan.groups] == []
    assert [u["key"] for u in losses(unmapped)
            if u["key"] == "targets[*].pool_rank"]


@pytest.mark.parametrize("ex_id, base", [("example-pool", BASE_POOL_IDS),
                                         ("example-campaign",
                                          BASE_CAMPAIGN_IDS)])
def test_the_members_keep_the_ids_the_base_tree_gave_them(ex_id, base):
    """A pool member's id is keyed on its pool and name, and its step ids on
    that: grouping the pool moves none, so the ledgers keep counting."""
    ex = _example(ex_id)
    plan, _ = compiled(ex.graph, ex.id)
    assert [(t.name, t.id, t.steps[0].id) for t in plan.targets] == base


@pytest.mark.parametrize("ex_id", ["example-pool", "example-campaign"])
def test_the_pool_examples_compile_with_no_blocking_loss(ex_id):
    ex = _example(ex_id)
    plan, unmapped = compiled(ex.graph, ex.id)
    assert blocking_reasons(unmapped, dome_connected=False) == []
    assert not [u for u in unmapped if "pool_rank" in u["key"]], unmapped
    assert [g.kind for g in plan.groups] == ["pool"]
    assert plan_identity_errors(plan) == []


# ------------------------------------------------------------------ the night

def _on_the_sky(plan, sky=SKY) -> None:
    """Rename the candidates and put each on a made-up field at its hour
    angle at ``T0``, with the extras of a bare night off."""
    for t, (name, ha, dec) in zip(plan.targets, sky):
        t.name = name
        t.ra_hours = ra_at(ha)
        t.dec_deg = dec
    _bare(plan)


def _bare(plan) -> None:
    for t in plan.targets:
        t.autofocus_first = False
    plan.guide = False
    plan.dither_every = 0
    plan.autofocus_every = 0
    plan.meridian_flip = False
    plan.park_when_done = False
    plan.warm_cooler_when_done = False
    plan.recover_guiding = False


async def _night(hub, monkeypatch, plan) -> Night:
    night = Night(hub, monkeypatch)
    try:
        night.done = await night.run(plan, wall_s=60.0)
    finally:
        await night.close()
    night.stored = session_store.load(night.session_id)
    return night


def _hops(night) -> list[str]:
    return [t for t, _f in night.visits()]


async def test_priority_order_shoots_the_members_in_rank_order(
        group_hub, monkeypatch):
    """Today's order, exactly: the box's order, each to completion, though
    the sky would score them another way."""
    plan, _ = compiled(pool_flow("Priority order"))
    _on_the_sky(plan)
    scores = pool_sky_scores(plan.targets, LAT, LON, T0)
    by_score = [t.name for t in sorted(plan.targets,
                                       key=lambda t: -scores[t.id])]
    assert by_score != ["Low", "High", "Mid"], "premise: rank is not score"
    night = await _night(group_hub, monkeypatch, plan)
    assert night.done, night.trace[-3:]
    assert _hops(night) == ["Low", "High", "Mid"], _hops(night)
    assert [t for t, _f in night.shots()] == [
        "Low", "Low", "High", "High", "Mid", "Mid"]
    assert night.stored.status == "complete"


async def test_best_available_shoots_the_best_score_first_each_to_completion(
        group_hub, monkeypatch):
    plan, _ = compiled(pool_flow(BEST))
    _on_the_sky(plan)
    scores = pool_sky_scores(plan.targets, LAT, LON, T0)
    best = [t.name for t in sorted(plan.targets, key=lambda t: -scores[t.id])]
    assert best[0] != "Low", "premise: the first rank is not the best"
    night = await _night(group_hub, monkeypatch, plan)
    assert night.done, night.trace[-3:]
    assert _hops(night) == best, (_hops(night), best)
    assert night.stored.owed() == 0 and night.stored.status == "complete"


@pytest.mark.parametrize("ex_id", ["example-pool", "example-campaign"])
async def test_the_pool_examples_complete_a_night_on_the_simulator(
        ex_id, group_hub, monkeypatch):
    """Best-of-four and the Campaign Example, compiled as `run_flow` compiles
    them, two subs a step: every candidate is shot to its count and the
    session completes."""
    ex = _example(ex_id)
    plan, _ = compiled(ex.graph, ex.id)
    _on_the_sky(plan, FOUR)
    for t in plan.targets:
        for s in t.steps:
            s.count = 2
    night = await _night(group_hub, monkeypatch, plan)
    assert night.done, night.trace[-3:]
    assert night.stored.status == "complete", night.stored.status
    assert night.stored.owed() == 0
    names = {t.name for t in plan.targets}
    shot = {}
    for who, _f in night.shots():
        if who in names:
            shot[who] = shot.get(who, 0) + 1
    assert shot == {t.name: 2 * len(t.steps) for t in plan.targets}, shot
