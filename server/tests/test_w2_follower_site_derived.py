"""Two follower defects in S2 T18, found by the verifier and left open
(#302, backlog WP-11 (b), owner-approved 2026-09-30):

1. A follower chosen to fill a meridian wait is no MEMBER of the group it
   fills in for (`_group_of` reads None for it), so `_site_timed`, which
   used to key on `_group_active` alone, read nothing while the follower's
   own hop ran: the hop line and the hop's own `_set_state` publish (both
   said at the moment every panel ran out of room before its flip point)
   went out unflagged.
2. `_follower_gate`'s ``after_group`` skip says "set aside tonight"
   whichever reason `_group_can_shoot_tonight` answered False for -- an
   escalation that actually set the group aside, OR every live member's
   window having simply closed for the night, which is a schedule fact,
   not a decision.

Case 1 runs the real clocked-simulator night (`tests/_group_harness.py`),
the shape `test_group_followers.py` (not owned by this work package) uses
for the same scenario; this file does not import from it, to keep its own
mutants independent of anyone else's mutation runs. Case 2 calls
`_after_group_verdict` directly against a hand-built group, since the
schedule machinery needed to make one panel's window close tonight while
another target depends on it in the ``after_group`` wiring is more
machinery than the two words under test are worth.
"""
from __future__ import annotations

from _group_harness import (GROUP_NAME, LON, T0, Night, grid_plan,  # noqa: F401
                            group_hub, group_store, panel, single)

from astrodeck.sequence.engine import SequenceEngine
from astrodeck.sequence.group_rules import GroupRun
from astrodeck.sequence.models import SequencePlan, TargetGroup

FOLLOWER = "Follower"


def _plan(*, follower_count: int = 40) -> SequencePlan:
    """A one-panel mosaic 8 min before transit (L and R, count 3) and one
    plain-order follower after it -- enough room for the hop estimate and
    one frame to NOT fit before the crossing, so the follower is chosen to
    fill the wait (the same shape as
    ``test_group_followers.py::test_a_follower_fills_the_wait_and_hands_the_night_back``,
    reproduced rather than imported; see the module docstring)."""
    after = single(FOLLOWER, count=follower_count)
    return grid_plan(rows=1, cols=1,
                     panel_kw={"ha_h": -8.0 / 60.0, "ha_step_h": 0.0,
                              "count": 3},
                     after=[after], meridian_flip=True)


async def _night(hub, monkeypatch, plan, *, wall_s: float = 60.0) -> Night:
    night = Night(hub, monkeypatch, coords_clock=True)
    try:
        night.done = await night.run(plan, wall_s=wall_s)
    finally:
        await night.close()
    return night


# ------------------------------------------------- the follower's own hop


async def test_a_followers_hop_into_the_wait_is_flagged_site_derived(
        group_hub, monkeypatch):
    """The mosaic waits 8 min for its crossing; the follower chosen to
    fill it is set up by the same `_setup_target` a member's hop uses, and
    that hop lands at the moment every panel ran out of room -- the same
    class of moment `_note_meridian_waits`'s own lines already carry.

    MUTANT "follower hop unflagged" (`_setup_target`'s condition read back
    to ``self._site_timed() and self._group_of(target) is not None``, the
    code before #302): RED (observed):
        AssertionError: the follower's hop line was not flagged: [{'level':
        'info', 'message': 'target 2/2: Follower', 'source': 'sequence'}]
    """
    from astrodeck.api.redact import _redact_log_rows_for, _redact_ws_event
    from astrodeck.auth import principal_for_role
    viewer = principal_for_role("viewer")

    night = await _night(group_hub, monkeypatch, _plan())
    assert night.done, night.trace[-3:]

    logs = [e for e in night.events if e["type"] == "log"
            and e["data"].get("source") == "sequence"]
    hops = [e for e in logs
           if e["data"].get("message") == f"target 2/2: {FOLLOWER}"]
    # TWO hops to the follower this night: this one, into the wait, and a
    # later one once the mosaic is done and the follower runs its course
    # to completion -- unflagged, correctly, since nothing waits on it by
    # then. The first is the one #302 is about.
    assert len(hops) == 2, (
        f"premise: the follower is hopped to twice: {logs}")
    hop = hops[0]
    assert hop["data"].get("site_derived") is True, (
        f"the follower's hop into the wait was not flagged: "
        f"{ {k: v for k, v in hop['data'].items() if k != 'ts'} }")
    assert hops[1]["data"].get("site_derived") is not True, (
        f"premise: the follower's later, unbounded hop is not site-timed: "
        f"{hops[1]['data']}")
    seen = _redact_log_rows_for(logs, viewer)
    assert not any(r is hop for r in seen), (
        "a viewer's /api/logs carried the follower's hop line")

    seq = [e for e in night.events if e["type"] == "sequence"]
    setups = [e for e in seq
             if e["data"].get("detail") == f"slewing to {FOLLOWER}"]
    assert len(setups) == 2, (
        f"premise: the follower's setup published twice: "
        f"{[e['data'].get('detail') for e in seq]}")
    setup = setups[0]
    assert setup["data"].get("site_derived") is True, (
        f"the follower's setup publish was not flagged: "
        f"{ {k: v for k, v in setup['data'].items() if k != 'ts'} }")
    assert _redact_ws_event(setup, viewer) is None, (
        "a viewer's WS received the follower's setup publish")


