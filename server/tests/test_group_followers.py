"""Followers of a mosaic, and ``after_group`` (#189 S2, task T18; spec 1.6,
D15, 6.4, 6.18).

A FOLLOWER, for S2, is a target that is no member of the group and comes
after it in plan order (the flow compile defines them from the wiring in
S3). While the group has live members every follower sorts after them, so
one is chosen only when every live panel waits: behind a limit, held by the
meridian rule, or not yet in its window, and since #304 in the group's
deferral wait after an all-deferred pass, whose cases are in
test_group_follower_fills_defer_wait.py.

"SHOOT LATER TARGETS, THEN COME BACK" (the default). Such a follower runs ONE
visit, bounded by ``group_ready_ts``, the earliest time a live panel becomes
eligible. The visit ends at the frame boundary before it, the follower
keeps every frame it banked, and the group resumes. Without the bound a
follower chosen at any all-waiting moment, the routine pre-flip idle, a
tree, one 60 s recheck, runs to completion and hands the rest of the night
away (spec 1.6).

"WAIT FOR THE MOSAIC" (``Target.after_group``). The follower waits, "after the
M31 mosaic", while the group can still shoot tonight; once the group is set
aside tonight it is skipped for the night, not done; once the group is
complete it runs.

The runs are the real `_run_scheduled` on the clocked simulator
(tests/_group_harness.py) with ``coords_clock``. The mosaic is one panel,
8 min before transit unless a case moves it, so the plan's 10 min lead
leaves it no room: it waits for its crossing, which is the wait a follower
fills. One case moves it behind a bump in the horizon mask instead.

Each case names the mutant it was shown RED under, with the failure
observed, verbatim. Every mutant was applied in a private scratch copy of
server/, never in the shared tree (#254).
"""
from __future__ import annotations

import pytest

import astrodeck.sequence.engine as engine_mod
from _group_harness import (GROUP_ID, GROUP_NAME, LON, T0, Night, grid_plan,
                            group_hub, group_store, single)
from astrodeck.sequence import schedule
from astrodeck.sequence.session import session_store

FOLLOWER = "Follower"


def _plan(*, follower_kw: dict | None = None, panel_kw: dict | None = None,
          follower_count: int = 40, **plan_kw):
    """A one-panel mosaic 8 min before transit (L and R, count 3) and one
    follower after it in plan order, 40 frames of L."""
    pkw = {"ha_h": -8.0 / 60.0, "ha_step_h": 0.0, "count": 3,
           **(panel_kw or {})}
    after = single(FOLLOWER, count=follower_count, **(follower_kw or {}))
    return grid_plan(rows=1, cols=1, panel_kw=pkw, after=[after],
                     meridian_flip=True, **plan_kw)


async def _night(hub, monkeypatch, plan, *, wall_s: float = 60.0,
                 before_run=None, **kw) -> Night:
    night = Night(hub, monkeypatch, coords_clock=True, **kw)
    if before_run is not None:
        before_run(night)
    try:
        night.done = await night.run(plan, wall_s=wall_s)
    finally:
        await night.close()
    night.stored = session_store.load(night.session_id)
    return night


def _record_bounds(night, into: list):
    real = night.engine._run_visit

    async def run_visit(ti, target, visit):
        into.append((target.name, visit))
        return await real(ti, target, visit)

    night.mp.setattr(night.engine, "_run_visit", run_visit)


def _ends(night, name: str) -> list[float]:
    return [c["t"] + c["exposure_s"] for c in night.captures
            if c["target"] == name]


# ------------------------------------------------- a bounded follower visit

