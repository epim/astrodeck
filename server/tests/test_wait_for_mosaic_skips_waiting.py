""""Wait for the mosaic" skips a WAITING target once the mosaic it waits for
is set aside (#374; spec 1.6 "Wait for the mosaic", 6.4 termination, 5.1).

THE DEFECT. The skip half of the gate was asked only on the scheduler's READY
path. A target still waiting on its own time window or altitude gate was
counted as a waiter at its opening, so when the mosaic it waits for was set
aside tonight the run stayed alive, idle, until that opening, only to skip
the target and end then. Both carriers of the gate had it: an
``after_group`` follower in no group (S2, `_follower_gate`'s own gate), and a
whole group one of whose members carries ``after_group`` (#330,
`_group_gate`).

THE FIX. The selection asks `_follower_gate` of a waiting target too, for
the skip half alone (``waiting=True``): a skip leaves tonight at once, a
member's with its whole group, and the wait and ready verdicts stay on the
ready path, so no "waits:" line is said before a target is ready.

THE NIGHTS, the issue's probe on the clocked simulator
(tests/_group_harness.py; the real `_run_scheduled`, `_eligibility_now`,
`_follower_gate`, `_group_gate`, `_skip_group_tonight`): an upstream 2x2
"M31", one L frame a panel, open from the start, whose panels never centre,
so each is deferred on three consecutive passes and set aside at 600 s.
Downstream, under "Wait for the mosaic", "M33", whose window opens at
``OPENS`` (about two hours in): a single target carrying ``after_group``
(the follower carrier), or a 1x2 group whose two panels carry it, as the
compile writes a mosaic that follows another (the group carrier).

Each case names the mutant it was shown RED under, with the failure
observed, verbatim (pytest's own lines, long ones wrapped). The mutants were
applied in a private scratch copy of server/ (scratchpad/S5-ENG-SCHED-mut),
never in the shared tree (#254):

* "skip half asked only for ready targets": the selection's
  ``if self._group_runs:`` around its `_follower_gate` call made
  ``if self._group_runs and state == "ready":``, the code before the fix.
* "a waiting target leaves on any verdict but ready": the selection's
  ``if gate.kind == "skip":`` made ``if gate.kind == "skip" or (state !=
  "ready" and gate.kind != "ready"):``, a fix that reads the upstream's wait
  as a skip. The controls are for it.
"""
from __future__ import annotations

import time as _time

import pytest

from _group_harness import (GROUP_ID, GROUP_NAME, T0, Night, group_hub,
                            group_store, panel, ra_at)  # noqa: F401
from astrodeck.sequence.models import (ExposureStep, Schedule, SequencePlan,
                                       Target, TargetGroup,
                                       plan_identity_errors)
from astrodeck.sequence.session import session_store

UP_ID, UP_NAME = GROUP_ID, GROUP_NAME
DOWN_ID, DOWN_NAME = "m33-mosaic", "M33"
#: M33's window opens here, a whole local minute 7191 s into the night: the
#: issue's probe, where the run waited from 600 s to 7191 s.
OPENS = T0 + 7191.0
SKIP = ("M33: skipped for tonight: the M31 mosaic it waits for is set aside "
        "tonight; not done, so the next night takes it up")
CARRIERS = ("follower", "group")


def _opens() -> Schedule:
    return Schedule(start_mode="time",
                    start_time=_time.strftime("%H:%M", _time.localtime(OPENS)))


def _downstream(carrier: str) -> tuple[list[Target], list[TargetGroup]]:
    """M33 under "Wait for the mosaic", its window opening at ``OPENS``: one
    target in no group, or a 1x2 group each of whose panels carries the
    gate."""
    if carrier == "follower":
        return [Target(id="m33", name=DOWN_NAME, ra_hours=ra_at(-2.6),
                       dec_deg=33.0, center=True, autofocus_first=False,
                       after_group=UP_ID, schedule=_opens(),
                       steps=[ExposureStep(id="m33-L", filter="L",
                                           exposure_s=30.0, count=1)])], []
    members = [Target(id=f"q0{c}", name=f"{DOWN_NAME} 1-{c + 1}",
                      ra_hours=ra_at(-2.6 + 0.03 * c), dec_deg=33.0,
                      center=True, autofocus_first=False, acquisition="cycle",
                      mosaic_group=DOWN_ID, panel_row=0, panel_col=c,
                      after_group=UP_ID, schedule=_opens(),
                      steps=[ExposureStep(id=f"q0{c}-L", filter="L",
                                          exposure_s=30.0, count=1,
                                          per_visit=1)])
               for c in range(2)]
    return members, [TargetGroup(id=DOWN_ID, name=DOWN_NAME,
                                 geometry={"rows": 1, "cols": 2})]


def _plan(carrier: str) -> SequencePlan:
    up = [panel(r, c, filters=("L",), count=1)
          for r in range(2) for c in range(2)]
    down, down_groups = _downstream(carrier)
    plan = SequencePlan(
        name="wait for the mosaic", targets=[*up, *down],
        groups=[TargetGroup(id=UP_ID, name=UP_NAME,
                            geometry={"rows": 2, "cols": 2}), *down_groups],
        guide=False, dither_every=0, autofocus_every=0, meridian_flip=False,
        park_when_done=False, warm_cooler_when_done=False,
        recover_guiding=False)
    assert plan_identity_errors(plan) == [], plan_identity_errors(plan)
    return plan


