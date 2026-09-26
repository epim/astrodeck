"""A follower fills a mosaic's deferral wait (#304; spec 1.6, 5.1 pass
boundary item 2, 6.4, 6.18).

WHAT WAS WRONG. A pass in which every visited panel deferred (a centring that
would not converge, a guider that would not start) and nothing was shot ends
in a wait of ``DEFER_WAIT_S`` before the next pass. S2 built that wait as one
``await self._wait_until(now + DEFER_WAIT_S)`` INSIDE the pass boundary,
`_close_group_pass`, so for its five minutes the scheduler could choose
nothing: a follower that was ready, and that the owner's default ("shoot
later targets, then come back", Revision 2 ruling 1) exists to shoot in
exactly such a gap, waited it out with the group. Three all-deferred passes
set every panel aside, so a mosaic that could not be shot tonight cost ten
minutes of idle sky before its followers had the night.

WHAT IT DOES NOW. The boundary sets the wait's end on the group
(`GroupRun.defer_next_pass`) and returns. `_eligibility_now` holds each
member that could otherwise be visited as a waiter until that end, so the
scheduler's own do-while wait path runs it, with the safety gate and the idle
watch on every tick, and `_group_ready_ts` counts its end, so a ready
follower is picked in a visit that hands the cursor back when the next pass
may begin.

THE RUNS are the real `_run_scheduled` on the clocked simulator
(tests/_group_harness.py). The mosaic is a 1x2 whose panels never centre, so
every pass defers both and the group is set aside after three passes
(``max_failed_visits``). A follower, when there is one, is a plain target
after the mosaic in plan order, 40 frames of L.

Each case names the mutant it was shown RED under, with the failure observed,
verbatim. Every mutant was applied in a private scratch copy of server/
(scratchpad s3-ec-mut-q7v2), never in the shared tree (#254):

* "blocking wait": `_close_group_pass`'s defer_wait branch back to S2's
  ``await self._wait_until(time.time() + DEFER_WAIT_S)``, no
  ``defer_next_pass``.
* "no hold": `_eligibility_now` without its deferral branch, so the wait's
  end is set and nothing waits on it.
* "the wait not counted": `_group_ready_ts` without its ``max`` against the
  wait's end.
* "early teardown": the scheduler's planned-wait park-hold applied to the
  deferral wait too (its ``and not deferral`` removed).
* "the wait resets the failures": `GroupRun.defer_next_pass` also zeroing
  every panel's ``failed`` count.
* "no defer publish" (added by the S3-EC verifier, scratchpad
  s3-ec-verify-k4m9): `_publish_group_wait` without its ``"defer"`` branch,
  so the scheduler's wait publishes the generic "waiting for <panel>".
"""
from __future__ import annotations

import pytest

import astrodeck.sequence.engine as engine_mod
from _group_harness import (GROUP_NAME, T0, Night, grid_plan, group_hub,
                            group_store, single)
from astrodeck.sequence.group_rules import DEFER_WAIT_S
from astrodeck.sequence.session import session_store

FOLLOWER = "Follower"
PANELS = (f"{GROUP_NAME} 1-1", f"{GROUP_NAME} 1-2")
WAITING = f"waiting {DEFER_WAIT_S:.0f} s before the next pass"


def _never_centres(who, n, result):
    """Every panel's centring fails, on every hop; the follower centres."""
    if who.startswith(GROUP_NAME + " "):
        return dict(result, centered=False, error_arcmin=None)
    return result


def _plan(*, follower: bool = True, panel_kw: dict | None = None):
    """A 1x2 mosaic 2 h east of the meridian, far from any flip, and, when
    ``follower``, one plain target after it: 40 frames of L."""
    after = [single(FOLLOWER, count=40)] if follower else []
    return grid_plan(rows=1, cols=2, after=after, panel_kw=panel_kw)


async def _night(hub, monkeypatch, plan, *, before_run=None,
                 wall_s: float = 60.0) -> Night:
    night = Night(hub, monkeypatch, goto=_never_centres)
    if before_run is not None:
        before_run(night)
    try:
        night.done = await night.run(plan, wall_s=wall_s)
    finally:
        await night.close()
    night.stored = session_store.load(night.session_id)
    return night