async def test_a_follower_fills_the_wait_and_hands_the_night_back(
        group_hub, monkeypatch):
    """The mosaic waits 8 min for its crossing; the follower after it is
    chosen, and runs ONE visit bounded by ``group_ready_ts``, the moment the
    panel becomes eligible (spec 1.6, 6.18). Its frames all end before that
    deadline, the next one would not have, and at the deadline the group
    resumes: the panel's hop comes within seconds of it. The follower keeps
    the frames it banked in the ledger and, once the mosaic is done, is
    taken up again and finishes all 40.

    MUTANT "follower runs to completion" (`_follower_gate` returning
    "ready" where it bounds the visit): RED (observed):
        AssertionError: the group came back 706 s after its panel was due
        assert 1788314889.0 <= (1788314182.6893919 + 10.0)
    """
    bounds: list = []
    night = await _night(group_hub, monkeypatch, _plan(),
                         before_run=lambda n: _record_bounds(n, bounds))
    assert night.done, night.trace[-3:]
    panel = f"{GROUP_NAME} 1-1"
    first_hop = min(t for t, who in night.gotos if who == panel)
    ra = night.engine.plan.targets[0].ra_hours
    # The panel may be visited once it is a quarter of a minute past its
    # crossing (`MERIDIAN_SIDE_MARGIN_S`); the group is back within seconds.
    due = (T0 + schedule.hours_to_meridian_flip(ra, LON, T0) * 3600.0
           / 1.0027379093 + engine_mod.MERIDIAN_SIDE_MARGIN_S)
    assert due <= first_hop <= due + 10.0, (
        f"the group came back {first_hop - due:.0f} s after its panel was "
        f"due")
    fbound = [vb for name, vb in bounds if name == FOLLOWER]
    assert fbound and fbound[0].passes is None, bounds
    deadline = fbound[0].deadline_ts
    assert deadline is not None, fbound
    # The group comes back AT the deadline: within seconds after it, or a
    # hair before it, since the wake is re-estimated nearer the crossing and
    # the countdown's hours run 0.27% fast against the clock's (group_rules'
    # meridian docstring), and never before the panel is past the meridian.
    slack = 0.003 * (deadline - T0) + 1.0
    assert deadline - slack <= first_hop <= deadline + 10.0, (
        f"the group came back {first_hop - deadline:.1f} s after the "
        f"follower's deadline")
    assert schedule.hours_to_meridian_flip(ra, LON, first_hop) < 0
    before = [e for e in _ends(night, FOLLOWER) if e <= first_hop]
    assert before, "the follower shot nothing while the mosaic waited"
    assert max(before) <= deadline, (max(before) - deadline)
    assert max(before) + 30.0 + 12.0 + 30.0 > deadline, (
        f"the visit ended {deadline - max(before):.0f} s early")
    assert len(before) < 40, len(before)
    # Kept, and finished once the mosaic was done.
    panel_ends = _ends(night, panel)
    after = [e for e in _ends(night, FOLLOWER) if e > first_hop]
    assert min(after) > max(panel_ends), "the follower cut into the mosaic"
    assert len(before) + len(after) == 40, (len(before), len(after))
    kept = [f for f in night.stored.frames if f.target_id == "follower"]
    assert len(kept) == 40, len(kept)
    assert night.stored.status == "complete", night.stored.status
    assert night.said("every panel waits; shooting Follower until the next "
                      "panel is due")
    assert night.said("Follower: back to the M31 mosaic; its frames are kept")


async def test_a_follower_with_no_room_before_the_panel_is_not_chosen(
        group_hub, monkeypatch):
    """The bound's floor: the mosaic's panel is due in 8.3 min, and the
    follower's frame is 460 s, so a hop (150 s estimated) and one frame
    (460 s, the 12 s overhead seed, the 30 s margin) do not fit before it.
    The follower is NOT chosen: its visit could shoot nothing before the
    next panel is due, and would be a hop for nothing (on a fake clock,
    where a hop takes no time, a loop of them). The night waits for the
    panel, and the follower runs once the mosaic is done.

    MUTANT "no room check" (`_follower_gate` bounding the visit whatever
    the room): RED (observed):
        AssertionError: [[0.0, 'goto', 'Follower', None, {}], [0.0, 'tracking',
        True], [0.0, 'slew', 22.118021, 35.0]]
        assert False
        (the run hopped to the follower again and again at one fake instant,
        shooting nothing, until the 20 s wall-clock bound)
    """
    from astrodeck.sequence.models import ExposureStep
    plan = _plan(follower_count=2)
    plan.targets[-1].steps = [ExposureStep(id="follower-L", filter="L",
                                           exposure_s=460.0, count=2)]
    night = await _night(group_hub, monkeypatch, plan, wall_s=20.0)
    assert night.done, night.trace[-3:]
    panel = f"{GROUP_NAME} 1-1"
    first_hop = min(t for t, who in night.gotos if who == panel)
    follower_hops = [t for t, who in night.gotos if who == FOLLOWER]
    assert follower_hops and min(follower_hops) > first_hop, (
        [(round(t - T0), w) for t, w in night.gotos])
    assert night.stored.status == "complete"