def _up_misses(passes: int):
    """A goto script: every M31 hop misses its centring on the panel's first
    ``passes`` visits, and every other hop centres."""
    def goto(who: str, n: int, result: dict) -> dict:
        if who.startswith(UP_NAME) and n <= passes:
            return dict(result, centered=False, error_arcmin=None)
        return result
    return goto


async def _night(hub, monkeypatch, plan: SequencePlan, goto) -> Night:
    night = Night(hub, monkeypatch, goto=goto)
    try:
        night.done = await night.run(plan, wall_s=60.0)
    finally:
        await night.close()
    night.stored = session_store.load(night.session_id)
    return night


def _published(night: Night) -> list[tuple[float, dict]]:
    return [(e[0], e[2]) for e in night.trace if e[1] == "state"]


@pytest.mark.parametrize("carrier", CARRIERS)
async def test_a_waiting_target_leaves_when_its_mosaic_is_set_aside(
        group_hub, monkeypatch, carrier):
    """No M31 panel ever centres, so M31 is set aside at its third pass.
    M33 is still waiting on its own window then, two hours off; nothing it
    waits for can come tonight, so it is skipped for the night at that
    selection, said once for the target or the mosaic, and the terminal
    publish comes at M31's set-aside, not at M33's window. It was never
    slewed to or shot, and it is still owed, so the session stays dormant.

    MUTANT "skip half asked only for ready targets": RED on both carriers,
    the run waiting from M31's set-aside to M33's window (observed,
    "follower", then "group"):
        AssertionError: the terminal publish came 6591 s after M31's set-aside
        at 600 s: [(600.0, 'running', 'waiting for M33'), (7191.0,
        'complete', 'targets set aside — frames still owed')]
        assert 7191.0 == 600.0
        AssertionError: the terminal publish came 6591 s after M31's set-aside
        at 600 s: [(600.0, 'running', 'waiting for M33 1-1'), (7191.0,
        'complete', 'targets set aside — frames still owed')]
        assert 7191.0 == 600.0
    MUTANT "a waiting target leaves on any verdict but ready": RED on both
    carriers too, M33 leaving at 0 s on M31's wait (observed, "follower";
    "group" word for word the same):
        AssertionError: [(0.0, 'M33: skipped for tonight: after the M31
        mosaic; not done, so the next night takes it up')]
        assert [(0.0, 'M33: ...takes it up')] == [(600.0, 'M33...takes it
        up')]
          At index 0 diff: (0.0, 'M33: skipped for tonight: after the M31
        mosaic; not done, so the next night takes it up') != (600.0, 'M33:
        skipped for tonight: the M31 mosaic it waits for is set aside
        tonight; not done, so the next night takes it up')
    """
    night = await _night(group_hub, monkeypatch, _plan(carrier), _up_misses(99))
    assert night.done, night.lines[-4:]
    assert sorted(r["target_id"] for r in night.stored.set_aside) == [
        "p00", "p01", "p10", "p11"], (
        f"premise: M31 was set aside tonight: {night.stored.set_aside}")
    set_aside = max(night.rel(t) for t, _lv, m in night.lines
                    if m.startswith("M31: centring failed"))
    published = _published(night)
    end_t, end = published[-1]
    assert end.get("state") == "complete", end
    assert end_t == set_aside, (
        f"the terminal publish came {end_t - set_aside:.0f} s after M31's "
        f"set-aside at {set_aside:.0f} s: "
        f"{[(t, v.get('state'), v.get('detail')) for t, v in published[-2:]]}")
    skipped = [(night.rel(t), m) for t, _lv, m in night.lines
               if "skipped for tonight" in m]
    assert skipped == [(set_aside, SKIP)], skipped
    assert not any(who.startswith(DOWN_NAME) for _t, who in night.gotos), (
        night.gotos)
    assert night.captures == [], night.captures[:2]
    assert night.stored.status == "dormant", night.stored.status


@pytest.mark.parametrize("carrier", CARRIERS)
async def test_while_its_mosaic_is_live_a_waiting_target_keeps_waiting(
        group_hub, monkeypatch, carrier):
    """CONTROL. M31 misses its centring on the first pass only: it waits out
    one deferral and then shoots and completes, so it is live while M33
    waits on its window. The skip half, asked of the waiting M33 all that
    time, answers "wait" and then "ready", neither a skip: M33 is never
    skipped, is shot once its window opens, and the night completes. GREEN
    under the mutant "skip half asked only for ready targets" (observed): it
    is the question the fix adds that this case grades, not the old path.

    MUTANT "a waiting target leaves on any verdict but ready": RED on both
    carriers (observed, "follower"; "group" word for word the same):
        AssertionError: M33 left the night while M31 was live: ['M33: skipped
        for tonight: after the M31 mosaic; not done, so the next night takes
        it up']
        assert not ['M33: skipped for tonight: after the M31 mosaic; not
        done, so the next night takes it up']
    """
    night = await _night(group_hub, monkeypatch, _plan(carrier), _up_misses(1))
    assert night.done, night.lines[-4:]
    assert night.said("M31: pass 1 took no exposures"), (
        f"premise: M31 waited out a deferral while M33 waited: "
        f"{night.said('pass 1')}")
    skipped = night.said("skipped for tonight")
    assert not skipped, f"M33 left the night while M31 was live: {skipped}"
    down = [c["t"] for c in night.captures
            if c["target"].startswith(DOWN_NAME)]
    assert down and min(down) >= OPENS, (
        f"M33 was not shot once its window opened: {night.shots()}")
    assert night.stored.status == "complete", night.stored.status
