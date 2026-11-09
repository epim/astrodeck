# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A pool's live state and retry say CANDIDATE, a mosaic's say PANEL (WP 5.1c
fix round 1, #183).

* ``state.group`` carries ``kind``; for a pool it has no ``pass`` (a pool has
  a pick at each boundary, no pass over its members) and ``panel`` is the
  candidate running, ``panels_done`` / ``panels_total`` counting candidates.
* The live retry (`SequenceEngine.retry_set_aside`) accepts a pool group and
  queues its set-aside candidates, and the night log says so in words.
* The start preflight refuses a pool whose every candidate is below the
  horizon, in the same terms as a mosaic but naming candidates and a pool.

The nights are the real scheduler on the clocked simulator at the fixed
instant the harness gives every night, so no hour or time zone changes them.
"""
from __future__ import annotations

from _group_harness import Night, grid_plan, group_hub, group_store  # noqa: F401
from test_w5_pool_group import GID, pool_plan
from astrodeck.events import night_key
from astrodeck.sequence.engine import SequenceEngine
from astrodeck.sequence.models import TargetGroup
from astrodeck.sequence.session import Session, session_store


async def _night(hub, monkeypatch, plan) -> Night:
    night = Night(hub, monkeypatch)
    try:
        night.done = await night.run(plan, wall_s=60.0)
    finally:
        await night.close()
    assert night.done, night.lines[-4:]
    return night


async def test_a_pools_published_group_says_what_it_is(group_hub, monkeypatch):
    night = await _night(group_hub, monkeypatch, pool_plan("priority"))
    groups = [rec["group"] for rec in night.captures]
    assert groups and all(g is not None for g in groups), groups
    assert {g["kind"] for g in groups} == {"pool"}
    assert all("pass" not in g for g in groups), "a pool has no pass"
    assert all(g["panels_total"] == 3 for g in groups)
    by_shot = {rec["target"]: rec["group"] for rec in night.captures}
    assert all(g["panel"] == who for who, g in by_shot.items()), by_shot
    assert max(g["panels_done"] for g in groups) >= 2, (
        "candidates complete are counted")


async def test_a_mosaics_published_group_keeps_its_pass(group_hub, monkeypatch):
    night = await _night(group_hub, monkeypatch, grid_plan())
    groups = [rec["group"] for rec in night.captures]
    assert {g["kind"] for g in groups} == {"mosaic"}
    assert all(isinstance(g.get("pass"), int) for g in groups), groups[:2]


async def test_a_live_retry_queues_a_pools_set_aside_candidate(
        group_hub, monkeypatch):
    """A restart tonight reads High's record back; asked in the same breath as
    the start (before the scheduler has built its groups), the retry finds
    the candidate in the records the start seeded and queues it, and the run
    shoots it. The log names a candidate, not a panel."""
    plan = pool_plan("priority")
    night = Night(group_hub, monkeypatch)
    session = Session(name=plan.name, status="dormant", plan=plan)
    session.note_set_aside("high", "rejected every frame for 3 visits",
                           night=night_key(night.clock.t), kind="rejects",
                           ts=1.0)
    session_store.save(session)
    try:
        night.engine.start(plan, session=session)
        assert night.engine._group_runs == {}, "premise: no scheduler yet"
        assert night.engine.retry_set_aside(GID) == {"live": True,
                                                     "queued": ["High"]}
        await night.finish(wall_s=60.0)
    finally:
        await night.close()
    assert [x for x, _f in night.shots()].count("High") == 2, night.shots()
    assert night.said("set-aside candidates retried by the operator") == [
        "Pool: set-aside candidates retried by the operator: High"]


def test_the_word_for_a_member_follows_the_kind():
    pool = TargetGroup(id="p", name="P", kind="pool")
    assert SequenceEngine._member_word(pool) == "candidate"
    assert SequenceEngine._member_word(pool.model_copy(
        update={"kind": "mosaic"})) == "panel"