async def test_a_blocks_follower_keeps_its_block_order_under_the_bound(
        group_hub, monkeypatch):
    """A follower with BLOCKS acquisition (20 L, then 20 R) fills the same
    8 min wait. Its bounded visit shoots one frame per `_run_step` and asks
    the deadline between them (spec 1.6: "for a follower with blocks
    acquisition that means calling `_run_step(max_frames=1)` in a loop"),
    in its own block order: every frame before the mosaic's panel is due
    is L, and across the bound and its return after the mosaic the
    follower shoots all 20 L and then all 20 R, exactly as an unbounded
    blocks target would. A round of every step would have turned it into a
    cycle.

    MUTANT "blocks bounded as a cycle" (`_run_visit`'s blocks-under-a-
    deadline path disabled, so the bound falls to the round loop): RED
    (observed, the T18 verifier's run):
        AssertionError: ['L', 'R', 'L', 'R', 'L', 'R', ...]
        assert ['L', 'R', 'L...'L', 'R', ...] == ['L', 'L', 'L...'L', 'L', ...]
    MUTANT "blocks in reverse" (the path walking the steps last first): RED
    (observed, the T18 verifier's run):
        AssertionError: ['R', 'R', 'R', 'R', 'R', 'R', ...]
        assert ['R', 'R', 'R...'R', 'R', ...] == ['L', 'L', 'L...'L', 'L', ...]
    """
    plan = _plan(follower_kw={"filters": ("L", "R")}, follower_count=20)
    plan.targets[-1].acquisition = "blocks"
    night = await _night(group_hub, monkeypatch, plan)
    assert night.done, night.trace[-3:]
    panel = f"{GROUP_NAME} 1-1"
    first_hop = min(t for t, who in night.gotos if who == panel)
    shot = [(c["t"], c["filter"]) for c in night.captures
            if c["target"] == FOLLOWER]
    before = [f for t, f in shot if t < first_hop]
    assert 2 <= len(before) < 20, before
    assert before == ["L"] * len(before), before
    assert [f for _t, f in shot] == ["L"] * 20 + ["R"] * 20, shot
    assert night.said("every panel waits; shooting Follower until the next "
                      "panel is due")
    assert night.said("Follower: back to the M31 mosaic; its frames are kept")
    assert night.stored.status == "complete", night.stored.status