def _record_bounds(night, into: list) -> None:
    real = night.engine._run_visit

    async def run_visit(ti, target, visit):
        into.append((night.clock.t, target.name, visit))
        return await real(ti, target, visit)

    night.mp.setattr(night.engine, "_run_visit", run_visit)


def _waits(night) -> list[float]:
    """When each deferral wait began: the fake time of the boundary's line."""
    return [t for t, _lvl, m in night.lines if WAITING in m]


def _panel_hops(night) -> list[float]:
    return [t for t, who in night.gotos if who in PANELS]


# ----------------------------------------------- the follower takes the gap

async def test_a_ready_follower_shoots_through_the_deferral_wait(group_hub,
                                                                 monkeypatch):
    """Both panels defer on every pass. At each pass boundary the follower
    is chosen and runs ONE visit bounded by the wait's end, ``DEFER_WAIT_S``
    after the boundary: its frames all end by then, it hands the cursor
    back, and the next pass's first hop comes AT the wait's end. After the
    third pass both panels are set aside (``max_failed_visits``), and the
    follower then runs its course.

    MUTANT "blocking wait": RED (observed; the follower's only hop comes
    after the third pass, once the group is set aside):
        AssertionError: the follower shot nothing in the deferral wait that
        began 0 s in: [(0.0, 'M31 1-1'), (0.0, 'M31 1-2'), (300.0, 'M31
        1-1'), (300.0, 'M31 1-2'), (600.0, 'M31 1-1'), (600.0, 'M31 1-2'),
        (600.0, 'Follower')]
        assert []
    MUTANT "no hold": RED (observed):
        AssertionError: pass 2 began at None s, the wait began at 0.0 s: hops
        at [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        assert (None is not None)
    MUTANT "the wait resets the failures": RED at the 16 h fake horizon
    (observed; the passes never end):
        AssertionError: [[57420.0, 'log', 'info', 'M31 1-2: nothing has been
        shot for a while and the mount is still tracking it — stopping
        tracking until the next target is set up'], [57420.0, 'guider',
        'stop', None], [57420.0, 'tracking', False]]
        assert False
         +  where False = <_group_harness.Night object at
        0x000001FEE6741460>.done
    """
    bounds: list = []
    night = await _night(group_hub, monkeypatch, _plan(),
                         before_run=lambda n: _record_bounds(n, bounds))
    assert night.done, night.trace[-3:]
    waits = _waits(night)
    assert len(waits) == 2, night.said("pass")
    hops = _panel_hops(night)
    # Premise: three passes of two deferred hops each, as the control has.
    assert len(hops) == 6, [(night.rel(t), w) for t, w in night.gotos]
    for k, began in enumerate(waits):
        ends = began + DEFER_WAIT_S
        # The next pass comes AT the wait's end, not before it.
        nxt = min((t for t in hops if t > began), default=None)
        assert nxt is not None and ends <= nxt <= ends + 1.0, (
            f"pass {k + 2} began at {nxt and night.rel(nxt)} s, the wait "
            f"began at {night.rel(began)} s: hops at "
            f"{[night.rel(t) for t in hops]}")
        shot = [c for c in night.captures if c["target"] == FOLLOWER
                and began <= c["t"] < ends]
        assert shot, (
            f"the follower shot nothing in the deferral wait that began "
            f"{night.rel(began):.0f} s in: "
            f"{[(night.rel(t), w) for t, w in night.gotos]}")
        # Handed back AT the wait's end: every frame is over by then, and
        # the next one would not have fitted (a 30 s frame, the 12 s
        # overhead seed and the 30 s margin).
        last_end = max(c["t"] + c["exposure_s"] for c in shot)
        assert last_end <= ends, (last_end - ends)
        assert ends - last_end < 30.0 + 12.0 + 30.0, (
            f"the follower stopped {ends - last_end:.0f} s before the wait "
            f"ended")
        fb = [vb for t, name, vb in bounds
              if name == FOLLOWER and began <= t < ends]
        assert len(fb) == 1, fb
        assert fb[0].passes is None and fb[0].deadline_ts == pytest.approx(
            ends, abs=1e-6), fb
    # The follower keeps what it banked and finishes once the mosaic is set
    # aside; the mosaic still owes, so the session stays dormant.
    assert len([c for c in night.captures if c["target"] == FOLLOWER]) == 40
    assert sorted(r["target_id"] for r in night.stored.set_aside) == [
        "p00", "p01"], night.stored.set_aside
    assert night.stored.status == "dormant", night.stored.status
    assert len(night.said("every panel waits; shooting Follower until the "
                          "next panel is due")) == 2


