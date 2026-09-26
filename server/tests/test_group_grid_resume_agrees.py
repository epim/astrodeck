"""A resumed ``grid`` mosaic goes on where the ledger left it, in the engine as
in ResumeArm (#189 S2, #317; spec 5.2, 5.9; `sequence/panel_order.py`).

``order = "grid"`` is the snake rotated to start after the panel visited
last (`panel_order.order_panels`). ResumeArm reads "visited last" from the
ledger: the panel with the newest frame, complete ones included
(`resume_arm._group_order`), and re-centres on the panel after it. The
engine's own snapshot (`SequenceEngine._order_snapshot`) read it only from
``_group_last_visited``, which `start` empties and only a visit in THIS run
fills. So the first pass of a resumed grid mosaic started at the top of the
snake: the recovery re-centred on one panel and the run slewed straight to
another, and the grid began again from 1-1 instead of going on. The two
callers of the one order function disagreed, which is the drift
panel_order.py exists to prevent.

The S2 review found it by mutation: deleting the only write of
``_group_last_visited`` (in `_visit_panel`) passed every S2 test (396
passed), because nothing read that value on a path a test drove.

Each case runs the real engine on the clocked simulator
(tests/_group_harness.py) from a seeded dormant session.

MUTANT "the first pass ignores the ledger's last visit" (the ledger
fallback in `_order_snapshot` removed, ``if last_visited is None:`` made
``if False:``, which is the code before this fix), run in a private scratch
copy of server/ (#254): RED on both resume cases, the control green
(observed, ``-n0``):
    E       AssertionError: assert ['1-1', '1-2', '2-2', '2-1'] == ['2-2',
            '2-1', '1-1', '1-2']
    E         At index 0 diff: '1-1' != '2-2'
    E       AssertionError: assert ['1-1', '1-2', '2-1'] == ['2-1', '1-1',
            '1-2']
    E         At index 0 diff: '1-1' != '2-1'
"""
from __future__ import annotations

from _group_harness import (GROUP_NAME, T0, Night, grid_plan, group_hub,  # noqa: F401
                            group_store)
from astrodeck.events import night_key
from astrodeck.sequence.resume_arm import recentre_candidates
from astrodeck.sequence.session import Session, SessionFrame


def _label(target: str) -> str:
    prefix = GROUP_NAME + " "
    return target[len(prefix):] if target.startswith(prefix) else target


def _seeded(plan, frames) -> Session:
    """A dormant session of ``plan`` holding one accepted frame per
    ``(target id, ts)`` (an L) or ``(target id, ts, filter)``."""
    s = Session(name=plan.name, created_ts=T0 - 86400.0, status="dormant",
                plan=plan)
    for tid, ts, *filt in frames:
        s.frames.append(SessionFrame(ts=ts, night="n0", target_id=tid,
                                     step_id=f"{tid}-{(filt or ['L'])[0]}",
                                     auto_accepted=True))
    return s


async def _first_pass(hub, monkeypatch, session, n: int = 4) -> list[str]:
    """The labels of the run's first ``n`` hops: its first pass over ``n``
    live panels."""
    night = Night(hub, monkeypatch)
    try:
        done = await night.run(session.plan, session=session)
    finally:
        await night.close()
    assert done, night.trace[-3:]
    return [_label(who) for _t, who in night.gotos][:n]


async def test_a_resumed_grid_starts_after_the_panel_the_ledger_visited_last(
        group_hub, monkeypatch):
    """The ledger holds 1-1 (oldest), 2-2, then 1-2 (newest), so 1-2 was
    visited last. The snake is 1-1, 1-2, 2-2, 2-1: the grid goes on at 2-2,
    then 2-1, then wraps to 1-1 and 1-2. ResumeArm's candidates start at
    2-2 as well, so the panel the recovery re-centres on is the panel the
    run shoots first."""
    plan = grid_plan(group_kw={"order": "grid"})
    session = _seeded(plan, [("p00", T0 - 7200.0), ("p11", T0 - 5400.0),
                             ("p01", T0 - 1800.0)])
    arm_first = recentre_candidates(session, night_key(T0))[0]
    assert _label(arm_first.name) == "2-2", (
        f"premise: ResumeArm re-centres on 2-2, not {arm_first.name}")
    assert await _first_pass(group_hub, monkeypatch, session) == [
        "2-2", "2-1", "1-1", "1-2"]


async def test_a_grid_goes_on_after_a_panel_that_completed_on_its_visit(
        group_hub, monkeypatch):
    """The panel visited last may have completed on that visit (ResumeArm's
    own case, test_resume_arm_picks_what_runs.py). 1-2 and 2-1 hold one L
    each, older, and 2-2 completed its three L and three R on the newest
    visit. The grid goes on after 2-2's place, at 2-1, in the engine as in
    ResumeArm, although 2-2 is no longer one of the panels being ordered.

    MUTANT "the fallback skips a complete panel" (``seen`` also requiring
    ``t.id not in self._group_runs[group.id].completed``): RED (observed,
    ``-n0``):
        E       AssertionError: assert ['1-1', '1-2', '2-1'] == ['2-1',
                '1-1', '1-2']
        E         At index 0 diff: '1-1' != '2-1'
    Building ``placed`` from ``members`` instead is an EQUIVALENT mutant
    here (3 passed): at the run's start a complete panel is still in
    ``remaining``, so it is among the members being ordered; the plan's
    targets are read so the answer does not rest on that.
    """
    plan = grid_plan(group_kw={"order": "grid"})
    newest = [("p11", T0 - 3600.0, f) for f in ("L", "R") for _ in range(3)]
    session = _seeded(plan, [("p01", T0 - 10800.0), ("p10", T0 - 7200.0),
                             *newest])
    assert session.remaining()["p11-L"] == 0 == session.remaining()["p11-R"], (
        "premise: 2-2 completed on the last visit")
    assert _label(recentre_candidates(session, night_key(T0))[0].name) == "2-1"
    assert await _first_pass(group_hub, monkeypatch, session, 3) == [
        "2-1", "1-1", "1-2"]


async def test_control_a_fresh_grid_starts_at_the_top_of_the_snake(
        group_hub, monkeypatch):
    """The control: nothing in the ledger, nothing visited, so the grid
    starts at the top of the snake, in the engine and in ResumeArm."""
    plan = grid_plan(group_kw={"order": "grid"})
    session = _seeded(plan, [])
    assert _label(recentre_candidates(session, night_key(T0))[0].name) == "1-1"
    assert await _first_pass(group_hub, monkeypatch, session) == [
        "1-1", "1-2", "2-2", "2-1"]