async def test_a_follower_whose_window_opens_first_sorts_behind_the_mosaic(
        group_hub, monkeypatch):
    """Spec 1.6: while the group has live members, its followers sort after
    every member in ``remaining``, whatever their window start. Here the
    follower's window opens at dusk, before the mosaic's (now), so
    `schedule_order` puts it ahead of the panel, and `_start_groups` moves
    it behind the group's last member. A target BEFORE the group in plan
    order with the same early window is no follower and keeps its place.

    Nothing the night does rests on this order alone: `_follower_gate` holds
    a follower whenever a live panel is eligible, wherever it sits, and the
    T18 verifier ran this night with the placement removed and saw the same
    hops in the same order. The order decides which of two waiters with the
    same wake time the wait publishes, the first in ``remaining``; this case
    pins the order the spec states.

    MUTANT "no placement" (`_start_groups` not calling `_place_followers`):
    RED (observed, the T18 verifier's run):
        AssertionError: ['Before', 'Follower', 'M31 1-1']
        assert ['Before', 'F...r', 'M31 1-1'] == ['Before', 'M...', 'Follower']
          At index 1 diff: 'Follower' != 'M31 1-1'
    """
    from _group_harness import single
    from astrodeck.sequence.models import Schedule
    before = single("Before", count=1, schedule=Schedule(start_mode="dusk"))
    plan = _plan(follower_kw={"schedule": Schedule(start_mode="dusk")},
                 panel_kw={"ha_h": -2.0}, before=[before])
    orders: list = []

    def setup(night):
        real = night.engine._start_groups

        def start_groups(plan, remaining):
            orders.append([t.name for t in remaining])
            real(plan, remaining)
            orders.append([t.name for t in remaining])

        night.mp.setattr(night.engine, "_start_groups", start_groups)

    night = await _night(group_hub, monkeypatch, plan, before_run=setup)
    assert night.done, night.trace[-3:]
    panel = f"{GROUP_NAME} 1-1"
    assert orders[0] == ["Before", FOLLOWER, panel], (
        f"premise: the follower's window opens first: {orders[0]}")
    assert orders[1] == ["Before", panel, FOLLOWER], orders[1]
    assert [w for _t, w in night.gotos] == [
        "Before", panel, panel, panel, FOLLOWER], night.gotos
    assert night.stored.status == "complete", night.stored.status


# --------------------------------------------------------------- after_group

async def test_an_after_group_follower_waits_for_the_mosaic(group_hub,
                                                            monkeypatch):
    """"Wait for the mosaic": the same follower, with ``after_group`` naming
    the mosaic, is NOT shot while the mosaic can still shoot tonight, even
    through the 8 min the panel waits for its crossing; it waits, "after the
    M31 mosaic", said once. Once the mosaic is complete it is ready, and
    runs its whole course.

    MUTANT "after_group ignored" (`_follower_gate` reading no
    ``after_group``): RED (observed):
        AssertionError: the follower shot 15 frames before the mosaic it waits
        for was done
        assert 1788313719.0 > 1788314363.7679677
    """
    night = await _night(group_hub, monkeypatch,
                         _plan(follower_kw={"after_group": GROUP_ID}))
    assert night.done, night.trace[-3:]
    panel = f"{GROUP_NAME} 1-1"
    follower = sorted(_ends(night, FOLLOWER))
    assert follower, "the follower never ran"
    done_at = max(_ends(night, panel))
    early = sum(1 for e in follower if e <= done_at)
    assert min(follower) > done_at, (
        f"the follower shot {early} frames before the mosaic it waits for "
        f"was done")
    assert len(follower) == 40
    said = night.said("Follower: waits: after the M31 mosaic")
    assert len(said) == 1, said
    assert night.stored.status == "complete"