async def test_a_member_due_before_the_wait_ends_does_not_cut_the_bound(
        group_hub, monkeypatch):
    """The end of the wait JOINS the times ``group_ready_ts`` takes the
    earliest of (spec 1.6): 1-2's window opens 171 s after the night
    starts, inside the first deferral wait, but it cannot be visited before
    the wait ends either, so the follower's bound is still the wait's end,
    and it fills the wait in one visit. Pass 1 is 1-1 alone (1-2 was not
    open), so the first wait begins after 1-1's deferred hop.

    MUTANT "the wait not counted": RED (observed; bounded by 1-2's opening,
    171 s in, the follower had no room for a hop and a frame, and by then
    the wait's end was as near, so it never shot in the wait):
        AssertionError: bounded visits in the first wait, as (start, target,
        deadline): []
        assert 0 == 1
         +  where 0 = len([])
    MUTANT "blocking wait": RED (observed), on the same line, the same
    words.
    MUTANT "no hold": RED (observed):
        AssertionError: the follower's hops in the first wait: [0.0, 240.0]
        assert 2 == 1
         +  where 2 = len([0.0, 240.0])
    """
    import time as _time

    from astrodeck.sequence import schedule
    opens = T0 + 171.0
    hhmm = _time.strftime("%H:%M", _time.localtime(opens))
    plan = _plan()
    one_two = plan.targets[1]
    one_two.schedule = one_two.schedule.model_copy(
        update={"start_mode": "time", "start_time": hhmm})
    start, _stop = schedule.resolve_window(
        one_two.schedule, {"latitude": 40.0, "longitude": -74.0}, -12.0, T0)
    assert start == opens, f"premise: 1-2 opens at {start - T0:.0f} s"
    bounds: list = []
    night = await _night(group_hub, monkeypatch, plan,
                         before_run=lambda n: _record_bounds(n, bounds))
    assert night.done, night.trace[-3:]
    first = _waits(night)[0]
    ends = first + DEFER_WAIT_S
    assert first < opens < ends, "premise: 1-2 opens inside the first wait"
    seen = [(night.rel(t), name, None if vb.deadline_ts is None
             else night.rel(vb.deadline_ts))
            for t, name, vb in bounds if t < ends]
    fb = [d for _t, name, d in seen if name == FOLLOWER]
    assert len(fb) == 1, (
        f"bounded visits in the first wait, as (start, target, deadline): "
        f"{seen}")
    assert fb[0] == night.rel(ends), seen
    follower_hops = [night.rel(t) for t, who in night.gotos
                     if who == FOLLOWER and first <= t < ends]
    assert len(follower_hops) == 1, (
        f"the follower's hops in the first wait: {follower_hops}")
    assert min(t for t in _panel_hops(night) if t > first) == pytest.approx(
        ends, abs=1.0)


# ------------------------------------------------------------ the control

