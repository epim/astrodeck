# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A TARGET POOL as a TargetGroup of kind "pool" (WP 5.1a, #183 engine half).

THE CLAIM. POOL was reported as "not something the engine can do yet" and
compiled as plain targets in rank order, so the best-of-several examples
could not run. A pool group's members are CANDIDATES: at each visit boundary
the scheduler lets exactly one of the eligible candidates run, the one
``pool_strategy`` selects, on the same eligibility, requeue and set-aside
machinery a mosaic uses. "best" scores altitude times the Moon's factor,
"priority" is the plan's rank, "round_robin" the least recently visited. A
candidate runs while it is the pick and leaves on complete, floor or window,
as any panel does.

Every night runs the REAL scheduler on the clocked simulator at the fixed
instant ``T0`` (tests/_group_harness.py): the sky, the Moon and the clock are
the same at any hour and in any time zone. The Moon is up at ``T0`` (about
4 degrees, 74 percent lit), so the best-available score exercises its factor.
"""
from __future__ import annotations

import math
from types import SimpleNamespace

import pytest

from _group_harness import (LAT, LON, T0, Night, group_hub,  # noqa: F401
                            group_store, ra_at)
from astrodeck.catalog.coords import (altaz, angular_sep_deg,
                                      moon_illumination, moon_radec)
from astrodeck.events import night_key
from astrodeck.catalog import visibility
from astrodeck.catalog.moon_factor import moon_factor
from astrodeck.sequence import group_rules
from astrodeck.sequence.group_rules import pool_sky_scores
from astrodeck.sequence.models import (ExposureStep, Schedule, SequencePlan,
                                       Target, TargetGroup,
                                       plan_identity_errors)
from astrodeck.sequence.panel_order import OrderSnapshot, order_pool
from astrodeck.sequence.resume_arm import _group_order, recentre_candidates
from astrodeck.sequence.session import Session, SessionFrame, session_store
import astrodeck.sequence.engine as engine_mod

GID = "best-of-three"
#: (name, hour angle at T0 in hours, dec): the plan's rank is this order. The
#: first is the lowest in the sky and the last is nearer the meridian, so
#: rank and score disagree.
CANDIDATES = (("Low", 3.0, 20.0), ("High", -0.5, 20.0), ("Mid", 1.0, 20.0))


def _candidate(name: str, ha_h: float, dec: float, count: int) -> Target:
    tid = name.lower()
    return Target(id=tid, name=name, ra_hours=ra_at(ha_h), dec_deg=dec,
                  center=True, autofocus_first=False, acquisition="cycle",
                  mosaic_group=GID,
                  steps=[ExposureStep(id=f"{tid}-L", filter="L",
                                      exposure_s=30.0, count=count)])


def pool_plan(strategy: str | None, *, mode: str = "sequential",
              count: int = 2, **group_kw) -> SequencePlan:
    group = TargetGroup(id=GID, name="Pool", kind="pool", mode=mode,
                        pool_strategy=strategy, **group_kw)
    return SequencePlan(
        name="pool", guide=False, dither_every=0, autofocus_every=0,
        meridian_flip=False, park_when_done=False,
        warm_cooler_when_done=False, recover_guiding=False,
        targets=[_candidate(n, ha, dec, count) for n, ha, dec in CANDIDATES],
        groups=[group])


async def _night(hub, monkeypatch, plan, *, session=None) -> Night:
    night = Night(hub, monkeypatch)
    start_kw = {"session": session} if session is not None else {}
    try:
        night.done = await night.run(plan, wall_s=60.0, **start_kw)
    finally:
        await night.close()
    night.stored = session_store.load(night.session_id)
    return night


def _hops(night) -> list[str]:
    return [t for t, _f in night.visits()]


def _by_score(plan: SequencePlan) -> list[str]:
    """The candidates' names best first at ``T0``, as the strategy defines
    best, worked out here from the sky."""
    scores = pool_sky_scores(plan.targets, LAT, LON, T0)
    return [t.name for t in sorted(plan.targets,
                                   key=lambda t: -scores[t.id])]


# -------------------------------------------------------------- the engine

async def test_best_available_picks_the_best_score_not_the_first_rank(
        group_hub, monkeypatch):
    """Rank order is Low, High, Mid and the score order is not: the night
    shoots them best first, each to completion."""
    plan = pool_plan("best")
    best = _by_score(plan)
    assert best[0] != plan.targets[0].name and best != [
        t.name for t in plan.targets], f"premise: rank and score differ {best}"
    night = await _night(group_hub, monkeypatch, plan)
    assert night.done, night.trace[-3:]
    assert _hops(night) == best, (_hops(night), best)
    assert [t for t, _f in night.shots()] == [n for n in best for _ in (0, 1)]
    assert night.stored.owed() == 0 and night.stored.status == "complete"


async def test_a_pool_with_no_strategy_reads_as_best_available(
        group_hub, monkeypatch):
    plan = pool_plan(None)
    night = await _night(group_hub, monkeypatch, plan)
    assert night.done, night.trace[-3:]
    assert _hops(night) == _by_score(plan), _hops(night)


async def test_priority_order_keeps_the_pick_on_the_first_rank(
        group_hub, monkeypatch):
    """Priority, one pass a visit: the candidate of first rank is picked at
    every boundary until it is complete, then the next. A mosaic's rotation
    would visit Low, High, Mid, Low, High, Mid."""
    night = await _night(group_hub, monkeypatch,
                         pool_plan("priority", mode="rotate"))
    assert night.done, night.trace[-3:]
    assert _hops(night) == ["Low", "Low", "High", "High", "Mid", "Mid"], (
        _hops(night))


async def test_round_robin_picks_the_least_recently_visited(
        group_hub, monkeypatch):
    """Round robin, one pass a visit, with one frame of Low banked ten
    minutes ago and one of High two hours ago: the never-visited Mid goes
    first, then High (visited longest ago), then Low, then Mid again. Rank
    order, which a mosaic's rotation would keep, is Low, High, Mid."""
    plan = pool_plan("round_robin", mode="rotate")
    session = Session(name=plan.name, created_ts=T0 - 86400.0,
                      status="dormant", plan=plan)
    for tid, when in (("low", T0 - 600.0), ("high", T0 - 7200.0)):
        session.frames.append(SessionFrame(
            ts=when, night="n0", target_id=tid, step_id=f"{tid}-L",
            auto_accepted=True))
    night = await _night(group_hub, monkeypatch, plan, session=session)
    assert night.done, night.trace[-3:]
    assert _hops(night) == ["Mid", "High", "Low", "Mid"], _hops(night)