async def test_an_after_group_follower_is_skipped_once_the_mosaic_is_set_aside(
        group_hub, monkeypatch):
    """The mosaic's one panel never centres: it is deferred on three
    consecutive passes and set aside tonight (spec 5.1), so the mosaic is
    set aside and cannot be done tonight. Its ``after_group`` follower is
    then SKIPPED FOR THE NIGHT, NOT DONE: never slewed to, marked skipped,
    still owed, and the session stays dormant for the next night.

    RE-PINNED FOR H4 (#534, H4 orchestrator ruling 2). A lone panel's miss
    is still its own and still counts, but a streak of centring misses now
    sets the panel aside "for now": the set-aside expires once a night, 45
    minutes on here, the panel strikes out again, and only that second
    streak sets it aside for the night. So the session holds two records
    for 1-1, the first marked expired, where it held one before H4, and
    the follower waits for the mosaic through the expiry, as a follower of
    a live mosaic does, until the mosaic is set aside for the night.

    MUTANT "after_group ignored", re-run by the H4 integration in a private
    copy of server/ (scratchpad H4-INTEG-mut; `_follower_gate`'s
    ``after_group`` branch made ``if False:``): RED (observed):
        AssertionError: [(1788313689.0, 'M31 1-1'), (1788313689.0,
        'Follower'), (1788313989.0, 'M31 1-1'), (1788313989.0, 'Follower'),
        (1788314289.0, 'M31 1-1'), (1788314289.0, 'Follower'), ...]
        assert False

    MUTANT "after_group ignored": RED (observed):
        AssertionError: [(1788313689.0, 'M31 1-1'), (1788313989.0, 'M31 1-1'),
        (1788314289.0, 'M31 1-1'), (1788314289.0, 'Follower')]
        assert False
    MUTANT "an empty night read as a dawn cutoff" (the selection's ``if not
    remaining: break`` after the skip removed, so the all-closed branch sees
    an empty list): RED (observed):
        assert True is False
         +  where True = <astrodeck.sequence.engine.SequenceEngine object at
        0x000001B69018CBC0>._dawn_cutoff
    """
    def no_centre(who, n, result):
        if who == f"{GROUP_NAME} 1-1":
            return dict(result, centered=False, error_arcmin=None)
        return result

    plan = _plan(follower_kw={"after_group": GROUP_ID},
                 panel_kw={"ha_h": -2.0})
    night = await _night(group_hub, monkeypatch, plan, goto=no_centre)
    assert night.done, night.trace[-3:]
    # The first streak's record, expired (#534), and the second's.
    assert [(r["target_id"], bool(r.get("expired")))
            for r in night.stored.set_aside] == [("p00", True),
                                                 ("p00", False)]
    assert all(who != FOLLOWER for _t, who in night.gotos), night.gotos
    assert night.captures == [], night.captures[:2]
    assert night.said("Follower: skipped for tonight: the M31 mosaic it "
                      "waits for is set aside tonight; not done")
    assert night.stored.status == "dormant", night.stored.status
    # The night ran its course; nothing closed a window on it.
    assert night.engine._dawn_cutoff is False


async def test_a_follower_is_bounded_by_the_moment_a_panel_clears_its_mask(
        group_hub, group_store, monkeypatch):
    """``group_ready_ts`` for a panel behind a limit is the projected moment
    it clears it: a forward scan in 60 s steps over the slew gate's own
    predicate (spec 1.6, `_reach_clear_ts`), never the 60 s recheck, which
    is a cadence and not a deadline. The one panel sits behind a bump in the
    horizon mask on its own azimuth that it rises clear of 15 min in; the
    follower, west of the meridian and clear of the bump, is shot until
    then, and the panel is acquired at the minute it clears.

    MUTANT "a limit bounds nothing" (`_reach_clear_ts` returning None, so a
    panel behind a limit gives no ``group_ready_ts``): RED (observed):
        AssertionError: 1200.0
        assert 1788314889.0 <= (1788314589.0 + 60.0)
    """
    from _group_harness import LAT, ra_at
    from astrodeck.catalog.coords import altaz
    from astrodeck.config import SafetyConfig
    ra, dec = ra_at(-2.0), 40.0
    clear_at = T0 + 900.0
    bump, az = altaz(ra, dec, LAT, LON, clear_at)
    group_store.set_safety(SafetyConfig(enabled=False, horizon=[
        (0.0, 0.0), (az - 8.0, 0.0), (az - 6.0, bump), (az + 6.0, bump),
        (az + 8.0, 0.0), (360.0, 0.0)]))
    plan = _plan(panel_kw={"ha_h": -2.0}, follower_kw={"ha_h": 1.0})
    night = await _night(group_hub, monkeypatch, plan)
    assert night.done, night.trace[-3:]
    panel = f"{GROUP_NAME} 1-1"
    first_hop = min(t for t, who in night.gotos if who == panel)
    assert clear_at <= first_hop <= clear_at + 60.0, (first_hop - T0)
    before = [e for e in _ends(night, FOLLOWER) if e <= first_hop]
    assert 0 < len(before) < 40, len(before)
    assert max(before) <= first_hop, (max(before) - first_hop)
    assert first_hop - max(before) < 30.0 + 12.0 + 30.0 + 60.0, (
        f"the follower stopped {first_hop - max(before):.0f} s before the "
        f"panel cleared")
    assert night.stored.status == "complete"