async def test_with_no_follower_the_wait_is_still_the_whole_wait(group_hub,
                                                                 monkeypatch):
    """CONTROL, the case nothing about #304 should change: no follower. Each
    deferral wait still lasts exactly ``DEFER_WAIT_S``; the safety gate is
    asked on every ``SCHEDULE_WAIT_STEP_S`` tick of it; the mount, left
    tracking the panel whose hop deferred, is stopped by the IDLE CLOCK,
    ``WAIT_TEARDOWN_S`` after that hop and no earlier, and stays stopped
    until the next pass's hop; the state left published through it is the
    boundary's sentence; and ``max_failed_visits`` still bounds the passes:
    three, two waits, both panels set aside, nothing shot.

    It is GREEN under "blocking wait" (observed, "2 failed, 1 passed", this
    case the one that passed): with no follower, this is S2's night.

    MUTANT "no hold": RED (observed):
        AssertionError: the next pass began 0 s after the wait did: hops at
        [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        assert 0.0 >= 300.0
    MUTANT "early teardown": RED (observed):
        AssertionError: the mount was stopped 0 s after the last hop, not on
        the idle clock
        assert 120.0 <= 0.0
    MUTANT "the wait resets the failures": RED at the 16 h fake horizon
    (observed; the passes never end):
        AssertionError: [[57420.0, 'log', 'info', 'M31 1-2: nothing has been
        shot for a while and the mount is still tracking it — stopping
        tracking until the next target is set up'], [57420.0, 'guider',
        'stop', None], [57420.0, 'tracking', False]]
        assert False
         +  where False = <_group_harness.Night object at
        0x000001FEB58E7170>.done
    MUTANT "no defer publish": RED (observed; it stays GREEN under "blocking
    wait", which is S2's publish):
        AssertionError: the wait that began 0 s in left published: ['M31:
        every panel was deferred this pass; waiting before the next',
        'waiting for M31 1-1']
        assert ['waiting for M31 1-1'] == ['M31: every ...ore the next']
    """
    night = await _night(group_hub, monkeypatch, _plan(follower=False))
    assert night.done, night.trace[-3:]
    waits = _waits(night)
    hops = _panel_hops(night)
    assert len(hops) == 6, [(night.rel(t), w) for t, w in night.gotos]
    assert len(waits) == 2, night.said("pass")
    step = engine_mod.SCHEDULE_WAIT_STEP_S
    for began in waits:
        nxt = min((t for t in hops if t > began), default=began)
        gap = nxt - began
        assert gap >= DEFER_WAIT_S, (
            f"the next pass began {gap:.0f} s after the wait did: hops at "
            f"{[night.rel(t) for t in hops]}")
        assert gap <= DEFER_WAIT_S + 1.0, gap
        ticks = [t for t, ctx in night.gates
                 if began <= t < nxt and ctx == "frame"]
        assert len(ticks) >= DEFER_WAIT_S / step, len(ticks)
        # Tracking off, once, on the idle clock, between the last hop of
        # the pass and the next pass's first.
        last_hop = max(t for t in hops if t <= began)
        offs = [night.t0 + e[0] for e in night.trace
                if e[1] == "tracking" and e[2] is False
                and last_hop <= night.t0 + e[0] < nxt]
        assert offs, "the mount was left tracking through the wait"
        off = offs[0] - last_hop
        teardown = engine_mod.WAIT_TEARDOWN_S
        assert teardown <= off, (
            f"the mount was stopped {off:.0f} s after the last hop, not on "
            f"the idle clock")
        assert off <= teardown + step + 1.0, off
        # WHAT THE WAIT LEAVES PUBLISHED up to the next pass's hop: the
        # boundary's sentence, word for word as S2 left it, now published
        # again by the scheduler's wait (`_publish_defer_wait`).
        details = [e["data"].get("detail") for e in night.events
                   if e["type"] == "sequence" and e["ts"] < nxt - 0.5
                   and "detail" in e["data"]]
        assert details[-1:] == [f"{GROUP_NAME}: every panel was deferred "
                                f"this pass; waiting before the next"], (
            f"the wait that began {night.rel(began):.0f} s in left "
            f"published: {details[-2:]}")
    assert len(night.said("nothing has been shot for a while")) == 2
    assert night.said("the next target is a long wait away") == []
    assert night.captures == [], night.captures[:2]
    assert sorted(r["target_id"] for r in night.stored.set_aside) == [
        "p00", "p01"], night.stored.set_aside
    assert night.stored.status == "dormant", night.stored.status