async def test_a_floor_hit_hands_the_night_to_the_next_candidate(
        group_hub, monkeypatch):
    """The best candidate sinks below its own floor (on_floor advance) at its
    first frame and is set aside for tonight; the pool picks the next best,
    then the last, and the night completes without the first."""
    plan = pool_plan("best")
    best = _by_score(plan)
    target = next(t for t in plan.targets if t.name == best[0])
    target.schedule = Schedule(min_altitude_deg=30.0, on_floor="advance")
    real = engine_mod._frame_altitude
    monkeypatch.setattr(
        engine_mod, "_frame_altitude",
        lambda t, site, when: (10.0 if t.name == best[0]
                               else real(t, site, when)))
    night = await _night(group_hub, monkeypatch, plan)
    assert night.done, night.trace[-3:]
    shot = [t for t, _f in night.shots()]
    assert shot == [n for n in best[1:] for _ in (0, 1)], (shot, best)
    records = night.stored.set_aside
    assert [(r["target_id"], r["step_id"], r["kind"]) for r in records] == [
        (best[0].lower(), None, "floor")], records
    assert records[0]["night"] == night_key(T0), records


async def test_the_pick_is_said_by_name_and_never_with_the_score(
        group_hub, monkeypatch):
    night = await _night(group_hub, monkeypatch, pool_plan("best"))
    said = night.said("the pool picks")
    assert said and all("Pool: the pool picks " in m for m in said), said
    assert not any(ch.isdigit() for m in said for ch in m), said


async def test_a_pool_resumed_after_a_candidate_completed_skips_it(
        group_hub, monkeypatch):
    plan = pool_plan("priority")
    session = Session(name=plan.name, created_ts=T0 - 86400.0,
                      status="dormant", plan=plan)
    for _ in range(2):
        session.frames.append(SessionFrame(
            ts=T0 - 3600.0, night="n0", target_id="low", step_id="low-L",
            auto_accepted=True))
    night = await _night(group_hub, monkeypatch, plan, session=session)
    assert night.done, night.trace[-3:]
    assert _hops(night) == ["High", "Mid"], _hops(night)


# --------------------------------------------------- ResumeArm agreement

def test_resume_arm_recentres_on_the_pick_of_a_priority_pool():
    plan = pool_plan("priority")
    session = Session(name=plan.name, created_ts=T0 - 86400.0,
                      status="dormant", plan=plan)
    got = [t.name for t in recentre_candidates(session, night_key(T0))]
    assert got == ["Low", "High", "Mid"], got


def test_resume_arm_recentres_on_the_least_recently_visited_of_a_round_robin():
    plan = pool_plan("round_robin")
    session = Session(name=plan.name, created_ts=T0 - 86400.0,
                      status="dormant", plan=plan)
    for tid, when in (("low", T0 - 600.0), ("high", T0 - 7200.0)):
        session.frames.append(SessionFrame(
            ts=when, night="n0", target_id=tid, step_id=f"{tid}-L",
            auto_accepted=True))
    got = [t.name for t in recentre_candidates(session, night_key(T0))]
    assert got == ["Mid", "High", "Low"], got