async def test_the_followers_own_frames_are_not_flagged(group_hub,
                                                        monkeypatch):
    """Control: once set up, the follower shoots its OWN exposures, timed
    by its own exposures and not by the crossing -- #302 names the hop
    only ("its hop line and its first publishes"). Pins that
    `_follower_group_active` is cleared the moment the hop ends, which
    would break if it rode the whole bounded visit instead.

    MUTANT "the flag rides the whole visit" (`_visit_follower`'s
    ``finally`` moved from around ``await self._hop(ti, target)`` alone to
    around it and ``await self._run_steps(...)`` together): RED (observed,
    truncated):
        AssertionError: a follower frame published after its own hop was
        flagged: ['Follower: L 30s  [1/40]', 'Follower: L 30s  [1/40]',
        'Follower: L 30s  [2/40]', ...] (30 states in all, every one of the
        follower's own frame publishes before the mosaic's panel was due)
    """
    night = await _night(group_hub, monkeypatch, _plan())
    assert night.done, night.trace[-3:]

    seq = [e for e in night.events if e["type"] == "sequence"]
    # The follower is no group member, so `_group_active` (and so
    # `state.group`) is None for the length of its own run (`_note_group_
    # selected`); filtering on ``group is None`` excludes the mosaic's own
    # republished wait state, which still carries a stale ``target:
    # "Follower"`` the merge never clears until a member is ready again.
    follower_frames = [e for e in seq
                       if e["data"].get("target") == FOLLOWER
                       and e["data"].get("detail") != f"slewing to {FOLLOWER}"
                       and e["data"].get("group") is None]
    assert follower_frames, (
        "premise: the follower published states past its own setup")
    flagged = [e for e in follower_frames if e["data"].get("site_derived")]
    assert flagged == [], (
        f"a follower frame published after its own hop was flagged: "
        f"{[e['data'].get('detail') for e in flagged]}")


# ------------------------------------------- the after_group skip's words


def _after_group_engine(*, member_live: bool) -> tuple[SequenceEngine, str]:
    """A bare engine with one group of one member, live (its window has
    simply closed tonight, ``gs_now`` reading ``window_closed``) or set
    aside (an escalation decided it, ``run.set_aside``). Neither
    `_group_runs` nor the group's own member needs a plan, a hub tick or a
    clock: `_after_group_verdict` reads only `self._groups`/
    `self._group_runs` and the two arguments it is handed."""
    import types
    e = SequenceEngine(types.SimpleNamespace())
    group = TargetGroup(id="g1", name="Mosaic",
                        geometry={"rows": 1, "cols": 1})
    t = panel(0, 0, group_id="g1", count=1)
    run = GroupRun({t.id: "1-1"}, max_failed_visits=3)
    if not member_live:
        run.set_aside[t.id] = "escalated"
    e._groups = {"g1": group}
    e._group_runs = {"g1": run}
    remaining = [t]
    gs_now = {id(t): {"state": "window_closed" if member_live else "ready"}}
    return e, group.id, run, t, remaining, gs_now


def test_the_skip_says_out_of_window_when_a_live_member_remains():
    """Every member is still LIVE (no escalation set anything aside): the
    mosaic simply has no live panel whose window is open tonight. The
    follower's skip must not claim a set-aside that never happened (#302
    item 2).

    MUTANT "always set aside" (`_after_group_verdict`'s skip reading back
    to the fixed "set aside tonight" wording, the code before #302): RED
    (observed):
        AssertionError: the skip claimed a set-aside with a live member
        still in play: 'the Mosaic mosaic it waits for is set aside
        tonight'
    """
    e, gid, run, t, remaining, gs_now = _after_group_engine(member_live=True)
    gate = e._after_group_verdict(gid, remaining, gs_now)
    assert gate.kind == "skip", gate
    assert gate.reason == "the Mosaic mosaic it waits for is out of window tonight", (
        f"the skip claimed a set-aside with a live member still in play: "
        f"{gate.reason!r}")


def test_the_skip_says_set_aside_when_no_live_member_remains():
    """The known positive: the member really was set aside (an escalation,
    #302 item 2's actual "set aside" case), so the words stand."""
    e, gid, run, t, remaining, gs_now = _after_group_engine(member_live=False)
    gate = e._after_group_verdict(gid, remaining, gs_now)
    assert gate.kind == "skip", gate
    assert gate.reason == "the Mosaic mosaic it waits for is set aside tonight", (
        f"premise: a real set-aside still reads as one: {gate.reason!r}")
