# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""RETRY SET-ASIDE PANELS, the controls the first two files left ungraded
(#600, backlog ruling D-07, owner-approved 2026-09-30; WP-104, the
verifier's additions).

test_w15_retry_set_aside_night.py and ..._records.py drive the action on a
mosaic of ONE group with no panel complete, so a handful of things the code
and its docstrings claim could be removed without any test turning red:

* the retry is scoped to the GROUP it names, and ``null`` means every group;
* a panel that is COMPLETE is not brought back, live or dormant, and its
  record is left alone ("Panels already complete are not touched");
* a second press is not a second retry (the queue holds a panel once);
* D-03's held-pass counter is restarted by BOTH of its halves: the streak
  and the reason the last held pass gave, which decides whether the very
  next held pass is "the same reason twice in a row" and sets the group
  aside at once;
* the panel's streak kinds start again, as the docstring says.

Each case names its mutant and the failure it produced, run from a byte
backup of the file named, restored and sha256-compared, the mutant's marker
grepped absent (#254).
"""
from __future__ import annotations

import types

import pytest

import astrodeck.api.app as app_module
from _flow_night import ZONE_S, FlowRig, ZonedTime, flow_rig  # noqa: F401
from _group_harness import T0, grid_plan, group_store  # noqa: F401
from _simhub import sim_hub  # noqa: F401
from astrodeck.events import night_key
from astrodeck.sequence import SequenceEngine
from astrodeck.sequence.group_rules import (CENTRING, HELD_PASS_SET_ASIDE_AT,
                                            GroupRun, PanelDeferred)
from astrodeck.sequence.models import TargetGroup
from astrodeck.sequence.session import Session, SessionFrame, session_store

FIRST, SECOND = "m31-mosaic", "second"
ROUTE = "/api/sequence/retry-set-aside"


def _two_groups(**kw):
    """Four panels in two groups: the first holds 1-1 (p00) and 1-2 (p01), the
    second 2-1 (p10) and 2-2 (p11)."""
    plan = grid_plan(panel_kw={"count": 3}, **kw)
    plan.groups.append(TargetGroup(id=SECOND, name="Second",
                                   geometry={"rows": 2, "cols": 2}))
    for t in plan.targets[2:]:
        t.mosaic_group = SECOND
    return plan


# ----------------------------------------------------------------- the engine

async def test_a_live_retry_keeps_to_its_group_leaves_a_complete_panel_and_is_idempotent(
        sim_hub):
    """1-1 and 1-2 are set aside in the first group, 2-1 in the second, and
    1-2 is COMPLETE (its frames are banked; a record of its set-aside
    stands, as it does for a panel set aside after its last frame). Naming
    the first group queues 1-1 alone: not the other group's 2-1, and not the
    complete 1-2, which owes nothing and has nothing to bring back. A second
    press queues nothing more and answers the same. ``null`` is every group,
    in each group's order. An unknown group is a ``KeyError``, and an engine
    with no run answers that there is none to hand it to.

    RED under mutant "group filter ignored" (``if group is not None and gid
    != group: continue`` made ``if False: continue``), observed:

        AssertionError: assert {'live': True...'1-1', '2-1']} == {'live': True...ued': ['1-1']}
          Differing items:
          {'queued': ['1-1', '2-1']} != {'queued': ['1-1']}

    RED under mutant "complete panel retried" (``if self._target_complete(i,
    t): continue`` replaced by ``pass``), observed:

        AssertionError: assert {'live': True...'1-1', '1-2']} == {'live': True...ued': ['1-1']}
          Differing items:
          {'queued': ['1-1', '1-2']} != {'queued': ['1-1']}

    RED under mutant "a second press queues it again" (``if pid not in
    self._pending_retries:`` made ``if True:``), observed:

        AssertionError: a second press queued it again
        assert ['p00', 'p00'] == ['p00']
          Left contains one more item: 'p00'
    """
    eng = SequenceEngine(sim_hub)
    plan = _two_groups()
    eng.plan = plan
    eng._session = Session(name="unit", plan=plan)
    names = {t.id: eng._panel_name(t) for t in plan.targets}
    assert names == {"p00": "1-1", "p01": "1-2", "p10": "2-1", "p11": "2-2"}
    for gid in (FIRST, SECOND):
        eng._group_runs[gid] = GroupRun(
            {t.id: names[t.id] for t in plan.targets if t.mosaic_group == gid},
            max_failed_visits=3)
    for pid in ("p00", "p01"):
        eng._group_runs[FIRST].set_aside_panel(pid, "gave up", kind="rejects")
    eng._group_runs[SECOND].set_aside_panel("p10", "gave up", kind="rejects")
    for s in plan.targets[1].steps:      # 1-2 banked everything it asked for
        eng._done[f"p01:{s.id}"] = s.count
    assert eng._target_complete(1, plan.targets[1]), "premise: 1-2 is complete"

    assert eng.retry_set_aside(FIRST) == {"live": False, "queued": []}, (
        "an engine with no run has nothing to hand a retry to")
    eng._task = types.SimpleNamespace(done=lambda: False)
    try:
        assert eng.running, "premise"
        assert eng.retry_set_aside(FIRST) == {"live": True, "queued": ["1-1"]}
        assert eng._pending_retries == ["p00"]
        assert eng.retry_set_aside(FIRST) == {"live": True, "queued": ["1-1"]}
        assert eng._pending_retries == ["p00"], "a second press queued it again"
        assert eng.retry_set_aside(None) == {"live": True,
                                             "queued": ["1-1", "2-1"]}
        assert eng._pending_retries == ["p00", "p10"]
        with pytest.raises(KeyError):
            eng.retry_set_aside("no-such-group")
    finally:
        eng._task = None


# ------------------------------------------------------------- the dormant route

def _pin_the_clock(rig: FlowRig) -> str:
    rig.mp.setattr(app_module, "time", ZonedTime(lambda: T0, ZONE_S))
    return night_key(T0)


def _stored(night: str) -> Session:
    """A dormant session over two groups with a record standing for 1-1, 1-2
    and 2-1; 1-2 is COMPLETE (every step has its frames)."""
    plan = _two_groups()
    s = Session(name="stored", plan=plan, status="dormant")
    for pid in ("p00", "p01", "p10"):
        s.note_set_aside(pid, "rejected every frame", night=night,
                         kind="rejects", ts=1.0)
    for st in plan.targets[1].steps:
        s.frames.extend(SessionFrame(target_id="p01", step_id=st.id,
                                     night="banked")
                        for _ in range(st.count))
    assert s.remaining()["p01-L"] == 0 and s.remaining()["p01-R"] == 0
    session_store.save(s)
    return s


def _cleared(sid: str) -> dict[str, bool | None]:
    return {r["target_id"]: r.get("cleared")
            for r in session_store.load(sid).set_aside}


async def _post(rig: FlowRig, **body):
    rig.as_role("operator")
    try:
        return await rig.client.post(ROUTE, json=body)
    finally:
        rig.as_role(None)


async def test_the_dormant_route_keeps_to_its_group_and_leaves_a_complete_panel_alone(
        flow_rig):
    """Naming the first group clears 1-1 alone: 2-1's record (another group)
    stands, and so does 1-2's, a panel that is complete and owes nothing,
    which is not touched. ``null`` then takes the other group's 2-1 and still
    leaves the complete panel, in the order the panels are in the plan; what
    is left is nothing to bring back, 409. On a session of its own ``null``
    takes both groups at once.

    RED under mutant "group filter ignored" (the route's ``(group is None or
    t.mosaic_group == group)`` made ``True``), observed:

        AssertionError: assert {'cleared': [...'live': False} == {'cleared': [...'live': False}
          Differing items:
          {'cleared': ['1-1', '2-1']} != {'cleared': ['1-1']}

    RED under mutant "complete panel cleared" (the route's ``and (sum(x.count
    for x in t.steps) == 0 or any(owed.get(x.id, 0) for x in t.steps))``
    made ``and True``), observed:

        AssertionError: assert {'cleared': [...'live': False} == {'cleared': [...'live': False}
          Differing items:
          {'cleared': ['1-1', '1-2']} != {'cleared': ['1-1']}
    """
    rig: FlowRig = flow_rig
    tonight = _pin_the_clock(rig)
    s = _stored(tonight)

    r = await _post(rig, group=FIRST, session_id=s.id)
    assert r.status_code == 200, r.text
    assert r.json() == {"cleared": ["1-1"], "live": False}
    assert _cleared(s.id) == {"p00": True, "p01": None, "p10": None}

    r = await _post(rig, group=None, session_id=s.id)
    assert r.status_code == 200, r.text
    assert r.json() == {"cleared": ["2-1"], "live": False}
    assert _cleared(s.id) == {"p00": True, "p01": None, "p10": True}, (
        "the complete panel's record was touched")

    r = await _post(rig, group=None, session_id=s.id)
    assert (r.status_code, r.json()["detail"]) == (409, "nothing is set aside")

    both = _stored(tonight)
    r = await _post(rig, group=None, session_id=both.id)
    assert r.status_code == 200, r.text
    assert r.json() == {"cleared": ["1-1", "2-1"], "live": False}
    assert _cleared(both.id) == {"p00": True, "p01": None, "p10": True}


# ---------------------------------------------------------------- the group rules

def _run(n: int = 3) -> GroupRun:
    return GroupRun({f"p{i}": f"1-{i}" for i in range(1, n + 1)},
                    max_failed_visits=3)


def test_a_retry_forgets_the_reason_the_last_held_pass_gave():
    """D-03 (#563): two held passes in a row that give the SAME rig-side
    reason set the group aside at once ("a rig-side fault, not the sky"). The
    reason of the last held pass is half the counter the operator restarts:
    the streak went back to 0 and the reason stayed, so the first held pass
    after a retry, giving the reason the pass before the retry had given,
    read as "twice in a row" and set both panels aside again, the retry
    undone before it was tried.

    The first pass here gives a reason (the solver is not found), the
    operator retries the other panel, and the next held pass gives the same
    reason: it is the FIRST of its streak, held, not set aside.

    RED under mutant "held reason kept" (``self._held_pass_reason = None``
    deleted from ``retry_set_aside``, ``held_streak = 0`` kept), observed:

        AssertionError: the first held pass after a retry is held, not set
        aside as a repeat: ('set_aside_all', 0)
        assert ('set_aside_all', 0) == ('centring_hold', 1)
    """
    miss = PanelDeferred("centring failed", kind=CENTRING,
                         last_error="solver not found")
    run = _run(2)
    run.set_aside_panel("p1", "floor", kind="floor")
    run.visit_outcome("p2", complete=False, exposures=0, accepted=0,
                      deferred=miss)
    end = run.close_pass()
    assert (end.boundary, end.held_streak) == ("centring_hold", 1)
    run.start_pass()
    assert run._held_pass_reason == "solver not found", "premise: a reason"
    run.retry_set_aside("p1")
    assert run.held_streak == 0
    for p in ("p1", "p2"):
        run.visit_outcome(p, complete=False, exposures=0, accepted=0,
                          deferred=miss)
    end = run.close_pass()
    assert (end.boundary, end.held_streak) == ("centring_hold", 1), (
        f"the first held pass after a retry is held, not set aside as a "
        f"repeat: {(end.boundary, end.held_streak)}")
    assert HELD_PASS_SET_ASIDE_AT > 1, "premise: one held pass sets nothing aside"
    assert run.live() == ["p1", "p2"]


def test_a_retry_starts_the_panels_streak_kinds_again():
    """The panel's deferral kinds this streak (``_streak_kinds``) decide how
    its next set-aside is worded and whether it is a centring one that
    expires; a retry is a clean slate, as ``expire_set_aside``'s is, and
    leaves none of them behind.

    RED under mutant "streak kinds kept" (``self._streak_kinds[panel] =
    set()`` deleted from ``retry_set_aside``), observed:

        AssertionError: assert {'centring', 'floor'} == set()
          Extra items in the left set: 'centring' 'floor'
    """
    run = _run(3)
    run._streak_kinds["p2"] = {CENTRING, "floor"}
    run.set_aside_panel("p2", "gave up", kind="rejects")
    run.retry_set_aside("p2")
    assert run._streak_kinds["p2"] == set()