def test_resume_arm_orders_a_best_pool_by_the_score_it_is_given():
    plan = pool_plan("best")
    scores = {"low": 10.0, "high": 30.0, "mid": 20.0}
    got = [t.name for t in _group_order(plan.groups[0], plan.targets,
                                        plan.targets, {}, {}, scores)]
    assert got == ["High", "Mid", "Low"], got


# ----------------------------------------------------------- order_pool

class _C:
    def __init__(self, id: str):
        self.id = id


def _ids(cs) -> list[str]:
    return [c.id for c in cs]


def test_best_orders_by_score_descending_and_a_missing_score_last():
    cs = [_C("a"), _C("b"), _C("c"), _C("d")]
    snap = OrderSnapshot(pool_score={"a": 10.0, "b": None, "c": 30.0})
    assert _ids(order_pool(cs, strategy="best", snapshot=snap)) == [
        "c", "a", "b", "d"]


def test_best_ties_keep_the_plans_order():
    cs = [_C("a"), _C("b"), _C("c")]
    snap = OrderSnapshot(pool_score={"a": 5.0, "b": 9.0, "c": 9.0})
    assert _ids(order_pool(cs, strategy="best", snapshot=snap)) == [
        "b", "c", "a"]


def test_priority_is_the_plans_order_whatever_the_snapshot_says():
    cs = [_C("a"), _C("b"), _C("c")]
    snap = OrderSnapshot(pool_score={"c": 99.0}, last_visit_ts={"a": 1.0})
    assert _ids(order_pool(cs, strategy="priority", snapshot=snap)) == [
        "a", "b", "c"]


def test_round_robin_puts_the_never_visited_first_then_the_oldest():
    cs = [_C("a"), _C("b"), _C("c"), _C("d")]
    snap = OrderSnapshot(last_visit_ts={"a": 500.0, "b": 100.0, "c": None})
    assert _ids(order_pool(cs, strategy="round_robin", snapshot=snap)) == [
        "c", "d", "b", "a"]


def test_every_candidate_comes_out_once():
    cs = [_C("a"), _C("b"), _C("c")]
    for strategy in ("best", "priority", "round_robin"):
        assert sorted(_ids(order_pool(cs, strategy=strategy))) == [
            "a", "b", "c"]


def test_an_unknown_strategy_and_a_nan_score_raise():
    with pytest.raises(ValueError, match="unknown pool strategy"):
        order_pool([_C("a")], strategy="lucky")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="pool score of candidate a"):
        order_pool([_C("a")], strategy="best",
                   snapshot=OrderSnapshot(pool_score={"a": math.nan}))


# ------------------------------------------------------------- the score

def test_the_moon_factor_is_one_with_the_moon_down_and_never_below_a_third():
    assert moon_factor(0.0, 1.0, False) == 1.0
    assert moon_factor(0.0, 1.0, True) == pytest.approx(0.3)
    assert moon_factor(120.0, 1.0, True) == 1.0
    assert moon_factor(60.0, 0.5, True) == pytest.approx(1 - 0.7 * 0.5 * 0.5)


def test_the_atlas_and_the_pool_share_one_moon_factor():
    assert visibility._moon_factor is moon_factor
    assert group_rules.moon_factor is moon_factor


def test_the_score_is_altitude_times_the_moon_factor_with_the_moon_up():
    m_ra, m_dec = moon_radec(T0)
    assert altaz(m_ra, m_dec, LAT, LON, T0)[0] > 0, "premise: the Moon is up"
    on_moon = SimpleNamespace(id="on", ra_hours=m_ra, dec_deg=m_dec)
    far = SimpleNamespace(id="far", ra_hours=ra_at(-1.0), dec_deg=30.0)
    scores = pool_sky_scores([on_moon, far], LAT, LON, T0)
    alt_on = altaz(m_ra, m_dec, LAT, LON, T0)[0]
    assert scores["on"] == pytest.approx(
        alt_on * (1 - 0.7 * moon_illumination(T0)))
    sep = angular_sep_deg(far.ra_hours, far.dec_deg, m_ra, m_dec)
    alt_far = altaz(far.ra_hours, far.dec_deg, LAT, LON, T0)[0]
    assert scores["far"] == pytest.approx(
        alt_far * moon_factor(sep, moon_illumination(T0), True))
    assert scores["far"] / alt_far > scores["on"] / alt_on


# ------------------------------------------------------ identity refusals

def test_a_well_formed_pool_is_accepted():
    assert plan_identity_errors(pool_plan("round_robin")) == []
